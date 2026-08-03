# HyCLIP

Search your [Hydrus](https://hydrusnetwork.github.io/hydrus/) library by *what's in the picture*, not just its tags. Point HyCLIP at your Hydrus client and it builds a searchable index of your images, so you can find them by describing what you're looking for in a text prompt, or by uploading a reference image that looks like what you want.

## How it works

- **Index** — each image is evaluated by a CLIP model (SigLIP2 by default) into a numeric "embedding" that captures its visual content.
- **Store** — embeddings are kept in a SQLite database (`hyclip.db`) with fast vector-search support.
- **Search** — search your whole library, or limit results to a named *bucket* (a group of images searched together).
- **Proxy** — the server talks to the Hydrus client API on your behalf, so your browser never needs your API key.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On first run the CLIP model is downloaded from HuggingFace and cached locally (subsequent loads are offline).

## Running

```bash
./run.sh
```

Serves the API and web UI at `http://localhost:8000` (see `HYCLIP_API_URL`).

## Web UI

Open `http://localhost:8000/webui/index.html`. Four tabs:

- **Search** — combine one or more text prompts and/or reference images, then search all images or a single bucket. Reference images can be uploaded, dropped onto the page, or referenced by their Hydrus sha256 hash.
- **Buckets** — create and delete buckets, and add images to them. Paste Hydrus sha256 hashes; files already ingested are added immediately, the rest are ingested on the spot (requires the model to be loaded) and then added.
- **Ingest** — enqueue all Hydrus files tagged `hyclip:ingest`, then process the queue in batches with a live progress bar and a start/stop control. The queue is persistent, so you can interrupt and resume.
- **Config** — view and edit configuration from the browser instead of editing `config.json` by hand.

## Configuration

Config lives in `config.json` (platform-appropriate location: `%APPDATA%/hyclip/` on Windows, `~/Library/Application Support/hyclip/` on macOS, `~/.config/hyclip/` on Linux). File values override defaults; see `config.py` for the full default set.

Key options:

| Key | Purpose |
| --- | --- |
| `API_URL` | Hydrus client API URL (default `http://localhost:45869`) |
| `API_KEY` | Hydrus API key (needed for the proxy endpoints) |
| `TAG_SERVICE_KEY` | Hydrus tag service key (needed for `ingest_enqueue`) |
| `HYCLIP_API_URL` | URL the server binds to (default `http://localhost:8000`) |
| `CLIP_MODEL` | open_clip model name (default `ViT-B-16-SigLIP2`) |
| `LOAD_MODEL_ON_STARTUP` | Load the model when the server starts |
| `VECTOR_QUANT` | Vector storage quantization (e.g. `UINT8`) |

## Ingesting from Hydrus

`ingest.py` scans your Hydrus `client_files` folder and writes embeddings straight into `hyclip.db` — no server needed. It reads Hydrus's `client.master.db` read-only to map the sha256-hash filenames to their file ids. Run it from the repo root so it writes to the same `hyclip.db` the server uses:

```bash
.venv/bin/python ingest.py /path/to/hydrus/db /path/to/client_files
.venv/bin/python ingest.py /path/to/hydrus/db /path/to/client_files --max-eval 100 --batch-size 40
```

Already-ingested files are skipped, so an interrupted run can be resumed by re-running. The web UI's Ingest tab still provides the server-based alternative (tag files in Hydrus with `hyclip:ingest` and drain the persistent queue).

## API

See [API.md](API.md) for the full endpoint reference.

## Project layout

- `api.py` — FastAPI server and routes
- `db.py` — SQLite + vector search, buckets, ingest queue
- `model.py` — open_clip model load/eval (image + text)
- `config.py` — config load/save
- `ingest.py` — direct Hydrus→HyCLIP bulk ingest (reads `client.master.db` + `client_files` directly, no server)
- `webui/` — static web UI and the Hydrus proxy router (`hydrus_api.py`)

## Tests

```bash
.venv/bin/python test/test_db.py
.venv/bin/python test/test_model.py
.venv/bin/python test/test_api.py
```
