# AGENTS.md

CLIP-based semantic image search over a [Hydrus](hydrusnetwork.github.io/hydrus/) library. FastAPI server (`api.py`) + static JS UI (`webui/`) + SQLite vector DB. Not a package; modules import each other from the repo root and are run from there.

## Commands

```bash
# run the server (needs .venv)
./run.sh                                   # binds to HYCLIP_API_URL or config; see env.sh

# tests are plain assert scripts, NOT pytest
.venv/bin/python test/test_db.py           # fast, no model, generates fixtures
.venv/bin/python test/test_api.py          # loads the real CLIP model (slow; needs HF download on first run)
.venv/bin/python test/test_model.py        # loads the real CLIP model

# ingest tagged images from hydrus (starts server in background, logs to server.log)
./ingest.sh [--tag mytag --batch-size 20]
```

There is no lint, typecheck, formatter, or CI config — don't invent commands for them.

## Gotchas

- **Fixture order**: `test/test_images/*` is gitignored. `test_db.py` auto-creates fixtures; run it first — `test_api.py`/`test_model.py` assert fixtures exist and will fail on a fresh checkout.
- **Tests isolate themselves** by redirecting to `test/test.db` and a temp config. Never point the real `hyclip.db` (repo root, gitignored, your live data) at anything that writes.
- **Config is not in the repo** — lives in `~/.config/hyclip/config.json` (Linux) / `%APPDATA%` (Win) / `~/Library/Application Support` (macOS). `API_KEY` and `TAG_SERVICE_KEY` for the Hydrus proxy go there, never in code or commits. Hydrus proxy endpoints return 503 without them.
- **Model loads lazily**: eval/search-ingest endpoints return 409 until `/load_model`. `ingest.py` loads then unloads per run. First load downloads from HuggingFace; `model.py:cached_path` loads from the local HF cache offline afterward.
- **Embedding dims are coupled**: `api.py` passes `model.dims` to search, but `db.py` search methods default to `768` and `test_*.py` hardcode `EMB_DIM = 768`. Changing `CLIP_MODEL` to a model with different dims breaks db tests and the db.py defaults.
- **`db.py:insert_embedding` does NOT auto-commit** (documented in code). Callers must `db.commit()`.
- **`db.qe()` return shape varies** (scalar / tuple / list / None depending on result), and callers wrap single tuples into lists (e.g. `search_global`, `get_buckets`). Mind this when using db helpers.
- Bucket search materializes `temp_bucket_{id}` tables, dropped at startup by `clean_temp_buckets`; `remove_bucket` drops them too.
- **Indentation is tabs** everywhere; match it.

## Notes

- `ingest2.py` (untracked) bypasses the API: reads Hydrus's `client.master.db` directly (read-only) and writes straight to `HyCLIP_DB()` in cwd. Not part of the normal flow.
- `webui/` is plain static JS served at `/webui/index.html`, no build step. `hydrus_api.py` is the Hydrus proxy router mounted into the app.
- `ingest.py` is the Hydrus→HyCLIP CLI used by `ingest.sh`; the persistent ingest queue in SQLite lets interrupted runs resume.
