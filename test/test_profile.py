import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import HyCLIP_DB
from config import HyCLIP_Config
from model import HyCLIP_Model

# Run from the repo root so this hits the real hyclip.db + config.json.
# Search is read-only; only the first (cold) search re-quantizes, same as server startup.

N_SEARCHES = 100
NUM_RESULTS = 10

cfg = HyCLIP_Config()
EMB_DIM = HyCLIP_Model(cfg.CLIP_MODEL, verbose=False).dims  # config only, no weights


class ProfiledDB(HyCLIP_DB):
	def random_hash_ids(self, n: int) -> list[int]:
		ids = self.qe("SELECT hash_id FROM embeddings ORDER BY RANDOM() LIMIT ?", [n])
		if ids is None:
			return []
		return [ids] if isinstance(ids, int) else ids


def main():
	db = ProfiledDB(EMB_DIM, cfg.VECTOR_QUANT)
	n = db.get_num_embeddings()
	assert n, "no embeddings in hyclip.db — run ingest.py first"

	first_start = time.perf_counter()
	db.search_id(db.random_hash_ids(1)[0], EMB_DIM, num_results=NUM_RESULTS)
	first_elapsed = time.perf_counter() - first_start

	ids = db.random_hash_ids(N_SEARCHES)
	assert len(ids) == N_SEARCHES

	times = []
	for hash_id in ids:
		start = time.perf_counter()
		results = db.search_id(hash_id, EMB_DIM, num_results=NUM_RESULTS)
		times.append(time.perf_counter() - start)
		assert len(results) == NUM_RESULTS

	avg = sum(times) / len(times)
	print(f"N={n} embeddings, {N_SEARCHES} searches, {NUM_RESULTS} results each")
	print(f"first search (cold, incl quant): {first_elapsed:.3f}s")
	print(f"avg search: {avg * 1000:.2f} ms")
	print(f"min search: {min(times) * 1000:.2f} ms")
	print(f"max search: {max(times) * 1000:.2f} ms")
	print("PROFILE OK")


if __name__ == "__main__":
	main()
