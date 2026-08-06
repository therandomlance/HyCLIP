"use strict";

let running = false;
let stopRequested = false;

async function refreshQueueCount() {
	try {
		const { queued } = await api("/queue_status");
		$("#queue-count").textContent = queued;
		return queued;
	} catch (e) {
		$("#queue-count").textContent = "?";
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
		status("Loading model…");
		await api("/load_model", { method: "POST" });

		const total = await refreshQueueCount();
		redraw(0, total);

		while (total > 0 && !stopRequested) {
			const r = await post("/work_queue", { batch_size: batchSize });
			if (!r.processed) break;
			counts.ingested += r.ingested;
			counts.already += r.already_ingested;
			counts.skipped += r.skipped;
			counts.errors += r.errors;
			redraw(total - r.remaining, total);
			status(stopRequested ? "Stopping…" : "Processing…");
		}
		status(stopRequested ? "Stopped — progress is kept in the queue" : "Done");
	} catch (e) { status(`Error: ${e.message}`); }

	$("#stop-btn").hidden = true;
	$("#start-btn").hidden = false;
	running = false;
	refreshQueueCount();
};

refreshQueueCount();
api("/get_config").then((cfg) => { $("#batch-input").value = cfg.INGEST_BATCH_SIZE ?? 20; }).catch(() => {});
