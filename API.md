# HyCLIP API Reference

Base URL: `http://localhost:8000` (default FastAPI/uvicorn port)

All requests/responses are JSON. Errors use FastAPI's standard shape: `{"detail": "<message>"}` (Pydantic validation errors instead return `{"detail": [...]}`).

## Server

### `GET /`
Health check.

**Response**
```json
{"Hello": "World"}
```

### `POST /exit`
Unloads the model, commits the DB, and shuts down the server process. No response.

## Model

### `POST /load_model`
Loads the model configured in `CLIP_MODEL`.

**Response**
```json
{"model": "ViT-B-16-SigLIP2", "loaded": true}
```

### `POST /unload_model`
Frees the model from memory.

**Response**
```json
{"model": "ViT-B-16-SigLIP2", "loaded": false}
```

### `GET /model_status`
Reports whether a model is currently loaded.

**Response**
```json
{"model": "ViT-B-16-SigLIP2", "loaded": true}
```

## Eval

Both return an embedding as an array of floats (`768` with the default model; the length comes from the model config's `embed_dim`). Requires a loaded model (else **409**).

### `POST /eval_image`
Evaluate an image on disk into an embedding.

**Request**
```json
{"path": "/media/img/photo.jpg"}
```

**Response**
```json
[0.01234, -0.4567, 0.8910, ...]
```

### `POST /eval_text`
Evaluate text into an embedding.

**Request**
```json
{"text": "a photo of a cat"}
```

**Response**
```json
[0.01234, -0.4567, 0.8910, ...]
```

## Ingest

Evaluates the file with `HyCLIP_Model.eval_image`, then inserts the embedding with `HyCLIP_DB.insert_embedding` under the client-supplied `hash_id`. Requires a loaded model. Successfully ingested files are removed from the ingest queue if present.

Only `jpg`, `jpeg`, `png`, and `webp` files are ingested (checked by filename extension). Any other filetype is returned as `"status": "skipped"` without error and logged to the server console.

### `POST /ingest_image`
Ingest a single image.

**Request**
```json
{"hash_id": 123456789, "path": "/media/img/photo.jpg"}
```

**Responses**
```json
{"hash_id": 123456789, "status": "ingested"}
```
```json
{"hash_id": 123456789, "status": "already_ingested"}
```
```json
{"hash_id": 123456789, "status": "skipped"}
```

### `POST /ingest_image_batch`
Ingest multiple images. Evaluates all pending images first, then inserts all embeddings in one commit; one result per input item.

**Request**
```json
{"items": [{"hash_id": 111, "path": "/media/img/a.jpg"}, {"hash_id": 222, "path": "/media/img/b.jpg"}]}
```

**Response**
```json
[
  {"hash_id": 111, "status": "ingested"},
  {"hash_id": 222, "status": "ingested"}
]
```

## Ingest Queue

A persistent queue of `(hash_id, path)` entries, typically fed by the Hydrus proxy (`ingest_enqueue`). The client drains it in batches; entries are removed only after processing.

### `GET /ingest_status`
Number of items currently queued.

**Response**
```json
{"queued": 5}
```

### `POST /ingest_process_batch`
Process one batch from the queue (`batch_size` optional; defaults to the `INGEST_BATCH_SIZE` config value). Files that fail evaluation (e.g. moved/deleted since enqueue) count as `errors` and are dropped.

**Request**
```json
{"batch_size": 20}
```

**Response**
```json
{"processed": 20, "ingested": 18, "already_ingested": 1, "skipped": 0, "errors": 1, "remaining": 42}
```

## Buckets

### `POST /create_bucket`
Create a persistent group of images to search together.

**Request**
```json
{"bucket_name": "landscapes"}
```

**Response**
```json
{"bucket_id": 1}
```

### `POST /rename_bucket`
Rename a bucket. Renaming a bucket that doesn't exist is a silent no-op.

**Request**
```json
{"bucket_id": 1, "bucket_name": "mountains"}
```

**Response**
```json
{"bucket_id": 1, "bucket_name": "mountains"}
```

### `POST /insert_into_bucket`
Add already-ingested `hash_id`s to a bucket. Un-ingested `hash_id`s are skipped and returned in `unknown` (re-adding an existing member is an error).

**Request**
```json
{"bucket_id": 1, "hash_ids": [111, 222, 333]}
```

**Response**
```json
{"bucket_id": 1, "inserted": 2, "unknown": [333]}
```

### `GET /list_buckets`
List all buckets (always an array; `[]` when empty).

**Response**
```json
[[1, "landscapes"], [2, "portraits"]]
```

### `GET /list_bucket_members`
List the `hash_id`s in a bucket.

**Query params:** `bucket_id`

**Response**
```json
[111, 222, 333]
```

### `GET /get_bucket_membership`
Inverse of `list_bucket_members`: list the `bucket_id`s containing a `hash_id`. **404** if the `hash_id` is unknown.

**Query params:** `hash_id`

**Response**
```json
[1, 2]
```

### `POST /remove_from_bucket`
Remove `hash_id`s from a bucket. Non-members are silently ignored. Like `insert_into_bucket`, removals don't reach an already-searched bucket's temp table until the server restarts.

**Request**
```json
{"bucket_id": 1, "hash_ids": [111, 222]}
```

**Response**
```json
{"bucket_id": 1, "removed": 2}
```

### `POST /delete_bucket`
Delete a bucket and its temp search table.

**Query params:** `bucket_id`

**Response**
```json
{"bucket_id": 1, "deleted": true}
```

## Embeddings

### `GET /get_embedding`
Fetch the stored embedding for a `hash_id` (`embed_dim` floats). **404** if unknown.

**Query params:** `hash_id`

### `POST /eval_image_upload`
Evaluate raw uploaded image bytes into an embedding (same response shape as `eval_image`). Body is the raw image file. If the optional `hash_id` query param is given and already ingested, returns the stored embedding instead — no model required.

**Query params:** `hash_id` (optional)


### `POST /delete_hash`
Delete an embedding and its bucket memberships.

**Query params:** `hash_id`

**Response**
```json
{"hash_id": 111, "deleted": true}
```

## Search

### `GET /num_embeddings`
Total number of ingested embeddings (a bare integer).

Takes an embedding (from `eval_image`/`eval_text`) and returns the nearest `hash_id`s ordered by distance (ascending). Both return arrays and auto-initialize their search tables on first use.

### `POST /search`
Global search across all embeddings (re-quantizes the embeddings table on each call).

**Request**
```json
{
  "embedding": [0.01234, -0.4567, 0.8910, ...],
  "num_results": 10
}
```
`num_results` defaults to `100`.

**Response**
```json
[
  [111, 0.0012],
  [333, 0.0451],
  [222, 0.1023]
]
```

### `POST /search_bucket`
Search only within a bucket (builds the bucket's temp table on first search).

**Request**
```json
{
  "embedding": [0.01234, -0.4567, 0.8910, ...],
  "bucket_id": 1,
  "num_results": 10
}
```

**Response**
```json
[
  [111, 0.0012],
  [222, 0.1023]
]
```

## Config

### `GET /get_config`
Returns the full config (file values merged over defaults).

**Response**
```json
{
  "API_URL": "http://localhost:45869",
  "HYCLIP_API_URL": "http://127.0.0.1:8000",
  "API_KEY": null,
  "TAG_SERVICE_KEY": null,
  "RATING_SERVICE_KEY": null,
  "VECTOR_QUANT": "UINT8",
  "CLIP_MODEL": "ViT-B-16-SigLIP2",
  "LOAD_MODEL_ON_STARTUP": false,
  "THUMB_SIZE": 200,
  "SEARCH_LIMIT": 100,
  "BUCKET_CACHE_TIMEOUT": 300,
  "EVAL_WORKERS": 8,
  "INGEST_BATCH_SIZE": 20
}
```

### `POST /update_config`
Update one or more config keys and save to disk. Unknown keys are rejected and nothing is saved.

**Request**
```json
{"updates": {"SEARCH_LIMIT": 50, "THUMB_SIZE": 300}}
```

**Response** (the full updated config, persisted)
```json
{"API_URL": "http://localhost:45869", "SEARCH_LIMIT": 50, "THUMB_SIZE": 300, ...}
```

## Errors

| Code | When |
| --- | --- |
| 400 | `update_config` with unknown keys: `{"detail": "unknown config keys: [...]"}`; `eval_image_upload` with an empty body: `{"detail": "empty upload"}` |
| 404 | `ingest_image` with a missing file: `{"detail": "file not found: <path>"}`; `get_embedding`/`resolve_hash`/`add_hashes_to_bucket` on unknown id/hash/bucket |
| 409 | eval/ingest with no loaded model: `{"detail": "model not loaded"}` |
| 422 | Bad body/query types (Pydantic), or un-evaluable image: `{"detail": "could not evaluate image: <path>"}` / `{"detail": "could not evaluate image"}` |
| 500 | `delete_hash`/`delete_bucket` on a nonexistent id (DB raises `ValueError`), or `eval_image` on a missing/unreadable file (PIL raises) |
| 503 | Hydrus proxy endpoints with `API_KEY`/`TAG_SERVICE_KEY` unconfigured |

Hydrus proxy errors (thumbnail/file/file_path/resolve_hash) are forwarded with Hydrus's own status code and a `"hydrus: "`-prefixed detail.

## Gotchas

- `insert_into_bucket` does not refresh a bucket's temp search table. Members added to a bucket that has already been searched won't appear in `search_bucket` results until the server restarts (temp tables are dropped on startup).
- `search` re-quantizes the whole embeddings table on every call, so it's slow on large libraries; `search_bucket` only pays that cost when its bucket is first searched.

## Web UI / Hydrus proxy

The web UI is served at `/webui/index.html` (`/` remains the health check). These endpoints proxy the Hydrus client API server-side so the browser never needs the Hydrus API key (requires `API_KEY` in config, else **503**); `hash_id` is the Hydrus `file_id`. Hydrus errors are passed through with their status code.

### `GET /thumbnail?hash_id=` → thumbnail image bytes
### `GET /file?hash_id=` → full file bytes
### `GET /file_path?hash_id=` → `{"path": "<server path>"}`
### `GET /resolve_hash?hash=` → `{"hash_id": <file_id>, "ingested": <bool>}` — resolve a sha256 hash to a `hash_id`; **404** if unknown to hydrus

### `GET /hydrus_status`
Probe the hydrus connection for the web UI status dot (probes `search_files`, which needs the same permission the thumbnail/file proxies do).

**Response** — `status` is `ok`, `denied` (key missing/rejected/insufficient permissions), or `unreachable` (API URL not responding):
```json
{"status": "ok", "detail": "connected"}
```

### `POST /ingest_enqueue`
Find hydrus files tagged `tag` (default `hyclip:ingest`), enqueue them for ingestion, and remove the tag from those successfully enqueued. Requires `TAG_SERVICE_KEY` (else **503**). Optional `max_evaluate` caps how many are enqueued.

**Request**
```json
{"tag": "hyclip:ingest", "max_evaluate": 100}
```

**Response**
```json
{"found": 120, "enqueued": 100, "skipped": 5}
```
`skipped` counts tagged files with no local path (e.g. trashed/deleted). Drive the queue with `ingest_status`/`ingest_process_batch`.

### `POST /add_hashes_to_bucket`
Resolve sha256 hashes via hydrus: already-ingested files are added to the bucket immediately (and removed from the ingest queue if present); the rest are returned as `pending` for the client to ingest via `ingest_image_batch` and then add via `insert_into_bucket`. Requires `API_KEY`; **404** if the bucket doesn't exist.

**Request**
```json
{"bucket_id": 1, "hashes": ["<sha256>", ...]}
```

**Response**
```json
{"added": 3, "pending": [{"hash_id": 222, "path": "/media/img/b.jpg"}], "already_queued": 1, "unknown": ["<sha256>", ...]}
```
`already_queued` counts how many `pending` files are also sitting in the ingest queue (ingesting them dequeues them). `unknown` lists hashes hydrus didn't recognize.
