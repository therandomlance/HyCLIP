import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import api
from db import HyCLIP_DB
from config import HyCLIP_Config
from model import HyCLIP_Model

TEST_DIR = Path(__file__).resolve().parent
IMG_DIR = TEST_DIR / "test_images"
DB_PATH = TEST_DIR / "test.db"


def get_images(n: int) -> list[Path]:
	imgs = sorted(f for f in IMG_DIR.iterdir() if f.suffix.lower() in (".jpg", ".png", ".jpeg"))
	assert len(imgs) >= n, f"need at least {n} images, found {len(imgs)}"
	return imgs[:n]


def main():
	# Isolate from the real db/config/model so the test never touches them.
	# All three module singletons (db, config, model) are swapped so that both
	# the api.py endpoints and the hydrus-router endpoints (which now read
	# api.db/api.model/api.config at call time) see the test instances.
	if DB_PATH.exists():
		DB_PATH.unlink()
	real_db = api.db
	api.db = HyCLIP_DB(str(DB_PATH), verbose=False)

	real_config = api.config
	api.config = HyCLIP_Config()
	api.config.config_path = Path(tempfile.mkdtemp(prefix="hyclip_test_config")) / "config.json"
	# Force the hydrus proxy offline so the test never reaches a real hydrus,
	# regardless of what the user's actual config.json contains.
	for key in ("API_KEY", "TAG_SERVICE_KEY", "RATING_SERVICE_KEY"):
		setattr(api.config, key, None)
		api.config.cfg[key] = None

	real_model = api.model
	api.model = HyCLIP_Model(api.config.CLIP_MODEL, verbose=False)
	EMB_DIM = api.model.dims  # follow the configured model, not a hardcoded dim

	client = TestClient(api.app)

	# ---- root ----
	r = client.get("/")
	assert r.status_code == 200 and r.json() == {"Hello": "World"}

	# ---- 409: model not loaded (every model-dependent endpoint) ----
	assert client.get("/model_status").json()["loaded"] is False
	for url, payload in [
		("/eval_text", {"text": "a cat"}),
		("/eval_image", {"path": str(get_images(1)[0])}),
		("/ingest_image", {"hash_id": 1, "path": str(get_images(1)[0])}),
		("/ingest_image_batch", {"items": []}),
		("/work_queue", {}),
	]:
		assert client.post(url, json=payload).status_code == 409, f"{url} should 409 with no model loaded"
	# router endpoint: eval_image_upload with no known hash_id needs the model
	assert client.post("/eval_image_upload").status_code == 409, "eval_image_upload should 409 with no model loaded"

	# ---- hydrus proxy: no API_KEY / TAG_SERVICE_KEY configured -> 503/early-return ----
	assert client.get("/hydrus_status").json()["status"] == "denied", "hydrus_status should be denied without API_KEY"
	for url in ["/thumbnail", "/file", "/file_path"]:
		assert client.get(url, params={"hash_id": 1}).status_code == 503, f"{url} should 503 without API_KEY"
	assert client.get("/resolve_hash", params={"hash": "deadbeef"}).status_code == 503, "resolve_hash should 503 without API_KEY"
	assert client.post("/ingest_enqueue", json={"tag": "hyclip:ingest"}).status_code == 503, "ingest_enqueue should 503 without TAG_SERVICE_KEY"

	# add_hashes_to_bucket: empty hash list short-circuits before reaching hydrus
	r = client.post("/add_hashes_to_bucket", json={"bucket_id": 1, "hashes": []})
	assert r.status_code == 200 and r.json() == {"added": 0, "pending": [], "already_queued": 0, "unknown": []}
	# non-empty hash list requires hydrus -> 503
	assert client.post("/add_hashes_to_bucket", json={"bucket_id": 1, "hashes": ["deadbeef"]}).status_code == 503

	# ---- heartbeat: aggregates model + hydrus + db status ----
	hb = client.get("/heartbeat").json()
	assert {"model", "hydrus", "quant_status", "last_search"} <= set(hb), "heartbeat missing keys"
	assert hb["model"]["loaded"] is False
	assert hb["hydrus"]["status"] == "denied"

	# ---- model: load ----
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

	# ---- ingest queue: queue_status + work_queue ----
	assert client.get("/queue_status").json()["queued"] == 0
	# 100 is new (gets ingested); 1 is already ingested (counted as exists).
	# Distinct paths: the queue table enforces path UNIQUE.
	api.db.enqueue_hashes([(100, str(images[0])), (1, str(images[1]))])
	assert client.get("/queue_status").json()["queued"] == 2
	r = client.post("/work_queue", json={"batch_size": 10})
	body = r.json()
	assert body["processed"] == 2, "work_queue should process the whole batch"
	assert body["ingested"] == 1 and body["already_ingested"] == 1, "work_queue counts wrong"
	assert body["remaining"] == 0, "queue should be empty after work_queue"
	assert client.get("/queue_status").json()["queued"] == 0

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
	# after unload, model-dependent endpoints 409 again
	assert client.post("/eval_text", json={"text": "a cat"}).status_code == 409, "eval_text should 409 after unload"

	api.config = real_config
	api.db.DB.close()
	api.db = real_db
	api.model = real_model
	print("ALL TESTS PASSED")


if __name__ == "__main__":
	main()
