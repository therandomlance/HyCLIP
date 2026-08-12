# AGENTS.md

CLIP-based semantic image search over a [Hydrus](hydrusnetwork.github.io/hydrus/) library. FastAPI server (`api.py`) + static JS UI (`webui/`) + SQLite vector DB. Not a package; modules import each other from the repo root and are run from there.

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

- **Fixture order**: `test/test_images/*` is gitignored. `test_db.py` auto-creates fixtures; run it first — `test_api.py`/`test_model.py` assert fixtures exist and will fail on a fresh checkout.
- **Tests isolate themselves** by redirecting to `test/test.db` and a temp config (and swapping `api.db`/`api.config`/`api.model` singletons). Never point the real `hyclip.db` (repo root, gitignored, your live data) at anything that writes. `test_profile.py` is the exception — it intentionally runs against the real db.
- **Config is `config.json` in the repo root** (cwd), gitignored, generated on first run. `run.sh` cd's to the repo root, so the server reads that file. `API_KEY` and `TAG_SERVICE_KEY` for the Hydrus proxy go there, never in commits. Hydrus proxy endpoints return 503 without them; `test_api.py` nulls them to force the proxy offline.
- **Model loads lazily**: eval/search/ingest endpoints return 409 until `/load_model`. `ingest.py` loads the model for the run (process exit frees it). First load downloads from HuggingFace; `model.py:cached_path` loads from the local HF cache offline afterward.
- **Only `.jpg .jpeg .png .webp` are ingestible** (`model.py:check_filetype`); other image types are skipped as "skipped", not errors.
- **Embedding dims are coupled**: `api.py` passes `model.dims` to search, but `db.py` search methods default to `768` and `test_db.py` hardcodes `EMB_DIM = 768`. Changing `CLIP_MODEL` to a model with different dims breaks db tests and the db.py defaults.
- **`db.py:insert_embedding` does NOT auto-commit** (documented in code). Callers must `db.commit()`.
- **`db.qe()` return shape varies** (scalar / tuple / list / None depending on result), and callers wrap single tuples into lists (e.g. `search_embedding`, `get_buckets`). Mind this when using db helpers.
- **Search re-quantizes on every global↔bucket switch**: `quant_status`+`last_search` gate it, and the whole table quant is synchronous and slow (multi-minute on a big library). `db.py` also resets to `needs_quant` on every startup. Bucket search materializes `temp_bucket_{id}` tables, dropped at startup by `clean_temp_buckets`; `remove_bucket` drops them too.
- **`POST /exit` is an unauthenticated `os._exit(0)` kill switch** — the whole server dies. Don't hit it in tests.
- **Indentation is tabs** everywhere; match it.

## Notes

- `webui/` is plain static JS served at `/webui/index.html`, no build step. The Hydrus proxy router is `webui/hydrus_api.py`, mounted via `build_router()`; it reads `api.db`/`api.config`/`api.model` at call time (so the tests' singleton swaps affect it too).
- `ingest.py` bypasses the API: reads Hydrus's `client.master.db` directly (read-only) and writes straight to `HyCLIP_DB()` in cwd. `run.sh` is self-contained (no `env.sh`).
- `TODO.md` holds an up-to-date code review of open bugs/debt (auth, concurrency, quant perf) — read it before touching those areas.
