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
// Buttons marked data-requires="model,hydrus" are disabled (with a tooltip why)
// until the model is loaded and the hydrus API is reachable with a valid key.
const hyclip = { modelLoaded: false, hydrus: "unknown" };

const HYDRUS_LABEL = {
	ok: "Hydrus API connected",
	denied: "Hydrus API key invalid or lacks permissions",
	unreachable: "Hydrus API unreachable",
	unknown: "Hydrus API status unknown",
};

function updateRequires() {
	for (const el of document.querySelectorAll("[data-requires]")) {
		if (el.dataset.busy) continue;
		const why = [];
		if (el.dataset.requires.includes("model") && !hyclip.modelLoaded) why.push("model not loaded");
		if (el.dataset.requires.includes("hydrus") && hyclip.hydrus !== "ok") why.push(HYDRUS_LABEL[hyclip.hydrus] ?? "Hydrus API not connected");
		el.disabled = why.length > 0;
		el.title = why.length ? `Unavailable: ${why.join("; ")}` : "";
	}
}

async function refreshModelStatus() {
	try {
		const s = await api("/model_status");
		hyclip.modelLoaded = s.loaded;
		$("#model-dot").className = "dot " + (s.loaded ? "on" : "off");
		$("#model-name").textContent = `${s.model} — ${s.loaded ? "loaded" : "not loaded"}`;
		$("#model-toggle").textContent = s.loaded ? "Unload model" : "Load model";
	} catch {
		hyclip.modelLoaded = false;
		$("#model-dot").className = "dot off";
		$("#model-name").textContent = "server unreachable";
	}
	updateRequires();
}

async function refreshHydrusStatus() {
	let st = "unknown";
	try {
		st = (await api("/hydrus_status")).status;
	} catch { /* server unreachable */ }
	hyclip.hydrus = st;
	const dot = $("#hydrus-dot");
	dot.className = "dot " + (st === "ok" ? "on" : st === "denied" ? "warn" : "off");
	dot.title = $("#hydrus-name").textContent = HYDRUS_LABEL[st] ?? st;
	updateRequires();
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
	refreshModelStatus();
	refreshHydrusStatus();
	setInterval(() => { refreshModelStatus(); refreshHydrusStatus(); }, 10000);
}

buildTopbar();
