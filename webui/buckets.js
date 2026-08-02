"use strict";

async function refreshBuckets() {
	const buckets = await api("/list_buckets");

	const sel = $("#bucket-select");
	const prev = sel.value;
	sel.replaceChildren();
	for (const [id, name] of buckets) sel.append(new Option(name, id));
	sel.value = prev;

	const list = $("#bucket-list");
	list.replaceChildren();
	for (const [id, name] of buckets) {
		const row = document.createElement("div");
		row.className = "term-row";
		const label = document.createElement("span");
		label.className = "ref-label";
		label.textContent = name;
		try {
			const members = await api(`/list_bucket_members?bucket_id=${id}`);
			label.textContent = `${name} (${members.length})`;
		} catch {}
		const del = document.createElement("button");
		del.className = "btn remove-btn"; del.textContent = "✕"; del.title = "Delete bucket";
		del.onclick = async () => {
			if (!confirm(`Delete bucket "${name}"?`)) return;
			await api(`/delete_bucket?bucket_id=${id}`, { method: "POST" });
			refreshBuckets();
		};
		row.append(label, del);
		list.append(row);
	}
}

$("#create-bucket").onclick = async () => {
	const name = $("#bucket-name-input").value.trim();
	if (!name) return;
	try {
		await post("/create_bucket", { bucket_name: name });
		$("#bucket-name-input").value = "";
		refreshBuckets();
	} catch (e) { status(`Error: ${e.message}`); }
};

$("#add-hashes").onclick = async () => {
	const bucketId = $("#bucket-select").value;
	if (!bucketId) { status("Create a bucket first"); return; }
	const hashes = $("#hash-input").value.split("\n").map((h) => h.trim()).filter(Boolean);
	if (!hashes.length) return;

	$("#add-hashes").disabled = true;
	const bar = $("#add-progress");
	const counts = $("#add-counts");
	const step = $("#add-step");
	bar.hidden = true;
	counts.textContent = "";
	step.textContent = "Step 1: Resolving hashes…";
	status(`Resolving ${hashes.length} hash(es)…`);
	try {
		const r = await post("/add_hashes_to_bucket", { bucket_id: Number(bucketId), hashes });

		// Ingest the pending files in batches, adding each newly-ingested batch to the bucket
		const pending = r.pending;
		const BATCH = 20;
		let ingested = 0, skipped = 0, errors = 0;
		if (pending.length) {
			step.textContent = "Step 2: Ingesting new images…";
			const ms = await api("/model_status");
			if (!ms.loaded) {
				status("Loading model…");
				await api("/load_model", { method: "POST" });
				refreshModelStatus();
			}
			bar.hidden = false;
			bar.max = pending.length;
			bar.value = 0;
		}
		for (let i = 0; i < pending.length; i += BATCH) {
			const chunk = pending.slice(i, i + BATCH);
			status(`Ingesting ${Math.min(i + BATCH, pending.length)}/${pending.length}…`);
			const results = await post("/ingest_image_batch", { items: chunk });
			const newIds = [];
			for (const res of results) {
				if (res.status === "ingested") newIds.push(res.hash_id);
				else if (res.status === "skipped") skipped++;
			}
			errors += chunk.length - results.length;
			if (newIds.length) {
				await post("/insert_into_bucket", { bucket_id: Number(bucketId), hash_ids: newIds });
				ingested += newIds.length;
			}
			bar.value = Math.min(i + BATCH, pending.length);
			counts.textContent = `${bar.value}/${pending.length} | ingested: ${ingested}  skipped: ${skipped}  errors: ${errors}`;
		}

		const out = $("#add-result");
		out.replaceChildren();
		const summary = document.createElement("p");
		summary.textContent = `Added ${r.added + ingested} to bucket (${r.added} already ingested, ${ingested} newly ingested), ` +
			`${skipped + errors} failed, ${r.already_queued} were already in the ingest queue, ${r.unknown.length} unknown.`;
		out.append(summary);
		if (r.unknown.length) {
			const pre = document.createElement("pre");
			pre.textContent = r.unknown.join("\n");
			out.append(pre);
		}
		status("Done");
		refreshBuckets();
	} catch (e) { status(`Error: ${e.message}`); }
	step.textContent = "";
	$("#add-hashes").disabled = false;
};

refreshBuckets().catch((e) => status(`Buckets: ${e.message}`));
