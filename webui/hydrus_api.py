import os
import tempfile

import hydrus_api
from hydrus_api import TagAction
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel


class IngestEnqueueRequest(BaseModel):
	tag: str = "hyclip:ingest"
	max_evaluate: int | None = None

class AddHashesToBucketRequest(BaseModel):
	bucket_id: int
	hashes: list[str]


def build_router(db, model, config):
	router = APIRouter()

	def _require_model():
		if model.model is None:
			raise HTTPException(status_code=409, detail="model not loaded")

	def _hydrus():
		if not config.API_KEY:
			raise HTTPException(status_code=503, detail="hydrus API_KEY not configured")
		return hydrus_api.Client(access_key=config.API_KEY, api_url=config.API_URL)

	def _hydrus_file(r, fallback_type: str):
		return Response(content=r.content, media_type=r.headers.get("Content-Type", fallback_type))

	@router.get("/thumbnail")
	def thumbnail(hash_id: int):
		try:
			return _hydrus_file(_hydrus().get_thumbnail(file_id=hash_id), "image/jpeg")
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

	@router.get("/file")
	def get_file(hash_id: int):
		try:
			return _hydrus_file(_hydrus().get_file(file_id=hash_id), "application/octet-stream")
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

	@router.get("/file_path")
	def get_file_path(hash_id: int):
		try:
			return {"path": _hydrus().get_file_path(file_id=hash_id)["path"]}
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

	@router.get("/get_embedding")
	def get_embedding(hash_id: int):
		try:
			return db.get_embedding(hash_id)
		except ValueError as e:
			raise HTTPException(status_code=404, detail=str(e))

	@router.get("/resolve_hash")
	def resolve_hash(hash: str):
		"""Resolve a hydrus sha256 hash to a file_id (the db's hash_id)."""
		try:
			meta = _hydrus().get_file_metadata(hashes=[hash], only_return_identifiers=True).get("metadata", [])
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)
		if not meta:
			raise HTTPException(status_code=404, detail="hash not found in hydrus")
		file_id = meta[0]["file_id"]
		return {"hash_id": file_id, "ingested": db.exists_hash_id(file_id)}

	@router.post("/eval_image_upload")
	async def eval_image_upload(request: Request, hash_id: int | None = None):
		# Known hash_id -> reuse the stored embedding instead of re-evaluating
		if hash_id is not None and db.exists_hash_id(hash_id):
			return db.get_embedding(hash_id)
		_require_model()
		data = await request.body()
		if not data:
			raise HTTPException(status_code=400, detail="empty upload")
		fd, path = tempfile.mkstemp()
		try:
			with os.fdopen(fd, "wb") as f:
				f.write(data)
			embedding = model.eval_image(path)
		finally:
			os.unlink(path)
		if embedding is None:
			raise HTTPException(status_code=422, detail="could not evaluate image")
		return embedding

	@router.post("/ingest_enqueue")
	def ingest_enqueue(req: IngestEnqueueRequest):
		"""Find hydrus files tagged `tag`, load them into the ingest queue, and remove the tag."""
		if not config.TAG_SERVICE_KEY:
			raise HTTPException(status_code=503, detail="TAG_SERVICE_KEY not configured")
		hydrus = _hydrus()
		try:
			file_ids = hydrus.search_files(tags=[req.tag], return_file_ids=True).get("file_ids", [])
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)
		found = len(file_ids)
		if req.max_evaluate:
			file_ids = file_ids[:req.max_evaluate]

		to_enqueue = []
		skipped = 0
		for fid in file_ids:
			try:
				path = hydrus.get_file_path(file_id=fid)["path"]
			except Exception:
				skipped += 1  # no local path (e.g. trashed/deleted)
				continue
			to_enqueue.append((fid, path))

		if to_enqueue:
			db.enqueue_hashes(to_enqueue)
			hydrus.add_tags(
				file_ids=[fid for fid, _ in to_enqueue],
				service_keys_to_actions_to_tags={config.TAG_SERVICE_KEY: {TagAction.DELETE: [req.tag]}},
			)
		return {"found": found, "enqueued": len(to_enqueue), "skipped": skipped}

	@router.post("/add_hashes_to_bucket")
	def add_hashes_to_bucket(req: AddHashesToBucketRequest):
		"""Resolve sha256 hashes via hydrus: add ingested ones to the bucket, queue the rest for ingest."""
		hashes = [h.strip() for h in req.hashes if h.strip()]
		if not hashes:
			return {"added": 0, "enqueued": 0, "unknown": []}
		hydrus = _hydrus()
		try:
			meta = hydrus.get_file_metadata(hashes=hashes, only_return_identifiers=True).get("metadata", [])
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

		known = {m["hash"]: m["file_id"] for m in meta if m.get("file_id") is not None}
		unknown = [h for h in hashes if h not in known]

		# bucket_members requires the embedding to exist; only already-ingested files can be added now
		try:
			members = set(db.get_bucket_members(req.bucket_id) or [])
		except ValueError as e:
			raise HTTPException(status_code=404, detail=str(e))
		ingested = [fid for fid in known.values() if db.exists_hash_id(fid) and fid not in members]
		if ingested:
			db.add_to_bucket(req.bucket_id, ingested)

		to_enqueue = []
		for fid in known.values():
			if db.exists_hash_id(fid):
				continue
			try:
				path = hydrus.get_file_path(file_id=fid)["path"]
			except Exception:
				continue
			to_enqueue.append((fid, path))
		if to_enqueue:
			db.enqueue_hashes(to_enqueue)

		return {"added": len(ingested), "enqueued": len(to_enqueue), "unknown": unknown}

	return router
