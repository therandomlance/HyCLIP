import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import HyCLIP_DB
from model import HyCLIP_Model
from config import HyCLIP_Config
from webui.hydrus_api import build_router

db = HyCLIP_DB()
config = HyCLIP_Config()
model = HyCLIP_Model(config.CLIP_MODEL)

@asynccontextmanager
async def lifespan(_):
	if config.LOAD_MODEL_ON_STARTUP:
		model.load_model()
	print(f"Web UI: {config.HYCLIP_API_URL.rstrip('/')}/webui/index.html")
	yield

app = FastAPI(lifespan=lifespan)


# ===== Request Bodies =====
class PathRequest(BaseModel):
	path: str

class TextRequest(BaseModel):
	text: str

class IngestRequest(BaseModel):
	hash_id: int
	path: str

class IngestBatchRequest(BaseModel):
	items: list[IngestRequest]

class CreateBucketRequest(BaseModel):
	bucket_name: str

class RenameBucketRequest(BaseModel):
	bucket_id: int
	bucket_name: str

class InsertBucketRequest(BaseModel):
	bucket_id: int
	hash_ids: list[int]

class SearchRequest(BaseModel):
	embedding: list[float]
	num_results: int = 100

class ProcessBatchRequest(BaseModel):
	batch_size: int | None = None  # falls back to config.INGEST_BATCH_SIZE

class SearchBucketRequest(BaseModel):
	embedding: list[float]
	bucket_id: int
	num_results: int = 100

class UpdateConfigRequest(BaseModel):
	updates: dict


# ===== Helpers =====
def _assert_model_loaded():
	if model.model is None:
		raise HTTPException(status_code=409, detail="model not loaded")

def _assert_hash_id(hash_id:int):
	if not db.exists_hash_id(hash_id):
		raise HTTPException(status_code=404, detail=f"hash_id not found: {hash_id}")

def _assert_bucket_id(bucket_id:int):
	if not db.exists_bucket(bucket_id):
		raise HTTPException(status_code=404, detail=f"bucket_id not found: {bucket_id}")

# ===== Server =====
@app.get("/")
def read_root():
	return {"Hello": "World"}

@app.post("/exit")
def exit():
	model.unload_model()
	db.commit()
	os._exit(0)


# ===== Model =====
@app.post("/load_model")
def load_model():
	model.load_model()
	return {"model": model.model_name, "loaded": True}

@app.post("/unload_model")
def unload_model():
	model.unload_model()
	return {"model": model.model_name, "loaded": False}

@app.get("/model_status")
def model_status():
	return {"model": model.model_name, "loaded": model.model is not None}


# ===== Eval =====
@app.post("/eval_image")
def eval_image(req: PathRequest):
	_assert_model_loaded()
	return model.eval_image(req.path)

@app.post("/eval_text")
def eval_text(req: TextRequest):
	_assert_model_loaded()
	return model.tokenize_text(req.text)


# ===== Ingest =====
@app.post("/ingest_image")
def ingest_image(req: IngestRequest):
	_assert_model_loaded()

	hash_id = req.hash_id
	path = req.path
	
	if db.exists_hash_id(hash_id):
		return {"hash_id": hash_id, "status": "already_ingested"}
	if not model.check_filetype(path):
		return {"hash_id": hash_id, "status": "skipped"}
	try:
		embedding = model.eval_image(path)
	except FileNotFoundError: 
		raise HTTPException(status_code=404, detail=f"file not found: {path}")
	except RuntimeError as E:
		raise HTTPException(status_code=409, detail=E)
	if embedding is None:
		return {"hash_id": hash_id, "status": "failed"}

	db.insert_embedding(hash_id, embedding)
	db.quant_status = "needs_quant"
	db.commit()
	db.dequeue_hashes([hash_id])

	return {"hash_id": hash_id, "status": "ingested"}

@app.post("/ingest_image_batch")
def ingest_image_batch(req: IngestBatchRequest):
	_assert_model_loaded()

	results = []
	to_eval = []

	for i, item in enumerate(req.items):
		if not os.path.exists(item.path):
			raise HTTPException(status_code=404, detail=f"file not found: {item.path}")
		if db.exists_hash_id(item.hash_id):
			results.append({"hash_id": item.hash_id, "status": "already_ingested"})
			continue
		if not model.check_filetype(item.path):
			print(f"skipping unsupported filetype: {item.path}")
			results.append({"hash_id": item.hash_id, "status": "skipped"})
			continue
		to_eval.append(i)

	embeddings = model.eval_image_batch([req.items[i].path for i in to_eval], config.EVAL_WORKERS)

	inserts = []
	for i, embedding in zip(to_eval, embeddings):
		item = req.items[i]
		if embedding is None:
			results.append({"hash_id": item.hash_id, "status": "failed"})
			continue
		inserts.append((item.hash_id, embedding))
		results.append({"hash_id": item.hash_id, "status": "ingested"})

	for hash_id, embedding in inserts:
		db.insert_embedding(hash_id, embedding)

	db.quant_status = "needs_quant"
	db.commit()
	db.dequeue_hashes([hash_id for hash_id, _ in inserts])

	return results


# ===== Ingest Queue =====
@app.get("/queue_status")
def queue_status():
	return {"queued": db.get_num_queue() or 0}

@app.post("/work_queue")
def work_queue(req: ProcessBatchRequest):
	"""Process one batch from the persistent queue; the caller loops for progress."""
	_assert_model_loaded()

	batch = db.get_next_queue(req.batch_size or config.INGEST_BATCH_SIZE) or []
	if isinstance(batch, tuple):
		batch = [batch]

	ingested = exists = skipped = errors = 0
	to_eval = []
	for hash_id, path in batch:
		if not os.path.exists(path):
			errors += 1
			continue
		if db.exists_hash_id(hash_id):
			exists += 1
			continue
		if not model.check_filetype(path):
			skipped += 1
			continue
		to_eval.append((hash_id, path))

	for hash_id, embedding in zip([h for h, _ in to_eval], model.eval_image_batch([p for _, p in to_eval], config.EVAL_WORKERS)):
		if embedding is None:
			errors += 1
			continue
		db.insert_embedding(hash_id, embedding)
		ingested += 1

	db.quant_status = "needs_quant"
	db.commit()
	db.dequeue_hashes([fid for fid, _ in batch])

	return {"processed": len(batch), "ingested": ingested, "already_ingested": exists,
		"skipped": skipped, "errors": errors, "remaining": db.get_num_queue() or 0}


# ===== Buckets =====
@app.post("/create_bucket")
def create_bucket(req: CreateBucketRequest):
	bucket_id = db.new_bucket(req.bucket_name)
	return {"bucket_id": bucket_id}

@app.post("/rename_bucket")
def rename_bucket(req: RenameBucketRequest):
	_assert_bucket_id(req.bucket_id)
	db.rename_bucket(req.bucket_id, req.bucket_name)
	return {"bucket_id": req.bucket_id, "bucket_name": req.bucket_name}

@app.post("/insert_into_bucket")
def insert_into_bucket(req: InsertBucketRequest):
	_assert_bucket_id(req.bucket_id)
	# TODO add options for non-inserted hash_ids: strict, loose, deferred
	unknown = db.add_to_bucket(req.bucket_id, req.hash_ids)
	return {"bucket_id": req.bucket_id, "inserted": len(req.hash_ids) - len(unknown), "unknown": unknown}

@app.post("/remove_from_bucket")
def remove_from_bucket(req: InsertBucketRequest):
	_assert_bucket_id(req.bucket_id)
	db.remove_from_bucket(req.bucket_id, req.hash_ids)
	return {"bucket_id": req.bucket_id, "removed": len(req.hash_ids)}

@app.get("/list_buckets")
def list_buckets():
	return db.get_buckets()

@app.get("/list_bucket_members")
def list_bucket_members(bucket_id: int):
	_assert_bucket_id(bucket_id)
	return db.get_bucket_members(bucket_id)

@app.get("/get_bucket_membership")
def get_bucket_membership(hash_id: int):
	_assert_hash_id(hash_id)
	return db.get_bucket_membership(hash_id)

@app.post("/delete_bucket")
def delete_bucket(bucket_id: int):
	_assert_bucket_id(bucket_id)
	db.remove_bucket(bucket_id)
	return {"bucket_id": bucket_id, "deleted": True}


# ===== Embeddings =====
@app.get("/get_embedding")
def get_embedding(hash_id: int):
	_assert_hash_id(hash_id)
	return db.get_embedding(hash_id)

@app.post("/delete_hash")
def delete_hash(hash_id: int):
	_assert_hash_id(hash_id)
	db.remove_embedding(hash_id)
	return {"hash_id": hash_id, "deleted": True}


# ===== Search =====
@app.get("/num_embeddings")
def num_embeddings():
	return db.get_num_embeddings()

@app.get("/db_status")
def db_status():
	return {"quant_status": db.quant_status, "last_search": db.last_search}

@app.post("/search")
def search(req: SearchRequest):
	return db.search_global(req.embedding, model.dims, req.num_results)

@app.post("/search_bucket")
def search_bucket(req: SearchBucketRequest):
	return db.search_bucket(req.embedding, req.bucket_id, model.dims, req.num_results)


# ===== Config =====
@app.get("/get_config")
def get_config():
	return config.cfg

@app.post("/update_config")
def update_config(req: UpdateConfigRequest):
	unknown = set(req.updates) - set(config.cfg)
	if unknown:
		raise HTTPException(status_code=400, detail=f"unknown config keys: {sorted(unknown)}")
	for key, value in req.updates.items():
		setattr(config, key, value)
	config.save_config()
	return config.cfg


# ===== Web UI / Hydrus proxy =====
app.include_router(build_router())

# Mounted last so API routes take precedence
app.mount("/webui", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "webui"), html=True), name="webui")
