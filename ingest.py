import hydrus_api
import argparse
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

def _ingest_folder(folder:str):
	items = os.listdir(folder)

	if not items or len(items) == 0:
		print(f"Folder appears to be empty: {folder}")
		return

	# Folders in client_files are either all subfolders or all files, no mixing
	if os.path.isdir(os.path.join(folder, items[0])):
		for subfolder in items:
			_ingest_folder(os.path.join(folder, subfolder))

			if T.finished():
				T.print_prog()
				return

		# Once all subfolders are finished, return
		return

	# Moving to file handling if folders not present

	# Skipping past invalid filetypes
	# list of (hash, path)
	valid_hashes = []
	for file in items:
		F = Path(file)

		if not MODEL.check_filetype(file):
			T.skipped_filetype += 1
			continue

		filepath = os.path.join(folder, file)
		valid_hashes.append((F.stem, filepath))

	hash_batches = split_into_batches(valid_hashes, CFG.INGEST_BATCH_SIZE)

	for batch in hash_batches:		
		hashes, filepaths = zip(*batch)
		hash_ids = HY.get_hash_id_batch(hashes)

		# Check for existing hash_ids and unknown hashes
		to_eval = []
		for _hash, filepath in batch:
			hash_id = hash_ids[_hash]
			
			if hash_id is None:
				continue
			if hash_id in EXISTING:
				T.skipped_exists += 1
				# Print progress less often when loop is hot
				if T.skipped_exists % 2000 == 0:
					T.print_prog()
				continue
			to_eval.append((hash_id, filepath))
		
		# Batch embedding
		embeddings = MODEL.eval_image_batch([F for _, F in to_eval], CFG.EVAL_WORKERS)
		
		# Removing failed embeddings and inserting into db
		for (hash_id, _), embedding in zip(to_eval, embeddings):
			if embedding is None:
				T.failed += 1
				continue

			DB.insert_embedding(hash_id, embedding)
			T.ingested += 1

			if T.finished():
				T.print_prog()
				return

		# Print Progress each time a non-trivial batch is completed
		if len(to_eval) > 0:
			T.print_prog()

		DB.commit()

def split_into_batches(items:list, batch_size:int):
	return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

parser = argparse.ArgumentParser(
	description="Bypass the HyCLIP/Hydrus API to ingest your entire library faster"
)
parser.add_argument("db_location", type=str, help="Path to the hydrus db files")
parser.add_argument("folder", type=str, help="client_files folder to scan")
parser.add_argument("--max-eval", type=int, default=-1, help="max images to scan in one run")

args = parser.parse_args()

DB_PATH = args.db_location
HY = HyDB(DB_PATH)


CFG = HyCLIP_Config()
MODEL = HyCLIP_Model(CFG.CLIP_MODEL)
DB = HyCLIP_DB(MODEL.dims, CFG.VECTOR_QUANT)

MODEL.load_model()

TOP_DIR = args.folder
MAX_EVAL = args.max_eval

N = 0
T = Timer()

# TODO this could get pretty fat for very thick DBs
EXISTING = set(DB.get_all_hash_ids())

_ingest_folder(TOP_DIR)

DB.commit()



