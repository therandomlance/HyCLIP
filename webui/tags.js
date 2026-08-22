"use strict";

let running = false;
let stopRequested = false;

async function refreshTags() {
	const list = $("#tag-list");
	try {
		const tags = await api("/list_tags");
		list.replaceChildren();
		if (!tags.length) {
			const empty = document.createElement("p");
			empty.className = "hint";
			empty.textContent = "No tags stored yet.";
			list.append(empty);
			return;
		}
		for (const tag of tags) {
			const row = document.createElement("div");
			row.className = "term-row";
			const label = document.createElement("span");
			label.className = "ref-label";
			label.textContent = tag;
			row.append(label);
			list.append(row);
		}
	} catch (e) { status(`Error loading tags: ${e.message}`); }
}

$("#refresh-tags").onclick = () => refreshTags();

$("#stop-btn").onclick = () => { stopRequested = true; };

$("#start-btn").onclick = async () => {
	if (running) return;
	const tags = $("#tag-input").value.split("\n").map((t) => t.trim()).filter(Boolean);
	if (!tags.length) return;
	const limit = parseInt($("#limit-input").value) || 1000;

	running = true;
	stopRequested = false;
	$("#start-btn").hidden = true;
	$("#stop-btn").hidden = false;
	$("#start-btn").dataset.busy = "1";
	updateRequires();

	const bar = $("#tag-progress");
	const step = $("#tag-step");
	const counts = $("#tag-counts");
	const totals = { matched: 0, ingested: 0, errors: 0 };
	const total = tags.length;
	const redraw = (done, current) => {
		bar.max = total;
		bar.value = done;
		step.textContent = done < total ? `Working on: ${current}` : "Done";
		counts.textContent = `${done}/${total} | matched: ${totals.matched}  ingested: ${totals.ingested}  errors: ${totals.errors}`;
	};

	redraw(0, tags[0]);
	try {
		for (let i = 0; i < tags.length && !stopRequested; i++) {
			status(`Generating "${tags[i]}"…`);
			try {
				const r = await post("/make_tag", { tag: tags[i], search_limit: limit });
				totals.matched += r.matched;
				totals.ingested += r.ingested;
			} catch (e) {
				totals.errors++;
				status(`Error on "${tags[i]}": ${e.message}`);
			}
			redraw(i + 1, tags[i + 1]);
		}
		status(stopRequested ? "Stopped" : "Done");
		refreshTags();
	} catch (e) { status(`Error: ${e.message}`); }

	$("#stop-btn").hidden = true;
	$("#start-btn").hidden = false;
	delete $("#start-btn").dataset.busy;
	updateRequires();
	running = false;
	stopRequested = false;
};
