import os
import sys
import random
import hashlib
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from db import HyCLIP_DB

TEST_DIR = Path(__file__).resolve().parent
IMG_DIR = TEST_DIR / "test_images"
DB_PATH = TEST_DIR / "test.db"

EMB_DIM = 768  # must match quant_prepare's hardcoded dims
N_FIXTURES = 6


def ensure_images():
	"""Create small valid images in test_images if it doesn't have any yet."""
	IMG_DIR.mkdir(exist_ok=True)
	existing = [f for f in IMG_DIR.iterdir() if f.suffix.lower() in (".jpg", ".png", ".jpeg")]
	if existing:
		return sorted(existing)

	for i in range(N_FIXTURES):
		img = Image.new("RGB", (64, 64), (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
		img.save(IMG_DIR / f"fixture_{i}.png")
	return sorted(IMG_DIR.glob("fixture_*.png"))


def make_embedding(seed: int) -> list[float]:
	rng = random.Random(seed)
	return [rng.uniform(-1, 1) for _ in range(EMB_DIM)]


def main():
	images = ensure_images()
	assert images, "no images found"

	if DB_PATH.exists():
		DB_PATH.unlink()

	db = HyCLIP_DB(str(DB_PATH))
	hash_ids = list(range(1, len(images) + 1))

	# ---- ingest embeddings ----
	for i, (hash_id, path) in enumerate(zip(hash_ids, images)):
		digest = hashlib.sha256(path.read_bytes()).hexdigest()
		db.insert_embedding(hash_id, make_embedding(i))
		assert digest, "hash should not be empty"

	assert db.get_num_embeddings() == len(images), "wrong embedding count"

	# ---- get_embedding round-trip ----
	emb = db.get_embedding(hash_ids[0])
	assert len(emb) == EMB_DIM, "embedding dim mismatch"
	assert db.exists_hash_id(hash_ids[0])
	assert not db.exists_hash_id(99999), "ghost hash_id should not exist"

	# ---- buckets ----
	bucket_id = db.new_bucket("test-bucket")
	assert isinstance(bucket_id, int), "new_bucket should return bucket_id"
	assert db.exists_bucket(bucket_id)

	db.add_to_bucket(bucket_id, hash_ids[:4])
	assert db.get_bucket_size(bucket_id) == 4, "wrong bucket size"
	assert db.get_bucket_name(bucket_id) == "test-bucket"

	members = db.get_bucket_members(bucket_id)
	assert len(members) == 4, "wrong member count"
	assert set(members) == set(hash_ids[:4]), "members don't match"

	# ---- global search ----
	db.quant_prepare("embeddings")
	results = db.search_global(make_embedding(0), num_results=3)
	assert len(results) == 3, f"expected 3 results, got {len(results)}"
	dists = [d for _, d in results]
	assert dists == sorted(dists), "results not ordered by distance"

	# ---- bucket search ----
	db.init_bucket(bucket_id)
	assert db.bucket_is_init(bucket_id), "bucket should be initialized"
	bucket_results = db.search_bucket(make_embedding(1), bucket_id, num_results=2)
	assert len(bucket_results) == 2, "bucket search should return requested count"
	assert all(h in set(hash_ids[:4]) for h, _ in bucket_results), "bucket search returned non-member"

	# ---- queue ----
	db.enqueue_hashes([(hash_ids[0], str(images[0])), (hash_ids[1], str(images[1]))])
	assert db.get_num_queue() == 2, "queue count mismatch"

	# ---- removal ----
	db.remove_embedding(hash_ids[-1])
	assert not db.exists_hash_id(hash_ids[-1]), "embedding should be removed"
	assert db.get_num_embeddings() == len(images) - 1, "count after removal wrong"

	db.remove_bucket(bucket_id)
	assert not db.exists_bucket(bucket_id), "bucket should be removed"
	assert not db.bucket_is_init(bucket_id), "temp bucket should be gone"

	print(f"ALL TESTS PASSED ({len(images)} images)")


if __name__ == "__main__":
	main()
