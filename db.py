import sqlite3 as sql
import importlib.resources
import os
import array

class HyCLIP_DB:
	def __init__(self, filename="hyclip.db"):
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

			CREATE INDEX IF NOT EXISTS embeddings_index
			ON embeddings ( hash_id );

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
			)
		''')
		self.commit()

		self.clean_temp_buckets()
		self.clean_queue()

	def __del__(self):
		try:
			if self.DB is not None:
				self.clean_temp_buckets()
				self.clean_queue()
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

	def _get(self, col:str, table:str, values:tuple[str, str]):
		return self.qe(f"SELECT {col} FROM {table} WHERE {values[0]} = ?", [values[1]])

	def _exists(self, table:str, value:tuple[str, str]):
		return bool(self.qe(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {value[0]} = ?)", [value[1]]))
	
	def _delete(self, table:str, value:tuple[str, str]):
		self.DB.execute(f"DELETE FROM {table} WHERE {value[0]} = ? ", [value[1]])

	# ===== Value Checks =====
	def _assert_hash_id(self, hash_id:int):
		if not self.exists_hash_id(hash_id):
			raise ValueError(f"hash_id not found: {hash_id}")
	
	def _assert_bucket_id(self, bucket_id:int):
		if not self.exists_bucket(bucket_id):
			raise ValueError(f"bucket_id does not exist: {bucket_id}")

	# ===== Embeddings =====
	def insert_embedding(self, hash_id:int, embedding:list[float]):
		# IMPORTANT: DOES NOT AUTO-COMMIT
		Q = f"INSERT INTO embeddings (hash_id, embedding) VALUES ( ?, vector_as_f32(?) )"
		A = [hash_id, str(embedding)]
		self.DB.execute(Q, A)
	
	def remove_embedding(self, hash_id:int):
		# TODO remove from temp bucket tables without regenerating them
		self._assert_hash_id(hash_id)
		self._delete("bucket_members", ("hash_id", hash_id))
		self._delete("embeddings", ("hash_id", hash_id))
		self.commit()

	def get_embedding(self, hash_id:int) -> list[float]:
		self._assert_hash_id(hash_id)
		blob = self._get("embedding", "embeddings", ("hash_id", hash_id))
		embedding = array.array('f')
		embedding.frombytes(blob)
		return embedding.tolist()

	def get_embeddings(self, hash_ids:list[int]) -> list[list[float]]:
		return [self.get_embedding(X) for X in hash_ids]

	def get_num_embeddings(self) -> int:
		return self.qe("SELECT COUNT(*) FROM embeddings")
		
	def exists_hash_id(self, hash_id:int) -> bool:
		return self._exists("embeddings", ("hash_id", hash_id))

	# ===== Buckets =====
	# Buckets are a persistent group of images that can be searched on together
	def new_bucket(self, bucket_name:str) -> int:
		Q = "INSERT INTO buckets ( bucket_name ) VALUES ( ? ) RETURNING bucket_id"
		A = [bucket_name]
		bucket_id = self.qe(Q, A)
		self.commit()
		return bucket_id

	def add_to_bucket(self, bucket_id:int, hash_ids:list[int]):
		self._assert_bucket_id(bucket_id)

		unknown_hashes = [hash_id for hash_id in hash_ids if not self.exists_hash_id(hash_id)]
		hash_ids = [hash_id for hash_id in hash_ids if hash_id not in unknown_hashes]

		Q = "INSERT INTO bucket_members (bucket_id, hash_id) VALUES ( ?, ? )"
		A = [(bucket_id, X) for X in hash_ids]
		self.DB.executemany(Q, A)
		
		self.commit()

		return unknown_hashes

	def remove_bucket(self, bucket_id):
		self._assert_bucket_id(bucket_id)
		self.drop_temp_bucket(bucket_id)
		self._delete("bucket_members", ("bucket_id", bucket_id))
		self._delete("buckets", ("bucket_id", bucket_id))
		self.commit()

	def get_bucket_members(self, bucket_id) -> list[int]:
		self._assert_bucket_id(bucket_id)
		members = self._get("hash_id", "bucket_members", ("bucket_id", bucket_id))
		if members:
			return [members] if isinstance(members, int) else members
		else: 
			return []

	def get_bucket_name(self, bucket_id) -> str:
		self._assert_bucket_id(bucket_id)
		return self._get("bucket_name", "buckets", ("bucket_id", bucket_id))

	def get_buckets(self):
		buckets = self.qe("SELECT * FROM buckets")
		if buckets:
			return [buckets] if isinstance(buckets, tuple) else buckets
		else:
			return []

	def get_bucket_size(self, bucket_id:int) -> int:
		self._assert_bucket_id(bucket_id)
		return self._get("COUNT(*)", "bucket_members", ("bucket_id", bucket_id))

	def exists_bucket(self, bucket_id:int) -> bool:
		return self._exists("buckets", ("bucket_id", bucket_id))

	def exists_bucket_member(self, bucket_id:int, hash_id:int) -> bool:
		return bool(self.qe("SELECT EXISTS ( SELECT 1 FROM bucket_members WHERE bucket_id = ? AND hash_id = ? )", [bucket_id, hash_id]))

	# ========== Search ==========
	# ===== Global Search =====
	def search_global(self, embedding:list[float], model_dims, num_results=100) -> list[tuple[int, float]]:
		self.quant_prepare("embeddings", model_dims)

		Q = f'''
			SELECT hash_id, v.distance
			FROM vector_quantize_scan('embeddings', 'embedding', vector_as_f32( ? )) as v
			INNER JOIN embeddings ON embeddings.rowid = v.rowid
			ORDER BY v.distance
			LIMIT ?
		'''
		A = [str(embedding), num_results]
		results = self.qe(Q, A)
		if results:
			return [results] if isinstance(results, tuple) else results
		else:
			return []

	# ===== Bucket Search =====
	def init_bucket(self, bucket_id:int, model_dims:int=768):
		self._assert_bucket_id(bucket_id)

		self.DB.executescript(f'''
			DROP TABLE IF EXISTS temp_bucket_{bucket_id};

			CREATE TABLE temp_bucket_{bucket_id}(
				hash_id INTEGER PRIMARY KEY,
				embedding BLOB
			);''')

		self.DB.execute(f'''
			INSERT INTO temp_bucket_{bucket_id} ( hash_id, embedding )
			SELECT hash_id, embedding
			FROM embeddings
			NATURAL JOIN bucket_members
			WHERE bucket_id = ?
		''', [bucket_id])

		self.vector_init(f"temp_bucket_{bucket_id}", model_dims)
		self.vector_quantize(f"temp_bucket_{bucket_id}")
		self.commit()

	def bucket_is_init(self, bucket_id:int) -> bool:
		Q = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
		A = [f"temp_bucket_{bucket_id}"]
		return bool(self.qe(Q, A))

	def search_bucket(self, embedding:list[float], bucket_id:int, model_dims:int=768, num_results:int=100) -> list[tuple[int, float]]:
		self._assert_bucket_id(bucket_id)

		if not self.bucket_is_init(bucket_id):
			self.init_bucket(bucket_id, model_dims)

		table_name = f'temp_bucket_{bucket_id}'

		Q = f'''
			SELECT hash_id, v.distance
			FROM vector_quantize_scan('{table_name}', 'embedding', vector_as_f32( ? )) as v
			INNER JOIN {table_name} ON {table_name}.rowid = v.rowid
			ORDER BY v.distance
			LIMIT ?
		'''
		A = [str(embedding), num_results]
		results = self.qe(Q, A)
		if results:
			return [results] if isinstance(results, tuple) else results
		else:
			return []
	
	def drop_temp_bucket(self, bucket_id):
		self.DB.execute(f"DROP TABLE IF EXISTS temp_bucket_{bucket_id};")

	def clean_temp_buckets(self):
		tables = self.DB.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'temp_bucket_%'").fetchall()

		for (name,) in tables:
			self.DB.execute(f"DROP TABLE IF EXISTS {name}")

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
	# TODO find a way to check current quant status

	def quant_prepare(self, table_name:str, model_dims:int=768):
		self.vector_init(table_name, model_dims)
		self.vector_quantize(table_name)
		self.quantize_preload(table_name)
		self.commit()

	def show_quantize_preload_size(self, table_name:str) -> int:
		Q = f"SELECT vector_quantize_memory('{table_name}', 'embedding')"
		size = self.qe(Q)
		print(f"Quantize preload size: {size}")
		return size
	
	def vector_init(self, table_name:str, dimensions:int):
		Q = f"SELECT vector_init('{table_name}', 'embedding', 'dimension={dimensions}')"
		self.DB.execute(Q)

	# Returns the amount of successfully quantized rows 
	def vector_quantize(self, table_name:str) -> int:
		print("Quantizing...")
		quant = self.qe(f"SELECT vector_quantize('{table_name}', 'embedding')")
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
		self.DB.executemany("INSERT OR IGNORE INTO temp_embedding_queue (hash_id, path) VALUES ( ?, ? )", hashes)
		self.commit()

	def dequeue_hashes(self, hash_ids:list[int]):
		self.DB.executemany("DELETE FROM temp_embedding_queue WHERE hash_id = ?", [(h,) for h in hash_ids])
		self.commit()
	
	def get_next_queue(self, num:int) -> list[tuple[int, str]]:
		return self.qe("SELECT hash_id, path FROM temp_embedding_queue LIMIT ?", [num])

	def clean_queue(self):
		self.DB.execute("DELETE FROM temp_embedding_queue WHERE hash_id IN (SELECT hash_id FROM embeddings)")
		self.commit()

	def clear_queue(self):
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