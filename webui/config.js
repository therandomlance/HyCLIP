"use strict";

let original = {};

function buildForm(cfg) {
	original = cfg;
	const form = $("#config-form");
	form.replaceChildren();

	for (const [key, value] of Object.entries(cfg)) {
		const row = document.createElement("div");
		row.className = "row config-row";

		const label = document.createElement("label");
		label.textContent = key;
		label.htmlFor = `cfg-${key}`;

		let input;
		if (typeof value === "boolean") {
			input = document.createElement("input");
			input.type = "checkbox";
			input.checked = value;
		} else if (typeof value === "number") {
			input = document.createElement("input");
			input.type = "number";
			input.value = value;
		} else {
			input = document.createElement("input");
			input.type = "text";
			input.value = value ?? "";
		}
		input.id = `cfg-${key}`;
		input.dataset.key = key;

		row.append(label, input);
		form.append(row);
	}
}

$("#save-config").onclick = async () => {
	const updates = {};
	for (const input of $("#config-form").querySelectorAll("input")) {
		const key = input.dataset.key;
		const old = original[key];
		let val;
		if (input.type === "checkbox") val = input.checked;
		else if (input.type === "number") val = input.value === "" ? old : Number(input.value);
		else val = input.value === "" && old === null ? null : input.value;
		if (val !== old) updates[key] = val;
	}
	if (!Object.keys(updates).length) { status("No changes"); return; }

	try {
		const cfg = await post("/update_config", { updates });
		buildForm(cfg);
		status(`Saved: ${Object.keys(updates).join(", ")}`);
	} catch (e) { status(`Error: ${e.message}`); }
};

api("/get_config").then(buildForm).catch((e) => status(`Config: ${e.message}`));
