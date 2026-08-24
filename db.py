import sqlite3 as sql
import importlib.resources
import os
import array
import threading

class HyCLIP_DB:
	def __init__(self, model_dims:int, quant:str, filename="hyclip.db", verbose:bool=True):
		# TODO scaffolding for per-model dbs, probably outside of this class
		self.DB = sql.connect(filename, check_same_thread=False)

		self.DB.enable_load_extension(True)
		ext_path = importlib.resources.files("sqlite_vector.binaries") / "vector"
		self.DB.load_extension(str(ext_path))
		self.DB.enable_load_extension(False)

		self.DB.executescript('''
			CREATE TABLE IF NOT EXISTS embeddings (
				hash_id INTEGER PRIMARY KEY,
				embedding BLOB
			);

			CREATE TABLE IF NOT EXISTS buckets (
				bucket_id INTEGER PRIMARY KEY AUTOINCREMENT,
				bucket_name TEXT UNIQUE
			);

			CREATE TABLE IF NOT EXISTS bucket_members (
				bucket_id INTEGER,
				hash_id INTEGER,
				PRIMARY KEY ( bucket_id, hash_id )
			);

			CREATE TABLE IF NOT EXISTS temp_embedding_queue (
				hash_id INTEGER PRIMARY KEY,
				path TEXT UNIQUE
			);

			CREATE TABLE IF NOT EXISTS tags (
				tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
				tag TEXT UNIQUE,
				embedding BLOB
			);

			CREATE INDEX IF NOT EXISTS tag_index ON tags(tag);
		''')
		self.commit()

		self.model_dims = model_dims
		self.quant = quant
		# Status attributes are observability only (db_status/heartbeat); the
		# re-quant decision is driven by quant_ready, which tracks per-table
		# quantization validity (sqlite-vector quantizes tables independently)
		self.quant_status = "needs_quant"
		self.last_search = None
		self.quant_ready:set[str] = set()
		# One connection shared across FastAPI's threadpool; serialize writers
		# and quant transitions. RLock so orchestrator-level transactions can
		# hold it across the db calls they compose.
		self.lock = threading.RLock()
		self.VERBOSE = verbose

		self.clean_temp_buckets()
		self.clean_queue()

	def close(self):
		if getattr(self, "DB", None) is None:
			return
		self.clean_temp_buckets()
		self.clean_queue()
		self.DB.close()
		self.DB = None

	def __del__(self):
		# GC/interpreter teardown may run this with a broken interpreter or a
		# half-constructed instance; never let it raise
		try:
			self.close()
		except Exception:
			pass

	def commit(self):
		self.DB.commit()

	def qe(self, query:str, arguments:list=None):
		"""Quick execute"""
		if arguments is None:
			X = self.DB.execute(query).fetchall()
		else:
			X = self.DB.execute(query, arguments).fetchall()
		L = len(X)

		# No results
		if L == 0:
			return None

		# Single value
		if L == 1: 
			# Single column
			if len(X[0]) == 1:
				return X[0][0]
			# Multi column
			else:
				return X[0]

		# Multi value, Single column
		if len(X[0]) == 1:
			return [ Z[0] for Z in X ]
		# Otherwise return as-is
		else:
			return X

	# ====== Helpers ======
	def _get(self, col:str, table:str, values:tuple[str, str]):
		return self.qe(f"SELECT {col} FROM {table} WHERE {values[0]} = ?", [values[1]])

	def _exists(self, table:str, value:tuple[str, str]):
		return bool(self.qe(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {value[0]} = ?)", [value[1]]))
	
	def _delete(self, table:str, value:tuple[str, str]):
		self.DB.execute(f"DELETE FROM {table} WHERE {value[0]} = ? ", [value[1]])

	def vec_centroid(self, vecs:list[list[float]]) -> list[float] | None:
		"""Helper function to get the centroid of a list of embeddings"""
		if not vecs:
			return None
		N = len(vecs)
		return [sum(col) / N for col in zip(*vecs)]
	
	def blob2list(self, blob):
		embedding = array.array('f')
		embedding.frombytes(blob)
		return embedding.tolist()

	def list2blob(self, embedding:list[float]) -> bytes:
		# vector_as_f32 passes an already-formatted f32 blob straight through,
		# which skips the text parse a str(list) would need
		return array.array('f', embedding).tobytes()

	# ===== Value Checks =====
	def _assert_hash_id(self, hash_id:int):
		if not self.exists_hash_id(hash_id):
			raise ValueError(f"hash_id not found: {hash_id}")
	
	def _assert_bucket_id(self, bucket_id:int):
		if not self.exists_bucket(bucket_id):
			raise ValueError(f"bucket_id does not exist: {bucket_id}")

	def _assert_tag(self, tag:str):
		if not self.exists_tag(tag):
			raise ValueError(f"tag does not exist: {tag}")

	# ===== Existence Checks =====
	def exists_hash_id(self, hash_id:int) -> bool:
		return self._exists("embeddings", ("hash_id", hash_id))

	def exists_bucket(self, bucket_id:int) -> bool:
		return self._exists("buckets", ("bucket_id", bucket_id))

	def exists_tag(self, tag:str) -> bool:
		return self._exists("tags", ("tag", tag))

	def exists_bucket_member(self, bucket_id:int, hash_id:int) -> bool:
		Q = "SELECT EXISTS ( SELECT 1 FROM bucket_members WHERE bucket_id = ? AND hash_id = ? )"
		return bool(self.qe(Q, [bucket_id, hash_id]))

	def is_quantized(self, bucket_id:int|None=None) -> bool:
		table_name = self.quant_cache_table_name(bucket_id)
		Q = "SELECT EXISTS ( SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?)"
		# qe unwraps to the EXISTS value itself; fetchall() would always be a
		# non-empty list and truthy
		return bool(self.qe(Q, [table_name]))

	# ===== Data Insertion =====
	def insert_embedding(self, hash_id:int, embedding:list[float]):
		# IMPORTANT: DOES NOT AUTO-COMMIT
		# OR IGNORE: a client can abort mid-batch and re-pull the same rows while the
		# server finishes the old one — duplicate inserts are identical anyway
		Q = f"INSERT OR IGNORE INTO embeddings (hash_id, embedding) VALUES ( ?, vector_as_f32(?) )"
		A = [hash_id, self.list2blob(embedding)]
		self.DB.execute(Q, A)
		self.quant_ready.discard("embeddings")
	
	def new_bucket(self, bucket_name:str) -> int:
		with self.lock:
			Q = "INSERT INTO buckets ( bucket_name ) VALUES ( ? ) RETURNING bucket_id"
			A = [bucket_name]
			bucket_id = self.qe(Q, A)
			self.commit()
			return bucket_id

	def add_to_bucket(self, bucket_id:int, hash_ids:list[int]):
		with self.lock:
			self._assert_bucket_id(bucket_id)

			known = self._known_hash_ids(hash_ids)
			unknown_hashes = [hash_id for hash_id in hash_ids if hash_id not in known]
			hash_ids = [hash_id for hash_id in hash_ids if hash_id in known]

			# OR IGNORE: re-adding an existing member is a no-op, not an error
			Q = "INSERT OR IGNORE INTO bucket_members (bucket_id, hash_id) VALUES ( ?, ? )"
			A = [(bucket_id, X) for X in hash_ids]
			self.DB.executemany(Q, A)

			if self.bucket_is_init(bucket_id):
				self.init_bucket(bucket_id)

			# Membership changed: only this bucket's quantization is stale
			self.quant_ready.discard(f"temp_bucket_{bucket_id}")
			self.last_search = "global"
			self.commit()

			return unknown_hashes

	def _known_hash_ids(self, hash_ids:list[int]) -> set[int]:
		"""The subset of hash_ids that have embeddings, in one query per chunk."""
		known = set()
		# chunked to stay under SQLite's bound-parameter limit
		for i in range(0, len(hash_ids), 500):
			chunk = hash_ids[i:i + 500]
			marks = ",".join("?" * len(chunk))
			rows = self.DB.execute(f"SELECT hash_id FROM embeddings WHERE hash_id IN ({marks})", chunk).fetchall()
			known.update(r[0] for r in rows)
		return known

	def insert_tag(self, tag:str, embedding:list[float]):
		with self.lock:
			self.DB.execute("INSERT OR REPLACE INTO tags (tag, embedding) VALUES ( ?, vector_as_f32(?) )", (tag, self.list2blob(embedding)))
			self.commit()

	# ===== Data Retrieval =====
	# Embeddings / hash_id
	def get_embedding(self, hash_id:int) -> list[float]:
		self._assert_hash_id(hash_id)
		return self.blob2list(self._get("embedding", "embeddings", ("hash_id", hash_id)))

	def get_embeddings(self, hash_ids:list[int]) -> list[list[float]]:
		blobs = {}
		for i in range(0, len(hash_ids), 500):
			chunk = hash_ids[i:i + 500]
			marks = ",".join("?" * len(chunk))
			rows = self.DB.execute(f"SELECT hash_id, embedding FROM embeddings WHERE hash_id IN ({marks})", chunk).fetchall()
			blobs.update(rows)
		missing = [X for X in hash_ids if X not in blobs]
		if missing:
			raise ValueError(f"hash_id not found: {missing[0]}")
		return [self.blob2list(blobs[X]) for X in hash_ids]

	def get_num_embeddings(self) -> int:
		return self.qe("SELECT COUNT(*) FROM embeddings")

	def get_all_hash_ids(self):
		return [X[0] for X in self.DB.execute("SELECT hash_id FROM embeddings").fetchall()]

	# Tags
	def get_tags(self) -> list[str]:
		return [X[0] for X in self.DB.execute("SELECT tag FROM tags ORDER BY tag ASC").fetchall()]

	def get_tag_embedding(self, tag:str):
		self._assert_tag(tag)
		return self.blob2list(self._get("embedding", "tags", ("tag", tag)))

	# Buckets
	def get_bucket_members(self, bucket_id) -> list[int]:
		self._assert_bucket_id(bucket_id)
		return [X[0] for X in self.DB.execute("SELECT hash_id FROM bucket_members WHERE bucket_id = ?", [bucket_id]).fetchall()]

	def get_bucket_membership(self, hash_id:int) -> list[int]:
		return [X[0] for X in self.DB.execute("SELECT bucket_id FROM bucket_members WHERE hash_id = ?", [hash_id]).fetchall()]

	def get_bucket_name(self, bucket_id) -> str:
		self._assert_bucket_id(bucket_id)
		return self.qe("SELECT bucket_name FROM buckets WHERE bucket_id = ?", [bucket_id])

	def get_buckets(self):
		return self.DB.execute("SELECT * FROM buckets").fetchall()

	def get_bucket_size(self, bucket_id:int) -> int:
		self._assert_bucket_id(bucket_id)
		return self.qe("SELECT COUNT(*) FROM bucket_members WHERE bucket_id = ?", [bucket_id])

	# ===== Data Removal =====
	def remove_embedding(self, hash_id:int):
		with self.lock:
			self._assert_hash_id(hash_id)

			# Removes from all buckets it's part of
			for bucket_id in self.get_bucket_membership(hash_id):
				self.remove_from_bucket(bucket_id, [hash_id])

			self._delete("embeddings", ("hash_id", hash_id))
			self.quant_ready.discard("embeddings")

			self.commit()

	def remove_bucket(self, bucket_id:int):
		with self.lock:
			self._assert_bucket_id(bucket_id)
			self.drop_temp_bucket(bucket_id)
			self._delete("bucket_members", ("bucket_id", bucket_id))
			self._delete("buckets", ("bucket_id", bucket_id))
			self.commit()

	def remove_from_bucket(self, bucket_id:int, hash_ids:list[int]):
		with self.lock:
			self._assert_bucket_id(bucket_id)
			# Deleting a non-member is a no-op; no need to pre-check membership
			Q = "DELETE FROM bucket_members WHERE bucket_id = ? AND hash_id = ?"
			A = [(bucket_id, X) for X in hash_ids]
			self.DB.executemany(Q, A)

			# Keep an init'd bucket's temp search table in sync (no bucket_id column there)
			if self.bucket_is_init(bucket_id):
				self.DB.executemany(f"DELETE FROM temp_bucket_{bucket_id} WHERE hash_id = ?", [(X,) for X in hash_ids])

			self.quant_ready.discard(f"temp_bucket_{bucket_id}")
			if self.last_search == f"bucket_{bucket_id}":
				self.quant_status = "needs_quant"

			self.commit()

	# ===== Data Mutation =====
	def rename_bucket(self, bucket_id:int, new_bucket_name:str):
		with self.lock:
			self._assert_bucket_id(bucket_id)
			self.DB.execute("UPDATE buckets SET bucket_name = ? WHERE bucket_id = ?", [new_bucket_name, bucket_id])
			self.commit()

	# ========== Search ==========
	def _search_full_scan(self, embedding:list[float], num_results:int, table_name:str, id_col:str="hash_id"):
		self.vector_init(table_name, self.model_dims)
		self.commit()
		Q = f'''
			SELECT {id_col}, v.distance
			FROM vector_full_scan('{table_name}', 'embedding', vector_as_f32( ? )) as v
			INNER JOIN {table_name} ON {table_name}.rowid = v.rowid
			ORDER BY v.distance
			LIMIT ?
		'''
		A = [self.list2blob(embedding), num_results]
		return self.DB.execute(Q, A).fetchall()

	def _search_quantize_scan(self, embedding:list[float], num_results:int, table_name:str="embeddings"):
		Q = f'''
			SELECT hash_id, v.distance
			FROM vector_quantize_scan('{table_name}', 'embedding', vector_as_f32( ? )) as v
			INNER JOIN {table_name} ON {table_name}.rowid = v.rowid
			ORDER BY v.distance
			LIMIT ?
		'''
		A = [self.list2blob(embedding), num_results]
		return self.DB.execute(Q, A).fetchall()

	def search_embedding(self, embedding:list[float], num_results:int=100) -> list[tuple[int, float]]:
		with self.lock:
			if "embeddings" not in self.quant_ready:
				self.quant_prepare("embeddings", self.model_dims, self.quant)

			self.last_search = "global"

			if self.is_quantized():
				return self._search_quantize_scan(embedding, num_results)
			else:
				return self._search_full_scan(embedding, num_results, "embeddings")

	def search_embedding_bucket(self, embedding:list[float], bucket_id:int, num_results:int=100) -> list[tuple[int, float]]:
		with self.lock:
			self._assert_bucket_id(bucket_id)

			if not self.bucket_is_init(bucket_id):
				self.init_bucket(bucket_id)

			table_name = f'temp_bucket_{bucket_id}'

			if table_name not in self.quant_ready:
				self.quant_prepare(table_name, self.model_dims, self.quant)

			self.last_search = f"bucket_{bucket_id}"

			if self.is_quantized(bucket_id):
				return self._search_quantize_scan(embedding, num_results, table_name)
			else:
				return self._search_full_scan(embedding, num_results, table_name)

	def search_tags(self, embedding:list[float], limit:int=100) -> list[tuple[str, float]]:
		with self.lock:
			return self._search_full_scan(embedding, limit, "tags", id_col="tag")

	# ===== Bucket Search Cache Management =====
	def init_bucket(self, bucket_id:int):
		self._assert_bucket_id(bucket_id)

		table_name = f'temp_bucket_{bucket_id}'

		self.DB.executescript(f'''
			DROP TABLE IF EXISTS {table_name};

			CREATE TABLE {table_name} (
				hash_id INTEGER PRIMARY KEY,
				embedding BLOB
			);''')

		self.DB.execute(f'''
			INSERT INTO {table_name} ( hash_id, embedding )
			SELECT hash_id, embedding
			FROM embeddings
			NATURAL JOIN bucket_members
			WHERE bucket_id = ?
		''', [bucket_id])

		self.quant_ready.discard(table_name)
		self.commit()

	def bucket_is_init(self, bucket_id:int) -> bool:
		Q = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
		A = [f"temp_bucket_{bucket_id}"]
		return bool(self.qe(Q, A))
	
	def drop_temp_bucket(self, bucket_id):
		self.DB.execute(f"DROP TABLE IF EXISTS temp_bucket_{bucket_id};")
		self.quant_ready.discard(f"temp_bucket_{bucket_id}")

	def clean_temp_buckets(self):
		tables = self.DB.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'temp_bucket_%'").fetchall()

		for (name,) in tables:
			self.DB.execute(f"DROP TABLE IF EXISTS {name}")
			self.quant_ready.discard(name)

		self.commit()

	# ===== Filter Search =====
	# Similar to a bucket, but an ephemeral group of images
	# TODO If an unkown hash_id is provided, give an option to skip or ingest
	# def init_filter(self, hash_ids:list[int]):
	# 	# TODO filter on a bucket or globally based on list of hash_ids
	# 	# TODO find a good method for getting filter IDs
	# 	filter_id = 1

	# 	self.DB.execute(f'''
	# 		DROP TABLE IF EXISTS temp_filter_{filter_id};

	# 		CREATE TEMP TABLE temp_filter_{filter_id}(
	# 			hash_id INTEGER PRIMARY KEY,
	# 			embedding BLOB
	# 		);''')

	# 	embeddings = self.get_embeddings(hash_ids)
	# 	values = zip(hash_ids, embeddings)

	# 	self.executemany(f'INSERT INTO temp_filter_{filter_id} (hash_id, embedding) VALUES ( ?, ? )', values)

		# self.commit()


	# ===== Quant Prep =====
	# TODO Check if sqlite-vector can have multiple tables quantized at the same time
	# TODO Make sure these old methods still work with the new DB

	def quant_cache_table_name(self, bucket_id:int|None=None) -> str:
		if not bucket_id:
			return "vector0_embeddings_embedding"
		else:
			return f"vector0_temp_bucket_{bucket_id}_embedding"

	# quant_status transitions: needs_quant -> quantizing -> ready (back to needs_quant on failure)
	def quant_prepare(self, table_name:str, model_dims:int=768, quant:str="UINT8"):
		with self.lock:
			self.quant_status = "quantizing"
			try:
				self.quantize_preload_cleanup(table_name)
				self.vector_init(table_name, model_dims)
				self.vector_quantize(table_name, quant)
				self.quantize_preload(table_name)
				self.commit()
			except Exception:
				self.quant_status = "needs_quant"
				self.quant_ready.discard(table_name)
				raise
			self.quant_status = "ready"
			self.quant_ready.add(table_name)

	def show_quantize_preload_size(self, table_name:str) -> int:
		Q = f"SELECT vector_quantize_memory('{table_name}', 'embedding')"
		size = self.qe(Q)
		if self.VERBOSE:
			print(f"Quantize preload size: {size}")
		return size
	
	def vector_init(self, table_name:str, dimensions:int):
		Q = f"SELECT vector_init('{table_name}', 'embedding', 'dimension={dimensions}')"
		self.DB.execute(Q)

	# Returns the amount of successfully quantized rows 
	def vector_quantize(self, table_name:str, quant:str) -> int:
		if self.VERBOSE:
			print("Quantizing...")
		quant = self.qe(f"SELECT vector_quantize('{table_name}', 'embedding', 'qtype={quant}')")
		
		if self.VERBOSE:
			print(f'Quantized: {quant}')
		
		return quant
	
	def quantize_preload(self, table_name:str):
		Q = f"SELECT vector_quantize_preload('{table_name}', 'embedding')"
		self.DB.execute(Q)

	def quantize_preload_cleanup(self, table_name:str):
		Q = f"SELECT vector_quantize_cleanup('{table_name}', 'embedding')"
		self.DB.execute(Q)

	# ===== Ingest Queue =====
	# Persistent queue of files that need to be ingested
	def enqueue_hashes(self, hashes:list[tuple[int, str]]):
		# INSERT OR IGNORE so re-enqueueing between resumed runs is safe
		with self.lock:
			self.DB.executemany("INSERT OR IGNORE INTO temp_embedding_queue (hash_id, path) VALUES ( ?, ? )", hashes)
			self.commit()

	def dequeue_hashes(self, hash_ids:list[int]):
		with self.lock:
			self.DB.executemany("DELETE FROM temp_embedding_queue WHERE hash_id = ?", [(h,) for h in hash_ids])
			self.commit()
	
	def get_next_queue(self, num:int) -> list[tuple[int, str]]:
		return self.qe("SELECT hash_id, path FROM temp_embedding_queue LIMIT ?", [num])

	def clean_queue(self):
		with self.lock:
			self.DB.execute("DELETE FROM temp_embedding_queue WHERE hash_id IN (SELECT hash_id FROM embeddings)")
			self.commit()

	def clear_queue(self):
		with self.lock:
			self.DB.execute("DELETE FROM temp_embedding_queue")
			self.commit()

	def get_num_queue(self):
		return self.qe("SELECT COUNT(*) FROM temp_embedding_queue")

	def get_queued_ids(self, hash_ids:list[int]) -> set[int]:
		if not hash_ids:
			return set()
		marks = ",".join("?" * len(hash_ids))
		rows = self.DB.execute(f"SELECT hash_id FROM temp_embedding_queue WHERE hash_id IN ({marks})", hash_ids).fetchall()
		return {r[0] for r in rows}
