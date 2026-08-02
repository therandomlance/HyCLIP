import argparse
import sys
import time

import httpx

from config import HyCLIP_Config

BAR_WIDTH = 40


def bar(done: int, total: int, width: int = BAR_WIDTH) -> str:
	frac = done / total if total else 0
	filled = int(width * frac)
	return f"[{'#' * filled}{'.' * (width - filled)}] {done}/{total} ({frac:.0%})"


def redraw(line: str):
	sys.stdout.write(f"\r\x1b[K{line}")
	sys.stdout.flush()


def main():
	parser = argparse.ArgumentParser(
		description="Enqueue hydrus images tagged 'hyclip:ingest' into the HyCLIP queue via the API, then process the queue"
	)
	parser.add_argument("--batch-size", type=int, default=20, help="Images per HyCLIP API request")
	parser.add_argument("--tag", type=str, default="hyclip:ingest", help="Hydrus tag to enqueue")
	parser.add_argument("--max-to-evaluate", type=int, default=None, help="Cap the number of tagged images enqueued")
	parser.add_argument("--api-url", type=str, default=None, help="HyCLIP API base URL (default: config)")
	args = parser.parse_args()

	try:
		_run(args)
	except KeyboardInterrupt:
		print("\nInterrupted. Progress is preserved in the queue; run again to resume.")
		sys.exit(130)


def _run(args):
	cfg = HyCLIP_Config()
	url = (args.api_url or cfg.HYCLIP_API_URL).rstrip("/")
	client = httpx.Client(timeout=600)

	# ===== Phase 1: enqueue tagged images =====
	r = client.post(f"{url}/ingest_enqueue", json={"tag": args.tag, "max_evaluate": args.max_to_evaluate})
	r.raise_for_status()
	res = r.json()
	print(f"Tag {args.tag}: found {res['found']}, enqueued {res['enqueued']}, skipped {res['skipped']}")

	# ===== Phase 2: work through the queue one batch per request =====
	total = client.get(f"{url}/ingest_status").json()["queued"]
	print(f"Queue: {total} images to process")
	if not total:
		return

	client.post(f"{url}/load_model").raise_for_status()

	start = time.time()
	ingested = already = skipped = errors = done = 0

	try:
		while True:
			r = client.post(f"{url}/ingest_process_batch", json={"batch_size": args.batch_size})
			r.raise_for_status()
			res = r.json()
			if not res["processed"]:
				break

			ingested += res["ingested"]
			already += res["already_ingested"]
			skipped += res["skipped"]
			errors += res["errors"]

			done = total - res["remaining"]
			elapsed = time.time() - start
			ips = done / elapsed if elapsed else 0
			redraw(
				f"{bar(done, total)} | ingested: {ingested}  already: {already}  skipped: {skipped}  errors: {errors} | {ips:.1f} img/s | {elapsed:.1f}s"
			)
	finally:
		print()
		client.post(f"{url}/unload_model")
		client.close()

	remaining = total - done if total else 0
	print(
		f"Done in {time.time() - start:.1f}s. Ingested: {ingested}, already present: {already}, skipped: {skipped}, errors: {errors}. Queue remaining: {remaining}"
	)


if __name__ == "__main__":
	main()
