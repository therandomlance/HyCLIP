# AGENTS.md

CLIP-based semantic image search over a [Hydrus](hydrusnetwork.github.io/hydrus/) library. FastAPI server (`api.py`) + static JS UI (`webui/`) + SQLite vector DB. Not a package; modules import each other from the repo root and are run from there.

## Architecture

- `orchestrator.py` is the wiring layer (new in the big Aug 2026 refactor): a module-global `ORCH = Orchestrator()` owns `CFG`/`MODEL`/`DB`/`HY` (the hydrus client). `api.py` is thin HTTP endpoints that mostly delegate to `ORCH.*`; the `_assert_*` route guards read `ORCH.*` too. Follow this pattern — endpoints should not touch `db.py`/`model.py` directly.
- Hydrus proxy endpoints (`/thumbnail`, `/file`, `/file_path`, `/resolve_hash`, `/add_hashes_to_bucket`, `/ingest_enqueue`, `/eval_image_upload`) used to live in `webui/hydrus_api.py`; that file is **deleted**. They're now plain routes in `api.py`, and the webui JS calls the same-origin paths. There is no separate proxy router or `build_router()` anymore.
- `tags.py` is **deleted**; tag-centroid logic (`make_tag` via `search_files` + `vec_centroid` + `insert_tag`) now lives in `orchestrator.py` + `db.py`.

## Commands

```bash
# run the server (needs .venv)
./run.sh                                   # binds to HYCLIP_API_URL env or config

# tests are plain assert scripts, NOT pytest; run in this order (fixtures!)
./test.sh                                  # test_db -> test_api -> test_model
.venv/bin/python test/test_db.py           # fast, no model, generates fixtures
.venv/bin/python test/test_api.py          # loads the real CLIP model (slow; needs HF download on first run)
.venv/bin/python test/test_model.py        # loads the real CLIP model

# benchmark — NOT in test.sh, hits the REAL hyclip.db + config.json
.venv/bin/python test/test_profile.py      # needs ingested data; cold search re-quantizes (writes)

# ingest directly into hyclip.db (no server; reads hydrus client.master.db read-only)
.venv/bin/python ingest.py <hydrus_db_dir> <client_files_dir> [--max-eval N]
```

There is no lint, typecheck, formatter, or CI config — don't invent commands for them.

Run `ingest.py` from the repo root so it writes to the same `hyclip.db` the server uses; already-ingested files are skipped, so re-running resumes an interrupted ingest. Batch size is `INGEST_BATCH_SIZE` from config (no `--batch-size` flag). The server-based ingest flow (web UI Ingest tab, `ingest_enqueue`/`work_queue` endpoints, persistent queue) still exists in the API.

## Gotchas

- **Tests swap `api.ORCH.DB`/`ORCH.CFG`/`ORCH.MODEL`**, not the old `api.db`/`api.config`/`api.model`. `test_api.py` rewires the orchestrator's three objects (temp db path, temp config path, quiet model) so both the endpoints and the orchestrator methods see the test instances.
- **Fixture order**: `test/test_images/*` is gitignored. `test_db.py` auto-creates fixtures; run it first — `test_api.py`/`test_model.py` assert fixtures exist and will fail on a fresh checkout.
- **Tests isolate themselves** by redirecting to `test/test.db` and a temp config. Never point the real `hyclip.db` (repo root, gitignored, your live data) at anything that writes. `test_profile.py` is the exception — it intentionally runs against the real db.
- **Config is `config.json` in the repo root** (cwd), gitignored, generated on first run. `run.sh` cd's to the repo root, so the server reads that file. `API_KEY` and `TAG_SERVICE_KEY` for the Hydrus proxy go there, never in commits. Hydrus-proxy endpoints return 503 without them; `test_api.py` nulls them to force the proxy offline.
- **Model loads lazily**: eval/search/ingest endpoints return 409 until `/load_model`. `ingest.py` loads the model for the run (process exit frees it). First load downloads from HuggingFace; `model.py:cached_path` loads from the local HF cache offline afterward.
- **Only `.jpg .jpeg .png .webp` are ingestible** (`model.py:check_filetype`); other image types are skipped as "skipped", not errors.
- **Embedding dims are coupled**: `HyCLIP_DB(model_dims, quant)` bakes dims in at construction — `orchestrator.py`/`ingest.py` pass `MODEL.dims`, and db search uses `self.model_dims`. But `test_db.py` hardcodes `EMB_DIM = 768` (and `quant_prepare` still defaults to 768). Changing `CLIP_MODEL` to a model with different dims breaks db tests and any `HyCLIP_DB` constructed without explicit dims.
- **`db.py:insert_embedding` does NOT auto-commit** (documented in code). Callers must `db.commit()`.
- **`db.qe()` return shape varies** (scalar / tuple / list / None depending on result), and callers wrap single tuples into lists (e.g. `search_embedding`, `get_buckets`). Mind this when using db helpers. Exception: `get_next_queue` returns `.fetchall()` directly — always a `list[tuple]`, even when empty.
- **Search re-quantizes on every global↔bucket switch**: `quant_status`+`last_search` gate it, and the whole table quant is synchronous and slow (multi-minute on a big library). `db.py` also resets to `needs_quant` on every startup. Bucket search materializes `temp_bucket_{id}` tables, dropped at startup by `clean_temp_buckets`; `remove_bucket` drops them too.
- **`POST /exit` is an unauthenticated `os._exit(0)` kill switch** — the whole server dies. Don't hit it in tests.
- **Indentation is tabs** everywhere; match it.

## Notes

- `webui/` is plain static JS served at `/webui/index.html`, no build step. Hydrus proxying is plain `api.py` routes now (see Architecture); there is no separate router to keep in sync.
- `ingest.py` bypasses the API: reads Hydrus's `client.master.db` directly (read-only) and writes straight to `HyCLIP_DB()` in cwd. `run.sh` is self-contained (no `env.sh`).
- `TODO.md` holds an up-to-date code review of open bugs/debt (auth, concurrency, quant perf, dead `search_tags`/`db.init_filter` scaffolding) — read it before touching those areas.
