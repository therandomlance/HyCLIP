"use strict";

// $, api, post, status, refreshModelStatus and the topbar come from shared.js

// ===== Vector math =====
function normalize(v) {
	const mag = Math.hypot(...v);
	return mag === 0 ? v : v.map((x) => x / mag);
}

// ===== State =====
const state = {
	prompts: [],      // {text, weight, positive, enabled, vec, vecFor}
	refs: [],         // {hashId|null, vec, weight, positive, enabled, label, thumbURL, blobURL}
	results: [],      // {hashId, dist}
	selected: new Set(),
	lastClicked: -1,
	viewerIdx: -1,
	bucketScope: "",  // "" = global
};

// ===== Prompt rows =====
function addPrompt(data = {}) {
	const row = {
		text: data.text ?? "", weight: data.weight ?? 1.0,
		positive: data.positive ?? true, enabled: data.enabled ?? true,
		vec: null, vecFor: null,
	};
	state.prompts.push(row);

	const el = document.createElement("div");
	el.className = "term-row" + (row.enabled ? "" : " disabled");

	const chk = document.createElement("input");
	chk.type = "checkbox"; chk.checked = row.enabled; chk.className = "enable-check";
	chk.title = "Enable / disable";
	chk.onchange = () => { row.enabled = chk.checked; el.classList.toggle("disabled", !chk.checked); };

	const text = document.createElement("input");
	text.type = "text"; text.placeholder = "Search text…"; text.value = row.text;
	text.oninput = () => { row.text = text.value; };
	text.onkeydown = (e) => { if (e.key === "Enter") performSearch(); };

	const weight = document.createElement("input");
	weight.type = "number"; weight.min = 0; weight.max = 5; weight.step = 0.25; weight.value = row.weight;
	weight.title = "Weight";
	weight.onchange = () => { row.weight = parseFloat(weight.value) || 0; };

	const sign = document.createElement("button");
	sign.className = "btn sign-btn" + (row.positive ? "" : " neg");
	sign.textContent = row.positive ? "+" : "−";
	sign.title = "Positive / negative contribution";
	sign.onclick = () => {
		row.positive = !row.positive;
		sign.textContent = row.positive ? "+" : "−";
		sign.classList.toggle("neg", !row.positive);
	};

	const rm = document.createElement("button");
	rm.className = "btn remove-btn"; rm.textContent = "✕"; rm.title = "Remove";
	rm.onclick = () => {
		if (state.prompts.length <= 1) return;
		state.prompts.splice(state.prompts.indexOf(row), 1);
		el.remove(); refreshPromptRemoveButtons();
	};

	el.append(chk, text, weight, sign, rm);
	$("#prompt-list").append(el);
	refreshPromptRemoveButtons();
}
function refreshPromptRemoveButtons() {
	const rows = $("#prompt-list").children;
	for (const el of rows) el.querySelector(".remove-btn").style.visibility = rows.length > 1 ? "visible" : "hidden";
}

// ===== Reference images =====
let hoverTimer = null;
const HOVER_DELAY = 400;

function showHoverPreview(src, anchor) {
	const pv = $("#hover-preview");
	const r = anchor.getBoundingClientRect();
	pv.onload = () => {
		pv.style.left = Math.max(10, Math.min(r.right + 10, innerWidth - pv.offsetWidth - 10)) + "px";
		pv.style.top = Math.max(10, Math.min(r.top, innerHeight - pv.offsetHeight - 10)) + "px";
	};
	pv.src = src;
	pv.style.left = Math.min(r.right + 10, innerWidth - 60) + "px";
	pv.style.top = Math.max(10, r.top) + "px";
	pv.hidden = false;
}
function hideHoverPreview() {
	clearTimeout(hoverTimer);
	$("#hover-preview").hidden = true;
}

function addRef(ref) {
	// ref: {hashId|null, vec, label, thumbURL, previewURL?}
	state.refs.push({ weight: 1.0, positive: true, enabled: true, ...ref });

	const el = document.createElement("div");
	el.className = "term-row ref-item";
	const r = state.refs[state.refs.length - 1];

	const chk = document.createElement("input");
	chk.type = "checkbox"; chk.checked = true; chk.className = "enable-check";
	chk.title = "Enable / disable";
	chk.onchange = () => { r.enabled = chk.checked; el.classList.toggle("disabled", !chk.checked); };

	const img = document.createElement("img");
	img.src = ref.thumbURL; img.alt = "";
	img.onmouseenter = () => {
		const src = ref.previewURL || ref.thumbURL;
		hoverTimer = setTimeout(() => showHoverPreview(src, img), HOVER_DELAY);
	};
	img.onmouseleave = hideHoverPreview;

	const label = document.createElement("span");
	label.className = "ref-label"; label.textContent = ref.label; label.title = ref.label;

	const weight = document.createElement("input");
	weight.type = "number"; weight.min = 0; weight.max = 5; weight.step = 0.25; weight.value = 1.0;
	weight.title = "Weight";
	weight.onchange = () => { r.weight = parseFloat(weight.value) || 0; };

	const sign = document.createElement("button");
	sign.className = "btn sign-btn"; sign.textContent = "+";
	sign.title = "Positive / negative contribution";
	sign.onclick = () => {
		r.positive = !r.positive;
		sign.textContent = r.positive ? "+" : "−";
		sign.classList.toggle("neg", !r.positive);
	};

	const rm = document.createElement("button");
	rm.className = "btn remove-btn"; rm.textContent = "✕"; rm.title = "Remove";
	rm.onclick = () => {
		hideHoverPreview();
		state.refs.splice(state.refs.indexOf(r), 1);
		el.remove();
		status(`${state.refs.length} reference image(s)`);
	};

	el.append(chk, img, label, weight, sign, rm);
	$("#ref-list").append(el);
	status(`${state.refs.length} reference image(s)`);
}

async function addRefFiles(files) {
	for (const f of files) {
		status(`Evaluating ${f.name}…`);
		try {
			const vec = await api("/eval_image_upload", { method: "POST", body: f });
			addRef({ hashId: null, vec, label: f.name, thumbURL: URL.createObjectURL(f) });
		} catch (e) { status(`Error evaluating ${f.name}: ${e.message}`); }
	}
}

async function addRefByHash(hash) {
	// Resolve sha256 -> file_id; reuse the pre-computed embedding if ingested, else evaluate
	const { hash_id, ingested } = await api(`/resolve_hash?hash=${encodeURIComponent(hash)}`);
	const label = `${hash.slice(0, 8)}… (#${hash_id})`;
	const thumbURL = `/thumbnail?hash_id=${hash_id}`;
	const previewURL = `/file?hash_id=${hash_id}`;
	if (ingested) {
		const vec = await api(`/get_embedding?hash_id=${hash_id}`);
		addRef({ hashId: hash_id, vec, label, thumbURL, previewURL });
		return;
	}
	status(`#${hash_id} not ingested — evaluating…`);
	const r = await fetch(`/file?hash_id=${hash_id}`);
	if (!r.ok) throw new Error(`could not fetch file #${hash_id}`);
	const vec = await api("/eval_image_upload", { method: "POST", body: await r.blob() });
	addRef({ hashId: hash_id, vec, label, thumbURL, previewURL });
}

async function addRefById(hashId) {
	// Uses the pre-computed embedding from the DB; no re-evaluation
	const vec = await api(`/get_embedding?hash_id=${hashId}`);
	addRef({ hashId, vec, label: `file #${hashId}`,
		thumbURL: `/thumbnail?hash_id=${hashId}`, previewURL: `/file?hash_id=${hashId}` });
}

// ===== Search =====
async function resolvePromptVectors() {
	for (const p of state.prompts) {
		if (!p.enabled || !p.text.trim()) continue;
		if (p.vec && p.vecFor === p.text) continue;
		status(`Evaluating "${p.text}"…`);
		p.vec = await post("/eval_text", { text: p.text });
		p.vecFor = p.text;
	}
}

function combineVectors() {
	const terms = [
		...state.prompts.filter((p) => p.enabled && p.text.trim() && p.vec),
		...state.refs.filter((r) => r.enabled && r.vec),
	];
	let sum = null;
	for (const t of terms) {
		const w = t.positive ? t.weight : -t.weight;
		if (!sum) sum = new Array(t.vec.length).fill(0);
		for (let i = 0; i < sum.length; i++) sum[i] += t.vec[i] * w;
	}
	return sum && normalize(sum);
}

async function performSearch() {
	const hasInput =
		state.prompts.some((p) => p.enabled && p.text.trim()) || state.refs.length > 0;
	if (!hasInput) return;

	$("#search-btn").disabled = true;
	status("Searching…");
	try {
		await resolvePromptVectors();
		const vec = combineVectors();
		if (!vec) { displayResults([]); status("No search inputs"); return; }

		const num = parseInt($("#count-input").value) || 30;
		const results = state.bucketScope
			? await post("/search_bucket", { embedding: vec, bucket_id: Number(state.bucketScope), num_results: num })
			: await post("/search", { embedding: vec, num_results: num });

		displayResults(results.map(([hashId, dist]) => ({ hashId, dist })));
		status(`Found ${results.length} result(s)`);
	} catch (e) {
		if (e.status === 409) {
			status("Model not loaded — click “Load model” in the top bar first");
			$("#model-toggle").classList.add("attention");
		} else {
			status(`Search error: ${e.message}`);
		}
	} finally {
		$("#search-btn").disabled = false;
	}
}

// ===== Results grid =====
function displayResults(results) {
	state.results = results;
	clearSelection();
	$("#grid").replaceChildren();
	$("#results-info").textContent = results.length ? `${results.length} result(s)` : "No results";

	for (const [idx, res] of results.entries()) {
		const card = document.createElement("div");
		card.className = "card";
		card.dataset.idx = idx;

		const img = document.createElement("img");
		img.loading = "lazy";
		img.src = `/thumbnail?hash_id=${res.hashId}`;
		img.alt = `#${res.hashId}`;

		const dist = document.createElement("div");
		dist.className = "dist";
		dist.textContent = `#${res.hashId} · dist ${res.dist.toFixed(4)}`;

		card.append(img, dist);
		card.onclick = (e) => onCardClick(e, idx);
		card.ondblclick = () => openViewer(idx);
		card.oncontextmenu = (e) => { e.preventDefault(); showCtxMenu(e, res); };
		$("#grid").append(card);
	}
}

function openViewer(idx) {
	state.viewerIdx = idx;
	$("#overlay-img").src = `/file?hash_id=${state.results[idx].hashId}`;
	$("#overlay").hidden = false;
}

function stepViewer(delta) {
	const n = state.results.length;
	if (!n) return;
	openViewer((state.viewerIdx + delta + n) % n);
}

function onCardClick(e, idx) {
	if (e.shiftKey && state.lastClicked >= 0) {
		const [a, b] = [state.lastClicked, idx].sort((x, y) => x - y);
		if (!e.ctrlKey && !e.metaKey) state.selected.clear();
		for (let i = a; i <= b; i++) state.selected.add(state.results[i].hashId);
	} else if (e.ctrlKey || e.metaKey) {
		const id = state.results[idx].hashId;
		state.selected.has(id) ? state.selected.delete(id) : state.selected.add(id);
	} else {
		const id = state.results[idx].hashId;
		if (state.selected.size === 1 && state.selected.has(id)) {
			state.selected.clear();
		} else {
			state.selected.clear();
			state.selected.add(id);
		}
	}
	state.lastClicked = idx;
	refreshSelectionUI();
}

function clearSelection() {
	state.selected.clear();
	state.lastClicked = -1;
	refreshSelectionUI();
}

function refreshSelectionUI() {
	for (const card of $("#grid").children)
		card.classList.toggle("selected", state.selected.has(state.results[card.dataset.idx].hashId));

	const n = state.selected.size;
	$("#selection-bar").hidden = n === 0;
	$("#selection-count").textContent = `${n} selected`;
}

// ===== Context menu =====
function showCtxMenu(e, res) {
	const menu = $("#ctx-menu");
	menu.replaceChildren();

	const items = [
		["Search using this image", async () => {
			try { await addRefById(res.hashId); } catch (err) { status(`Error: ${err.message}`); }
		}],
		["View full size", () => openViewer(state.results.indexOf(res))],
		["Copy file path", async () => {
			try {
				const { path } = await api(`/file_path?hash_id=${res.hashId}`);
				await navigator.clipboard.writeText(path);
				status("Path copied");
			} catch (err) { status(`Error: ${err.message}`); }
		}],
	];

	for (const [label, fn] of items) {
		const b = document.createElement("button");
		b.textContent = label;
		b.onclick = () => { menu.hidden = true; fn(); };
		menu.append(b);
	}

	menu.hidden = false;
	menu.style.left = Math.min(e.clientX, innerWidth - 220) + "px";
	menu.style.top = Math.min(e.clientY, innerHeight - 150) + "px";
}

// ===== Buckets =====
async function updateScopeCount() {
	try {
		const n = state.bucketScope
			? (await api(`/list_bucket_members?bucket_id=${state.bucketScope}`)).length
			: await api("/num_embeddings");
		$("#scope-count").textContent = `(${n})`;
	} catch { $("#scope-count").textContent = ""; }
}

async function refreshBuckets() {
	const buckets = await api("/list_buckets");

	const scope = $("#scope-select");
	const prev = state.bucketScope;
	scope.replaceChildren(new Option("All images", ""));
	for (const [id, name] of buckets) scope.append(new Option(name, id));
	scope.value = [...scope.options].some((o) => o.value === String(prev)) ? prev : "";
	state.bucketScope = scope.value;

	const act = $("#bucket-action-select");
	act.replaceChildren(new Option("Add to bucket…", ""));
	for (const [id, name] of buckets) act.append(new Option(name, id));

	updateScopeCount();
}

// ===== Init =====
async function init() {
	const cfg = await api("/get_config").catch(() => ({}));

	addPrompt();
	$("#count-input").value = cfg.SEARCH_LIMIT ?? 100;
	$("#grid").classList.toggle("fit", $("#fit-select").value === "fit");

	const applyThumb = () => $("#grid").style.setProperty("--thumb", $("#thumb-size").value + "px");
	applyThumb();

	$("#add-prompt").onclick = () => { addPrompt(); };
	$("#search-btn").onclick = performSearch;
	$("#clear-btn").onclick = () => {
		hideHoverPreview();
		state.refs = [];
		$("#ref-list").replaceChildren();
		$("#prompt-list").replaceChildren();
		state.prompts = [];
		addPrompt();
		displayResults([]);
		status("Cleared");
	};
	$("#thumb-size").oninput = () => { applyThumb(); };
	$("#fit-select").onchange = () => {
		$("#grid").classList.toggle("fit", $("#fit-select").value === "fit");
	};
	$("#scope-select").onchange = (e) => { state.bucketScope = e.target.value; updateScopeCount(); };

	// Reference image inputs
	$("#ref-file-input").onchange = (e) => { addRefFiles(e.target.files); e.target.value = ""; };
	$("#add-ref-by-id").onclick = async () => {
		const hash = $("#ref-id-input").value.trim();
		if (!hash) return;
		try { await addRefByHash(hash); $("#ref-id-input").value = ""; }
		catch (e) { status(`Error: ${e.message}`); }
	};
	const dz = $("#drop-zone");
	dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
	dz.ondragleave = () => dz.classList.remove("over");
	dz.ondrop = (e) => {
		e.preventDefault(); dz.classList.remove("over");
		addRefFiles([...e.dataTransfer.files].filter((f) => f.type.startsWith("image/")));
	};

	// Selection actions
	$("#clear-selection").onclick = clearSelection;
	$("#use-as-refs").onclick = async () => {
		for (const id of state.selected) {
			try { await addRefById(id); } catch (e) { status(`Error on #${id}: ${e.message}`); }
		}
		clearSelection();
	};
	$("#bucket-action-select").onchange = async (e) => {
		const bucketId = e.target.value;
		const name = e.target.selectedOptions[0]?.textContent ?? `bucket ${bucketId}`;
		e.target.value = "";
		if (!bucketId || !state.selected.size) return;
		if (!confirm(`Add ${state.selected.size} selected image(s) to "${name}"?`)) return;
		try {
			const r = await post("/insert_into_bucket", { bucket_id: Number(bucketId), hash_ids: [...state.selected] });
			status(`Added ${r.inserted} image(s) to bucket (visible in its searches after server restart if it was searched before)`);
			clearSelection();
		} catch (err) { status(`Error: ${err.message}`); }
	};

	// Overlay / context menu dismissal
	$("#viewer-prev").onclick = (e) => { e.stopPropagation(); stepViewer(-1); };
	$("#viewer-next").onclick = (e) => { e.stopPropagation(); stepViewer(1); };
	$("#overlay").onclick = () => { $("#overlay").hidden = true; $("#overlay-img").src = ""; };
	document.addEventListener("click", (e) => {
		if (!$("#ctx-menu").contains(e.target)) $("#ctx-menu").hidden = true;
	});
	document.addEventListener("keydown", (e) => {
		if (!$("#overlay").hidden) {
			if (e.key === "ArrowLeft") stepViewer(-1);
			else if (e.key === "ArrowRight") stepViewer(1);
			else if (e.key === "Escape") $("#overlay").hidden = true;
			else return;
			e.preventDefault();
			return;
		}
		if (e.key === "Escape") {
			$("#ctx-menu").hidden = true;
			if (state.selected.size) clearSelection();
		}
	});

	refreshBuckets().catch((e) => status(`Buckets: ${e.message}`));
}

init();
