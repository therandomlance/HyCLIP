import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import api
from db import HyCLIP_DB
from config import HyCLIP_Config

TEST_DIR = Path(__file__).resolve().parent
IMG_DIR = TEST_DIR / "test_images"
DB_PATH = TEST_DIR / "test.db"

EMB_DIM = api.model.dims  # follow the configured model, not a hardcoded dim


def get_images(n: int) -> list[Path]:
	imgs = sorted(f for f in IMG_DIR.iterdir() if f.suffix.lower() in (".jpg", ".png", ".jpeg"))
	assert len(imgs) >= n, f"need at least {n} images, found {len(imgs)}"
	return imgs[:n]


def main():
	# Isolate from the real db/config so the test never touches them
	if DB_PATH.exists():
		DB_PATH.unlink()
	real_db = api.db
	api.db = HyCLIP_DB(str(DB_PATH))

	real_config = api.config
	api.config = HyCLIP_Config()
	api.config.config_path = Path(tempfile.mkdtemp(prefix="hyclip_test_config")) / "config.json"

	client = TestClient(api.app)

	# ---- root ----
	r = client.get("/")
	assert r.status_code == 200 and r.json() == {"Hello": "World"}

	# ---- model: unloaded -> 409 on eval, then load ----
	assert client.get("/model_status").json()["loaded"] is False
	r = client.post("/eval_text", json={"text": "a cat"})
	assert r.status_code == 409, "eval_text should 409 with no model loaded"

	r = client.post("/load_model")
	assert r.status_code == 200 and r.json()["loaded"] is True
	assert client.get("/model_status").json()["loaded"] is True

	# ---- eval ----
	text_emb = client.post("/eval_text", json={"text": "a photo of a cat"}).json()
	assert len(text_emb) == EMB_DIM, "text embedding dim mismatch"

	images = get_images(4)
	img_emb = client.post("/eval_image", json={"path": str(images[0])}).json()
	assert len(img_emb) == EMB_DIM, "image embedding dim mismatch"
	assert img_emb != text_emb, "text and image embeddings should differ"

	# ---- ingest ----
	r = client.post("/ingest_image", json={"hash_id": 1, "path": str(images[0])})
	assert r.status_code == 200 and r.json() == {"hash_id": 1, "status": "ingested"}
	r = client.post("/ingest_image", json={"hash_id": 1, "path": str(images[0])})
	assert r.json()["status"] == "already_ingested", "re-ingest should be idempotent"

	r = client.post("/ingest_image", json={"hash_id": 2, "path": "/does/not/exist.jpg"})
	assert r.status_code == 404, "missing file should 404"

	# wrong filetype (.gif is an image but not in the allowed set) -> skipped, not inserted
	gif_path = TEST_DIR / "skip_test.gif"
	shutil.copy(images[0], gif_path)
	try:
		r = client.post("/ingest_image", json={"hash_id": 3, "path": str(gif_path)})
		assert r.status_code == 200 and r.json()["status"] == "skipped", "wrong filetype should be skipped"
		assert not api.db.exists_hash_id(3), "skipped image should not be inserted"
	finally:
		gif_path.unlink()

	# batch: 3 new images (hash_ids 10-12)
	items = [{"hash_id": 10 + i, "path": str(images[i + 1])} for i in range(3)]
	r = client.post("/ingest_image_batch", json={"items": items})
	assert r.status_code == 200
	assert all(item["status"] == "ingested" for item in r.json()), r.json()
	assert api.db.get_num_embeddings() == 4, "expected 4 embeddings after ingest"

	# ingesting removes the file from the persistent queue
	api.db.enqueue_hashes([(99, str(images[0]))])
	assert api.db.get_num_queue() == 1
	r = client.post("/ingest_image_batch", json={"items": [{"hash_id": 99, "path": str(images[0])}]})
	assert r.json()[0]["status"] == "ingested"
	assert api.db.get_num_queue() == 0, "ingested file should be dequeued"

	# ---- buckets ----
	r = client.post("/create_bucket", json={"bucket_name": "test-bucket"})
	assert r.status_code == 200
	bucket_id = r.json()["bucket_id"]
	assert isinstance(bucket_id, int)

	r = client.post("/insert_into_bucket", json={"bucket_id": bucket_id, "hash_ids": [1, 10, 11]})
	assert r.status_code == 200 and r.json()["inserted"] == 3

	# un-ingested hash_ids are skipped and reported, not an error
	r = client.post("/insert_into_bucket", json={"bucket_id": bucket_id, "hash_ids": [99999]})
	assert r.status_code == 200 and r.json()["inserted"] == 0 and r.json()["unknown"] == [99999]

	buckets = client.get("/list_buckets").json()
	assert any(bucket_id == row[0] for row in buckets), buckets

	members = client.get("/list_bucket_members", params={"bucket_id": bucket_id}).json()
	assert set(members) == {1, 10, 11}, members

	# ---- search ----
	r = client.post("/search", json={"embedding": text_emb, "num_results": 2})
	assert r.status_code == 200
	hits = r.json()
	assert isinstance(hits, list) and len(hits) >= 1, f"global search returned nothing: {hits}"

	r = client.post("/search_bucket", json={"embedding": text_emb, "bucket_id": bucket_id, "num_results": 5})
	assert r.status_code == 200
	bucket_hits = [h for h, _ in r.json()]
	assert bucket_hits, "bucket search returned nothing"
	assert set(bucket_hits) <= {1, 10, 11}, f"bucket search leaked: {bucket_hits}"

	# ---- delete ----
	r = client.post("/delete_hash", params={"hash_id": 12})
	assert r.status_code == 200 and r.json()["deleted"] is True
	assert not api.db.exists_hash_id(12), "hash should be gone after delete"

	# deleting a missing id should 404, not 500
	r = client.post("/delete_hash", params={"hash_id": 12})
	assert r.status_code == 404, "delete of missing hash should 404"

	r = client.post("/delete_bucket", params={"bucket_id": bucket_id})
	assert r.status_code == 200 and r.json()["deleted"] is True
	assert not api.db.exists_bucket(bucket_id), "bucket should be gone after delete"

	# ---- status / counts ----
	assert isinstance(client.get("/num_embeddings").json(), int)
	db_stat = client.get("/db_status").json()
	assert {"quant_status", "last_search"} <= set(db_stat), "db_status missing keys"

	# get_embedding for a missing id should 404
	assert client.get("/get_embedding", params={"hash_id": 99999}).status_code == 404

	# ---- config ----
	cfg = client.get("/get_config").json()
	assert "CLIP_MODEL" in cfg and "SEARCH_LIMIT" in cfg, "config keys missing"

	r = client.post("/update_config", json={"updates": {"SEARCH_LIMIT": 5}})
	assert r.status_code == 200 and r.json()["SEARCH_LIMIT"] == 5
	assert (api.config.config_path).exists(), "update_config should persist to disk"

	r = client.post("/update_config", json={"updates": {"NOT_A_KEY": 1}})
	assert r.status_code == 400, "unknown config key should 400"

	# ---- unload ----
	r = client.post("/unload_model")
	assert r.status_code == 200 and r.json()["loaded"] is False

	api.config = real_config
	api.db.DB.close()
	api.db = real_db
	print("ALL TESTS PASSED")


if __name__ == "__main__":
	main()
