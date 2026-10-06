"use strict";

// Shared helpers + topbar for all HyCLIP WebUI pages.
// Each page needs: <header id="topbar"></header>, <footer id="statusbar"></footer>,
// and <script src="shared.js"></script> before its own script.

const $ = (s) => document.querySelector(s);

// ===== API =====
async function api(path, opts = {}) {
	const r = await fetch(path, opts);
	if (!r.ok) {
		let msg = r.statusText;
		try { msg = (await r.json()).detail ?? msg; } catch {}
		const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
		err.status = r.status;
		throw err;
	}
	return r.json();
}
const post = (path, body) =>
	api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function status(msg) { $("#statusbar").textContent = msg; }

// ===== Topbar =====
const PAGES = [
	["index.html", "Search"],
	["buckets.html", "Buckets"],
	["ingest.html", "Ingest"],
	["tags.html", "Tags"],
	["config.html", "Config"],
];

// ===== Readiness gating =====
// Buttons marked data-requires="model,hydrus,db,input" are disabled (with a tooltip why)
// until the model is loaded, the hydrus API is reachable with a valid key,
// no quantize is running, and there is search input.
// hyclip.quant is the /quant_status list (global + buckets), or null when unreachable.
const hyclip = { modelLoaded: false, hydrus: "unknown", quant: [], quantizing: false, currentBucketId: null, hasInput: false };

const HYDRUS_LABEL = {
	ok: "Hydrus API connected",
	denied: "Hydrus API key invalid or lacks permissions",
	unreachable: "Hydrus API unreachable",
	unknown: "Hydrus API status unknown",
};

const DB_LABEL = {
	ready: "Search index quantized",
	stale: "Search index quantized but stale",
	needs_quant: "Search index not quantized (full scan)",
	unreachable: "Server unreachable",
};

function updateRequires() {
	for (const el of document.querySelectorAll("[data-requires]")) {
		if (el.dataset.busy) continue;
		const why = [];
		if (el.dataset.requires.includes("model") && !hyclip.modelLoaded) why.push("model not loaded");
		if (el.dataset.requires.includes("hydrus") && hyclip.hydrus !== "ok") why.push(HYDRUS_LABEL[hyclip.hydrus] ?? "Hydrus API not connected");
		if (el.dataset.requires.includes("db") && hyclip.quantizing) why.push("search database is quantizing");
		if (el.dataset.requires.includes("input") && !hyclip.hasInput) why.push("no enabled prompts or reference images");
		el.disabled = why.length > 0;
		el.title = why.length ? `Unavailable: ${why.join("; ")}` : "";
	}
}

// s is a model_status payload, or null when the server is unreachable
function applyModelStatus(s) {
	hyclip.modelLoaded = !!s && s.loaded;
	$("#model-dot").className = "dot " + (hyclip.modelLoaded ? "on" : "off");
	$("#model-name").textContent = s ? `${s.model} — ${s.loaded ? "loaded" : "not loaded"}` : "server unreachable";
	$("#model-toggle").textContent = hyclip.modelLoaded ? "Unload model" : "Load model";
	updateRequires();
}

function applyHydrusStatus(st) {
	hyclip.hydrus = st;
	const dot = $("#hydrus-dot");
	dot.className = "dot " + (st === "ok" ? "on" : st === "denied" ? "warn" : "off");
	dot.title = $("#hydrus-name").textContent = HYDRUS_LABEL[st] ?? st;
	updateRequires();
}

// Per-scope quant state: green quantized, yellow quantized-but-stale, red not quantized.
function effectiveDbStatus() {
	if (hyclip.quant === null) return "unreachable";
	const scope = hyclip.quant.find((s) => s.bucket_id === hyclip.currentBucketId);
	if (!scope || !scope.quantized) return "needs_quant";
	return scope.stale ? "stale" : "ready";
}

function renderDb() {
	const eff = effectiveDbStatus();
	const dot = $("#db-dot");
	dot.className = "dot " + (eff === "ready" ? "on" : eff === "stale" ? "warn" : "off");
	dot.title = $("#db-name").textContent = DB_LABEL[eff] ?? eff;
	updateRequires();
}

function applyDbStatus(quant) {
	hyclip.quant = quant;
	renderDb();
}

function setScope(scope) {
	hyclip.currentBucketId = scope === "" || scope == null ? null : Number(scope);
	renderDb();
}

async function refreshModelStatus() {
	try { applyModelStatus(await api("/model_status")); }
	catch { applyModelStatus(null); }
}

// All topbar dots in one call, every 10s; pokeHeartbeat() runs a tick immediately.
let heartbeatTimer = null;
function scheduleHeartbeat() {
	clearTimeout(heartbeatTimer); // a poke + a pending timer can't double up
	heartbeatTimer = setTimeout(heartbeatTick, 10000);
}
async function heartbeatTick() {
	try {
		const s = await api("/heartbeat");
		applyModelStatus(s.model);
		applyHydrusStatus(s.hydrus.status);
		applyDbStatus(s.quant);
	} catch {
		applyModelStatus(null);
		applyHydrusStatus("unknown");
		applyDbStatus(null);
	}
	scheduleHeartbeat();
}
// Clear the pending 10s timer before ticking so a poke during a pending tick
// can't overlap a second tick during the await window.
function pokeHeartbeat() { clearTimeout(heartbeatTimer); heartbeatTick(); }

// ===== Quant status hover panel =====
let quantPanelOpen = false;
function buildQuantPanel() {
	const group = $("#db-group");
	const panel = $("#quant-panel");
	let hideTimer = null;

	const enter = () => { quantPanelOpen = true; clearTimeout(hideTimer); refreshQuantPanel(); };
	const leave = () => { hideTimer = setTimeout(() => { quantPanelOpen = false; panel.hidden = true; }, 250); };
	group.onmouseenter = enter;
	group.onmouseleave = leave;
	panel.onmouseenter = () => clearTimeout(hideTimer);
	panel.onmouseleave = leave;
}

async function refreshQuantPanel() {
	const panel = $("#quant-panel");
	let scopes;
	try { scopes = await api("/quant_status"); }
	catch { panel.hidden = true; return; }
	applyDbStatus(scopes);
	panel.replaceChildren();

	const title = document.createElement("div");
	title.className = "quant-title";
	title.textContent = "Quantization";
	panel.append(title);

	for (const s of scopes) {
		const row = document.createElement("div");
		row.className = "quant-row";

		const dot = document.createElement("span");
		dot.className = "dot " + (!s.quantized ? "off" : s.stale ? "warn" : "on");
		dot.title = !s.quantized ? "Not quantized" : s.stale ? "Quantized but stale" : "Quantized";

		const name = document.createElement("span");
		name.className = "quant-name";
		name.textContent = s.name;
		name.title = s.name;

		const info = document.createElement("span");
		info.className = "hint";
		info.textContent = s.quantized ? `${s.count}/${s.expected}` : `${s.expected} rows`;

		const quant = document.createElement("button");
		quant.className = "btn small";
		quant.textContent = s.quantized ? "Re-quant" : "Quantize";
		quant.disabled = s.expected === 0;
		quant.onclick = () => runQuantAction("/quantize", s);

		const clear = document.createElement("button");
		clear.className = "btn small";
		clear.textContent = "Clear";
		clear.disabled = !s.quantized;
		clear.onclick = () => runQuantAction("/clear_quant", s);

		row.append(dot, name, info, quant, clear);
		panel.append(row);
	}
	panel.hidden = !quantPanelOpen;
}

async function runQuantAction(path, scope) {
	hyclip.quantizing = true;
	renderDb();
	status(`${path === "/quantize" ? "Quantizing" : "Clearing"} ${scope.name}…`);
	let ok = true;
	try {
		await post(path, { bucket_id: scope.bucket_id });
	} catch (e) {
		ok = false;
		status(`Error: ${e.message}`);
	}
	hyclip.quantizing = false;
	await refreshQuantPanel();
	pokeHeartbeat();
	if (ok) status("Ready");
}

function buildTopbar() {
	const bar = $("#topbar");

	const group = document.createElement("div");
	group.className = "tb-group";
	group.innerHTML = `
		<span id="hydrus-dot" class="dot off"></span>
		<span id="hydrus-name">…</span>
		<span id="model-dot" class="dot off"></span>
		<span id="model-name">…</span>
		<button id="model-toggle" class="btn small">Load model</button>
		<div id="db-group" class="quant-group">
			<span id="db-dot" class="dot warn"></span>
			<span id="db-name">…</span>
			<div id="quant-panel" hidden></div>
		</div>
		`;

	const nav = document.createElement("nav");
	nav.className = "tb-group tb-tabs";
	const here = location.pathname.split("/").pop() || "index.html";
	for (const [href, label] of PAGES) {
		const a = document.createElement("a");
		a.href = href;
		a.textContent = label;
		a.className = "tab" + (href === here ? " active" : "");
		nav.append(a);
	}

	bar.append(group, nav);
	buildQuantPanel();

	$("#model-toggle").onclick = async () => {
		const loaded = $("#model-dot").classList.contains("on");
		$("#model-toggle").disabled = true;
		status(loaded ? "Unloading model…" : "Loading model… (this can take a while)");
		try {
			await api(loaded ? "/unload_model" : "/load_model", { method: "POST" });
		} catch (e) { status(`Error: ${e.message}`); }
		$("#model-toggle").disabled = false;
		refreshModelStatus();
		status("Ready");
	};

	updateRequires(); // start disabled until the first status check lands
	heartbeatTick();
}

buildTopbar();
