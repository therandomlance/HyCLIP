"use strict";

// $, api, post, status, refreshModelStatus and the topbar come from shared.js

// ===== Vector math =====
function normalize(v) {
	const mag = Math.hypot(...v);
	// ponytail: zero vector (weights cancel) → null so the caller rejects instead of searching with [0,0,…]
	return mag === 0 ? null : v.map((x) => x / mag);
}

// ===== State =====
const state = {
	prompts: [],      // {text, weight, positive, enabled, vec, vecFor}
	tags: [],         // {tag, weight, positive, enabled, vec, vecFor} — vec fetched from /get_tag_embedding
	refs: [],         // {hashId|null, vec, weight, positive, enabled, label, thumbURL, blobURL}
	results: [],      // {hashId, dist}
	selected: new Set(),
	lastClicked: -1,
	dragAnchor: null, // index where a drag-select started, null when not dragging
	dragMoved: false, // set during a drag so the click that ends it is ignored
	viewerIdx: -1,
	bucketScope: "",  // "" = global
	buckets: [],      // [[id, name]] from /list_buckets
	cfg: {},          // /get_config snapshot (DEFER_TAG etc.)
	meta: new Map(),  // hashId -> /file_metadata payload (viewer panel cache)
};

// ponytail: localStorage only for per-viewer UI prefs; wrapped since it can throw
const pref = {
	get(k, d) { try { const v = localStorage.getItem("hyclip." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
	set(k, v) { try { localStorage.setItem("hyclip." + k, JSON.stringify(v)); } catch {} },
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
	chk.onchange = () => { row.enabled = chk.checked; el.classList.toggle("disabled", !chk.checked); refreshHasInput(); };

	const text = document.createElement("input");
	text.type = "text"; text.placeholder = "Search text…"; text.value = row.text;
	text.oninput = () => { row.text = text.value; refreshHasInput(); };
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
	rm.className = "btn remove-btn"; rm.textContent = "✕";
	rm.title = "Remove (clears the last remaining row)";
	rm.onclick = () => {
		if (state.prompts.length > 1) {
			state.prompts.splice(state.prompts.indexOf(row), 1);
			el.remove();
		} else {
			// Lone row: clear it instead of leaving no input at all
			row.text = ""; row.weight = 1.0; row.positive = true; row.enabled = true;
			row.vec = null; row.vecFor = null;
			text.value = ""; weight.value = "1"; chk.checked = true;
			sign.textContent = "+"; sign.classList.remove("neg");
			el.classList.remove("disabled");
		}
		refreshHasInput();
	};

	el.append(chk, text, weight, sign, rm);
	$("#prompt-list").append(el);
}

// ===== Tag rows + autocomplete =====
// ponytail: custom fuzzy popup (~40 lines) instead of native <datalist> (no fuzzy
// match, no popup on focus, browser-dependent UI) or an autocomplete lib —
// subsequence match, ↑/↓ navigate, Tab fills, click selects.
let tagList = [];
async function loadTagList() {
	try { tagList = await api("/list_tags"); } catch { tagList = []; }
}
const fuzzyMatch = (q, t) => {
	q = q.toLowerCase(); t = t.toLowerCase();
	let i = 0;
	for (const c of t) if (i < q.length && c === q[i]) i++;
	return i === q.length;
};
function attachTagAutocomplete(input) {
	const popup = document.createElement("div");
	popup.className = "autocomplete-popup";
	popup.hidden = true;
	input.parentElement.append(popup);
	let matches = [];
	let hi = -1;
	let suppress = false;
	const show = () => {
		popup.replaceChildren();
		if (!matches.length) { popup.hidden = true; return; }
		matches.forEach((m, i) => {
			const d = document.createElement("div");
			d.className = "autocomplete-item" + (i === hi ? " active" : "");
			d.textContent = m;
			d.onmousedown = (e) => { e.preventDefault(); select(i); };
			popup.append(d);
		});
		popup.hidden = false;
	};
	const select = (i) => {
		if (i < 0 || i >= matches.length) return;
		input.value = matches[i];
		suppress = true;
		input.dispatchEvent(new Event("input"));
		suppress = false;
		popup.hidden = true;
	};
	input.addEventListener("input", () => {
		if (suppress) return;
		const q = input.value.trim();
		if (!q) { popup.hidden = true; return; }
		matches = tagList.filter((t) => fuzzyMatch(q, t)).slice(0, 10);
		hi = matches.length ? 0 : -1;
		show();
	});
	input.addEventListener("focus", () => { if (input.value.trim()) input.dispatchEvent(new Event("input")); });
	input.addEventListener("blur", () => { setTimeout(() => { popup.hidden = true; }, 150); });
	input.addEventListener("keydown", (e) => {
		if (popup.hidden || !matches.length) return;
		if (e.key === "ArrowDown") { e.preventDefault(); hi = (hi + 1) % matches.length; show(); }
		else if (e.key === "ArrowUp") { e.preventDefault(); hi = (hi - 1 + matches.length) % matches.length; show(); }
		else if (e.key === "Tab") { e.preventDefault(); select(hi); }
		else if (e.key === "Escape") { popup.hidden = true; }
	});
}

function addTag(data = {}) {
	const row = {
		tag: data.tag ?? "", weight: data.weight ?? 1.0,
		positive: data.positive ?? true, enabled: data.enabled ?? true,
		vec: null, vecFor: null,
	};
	state.tags.push(row);

	const el = document.createElement("div");
	el.className = "term-row" + (row.enabled ? "" : " disabled");

	const chk = document.createElement("input");
	chk.type = "checkbox"; chk.checked = row.enabled; chk.className = "enable-check";
	chk.title = "Enable / disable";
	chk.onchange = () => { row.enabled = chk.checked; el.classList.toggle("disabled", !chk.checked); refreshHasInput(); };

	const text = document.createElement("input");
	text.type = "text"; text.placeholder = "Tag…"; text.value = row.tag;
	text.oninput = () => { row.tag = text.value; row.vec = null; row.vecFor = null; refreshHasInput(); };
	text.onkeydown = (e) => { if (e.key === "Enter") performSearch(); };

	const wrap = document.createElement("div");
	wrap.className = "tag-input-wrap";
	wrap.append(text);
	attachTagAutocomplete(text);

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
	rm.className = "btn remove-btn"; rm.textContent = "✕";
	rm.title = "Remove (clears the last remaining row)";
	rm.onclick = () => {
		if (state.tags.length > 1) {
			state.tags.splice(state.tags.indexOf(row), 1);
			el.remove();
		} else {
			// Lone row: clear it instead of leaving no input at all
			row.tag = ""; row.weight = 1.0; row.positive = true; row.enabled = true;
			row.vec = null; row.vecFor = null;
			text.value = ""; weight.value = "1"; chk.checked = true;
			sign.textContent = "+"; sign.classList.remove("neg");
			el.classList.remove("disabled");
		}
		refreshHasInput();
	};

	el.append(chk, wrap, weight, sign, rm);
	$("#tag-list").append(el);
}

// ===== Reference images =====
let hoverTimer = null;
const HOVER_DELAY = 400;

// ponytail: only blob: URLs need revoking; server URLs are reused across refs
function revokeRefURLs(r) {
	if (r.thumbURL?.startsWith("blob:")) URL.revokeObjectURL(r.thumbURL);
	if (r.previewURL?.startsWith("blob:")) URL.revokeObjectURL(r.previewURL);
}

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
	chk.onchange = () => { r.enabled = chk.checked; el.classList.toggle("disabled", !chk.checked); refreshHasInput(); };

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
		const idx = state.refs.indexOf(r);
		if (idx >= 0) { revokeRefURLs(r); state.refs.splice(idx, 1); }
		el.remove();
		status(`${state.refs.length} reference image(s)`);
		refreshHasInput();
	};

	el.append(chk, img, label, weight, sign, rm);
	$("#ref-list").append(el);
	status(`${state.refs.length} reference image(s)`);
	refreshHasInput();
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
function refreshHasInput() {
	hyclip.hasInput = state.prompts.some((p) => p.enabled && p.text.trim())
		|| state.tags.some((t) => t.enabled && t.tag.trim())
		|| state.refs.some((r) => r.enabled);
	updateRequires();
}

async function resolvePromptVectors() {
	for (const p of state.prompts) {
		if (!p.enabled || !p.text.trim()) continue;
		if (p.vec && p.vecFor === p.text) continue;
		status(`Evaluating "${p.text}"…`);
		p.vec = await post("/eval_text", { text: p.text });
		p.vecFor = p.text;
	}
}

async function resolveTagVectors() {
	for (const t of state.tags) {
		if (!t.enabled || !t.tag.trim()) continue;
		if (t.vec && t.vecFor === t.tag) continue;
		status(`Resolving tag "${t.tag}"…`);
		try {
			t.vec = await api(`/get_tag_embedding?tag=${encodeURIComponent(t.tag)}`);
			t.vecFor = t.tag;
		} catch (e) {
			t.vec = null; t.vecFor = null;
			status(`Tag "${t.tag}" not stored — generate it on the Tags page first`);
		}
	}
}

function combineVectors() {
	const terms = [
		...state.prompts.filter((p) => p.enabled && p.text.trim() && p.vec),
		...state.tags.filter((t) => t.enabled && t.tag.trim() && t.vec),
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
	refreshHasInput();
	if (!hyclip.hasInput) return;

	$("#search-btn").dataset.busy = "1";
	updateRequires();
	status("Searching…");
	try {
		await resolvePromptVectors();
		await resolveTagVectors();
		const vec = combineVectors();
		if (!vec) { displayResults([]); status("Search terms cancelled out (zero vector) — adjust weights/signs"); return; }

		const num = parseInt($("#count-input").value) || 30;
		const searchPromise = state.bucketScope
			? post("/search_bucket", { embedding: vec, bucket_id: Number(state.bucketScope), num_results: num })
			: post("/search", { embedding: vec, num_results: num });
		const results = await searchPromise;

		displayResults(results.map(([hashId, dist]) => ({ hashId, dist })));
		status(`Found ${results.length} result(s)`);
	} catch (e) {
		if (e.status === 409) {
			status("Model not loaded — click “Load model” in the top bar first");
		} else {
			status(`Search error: ${e.message}`);
		}
	} finally {
		delete $("#search-btn").dataset.busy;
		pokeHeartbeat(); // re-sync db (and other) status after the search
		updateRequires();
	}
}

// ===== Results grid =====
function displayResults(results, keepScroll = false) {
	const scroll = $("#grid").scrollTop;
	state.results = results;
	// Clear the old cards first: refreshSelectionUI indexes state.results by each card's idx,
	// and old cards past the end of a shorter list would throw
	$("#grid").replaceChildren();
	clearSelection();
	$("#select-all").hidden = !results.length;
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

		img.draggable = false;
		card.append(img, dist);
		card.onclick = (e) => onCardClick(e, idx);
		card.ondblclick = () => openViewer(idx);
		card.oncontextmenu = (e) => { e.preventDefault(); showCtxMenu(e, res, idx); };
		// Drag-select: press on a card, sweep over others to add them on top of the
		// current selection. onCardClick skips the click that ends a drag.
		card.onmousedown = (e) => {
			if (e.button !== 0) return;
			state.dragAnchor = idx;
			state.dragMoved = false;
		};
		card.onmouseenter = (e) => {
			if (state.dragAnchor === null || !(e.buttons & 1)) return;
			state.dragMoved = true;
			const [a, b] = [state.dragAnchor, idx].sort((x, y) => x - y);
			for (let i = a; i <= b; i++) state.selected.add(state.results[i].hashId);
			state.lastClicked = idx;
			refreshSelectionUI();
		};
		$("#grid").append(card);
	}
	$("#grid").scrollTop = keepScroll ? scroll : 0;
}

function openViewer(idx) {
	state.viewerIdx = idx;
	const hashId = state.results[idx].hashId;
	$("#overlay-img").src = `/file?hash_id=${hashId}`;
	$("#overlay").hidden = false;
	loadViewerMeta(hashId);
}

function closeViewer() {
	$("#overlay").hidden = true;
	$("#overlay-img").src = "";
	hideCtxMenu();
}

// ===== Viewer metadata panel =====
async function loadViewerMeta(hashId) {
	const body = $("#viewer-meta-body");
	let meta = state.meta.get(hashId);
	if (!meta) {
		body.replaceChildren(metaHint("Loading metadata…"));
		try {
			meta = await api(`/file_metadata?hash_id=${hashId}`);
			state.meta.set(hashId, meta);
		} catch (e) {
			if (state.results[state.viewerIdx]?.hashId === hashId) body.replaceChildren(metaHint(`Metadata unavailable: ${e.message}`));
			return;
		}
	}
	// Stepping fast: only render the file still on screen
	if ($("#overlay").hidden || state.results[state.viewerIdx]?.hashId !== hashId) return;
	renderViewerMeta(meta, hashId);
}

function metaHint(text) {
	const d = document.createElement("div");
	d.className = "hint";
	d.textContent = text;
	return d;
}

function metaSection(title, entries, render) {
	const h = document.createElement("h3");
	h.textContent = `${title} (${entries.length})`;
	const list = document.createElement("div");
	list.className = "meta-list";
	if (!entries.length) list.append(metaHint("none"));
	for (const e of entries) list.append(render(e));
	return [h, list];
}

function metaItem(menuItems) {
	const d = document.createElement("div");
	d.className = "meta-item";
	d.oncontextmenu = (e) => { e.preventDefault(); e.stopPropagation(); openMenu(e, menuItems); };
	return d;
}

function renderViewerMeta(meta, hashId) {
	const head = document.createElement("div");
	head.className = "hint";
	head.textContent = `#${hashId}` + (meta.hash ? ` · ${meta.hash.slice(0, 16)}…` : "");
	head.title = meta.hash ?? "";

	const tags = metaSection("Tags", meta.tags, (tag) => {
		const d = metaItem([
			["Copy tag", () => copyText(tag, "Tag copied")],
			["Add tag to hyclip", () => addTagToHyclip(tag)],
		]);
		d.textContent = tag;
		return d;
	});

	const notes = metaSection("Notes", Object.entries(meta.notes), ([name, text]) => {
		const d = metaItem([["Copy note", () => copyText(text, "Note copied")]]);
		const n = document.createElement("div");
		n.className = "meta-note-name"; n.textContent = name;
		const t = document.createElement("div");
		t.className = "meta-note-text"; t.textContent = text;
		d.append(n, t);
		return d;
	});

	const urls = metaSection("Known URLs", meta.urls, (url) => {
		const d = metaItem([["Copy link", () => copyText(url, "Link copied")]]);
		const a = document.createElement("a");
		a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = url;
		d.append(a);
		return d;
	});

	$("#viewer-meta-body").replaceChildren(head, ...tags, ...notes, ...urls);
}

async function copyText(text, msg) {
	try { await navigator.clipboard.writeText(text); status(msg); }
	catch (e) { status(`Copy failed: ${e.message}`); }
}

async function addTagToHyclip(tag) {
	status(`Generating tag embedding for "${tag}"…`);
	try {
		const r = await post("/make_tag", { tag, search_limit: 1000 });
		await loadTagList();
		status(`Tag "${r.tag}" stored from ${r.ingested}/${r.matched} ingested file(s)`);
	} catch (e) { status(`Tag "${tag}": ${e.message}`); }
}

function stepViewer(delta) {
	const n = state.results.length;
	if (!n) return;
	openViewer((state.viewerIdx + delta + n) % n);
}

function onCardClick(e, idx) {
	if (state.dragMoved) { state.dragMoved = false; return; }
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
		card.classList.toggle("selected", state.selected.has(state.results[card.dataset.idx]?.hashId));

	const n = state.selected.size;
	$("#selection-bar").hidden = n === 0;
	$("#selection-actions").hidden = n === 0;
	$("#selection-count").textContent = `${n} selected`;
}

function selectAll() {
	for (const r of state.results) state.selected.add(r.hashId);
	refreshSelectionUI();
}

// ===== Hydrus file actions (selection) =====
function scopeName() {
	return state.buckets.find(([id]) => String(id) === String(state.bucketScope))?.[1] ?? "";
}

function refreshRemoveToggle() {
	const wrap = $("#act-remove-wrap");
	wrap.hidden = !state.bucketScope;
	const name = scopeName();
	wrap.title = `When checked, images you Archive, Delete or Defer are also removed from the bucket you're currently searching ("${name}"), and their cards disappear from these results.\n`
		+ "This only happens after hydrus accepts the action. Archived/deferred images stay in hyclip's global index and in any other buckets. "
		+ "Delete always removes images from hyclip (moved to its trash table), whether or not this is checked. Open in Hydrus never removes anything.";
}

async function openInHydrus(ids) {
	if (!ids.length) return;
	try {
		const r = await post("/open_in_hydrus", { hash_ids: ids });
		status(`Sent ${r.added} image(s) to ${r.created ? "new " : ""}"${r.page}" page in hydrus`);
	} catch (e) { status(`Open in Hydrus: ${e.message}`); }
}

const FILE_ACTIONS = {
	archive: { path: "/archive_files", past: "Archived" },
	delete: { path: "/delete_files", past: "Trashed" },
	defer: { path: "/defer_files", past: "Deferred" },
};

async function runFileAction(kind, ids) {
	if (!ids.length) return;
	const body = { hash_ids: ids };
	if (kind === "delete" && !confirm(`Send ${ids.length} image(s) to the hydrus trash?`)) return;
	if (kind === "defer") {
		const tag = prompt(`Defer tag for ${ids.length} image(s):`, state.cfg.DEFER_TAG ?? "hyclip:defer");
		if (tag === null || !tag.trim()) return;
		body.tag = tag.trim();
	}
	const removeFrom = state.bucketScope && $("#act-remove-bucket").checked ? Number(state.bucketScope) : null;
	if (removeFrom !== null) body.remove_from_bucket = removeFrom;

	try {
		const r = await post(FILE_ACTIONS[kind].path, body);
		let msg = `${FILE_ACTIONS[kind].past} ${r.count} image(s)` + (r.tag ? ` with "${r.tag}"` : "");
		// Delete moves embeddings to hyclip's trash table, so those cards go regardless of the toggle
		if (kind === "delete") msg += ` (${r.trashed} moved to hyclip trash)`;
		else if (removeFrom !== null) msg += `, removed from "${scopeName()}"`;
		if (kind === "delete" || removeFrom !== null) {
			const gone = new Set(ids);
			displayResults(state.results.filter((x) => !gone.has(x.hashId)), true);
			updateScopeCount();
		}
		status(msg);
	} catch (e) { status(`${kind}: ${e.message}`); }
}

// ===== Context menu =====
// Multi-select aware: right-clicking an unselected card selects just it first,
// then every action applies to the whole selection. Bucket add/remove is a
// hover flyout (tree) under the two bucket branches.
function ctxBucketAction(bucketId, name, add) {
	const ids = [...state.selected];
	if (!ids.length) return;
	const verb = add ? "Add" : "Remove";
	const prep = add ? "to" : "from";
	if (!confirm(`${verb} ${ids.length} selected image(s) ${prep} "${name}"?`)) return;
	(add
		? post("/insert_into_bucket", { bucket_id: Number(bucketId), hash_ids: ids })
		: post("/remove_from_bucket", { bucket_id: Number(bucketId), hash_ids: ids })
	).then((r) => {
		status(add
			? `Added ${r.inserted} image(s) to "${name}" (visible in its searches after server restart if it was searched before)`
			: `Removed ${r.removed} image(s) from "${name}"`);
		clearSelection();
		refreshBuckets().catch(() => {});
	}).catch((e) => status(`Error: ${e.message}`));
}

function buildCtxMenu(items, container) {
	for (const [label, fn, children] of items) {
		if (children) {
			const wrap = document.createElement("div");
			wrap.className = "ctx-item has-sub";
			const b = document.createElement("button");
			b.textContent = label;
			const sub = document.createElement("div");
			sub.className = "ctx-sub";
			buildCtxMenu(children, sub);
			wrap.append(b, sub);
			container.append(wrap);
		} else {
			const b = document.createElement("button");
			b.textContent = label;
			b.onclick = () => { $("#ctx-menu").hidden = true; fn(); };
			container.append(b);
		}
	}
}

async function showCtxMenu(e, res, idx) {
	if (!state.selected.has(res.hashId)) {
		state.selected.clear();
		state.selected.add(res.hashId);
		state.lastClicked = idx;
		refreshSelectionUI();
	}
	const ids = [...state.selected];
	const n = ids.length;

	const items = [
		[n === 1 ? "Search using this image" : `Use ${n} selected as references`, async () => {
			for (const id of ids) {
				try { await addRefById(id); } catch (err) { status(`Error on #${id}: ${err.message}`); }
			}
		}],
		[n === 1 ? "Copy file path" : `Copy ${n} file paths`, async () => {
			try {
				const paths = await Promise.all(ids.map(async (id) => (await api(`/file_path?hash_id=${id}`)).path));
				await navigator.clipboard.writeText(paths.join("\n"));
				status(`${paths.length} path(s) copied`);
			} catch (err) { status(`Error: ${err.message}`); }
		}],
	];
	if (n === 1) items.push(["View full size", () => openViewer(idx)]);
	if (hyclip.hydrus === "ok") {
		items.push(["Open in Hydrus", () => openInHydrus(ids)]);
		items.push(["Hydrus", null, [
			["Archive", () => runFileAction("archive", ids)],
			["Delete", () => runFileAction("delete", ids)],
			["Defer", () => runFileAction("defer", ids)],
		]]);
	}

	try {
		const buckets = await api("/list_buckets");
		if (buckets.length) {
			items.push(["Add to bucket", null, buckets.map(([id, name]) => [name, () => ctxBucketAction(id, name, true)])]);
			items.push(["Remove from bucket", null, buckets.map(([id, name]) => [name, () => ctxBucketAction(id, name, false)])]);
		}
	} catch {}

	openMenu(e, items);
}

function hideCtxMenu() { $("#ctx-menu").hidden = true; }

function openMenu(e, items) {
	const menu = $("#ctx-menu");
	menu.replaceChildren();
	buildCtxMenu(items, menu);
	// Flip the flyouts left when the menu is opened on the right half of the screen
	menu.classList.toggle("left-open", e.clientX > innerWidth / 2);
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
	state.buckets = buckets;

	const scope = $("#scope-select");
	const prev = state.bucketScope;
	scope.replaceChildren(new Option("All images", ""));
	for (const [id, name] of buckets) scope.append(new Option(name, id));
	scope.value = [...scope.options].some((o) => o.value === String(prev)) ? prev : "";
	state.bucketScope = scope.value;
	setScope(state.bucketScope);
	refreshRemoveToggle();

	updateScopeCount();
}

// ===== Init =====
async function init() {
	const cfg = await api("/get_config").catch(() => ({}));
	state.cfg = cfg;

	addPrompt();
	addTag();
	$("#count-input").value = cfg.SEARCH_LIMIT ?? 100;
	$("#thumb-size").value = cfg.THUMB_SIZE ?? 300;
	$("#grid").classList.toggle("fit", $("#fit-select").value === "fit");

	const applyThumb = () => {
		$("#grid").style.setProperty("--thumb", $("#thumb-size").value + "px");
		$("#thumb-size-val").textContent = $("#thumb-size").value;
	};
	applyThumb();

	const saveCfg = (updates) => post("/update_config", { updates }).catch((e) => status(`Config: ${e.message}`));

	$("#add-prompt").onclick = () => { addPrompt(); };
	$("#add-tag").onclick = () => { addTag(); };
	$("#search-btn").onclick = performSearch;
	$("#clear-btn").onclick = () => {
		hideHoverPreview();
		for (const r of state.refs) revokeRefURLs(r);
		state.refs = [];
		$("#ref-list").replaceChildren();
		$("#prompt-list").replaceChildren();
		state.prompts = [];
		state.tags = [];
		$("#tag-list").replaceChildren();
		addPrompt();
		addTag();
		displayResults([]);
		refreshHasInput();
		status("Cleared");
	};
	$("#thumb-size").oninput = () => { applyThumb(); };
	$("#thumb-size").onchange = () => saveCfg({ THUMB_SIZE: parseInt($("#thumb-size").value) });
	$("#count-input").onchange = () => saveCfg({ SEARCH_LIMIT: parseInt($("#count-input").value) || 100 });
	$("#fit-select").onchange = () => {
		$("#grid").classList.toggle("fit", $("#fit-select").value === "fit");
	};
	$("#scope-select").onchange = (e) => { state.bucketScope = e.target.value; setScope(state.bucketScope); refreshRemoveToggle(); updateScopeCount(); };

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
	$("#select-all").onclick = selectAll;
	const sel = () => [...state.selected];
	$("#act-open").onclick = () => openInHydrus(sel());
	$("#act-archive").onclick = () => runFileAction("archive", sel());
	$("#act-delete").onclick = () => runFileAction("delete", sel());
	$("#act-defer").onclick = () => runFileAction("defer", sel());
	$("#act-remove-bucket").checked = pref.get("removeFromBucket", false);
	$("#act-remove-bucket").onchange = (e) => pref.set("removeFromBucket", e.target.checked);

	// Viewer metadata panel: clicks inside it must not close the overlay
	const metaPanel = $("#viewer-meta");
	metaPanel.classList.toggle("collapsed", pref.get("viewerMetaCollapsed", false));
	metaPanel.onclick = (e) => e.stopPropagation();
	const toggleMeta = () => pref.set("viewerMetaCollapsed", metaPanel.classList.toggle("collapsed"));
	$("#viewer-meta-toggle").onclick = (e) => { e.stopPropagation(); toggleMeta(); };
	// A mouseup anywhere ends a drag-select sweep (the click that may follow is
	// swallowed by onCardClick via state.dragMoved).
	document.addEventListener("mouseup", () => { state.dragAnchor = null; });

	// Overlay / context menu dismissal
	$("#viewer-prev").onclick = (e) => { e.stopPropagation(); stepViewer(-1); };
	$("#viewer-next").onclick = (e) => { e.stopPropagation(); stepViewer(1); };
	$("#overlay").onclick = closeViewer;
	document.addEventListener("click", (e) => {
		if (!$("#ctx-menu").contains(e.target)) hideCtxMenu();
	});
	document.addEventListener("keydown", (e) => {
		if (!$("#overlay").hidden) {
			if (e.key === "ArrowLeft") stepViewer(-1);
			else if (e.key === "ArrowRight") stepViewer(1);
			else if (e.key === "Escape") { if (!$("#ctx-menu").hidden) hideCtxMenu(); else closeViewer(); }
			else if (e.key === "i" || e.key === "I") toggleMeta();
			else return;
			e.preventDefault();
			return;
		}
		const typing = e.target.closest?.("input, textarea, select, [contenteditable]");
		if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && !typing && state.results.length) {
			e.preventDefault();
			selectAll();
			return;
		}
		if (e.key === "Escape") {
			$("#ctx-menu").hidden = true;
			if (state.selected.size) clearSelection();
		}
	});

	refreshBuckets().catch((e) => status(`Buckets: ${e.message}`));
	loadTagList().catch(() => {});
}

init();
