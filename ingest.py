import hydrus_api
import argparse
import concurrent.futures
import sqlite3 as sql
import os
import sys
import time
from pathlib import Path

from db import HyCLIP_DB
from model import HyCLIP_Model
from config import HyCLIP_Config

class HyDB():
	def __init__(self, path:str):
		path = "file:" + path
		db_path = os.path.join(path, "client.master.db?mode=ro")
		print(f"DB location: {db_path}")
		self.DB = sql.connect(db_path, uri=True).cursor()

	def get_hash_id(self, _hash:str) -> int:
		blob = sql.Binary(bytes.fromhex(_hash))
		result = self.DB.execute(f"SELECT hash_id FROM hashes WHERE hash = ? ", [blob,]).fetchone()
		if result:
			return result[0]
		else:
			return None

	def get_hash_id_batch(self, hashes:list[str]) -> dict[str, int | None]:
		out = {_hash:None for _hash in hashes}
		blobs = [sql.Binary(bytes.fromhex(X)) for X in hashes]
		marks = ",".join("?" * len(hashes))
		results = self.DB.execute(f"SELECT hash_id, hex(hash) FROM hashes WHERE hash IN ({marks})", blobs).fetchall()
		out.update({_hash.lower():hash_id for hash_id, _hash in results})
		return out

class Timer():
	def __init__(self):
		self.start_time = time.perf_counter()
		self.skipped_filetype = 0
		self.skipped_exists = 0
		self.ingested = 0
		self.failed = 0

	def print_prog(self):
		image_rate = self.ingested / (time.perf_counter()-self.start_time)
		progress = f"Ingested: {self.ingested} | Skipped (Filetype): {self.skipped_filetype} | Skipped (Exists): {self.skipped_exists} | Failed: {self.failed} | {str(image_rate)[:5]} img/s "
		sys.stdout.write(f"\r\x1b[K{progress}")
		sys.stdout.flush()

	def finished(self) -> bool:
		return True if MAX_EVAL != -1 and self.ingested >= MAX_EVAL else False

def _scan_batches(folder:str):
	"""Walk the tree and yield batches of (hash_id, filepath) that need embedding.
	Pure CPU/SQLite work - the pipeline below overlaps it with the GPU."""
	# client_files is mostly "all subfolders or all files", but stray files
	# (desktop.ini and the like) turn up on network shares, so split the two
	# instead of guessing from the first entry
	subfolders = []
	files = []
	for entry in os.scandir(folder):
		if entry.is_dir():
			subfolders.append(entry.name)
		else:
			files.append(entry.name)

	if not subfolders and not files:
		print(f"Folder appears to be empty: {folder}")
		return

	for subfolder in subfolders:
		yield from _scan_batches(os.path.join(folder, subfolder))

	# Moving to file handling once the subfolders are done

	# Skipping past invalid filetypes
	# list of (hash, path)
	valid_hashes = []
	for file in files:
		F = Path(file)

		if not MODEL.check_filetype(file):
			T.skipped_filetype += 1
			continue

		# Hydrus names files by sha256; anything else (stray cover.jpg etc.)
		# would crash the hex lookup
		stem = F.stem.lower()
		if len(stem) != 64 or any(c not in "0123456789abcdef" for c in stem):
			T.skipped_filetype += 1
			continue

		filepath = os.path.join(folder, file)
		valid_hashes.append((stem, filepath))

	# Nothing to do here - Hydrus pre-creates every sector folder, so plenty are empty
	if not valid_hashes:
		return

	for batch in split_into_batches(valid_hashes, BATCH_SIZE):
		hashes, filepaths = zip(*batch)
		hash_ids = HY.get_hash_id_batch(hashes)

		# Check for existing hash_ids and unknown hashes
		# Asked per batch rather than from one up-front set of every hash_id:
		# that load grew linearly with the library and is the one thing here
		# that would not fit in memory on a big db
		existing = DB._known_hash_ids([X for X in hash_ids.values() if X is not None])

		to_eval = []
		for _hash, filepath in batch:
			hash_id = hash_ids[_hash]

			if hash_id is None:
				continue
			if hash_id in existing:
				T.skipped_exists += 1
				# Print progress less often when loop is hot
				if T.skipped_exists % 2000 == 0:
					T.print_prog()
				continue
			to_eval.append((hash_id, filepath))

		if to_eval:
			yield to_eval

def _ingest_pipelined(top_dir:str):
	"""Double-buffered ingest: while the GPU embeds batch N, a background job
	reads + preprocesses batch N+1 so SMB latency hides behind the forward pass."""
	batches = _scan_batches(top_dir)

	current = next(batches, None)
	if current is None:
		return

	with concurrent.futures.ThreadPoolExecutor(max_workers=1) as prefetcher:
		fut = prefetcher.submit(MODEL.preprocess_image_batch, [F for _, F in current], CFG.EVAL_WORKERS)

		while current is not None:
			# Scan ahead on this thread while the prefetcher preprocesses
			nxt = next(batches, None)

			tensors = fut.result()
			if nxt is not None:
				fut = prefetcher.submit(MODEL.preprocess_image_batch, [F for _, F in nxt], CFG.EVAL_WORKERS)

			# GPU forward for the current batch overlaps the prefetch above
			embeddings = MODEL.encode_preprocessed(tensors)

			# Removing failed embeddings and inserting into db
			for (hash_id, _), embedding in zip(current, embeddings):
				if embedding is None:
					T.failed += 1
					continue

				DB.insert_embedding(hash_id, embedding)
				T.ingested += 1

				if T.finished():
					T.print_prog()
					return

			T.print_prog()
			DB.commit()

			current = nxt

def split_into_batches(items:list, batch_size:int):
	return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

parser = argparse.ArgumentParser(
	description="Bypass the HyCLIP/Hydrus API to ingest your entire library faster"
)
parser.add_argument("db_location", type=str, help="Path to the hydrus db files")
parser.add_argument("folder", type=str, help="client_files folder to scan")
parser.add_argument("--max-eval", type=int, default=-1, help="max images to scan in one run")
parser.add_argument("--batch-size", type=int, default=None, help="images per GPU batch (default: INGEST_BATCH_SIZE from config.json)")

args = parser.parse_args()

DB_PATH = args.db_location
HY = HyDB(DB_PATH)


CFG = HyCLIP_Config()
MODEL = HyCLIP_Model(CFG.CLIP_MODEL)
DB = HyCLIP_DB(MODEL.dims, CFG.VECTOR_QUANT)

MODEL.load_model()

TOP_DIR = args.folder
MAX_EVAL = args.max_eval
BATCH_SIZE = args.batch_size or CFG.INGEST_BATCH_SIZE

T = Timer()

_ingest_pipelined(TOP_DIR)

DB.commit()



