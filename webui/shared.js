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
	["config.html", "Config"],
	["ingest.html", "Ingest"],
];

async function refreshModelStatus() {
	try {
		const s = await api("/model_status");
		$("#model-dot").className = "dot " + (s.loaded ? "on" : "off");
		$("#model-name").textContent = `${s.model} — ${s.loaded ? "loaded" : "not loaded"}`;
		$("#model-toggle").textContent = s.loaded ? "Unload model" : "Load model";
		if (s.loaded) $("#model-toggle").classList.remove("attention");
	} catch {
		$("#model-dot").className = "dot off";
		$("#model-name").textContent = "server unreachable";
	}
}

function buildTopbar() {
	const bar = $("#topbar");

	const group = document.createElement("div");
	group.className = "tb-group";
	group.innerHTML = `<span id="model-dot" class="dot off"></span>
		<span id="model-name">…</span>
		<button id="model-toggle" class="btn small">Load model</button>`;

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

	refreshModelStatus();
	setInterval(refreshModelStatus, 10000);
}

buildTopbar();
