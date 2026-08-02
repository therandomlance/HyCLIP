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
	status(`Resolving ${hashes.length} hash(es)…`);
	try {
		const r = await post("/add_hashes_to_bucket", { bucket_id: Number(bucketId), hashes });
		const out = $("#add-result");
		out.replaceChildren();
		const summary = document.createElement("p");
		summary.textContent = `Added ${r.added} to bucket, queued ${r.enqueued} for ingest, ${r.unknown.length} unknown.`;
		out.append(summary);
		if (r.unknown.length) {
			const pre = document.createElement("pre");
			pre.textContent = r.unknown.join("\n");
			out.append(pre);
		}
		status("Done");
		refreshBuckets();
	} catch (e) { status(`Error: ${e.message}`); }
	$("#add-hashes").disabled = false;
};

refreshBuckets().catch((e) => status(`Buckets: ${e.message}`));
