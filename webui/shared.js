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
	["config.html", "Config"],
];

// ===== Readiness gating =====
// Buttons marked data-requires="model,hydrus,db,input" are disabled (with a tooltip why)
// until the model is loaded, the hydrus API is reachable with a valid key,
// the search DB is not mid-quantize, and there is search input.
const hyclip = { modelLoaded: false, hydrus: "unknown", quantStatus: "needs_quant", searching: false, hasInput: false };

const HYDRUS_LABEL = {
	ok: "Hydrus API connected",
	denied: "Hydrus API key invalid or lacks permissions",
	unreachable: "Hydrus API unreachable",
	unknown: "Hydrus API status unknown",
};

const DB_LABEL = {
	ready: "Search ready",
	needs_quant: "Needs quant",
	quantizing: "Quantizing…",
	unreachable: "Server unreachable",
};

function updateRequires() {
	for (const el of document.querySelectorAll("[data-requires]")) {
		if (el.dataset.busy) continue;
		const why = [];
		if (el.dataset.requires.includes("model") && !hyclip.modelLoaded) why.push("model not loaded");
		if (el.dataset.requires.includes("hydrus") && hyclip.hydrus !== "ok") why.push(HYDRUS_LABEL[hyclip.hydrus] ?? "Hydrus API not connected");
		if (el.dataset.requires.includes("db") && hyclip.quantStatus === "quantizing") why.push("search database is quantizing");
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

function applyDbStatus(qs) {
	hyclip.quantStatus = qs;
	const dot = $("#db-dot");
	dot.className = "dot " + (qs === "ready" ? "on" : qs === "needs_quant" ? "warn" : "off");
	dot.title = $("#db-name").textContent = DB_LABEL[qs] ?? qs;
	updateRequires();
}

async function refreshModelStatus() {
	try { applyModelStatus(await api("/model_status")); }
	catch { applyModelStatus(null); }
}

// All topbar dots in one call; polls fast only while a search is running
// (here or elsewhere — a mid-quant DB means someone is searching)
const HEARTBEAT_IDLE = 10000, HEARTBEAT_SEARCHING = 250;

async function heartbeat() {
	let ok = false;
	try {
		const s = await api("/heartbeat");
		applyModelStatus(s.model);
		applyHydrusStatus(s.hydrus.status);
		applyDbStatus(s.quant_status);
		ok = true;
	} catch {
		applyModelStatus(null);
		applyHydrusStatus("unknown");
		applyDbStatus("unreachable");
	}
	setTimeout(heartbeat, ok && (hyclip.searching || hyclip.quantStatus === "quantizing") ? HEARTBEAT_SEARCHING : HEARTBEAT_IDLE);
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
		<span id="db-dot" class="dot warn"></span>
		<span id="db-name">…</span>
		<button id="model-toggle" class="btn small">Load model</button>
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
	heartbeat();
}

buildTopbar();
