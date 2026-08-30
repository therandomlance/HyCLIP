import os
import sys
import random
import hashlib
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from db import HyCLIP_DB
from config import HyCLIP_Config

TEST_DIR = Path(__file__).resolve().parent
IMG_DIR = TEST_DIR / "test_images"
DB_PATH = TEST_DIR / "test.db"

cfg = HyCLIP_Config()
QUANT = cfg.VECTOR_QUANT

EMB_DIM = 768  # must match the dims of the model used to generate embeddings
N_FIXTURES = 8  # >= N_EVAL in test_model.py so a fresh checkout passes


def ensure_images():
	"""Create small valid images in test_images if it doesn't have any yet."""
	IMG_DIR.mkdir(exist_ok=True)
	existing = [f for f in IMG_DIR.iterdir() if f.suffix.lower() in (".jpg", ".png", ".jpeg")]
	if existing:
		return sorted(existing)[:N_FIXTURES]

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

	db = HyCLIP_DB(EMB_DIM, QUANT, str(DB_PATH), verbose=False)
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

	embeds = db.get_embeddings(hash_ids[:2])
	assert len(embeds) == 2 and all(len(e) == EMB_DIM for e in embeds), "get_embeddings dim mismatch"
	assert set(db.get_all_hash_ids()) == set(hash_ids), "get_all_hash_ids wrong"

	# ---- buckets ----
	bucket_id = db.new_bucket("test-bucket")
	assert isinstance(bucket_id, int), "new_bucket should return bucket_id"
	assert db.exists_bucket(bucket_id)

	db.add_to_bucket(bucket_id, hash_ids[:4])
	# adding members only affects bucket searches; the last_search flip forces
	# that bucket to re-quant without re-quantizing the (slow) global index
	assert db.last_search == "global", "bucket change should force a bucket re-quant on next search"
	assert db.get_bucket_size(bucket_id) == 4, "wrong bucket size"
	assert db.get_bucket_name(bucket_id) == "test-bucket"

	# un-ingested hash_ids are skipped and returned, not an error
	unknown = db.add_to_bucket(bucket_id, [99999])
	assert unknown == [99999], f"expected [99999] unknown, got {unknown}"
	assert db.get_bucket_size(bucket_id) == 4, "unknown id should not be added"

	members = db.get_bucket_members(bucket_id)
	assert len(members) == 4, "wrong member count"
	assert set(members) == set(hash_ids[:4]), "members don't match"

	# rename + list buckets
	db.rename_bucket(bucket_id, "renamed")
	assert db.get_bucket_name(bucket_id) == "renamed", "rename_bucket didn't stick"
	assert db.list_buckets() == [(bucket_id, "renamed")], "get_buckets wrong"
	db.rename_bucket(bucket_id, "test-bucket")

	# ---- global search ----
	db.quant_prepare("embeddings", EMB_DIM, QUANT)
	assert db.quant_status == "ready", "quant status should be ready after quant_prepare"
	results = db.search_embedding(make_embedding(0), num_results=3)
	assert db.last_search == "global", "global search should record last_search"
	assert len(results) == 3, f"expected 3 results, got {len(results)}"
	dists = [d for _, d in results]
	assert dists == sorted(dists), "results not ordered by distance"

	# ---- bucket search ----
	db.init_bucket(bucket_id)
	assert db.bucket_is_init(bucket_id), "bucket should be initialized"
	bucket_results = db.search_embedding_bucket(make_embedding(1), bucket_id, num_results=2)
	assert len(bucket_results) == 2, "bucket search should return requested count"
	assert all(h in set(hash_ids[:4]) for h, _ in bucket_results), "bucket search returned non-member"

	# remove_from_bucket: only removes existing members; syncs the temp table; forces re-quant
	db.remove_from_bucket(bucket_id, [1, 99999])
	assert db.get_bucket_size(bucket_id) == 3, "remove_from_bucket wrong size"
	assert set(db.get_bucket_members(bucket_id)) == set(hash_ids[1:4]), "remove_from_bucket removed wrong members"
	assert not db.exists_bucket_member(bucket_id, 99999), "non-member should be skipped"
	assert db.quant_status == "needs_quant", "removing from the active bucket should force re-quant"
	assert db.qe(f"SELECT COUNT(*) FROM temp_bucket_{bucket_id}") == 3, "temp bucket should sync removals"

	# ---- tags ----
	assert db.vec_centroid([]) is None, "vec_centroid([]) should return None, not crash"
	cent = db.vec_centroid([make_embedding(0), make_embedding(1)])
	assert len(cent) == EMB_DIM, "centroid dim mismatch"

	assert not db.exists_tag("species:lopunny")
	db.insert_tag("species:lopunny", make_embedding(0))
	db.commit()
	assert db.exists_tag("species:lopunny")
	stored0 = db.get_tag_embedding("species:lopunny")
	assert len(stored0) == EMB_DIM, "tag embedding dim mismatch"

	# INSERT OR REPLACE updates in place; the stored value must change
	db.insert_tag("species:lopunny", make_embedding(1))
	db.commit()
	stored1 = db.get_tag_embedding("species:lopunny")
	assert stored0 != stored1, "insert_tag should replace the stored embedding"

	db.insert_tag("species:gardevoir", make_embedding(2))
	db.commit()
	tags = db.get_tags()
	assert isinstance(tags, list) and set(tags) == {"species:gardevoir", "species:lopunny"}, f"get_tags wrong: {tags}"

	# search_tags: linear scan ordered by distance; the query vector's own tag is nearest
	hits = db.search_tags(make_embedding(1), limit=10)
	assert hits and hits[0][0] == "species:lopunny", f"nearest tag wrong: {hits}"
	assert [d for _, d in hits] == sorted(d for _, d in hits), "search_tags not ordered by distance"
	assert len(db.search_tags(make_embedding(1), limit=1)) == 1, "limit not respected"

	# ---- queue ----
	db.enqueue_hashes([(hash_ids[0], str(images[0])), (hash_ids[1], str(images[1]))])
	assert db.get_num_queue() == 2, "queue count mismatch"
	assert db.get_queued_ids([hash_ids[0], 99999]) == {hash_ids[0]}, "queue membership check wrong"
	assert db.get_queued_ids([]) == set(), "empty lookup should be empty set"

	assert db.get_next_queue(1) == [(hash_ids[0], str(images[0]))], "get_next_queue wrong head"
	db.dequeue_hashes([hash_ids[0]])
	assert db.get_num_queue() == 1, "dequeue should remove one hash"
	assert db.get_next_queue(2) == [(hash_ids[1], str(images[1]))], "get_next_queue after dequeue"

	# ---- removal ----
	# remove_embedding on a hash that IS in a bucket tears down its membership + temp table
	db.add_to_bucket(bucket_id, [hash_ids[4]])
	db.remove_embedding(hash_ids[4])
	assert not db.exists_hash_id(hash_ids[4]), "embedding should be removed"
	assert db.get_bucket_membership(hash_ids[4]) == [], "removed hash should leave its bucket"
	assert db.get_bucket_size(bucket_id) == 3, "bucket should shed removed member"

	db.remove_embedding(hash_ids[-1])
	assert not db.exists_hash_id(hash_ids[-1]), "embedding should be removed"
	assert db.get_num_embeddings() == len(images) - 2, "count after removal wrong"
	assert db.get_bucket_membership(hash_ids[-1]) == [], "removed hash should have no bucket memberships"

	db.remove_bucket(bucket_id)
	assert not db.exists_bucket(bucket_id), "bucket should be removed"
	assert not db.bucket_is_init(bucket_id), "temp bucket should be gone"

	print(f"ALL TESTS PASSED ({len(images)} images)")


if __name__ == "__main__":
	main()
