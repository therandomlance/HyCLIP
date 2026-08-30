from config import HyCLIP_Config
from db import HyCLIP_DB
from model import HyCLIP_Model

import hydrus_api
from hydrus_api import TagAction
import os

class Orchestrator():
	def __init__(self):
		self.CFG = HyCLIP_Config()
		self.MODEL = HyCLIP_Model(self.CFG.CLIP_MODEL)
		self.DB = HyCLIP_DB(self.MODEL.dims, self.CFG.VECTOR_QUANT)

		self.connect_hydrus()

	# ===== Housekeeping =====
	def shutdown(self):
		self.MODEL.unload_model()
		self.DB.commit()
		self.DB.close()

	def connect_hydrus(self):
		self.HY = hydrus_api.Client(api_url=self.CFG.API_URL, access_key=self.CFG.API_KEY)

	def update_config(self, updates):
		for key, value in updates.items():
			setattr(self.CFG, key, value)
		self.CFG.save_config()
		
		# reconnects to hydrus API in case relevant config changes
		self.connect_hydrus()
		
		return self.CFG.cfg

	def heartbeat(self):
		return {
			"model": {"model": self.MODEL.model_name, "loaded": self.MODEL.model is not None},
			"hydrus": self.hydrus_status(),
			"quant_status": self.DB.quant_status,
			"last_search": self.DB.last_search,
		}

	def hydrus_status(self):
		"""Probe the hydrus API for the topbar dot. search_files needs the same permission the UI's thumbnails do."""
		# TODO switch to the /verify_access_key endpoint for hydrus
		if not self.CFG.API_KEY:
			return {"status": "denied", "detail": "API_KEY not configured"}
		try:
			self.HY.search_files(tags=["hyclip:status-probe"], return_file_ids=True)
		except hydrus_api.InsufficientAccess:
			return {"status": "denied", "detail": "API key rejected or lacks permissions"}
		except hydrus_api.ConnectionError:
			return {"status": "unreachable", "detail": f"cannot reach {self.CFG.API_URL}"}
		except hydrus_api.APIError as e:  # reachable + authenticated, but something else failed
			return {"status": "denied", "detail": f"hydrus error {e.response.status_code}"}
		return {"status": "ok", "detail": "connected"}


	# ===== Image Ingest =====
	def ingest_image(self, hash_id:int, path:str):
		if self.DB.exists_hash_id(hash_id):
			return {"hash_id": hash_id, "status": "already_ingested"}
		if not self.MODEL.check_filetype(path):
			return {"hash_id": hash_id, "status": "skipped"}
		
		embedding = self.MODEL.eval_image(path)

		if embedding is None:
			return {"hash_id": hash_id, "status": "failed"}

		self.DB.insert_embedding(hash_id, embedding)
		self.DB.quant_status = "needs_quant"
		self.DB.commit()
		self.DB.dequeue_hashes([hash_id])

		return {"hash_id": hash_id, "status": "ingested"}

	def ingest_image_batch(self, pending:list[tuple[int, str]]):
		results = []
		to_eval = []

		for k, (hash_id, path) in enumerate(pending):
			if self.DB.exists_hash_id(hash_id):
				results.append({"hash_id": hash_id, "status": "already_ingested"})
				continue
			if not self.MODEL.check_filetype(path):
				results.append({"hash_id": hash_id, "status": "skipped"})
				continue
			if not os.path.exists(path):
				results.append({"hash_id": hash_id, "status": "not_found"})
				continue
			# List of indexes of pending to evaluate
			to_eval.append(k)

		embeddings = self.MODEL.eval_image_batch([pending[k][1] for k in to_eval], self.CFG.EVAL_WORKERS)

		inserts = []
		for k, embedding in zip(to_eval, embeddings):
			hash_id = pending[k][0]
			if embedding is None:
				results.append({"hash_id": hash_id, "status": "failed"})
				continue
			results.append({"hash_id": hash_id, "status": "ingested"})
			inserts.append(hash_id)
			self.DB.insert_embedding(hash_id, embedding)

		self.DB.quant_status = "needs_quant"
		self.DB.commit()
		self.DB.dequeue_hashes(inserts)

		return results

	# ===== Buckets =====
	def add_hashes_to_bucket(self, bucket_id:int, hash_ids:list[int]):
		return


	# ===== Queue working =====
	def enqueue(self, tag:str="hyclip:ingest", max_evaluate:int|None=None, remove_tag:bool=True):
		"""Find hydrus files tagged `tag` and load them into the ingest queue. Removes the tag when remove_tag is set."""

		file_ids = self.HY.search_files(tags=[tag], return_file_ids=True).get("file_ids", [])

		found = len(file_ids)
		if max_evaluate:
			file_ids = file_ids[:max_evaluate]

		to_enqueue = []
		skipped = 0
		for fid in file_ids:
			try:
				path = self.HY.get_file_path(file_id=fid)["path"]
			except Exception:
				skipped += 1  # no local path (e.g. trashed/deleted)
				continue
			to_enqueue.append((fid, path))

		if to_enqueue:
			self.DB.enqueue_hashes(to_enqueue)
			
			if remove_tag:
				self.HY.add_tags(
					file_ids=[fid for fid, _ in to_enqueue],
					service_keys_to_actions_to_tags={self.CFG.TAG_SERVICE_KEY: {TagAction.DELETE: [tag]}},
				)

		return {"found": found, "enqueued": len(to_enqueue), "skipped": skipped}

	def work_queue_batch(self, batch_size:int=self.CFG.INGEST_BATCH_SIZE):
		# TODO wrap ingest_image_batch and edit webUI for new response structure
		batch = self.DB.get_next_queue(batch_size)

		ingested = exists = skipped = errors = 0
		to_eval = []
		for hash_id, path in batch:
			if not os.path.exists(path):
				errors += 1
				continue
			if self.DB.exists_hash_id(hash_id):
				exists += 1
				continue
			if not self.MODEL.check_filetype(path):
				skipped += 1
				continue
			to_eval.append((hash_id, path))

		for hash_id, embedding in zip([h for h, _ in to_eval], self.MODEL.eval_image_batch([p for _, p in to_eval], self.CFG.EVAL_WORKERS)):
			if embedding is None:
				errors += 1
				continue
			self.DB.insert_embedding(hash_id, embedding)
			ingested += 1

		self.DB.quant_status = "needs_quant"
		self.DB.commit()
		self.DB.dequeue_hashes([fid for fid, _ in batch])

		return {"processed": len(batch), "ingested": ingested, "already_ingested": exists,
			"skipped": skipped, "errors": errors, "remaining": self.DB.get_num_queue() or 0}

	# ===== Tags =====
	def make_tag(self, tag:str, search_limit:int=1000):
		"""Compute & store the centroid embedding for files hydrus tags with `tag`.
		Reuses already-ingested embeddings (no model load needed); files not yet
		ingested are skipped. The client loops one tag per call for progress."""
		query = [f"system:limit={search_limit}", tag]
		file_ids = self.HY.search_files(tags=query, file_sort_type=4, return_file_ids=True).get("file_ids", [])
		ingested = [fid for fid in file_ids if self.DB.exists_hash_id(fid)]

		embeddings = self.DB.get_embeddings(ingested)
		centroid = self.DB.vec_centroid(embeddings)
		if centroid is None:
			raise ValueError(f'no ingested files match "{tag}"')
		self.DB.insert_tag(tag, centroid)
		self.DB.commit()
		return {"tag": tag, "matched": len(file_ids), "ingested": len(ingested)}

