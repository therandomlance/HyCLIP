"use strict";

let running = false;
let stopRequested = false;

async function refreshQueueCount() {
	try {
		const { queued } = await api("/queue_status");
		const el = $("#queue-count");
		if (el) el.textContent = queued;
		return queued;
	} catch (e) {
		status(`Error: ${e.message}`);
		return 0;
	}
}

$("#enqueue-btn").onclick = async () => {
	const tag = $("#tag-input").value.trim() || "hyclip:ingest";
	const max = parseInt($("#max-input").value) || null;
	$("#enqueue-btn").dataset.busy = "1";
	updateRequires();
	status(`Searching hydrus for "${tag}"…`);
	try {
		const r = await post("/ingest_enqueue", { tag, max_evaluate: max });
		status(`Enqueued ${r.enqueued} of ${r.found} tagged image(s) (${r.skipped} skipped, tag removed)`);
	} catch (e) { status(`Error: ${e.message}`); }
	delete $("#enqueue-btn").dataset.busy;
	updateRequires();
	refreshQueueCount();
};

$("#stop-btn").onclick = () => { stopRequested = true; };

$("#start-btn").onclick = async () => {
	if (running) return;
	running = true;
	stopRequested = false;
	$("#start-btn").hidden = true;
	$("#stop-btn").hidden = false;

	const batchSize = parseInt($("#batch-input").value) || null; // null -> server's INGEST_BATCH_SIZE
	const counts = { ingested: 0, already: 0, skipped: 0, errors: 0 };
	const bar = $("#ingest-progress");
	const line = $("#ingest-counts");
	const redraw = (done, total) => {
		bar.max = total || 1;
		bar.value = done;
		line.textContent =
			`${done}/${total} | ingested: ${counts.ingested}  already: ${counts.already}  skipped: ${counts.skipped}  errors: ${counts.errors}`;
	};

	try {
		// hyclip.modelLoaded is kept fresh by the shared heartbeat; /load_model is idempotent server-side
		if (!hyclip.modelLoaded) {
			status("Loading model…");
			await api("/load_model", { method: "POST" });
		}

		let total = await refreshQueueCount();
		let done = 0;
		redraw(0, total);

		let remaining = total;
		while (remaining > 0 && !stopRequested) {
			const r = await post("/work_queue", { batch_size: batchSize });
			if (!r.processed) break;
			counts.ingested += r.ingested;
			counts.already += r.already_ingested;
			counts.skipped += r.skipped;
			counts.errors += r.errors;
			done += r.ingested + r.already_ingested + r.skipped + r.errors;
			remaining = r.remaining;
			redraw(done, total);
			status(stopRequested ? "Stopping…" : "Processing…");
		}
		const q = $("#queue-count");
		if (q) q.textContent = remaining;
		status(stopRequested ? "Stopped — progress is kept in the queue" : "Done");
	} catch (e) { status(`Error: ${e.message}`); }

	$("#stop-btn").hidden = true;
	$("#start-btn").hidden = false;
	running = false;
	stopRequested = false;
	refreshQueueCount();
};

refreshQueueCount();
api("/get_config").then((cfg) => { $("#batch-input").value = cfg.INGEST_BATCH_SIZE ?? 20; }).catch(() => {});
