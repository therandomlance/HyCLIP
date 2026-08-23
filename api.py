import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager

import hydrus_api
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator import Orchestrator

ORCH = Orchestrator()

@asynccontextmanager
async def lifespan(_):
	if ORCH.CFG.LOAD_MODEL_ON_STARTUP:
		ORCH.MODEL.load_model()
	print(f"Web UI: {ORCH.CFG.HYCLIP_API_URL.rstrip('/')}/webui/index.html")
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

class SearchIDRequest(BaseModel):
	hash_id: int
	num_results: int = 100

class SearchIDBucketRequest(BaseModel):
	hash_id: int
	bucket_id: int
	num_results: int = 100

class ProcessBatchRequest(BaseModel):
	batch_size: int | None = None  # falls back to ORCH.CFG.INGEST_BATCH_SIZE

class SearchBucketRequest(BaseModel):
	embedding: list[float]
	bucket_id: int
	num_results: int = 100

class UpdateConfigRequest(BaseModel):
	updates: dict

class IngestEnqueueRequest(BaseModel):
	tag: str = "hyclip:ingest"
	max_evaluate: int | None = None
	remove_tag: bool = True

class AddHashesToBucketRequest(BaseModel):
	bucket_id: int
	hashes: list[str]

class MakeTagRequest(BaseModel):
	tag: str
	search_limit: int = 1000


# ===== Helpers =====
def _assert_model_loaded():
	if ORCH.MODEL.model is None:
		raise HTTPException(status_code=409, detail="model not loaded")

def _assert_hash_id(hash_id:int):
	if not ORCH.DB.exists_hash_id(hash_id):
		raise HTTPException(status_code=404, detail=f"hash_id not found: {hash_id}")

def _assert_bucket_id(bucket_id:int):
	if not ORCH.DB.exists_bucket(bucket_id):
		raise HTTPException(status_code=404, detail=f"bucket_id not found: {bucket_id}")

def _assert_tag(tag:str):
	if not ORCH.DB.exists_tag(tag):
		raise HTTPException(status_code=404, detail=f"tag not found: {tag}")

def _assert_hydrus():
	if not ORCH.CFG.API_KEY:
		raise HTTPException(status_code=503, detail="hydrus API_KEY not configured")
	if not ORCH.CFG.API_URL:
		raise HTTPException(status_code=503, detail="hydrus API_URL not configured")

def _assert_tag_service():
	if not ORCH.CFG.TAG_SERVICE_KEY:
		raise HTTPException(status_code=503, detail="TAG_SERVICE_KEY not configured")

def _require_model():
	if ORCH.MODEL.model is None:
		raise HTTPException(status_code=409, detail="model not loaded")

def _hydrus_file(r, fallback_type: str):
	return Response(content=r.content, media_type=r.headers.get("Content-Type", fallback_type))

# ===== Server =====
@app.get("/")
def read_root():
	return {"Hello": "World"}

@app.post("/exit")
def exit():
	ORCH.shutdown()
	os._exit(0)


# ===== Model =====
@app.post("/load_model")
def load_model():
	if ORCH.MODEL.model is None:
		ORCH.MODEL.load_model()
	return {"model": ORCH.MODEL.model_name, "loaded": ORCH.MODEL.model is not None}

@app.post("/unload_model")
def unload_model():
	ORCH.MODEL.unload_model()
	return {"model": ORCH.MODEL.model_name, "loaded": False}

@app.get("/model_status")
def model_status():
	return {"model": ORCH.MODEL.model_name, "loaded": ORCH.MODEL.model is not None}


# ===== Eval =====
@app.post("/eval_image")
def eval_image(req: PathRequest):
	_assert_model_loaded()
	return ORCH.MODEL.eval_image(req.path)

@app.post("/eval_text")
def eval_text(req: TextRequest):
	_assert_model_loaded()
	return ORCH.MODEL.tokenize_text(req.text)


# ===== Ingest =====
@app.post("/ingest_image")
def ingest_image(req: IngestRequest):
	_assert_model_loaded()
	if not os.path.exists(req.path): 
		raise HTTPException(status_code=404, detail=f"file not found: {req.path}")
	try:
		return ORCH.ingest_image(req.hash_id, req.path)
	except RuntimeError as E:
		raise HTTPException(status_code=409, detail=E)

@app.post("/ingest_image_batch")
def ingest_image_batch(req: IngestBatchRequest):
	_assert_model_loaded()
	pending = [(X.hash_id, X.path) for X in req.items]
	return ORCH.ingest_image_batch(pending)


# ===== Ingest Queue =====
@app.get("/queue_status")
def queue_status():
	return {"queued": ORCH.DB.get_num_queue() or 0}

@app.post("/work_queue")
def work_queue(req: ProcessBatchRequest):
	"""Process one batch from the persistent queue; the caller loops for progress."""
	_assert_model_loaded()
	return ORCH.work_queue_batch(req.batch_size)


# ===== Buckets =====
@app.post("/create_bucket")
def create_bucket(req: CreateBucketRequest):
	bucket_id = ORCH.DB.new_bucket(req.bucket_name)
	return {"bucket_id": bucket_id}

@app.post("/rename_bucket")
def rename_bucket(req: RenameBucketRequest):
	_assert_bucket_id(req.bucket_id)
	ORCH.DB.rename_bucket(req.bucket_id, req.bucket_name)
	return {"bucket_id": req.bucket_id, "bucket_name": req.bucket_name}

@app.post("/insert_into_bucket")
def insert_into_bucket(req: InsertBucketRequest):
	_assert_bucket_id(req.bucket_id)
	# TODO add options for non-inserted hash_ids: strict, loose, deferred
	unknown = ORCH.DB.add_to_bucket(req.bucket_id, req.hash_ids)
	return {"bucket_id": req.bucket_id, "inserted": len(req.hash_ids) - len(unknown), "unknown": unknown}

@app.post("/remove_from_bucket")
def remove_from_bucket(req: InsertBucketRequest):
	_assert_bucket_id(req.bucket_id)
	ORCH.DB.remove_from_bucket(req.bucket_id, req.hash_ids)
	return {"bucket_id": req.bucket_id, "removed": len(req.hash_ids)}

@app.get("/list_buckets")
def list_buckets():
	return ORCH.DB.get_buckets()

@app.get("/list_bucket_members")
def list_bucket_members(bucket_id: int):
	_assert_bucket_id(bucket_id)
	return ORCH.DB.get_bucket_members(bucket_id)

@app.get("/get_bucket_membership")
def get_bucket_membership(hash_id: int):
	_assert_hash_id(hash_id)
	return ORCH.DB.get_bucket_membership(hash_id)

@app.post("/delete_bucket")
def delete_bucket(bucket_id: int):
	_assert_bucket_id(bucket_id)
	ORCH.DB.remove_bucket(bucket_id)
	return {"bucket_id": bucket_id, "deleted": True}


# ===== Embeddings =====
@app.get("/get_embedding")
def get_embedding(hash_id: int):
	_assert_hash_id(hash_id)
	return ORCH.DB.get_embedding(hash_id)

@app.post("/delete_hash")
def delete_hash(hash_id: int):
	_assert_hash_id(hash_id)
	ORCH.DB.remove_embedding(hash_id)
	return {"hash_id": hash_id, "deleted": True}


# ===== Search =====
@app.get("/num_embeddings")
def num_embeddings():
	return ORCH.DB.get_num_embeddings()

@app.get("/db_status")
def db_status():
	return {"quant_status": ORCH.DB.quant_status, "last_search": ORCH.DB.last_search}

@app.post("/search")
def search(req: SearchRequest):
	return ORCH.search_embedding(req.embedding, req.num_results)

@app.post("/search_id")
def search_id(req: SearchIDRequest):
	_assert_hash_id(req.hash_id)
	return ORCH.search_id(req.hash_id, req.num_results)

@app.post("/search_bucket")
def search_bucket(req: SearchBucketRequest):
	_assert_bucket_id(req.bucket_id)
	return ORCH.search_embedding_bucket(req.embedding, req.bucket_id, req.num_results)

@app.post("/search_id_bucket")
def search_id_bucket(req: SearchIDBucketRequest):
	_assert_hash_id(req.hash_id)
	_assert_bucket_id(req.bucket_id)
	return ORCH.search_id_bucket(req.hash_id, req.bucket_id, req.num_results)


# ===== Config =====
@app.get("/get_config")
def get_config():
	return ORCH.CFG.cfg

@app.post("/update_config")
def update_config(req: UpdateConfigRequest):
	unknown = set(req.updates) - set(ORCH.CFG.cfg)
	if unknown:
		raise HTTPException(status_code=400, detail=f"unknown config keys: {sorted(unknown)}")
	return ORCH.update_config(req.updates)


# ===== Hydrus API / WebUI =====
@app.get("/hydrus_status")
def hydrus_status():
	return ORCH.hydrus_status()

@app.get("/heartbeat")
def heartbeat():
	"""All topbar status dots in one call."""
	return ORCH.heartbeat()

@app.get("/thumbnail")
def thumbnail(hash_id: int):
	_assert_hydrus()
	try:
		return _hydrus_file(ORCH.HY.get_thumbnail(file_id=hash_id), "image/jpeg")
	except hydrus_api.APIError as e:
		raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

@app.get("/file")
def get_file(hash_id: int):
	_assert_hydrus()
	try:
		return _hydrus_file(ORCH.HY.get_file(file_id=hash_id), "application/octet-stream")
	except hydrus_api.APIError as e:
		raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

@app.get("/file_path")
def get_file_path(hash_id: int):
	_assert_hydrus()
	try:
		return {"path": ORCH.HY.get_file_path(file_id=hash_id)["path"]}
	except hydrus_api.APIError as e:
		raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

@app.get("/resolve_hash")
def resolve_hash(hash: str):
	"""Resolve a hydrus sha256 hash to a file_id (the db's hash_id)."""
	_assert_hydrus()
	# TODO find a way to get the hash in a faster way than this heavy API request
	meta = ORCH.HY.get_file_metadata(hashes=[hash], only_return_identifiers=True).get("metadata", [])
	
	if not meta:
		raise HTTPException(status_code=404, detail="hash not found in hydrus")
	
	file_id = meta[0]["file_id"]
	return {"hash_id": file_id, "ingested": ORCH.DB.exists_hash_id(file_id)}

@app.post("/eval_image_upload")
async def eval_image_upload(request: Request, hash_id: int | None = None):
	# Known hash_id -> reuse the stored embedding instead of re-evaluating
	if hash_id is not None and ORCH.DB.exists_hash_id(hash_id):
		return ORCH.DB.get_embedding(hash_id)
	_require_model()
	data = await request.body()
	if not data:
		raise HTTPException(status_code=400, detail="empty upload")
	fd, path = tempfile.mkstemp()
	try:
		with os.fdopen(fd, "wb") as f:
			f.write(data)
		embedding = ORCH.MODEL.eval_image(path)
	finally:
		os.unlink(path)
	if embedding is None:
		raise HTTPException(status_code=422, detail="could not evaluate image")
	return embedding

@app.post("/ingest_enqueue")
def ingest_enqueue(req: IngestEnqueueRequest):
	_assert_tag_service()
	return ORCH.enqueue(req.tag, req.max_evaluate, req.remove_tag)

@app.post("/add_hashes_to_bucket")
def add_hashes_to_bucket(req: AddHashesToBucketRequest):
	"""Resolve sha256 hashes via hydrus: add ingested ones to the bucket, return the rest for the client to ingest."""
	# TODO I can probably improve this a lot
	hashes = [h.strip() for h in req.hashes if h.strip()]
	if not hashes:
		return {"added": 0, "pending": [], "already_queued": 0, "unknown": []}

	_assert_hydrus()
	try:
		meta = ORCH.HY.get_file_metadata(hashes=hashes, only_return_identifiers=True).get("metadata", [])
	except hydrus_api.APIError as e:
		raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

	known = {m["hash"]: m["file_id"] for m in meta if m.get("file_id") is not None}
	unknown = [h for h in hashes if h not in known]

	# bucket_members requires the embedding to exist; only already-ingested files can be added now
	try:
		members = set(ORCH.DB.get_bucket_members(req.bucket_id) or [])
	except ValueError as e:
		raise HTTPException(status_code=404, detail=str(e))
	ingested = [fid for fid in known.values() if ORCH.DB.exists_hash_id(fid) and fid not in members]
	if ingested:
		ORCH.DB.add_to_bucket(req.bucket_id, ingested)
		ORCH.DB.dequeue_hashes(ingested)

	pending = []
	for fid in known.values():
		if ORCH.DB.exists_hash_id(fid):
			continue
		try:
			path = ORCH.HY.get_file_path(file_id=fid)["path"]
		except Exception:
			continue
		pending.append({"hash_id": fid, "path": path})

	already_queued = len(ORCH.DB.get_queued_ids([p["hash_id"] for p in pending]))

	return {"added": len(ingested), "pending": pending, "already_queued": already_queued, "unknown": unknown}

# ====== Tags ======
@app.get("/list_tags")
def list_tags():
	"""All tag centroids currently stored, alphabetical."""
	return ORCH.DB.get_tags()

@app.get("/get_tag_embedding")
def get_tag_embedding(tag: str):
	"""Fetch a stored tag centroid (for the search tab to fold into the combined vector)."""
	_assert_tag(tag)
	return ORCH.DB.get_tag_embedding(tag)

@app.post("/make_tag")
def make_tag(req: MakeTagRequest):
	_assert_hydrus()
	return ORCH.make_tag(req.tag, req.search_limit)


# Mounted last so API routes take precedence
class NoCacheStaticFiles(StaticFiles):
	async def get_response(self, path, scope):
		r = await super().get_response(path, scope)
		r.headers["Cache-Control"] = "no-cache"  # revalidate every load; stale JS has bitten us before
		return r

app.mount("/webui", NoCacheStaticFiles(directory=os.path.join(os.path.dirname(__file__), "webui"), html=True), name="webui")
