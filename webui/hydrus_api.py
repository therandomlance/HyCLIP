import os
import tempfile
import time

import hydrus_api
from hydrus_api import TagAction
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel


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


def build_router():
	# ponytail: read api.db/api.model/api.config at call time so tests that swap
	# the module singletons affect router endpoints too (build_router runs before
	# the test patches them; a closure would capture the pre-test objects).
	import api
	router = APIRouter()

	def _require_model():
		if api.model.model is None:
			raise HTTPException(status_code=409, detail="model not loaded")

	def _hydrus():
		if not api.config.API_KEY:
			raise HTTPException(status_code=503, detail="hydrus API_KEY not configured")
		return hydrus_api.Client(access_key=api.config.API_KEY, api_url=api.config.API_URL)

	def _hydrus_file(r, fallback_type: str):
		return Response(content=r.content, media_type=r.headers.get("Content-Type", fallback_type))

	@router.get("/hydrus_status")
	def hydrus_status():
		"""Probe the hydrus API for the topbar dot. search_files needs the same permission the UI's thumbnails do."""
		if not api.config.API_KEY:
			return {"status": "denied", "detail": "API_KEY not configured"}
		try:
			_hydrus().search_files(tags=["hyclip:status-probe"], return_file_ids=True)
		except hydrus_api.InsufficientAccess:
			return {"status": "denied", "detail": "API key rejected or lacks permissions"}
		except hydrus_api.ConnectionError:
			return {"status": "unreachable", "detail": f"cannot reach {api.config.API_URL}"}
		except hydrus_api.APIError as e:  # reachable + authenticated, but something else failed
			return {"status": "denied", "detail": f"hydrus error {e.response.status_code}"}
		return {"status": "ok", "detail": "connected"}

	@router.get("/heartbeat")
	def heartbeat():
		"""All topbar status dots in one call."""
		return {
			"model": {"model": api.model.model_name, "loaded": api.model.model is not None},
			"hydrus": hydrus_status(),
			"quant_status": api.db.quant_status,
			"last_search": api.db.last_search,
		}

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

	@router.get("/resolve_hash")
	def resolve_hash(hash: str):
		"""Resolve a hydrus sha256 hash to a file_id (the db's hash_id)."""
		try:
			# TODO find a way to get the hash in a faster way than this heavy API request
			meta = _hydrus().get_file_metadata(hashes=[hash], only_return_identifiers=True).get("metadata", [])
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)
		if not meta:
			raise HTTPException(status_code=404, detail="hash not found in hydrus")
		file_id = meta[0]["file_id"]
		return {"hash_id": file_id, "ingested": api.db.exists_hash_id(file_id)}

	@router.post("/eval_image_upload")
	async def eval_image_upload(request: Request, hash_id: int | None = None):
		# Known hash_id -> reuse the stored embedding instead of re-evaluating
		if hash_id is not None and api.db.exists_hash_id(hash_id):
			return api.db.get_embedding(hash_id)
		_require_model()
		data = await request.body()
		if not data:
			raise HTTPException(status_code=400, detail="empty upload")
		fd, path = tempfile.mkstemp()
		try:
			with os.fdopen(fd, "wb") as f:
				f.write(data)
			embedding = api.model.eval_image(path)
		finally:
			os.unlink(path)
		if embedding is None:
			raise HTTPException(status_code=422, detail="could not evaluate image")
		return embedding

	@router.post("/ingest_enqueue")
	def ingest_enqueue(req: IngestEnqueueRequest):
		"""Find hydrus files tagged `tag` and load them into the ingest queue. Removes the tag when remove_tag is set."""
		# TODO slim this down as much as I can
		if not api.config.TAG_SERVICE_KEY:
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
			api.db.enqueue_hashes(to_enqueue)
			if req.remove_tag:
				hydrus.add_tags(
					file_ids=[fid for fid, _ in to_enqueue],
					service_keys_to_actions_to_tags={api.config.TAG_SERVICE_KEY: {TagAction.DELETE: [req.tag]}},
				)
		return {"found": found, "enqueued": len(to_enqueue), "skipped": skipped}

	@router.post("/add_hashes_to_bucket")
	def add_hashes_to_bucket(req: AddHashesToBucketRequest):
		"""Resolve sha256 hashes via hydrus: add ingested ones to the bucket, return the rest for the client to ingest."""
		# TODO I can probably improve this a lot
		hashes = [h.strip() for h in req.hashes if h.strip()]
		if not hashes:
			return {"added": 0, "pending": [], "already_queued": 0, "unknown": []}
		hydrus = _hydrus()
		try:
			meta = hydrus.get_file_metadata(hashes=hashes, only_return_identifiers=True).get("metadata", [])
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

		known = {m["hash"]: m["file_id"] for m in meta if m.get("file_id") is not None}
		unknown = [h for h in hashes if h not in known]

		# bucket_members requires the embedding to exist; only already-ingested files can be added now
		try:
			members = set(api.db.get_bucket_members(req.bucket_id) or [])
		except ValueError as e:
			raise HTTPException(status_code=404, detail=str(e))
		ingested = [fid for fid in known.values() if api.db.exists_hash_id(fid) and fid not in members]
		if ingested:
			api.db.add_to_bucket(req.bucket_id, ingested)
			api.db.dequeue_hashes(ingested)

		pending = []
		for fid in known.values():
			if api.db.exists_hash_id(fid):
				continue
			try:
				path = hydrus.get_file_path(file_id=fid)["path"]
			except Exception:
				continue
			pending.append({"hash_id": fid, "path": path})

		already_queued = len(api.db.get_queued_ids([p["hash_id"] for p in pending]))

		return {"added": len(ingested), "pending": pending, "already_queued": already_queued, "unknown": unknown}

	# ====== Tags ======
	@router.get("/list_tags")
	def list_tags():
		"""All tag centroids currently stored, alphabetical."""
		return api.db.get_tags()

	@router.get("/get_tag_embedding")
	def get_tag_embedding(tag: str):
		"""Fetch a stored tag centroid (for the search tab to fold into the combined vector)."""
		try:
			return api.db.get_tag_embedding(tag)
		except ValueError as e:
			raise HTTPException(status_code=404, detail=str(e))

	@router.post("/make_tag")
	def make_tag(req: MakeTagRequest):
		"""Compute & store the centroid embedding for files hydrus tags with `tag`.
		Reuses already-ingested embeddings (no model load needed); files not yet
		ingested are skipped. The client loops one tag per call for progress."""
		hydrus = _hydrus()
		try:
			# random sort so system:limit takes an unbiased sample for the centroid
			file_ids = hydrus.search_files(
				tags=[f"system:limit={req.search_limit}", req.tag],
				file_sort_type=4, return_file_ids=True,
			).get("file_ids", [])
		except hydrus_api.APIError as e:
			raise HTTPException(status_code=e.response.status_code, detail="hydrus: " + e.response.text)

		# only files already ingested into HyCLIP have a stored embedding to centroid
		ingested = [fid for fid in file_ids if api.db.exists_hash_id(fid)]
		embeddings = api.db.get_embeddings(ingested)
		centroid = api.db.vec_centroid(embeddings)
		if centroid is None:
			raise HTTPException(status_code=422, detail=f'no ingested files match "{req.tag}"')
		api.db.insert_tag(req.tag, centroid)
		api.db.commit()
		return {"tag": req.tag, "matched": len(file_ids), "ingested": len(ingested)}

	return router
