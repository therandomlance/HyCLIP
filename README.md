# HyCLIP

CLIP-based visual search for [Hydrus](https://hydrusnetwork.github.io/hydrus/) libraries. Images are embedded with a CLIP model (SigLIP2 by default), stored in SQLite with vector-search support, and searched by text prompt or reference image.

## How it works

- **Embed** — a FastAPI server evaluates images and text into normalized 768-dim embeddings via `open_clip`.
- **Store** — embeddings live in a SQLite DB (`hyclip.db`) using the `sqliteai-vector` extension for fast vector search.
- **Search** — search globally or within a named *bucket* (a group of images searched together). Results return nearest `hash_id`s with distances.
- **Proxy** — the server talks to the Hydrus client API server-side so the browser never needs your API key. `hash_id` is the Hydrus `file_id`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On first run the CLIP model is downloaded from HuggingFace and cached locally (subsequent loads are offline).

## Configuration

Config lives in `config.json` (platform-appropriate location: `%APPDATA%/hyclip/` on Windows, `~/Library/Application Support/hyclip/` on macOS, `~/.config/hyclip/` on Linux). File values override defaults; see `config.py` for the full default set.

Key options:

| Key | Purpose |
| --- | --- |
| `API_URL` | Hydrus client API URL (default `http://localhost:45869`) |
| `API_KEY` | Hydrus API key (needed for the proxy endpoints) |
| `TAG_SERVICE_KEY` | Hydrus tag service key (needed for `ingest_enqueue`) |
| `CLIP_MODEL` | open_clip model name (default `ViT-B-16-SigLIP2`) |
| `LOAD_MODEL_ON_STARTUP` | Load the model when the server starts |

## Running

```bash
./run.sh
```

Serves the API and web UI at `http://localhost:8000` (see `HYCLIP_API_URL`).

## Ingesting from Hydrus

Tag images in Hydrus with `hyclip:ingest`, then:

```bash
./ingest.sh                      # enqueue + process everything tagged
./ingest.sh --tag mytag --max-to-evaluate 100 --batch-size 20
```

`ingest.sh` starts the server, enqueues tagged files via `ingest_enqueue`, then drains the queue one batch per request with a progress bar. The queue is persistent — interrupt it and re-run to resume. Files not yet ingested can also be added to a bucket via the web UI; they're queued automatically and can be added once processed.

## Web UI

Served at `/webui/index.html`. Pages: search (text prompts and/or reference images, selectable search scope), bucket management, ingest queue control, and config editing.

## API

See [API.md](API.md) for the full endpoint reference.

## Project layout

- `api.py` — FastAPI server and routes
- `db.py` — SQLite + vector search, buckets, ingest queue
- `model.py` — open_clip model load/eval (image + text)
- `config.py` — config load/save
- `ingest.py` — Hydrus→HyCLIP ingest client (used by `ingest.sh`)
- `webui/` — static web UI and the Hydrus proxy router (`hydrus_api.py`)

## Tests

```bash
.venv/bin/python test/test_db.py
.venv/bin/python test/test_model.py
.venv/bin/python test/test_api.py
```

## TODO

See [TODO.md](TODO.md).
