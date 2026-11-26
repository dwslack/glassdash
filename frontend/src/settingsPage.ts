import { DEFAULTS, onSettings, saveSettings, type Settings } from "./settings";

type Field = { key: keyof Settings; label: string; hint?: string; step?: number; unit?: string };
const SECTIONS: { title: string; fields: Field[] }[] = [
  { title: "Display", fields: [
    { key: "night", label: "Night mode", hint: "Auto switches at dusk for where the truck is." },
    { key: "night_brightness", label: "Night brightness", step: 0.05, hint: "0.2 is very dim, 1.0 is no dimming." },
    { key: "alert_sound", label: "Beep on critical alerts", hint: "On the iPad, sound works after you've tapped the screen once." },
    { key: "voice", label: "Spoken turn-by-turn prompts", hint: "Uses the iPad's built-in voice. Turn the iPad volume up; the mute switch silences it." },
    { key: "auto_tow_view", label: "Switch to the Tow view when the transmission runs hot", hint: "Only if trans temp isn't already on screen, once per hot spell. Swipe the gauges to change views; long-press a gauge to change what it shows." },
  ] },
  { title: "Fuel", fields: [
    { key: "tank_l", label: "Tank size", unit: "L", step: 1, hint: "Check your owner's manual. 2011 Silverado: 98 L (1500 standard), 129 L (1500 long box), 136 L (HD long box)." },
    { key: "fuel_cal", label: "Fuel calibration", step: 0.01, hint: "Litres pumped ÷ litres the dash estimated since the last fill. 1.00 = no correction." },
  ] },
  { title: "Online maps (need internet)", fields: [
    { key: "esri_key", label: "ArcGIS key for satellite", hint: "Optional. Free from developers.arcgis.com (Location Platform). Without it the public imagery endpoint is used." },
    { key: "mapillary_token", label: "Mapillary token for street photos", hint: "Free: mapillary.com/dashboard/developers, register an app, copy the client token (MLY|…)." },
  ] },
  { title: "Voice assistant (needs internet)", fields: [
    { key: "anthropic_key", label: "Anthropic API key", hint: "From console.anthropic.com, API keys. Basic commands (navigate home, stop, recenter, gauge readings) work without it. Stored only on the dash computer." },
    { key: "assistant_model", label: "Claude model", hint: "Haiku 5.5 (default) is the fastest and well under a cent per question; Opus 5.5 handles open-ended questions best but is slower and about 3-5 cents." },
  ] },
  { title: "Alert thresholds", fields: [
    { key: "coolant_warn", label: "Coolant warning", unit: "°C" }, { key: "coolant_bad", label: "Coolant critical", unit: "°C" },
    { key: "trans_warn", label: "Trans warning", unit: "°C" }, { key: "trans_bad", label: "Trans critical", unit: "°C" },
    { key: "volts_low", label: "Low charging warning", unit: "V", step: 0.1 },
    { key: "volts_bad", label: "Not charging (critical)", unit: "V", step: 0.1 },
    { key: "volts_high", label: "High voltage warning", unit: "V", step: 0.1 },
    { key: "oil_pressure_min", label: "Low oil pressure (critical)", unit: "kPa", step: 5 },
    { key: "oil_temp_warn", label: "Oil temp warning", unit: "°C" },
  ] },
];

// Settings page: every change saves immediately.
export function mountSettings(root: HTMLElement): void {
  root.innerHTML = `<div class="settings-inner"><div class="settings-msg"></div></div>`;
  const inner = root.firstElementChild as HTMLElement;
  const msg = root.querySelector<HTMLElement>(".settings-msg")!;
  const inputs = new Map<keyof Settings, HTMLInputElement | HTMLSelectElement>();

  for (const sec of SECTIONS) {
    const s = document.createElement("section");
    s.innerHTML = `<h3>${sec.title}</h3>`;
    for (const f of sec.fields) {
      const row = document.createElement("label");
      row.className = "setting";
      const d = DEFAULTS[f.key];
      let input: HTMLInputElement | HTMLSelectElement;
      if (f.key === "night") {
        input = document.createElement("select");
        input.innerHTML = `<option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option>`;
      } else if (f.key === "assistant_model") {
        input = document.createElement("select");
        input.innerHTML = `<option value="claude-haiku-5-5">Claude Haiku 5.5 (fastest, cheapest)</option><option value="claude-sonnet-5-5">Claude Sonnet 5.5</option><option value="claude-opus-5-5">Claude Opus 5.5 (best for open questions)</option>`;
      } else if (typeof d === "string") {
        input = Object.assign(document.createElement("input"), { type: "text", autocapitalize: "off", spellcheck: false, placeholder: "not set" });
        input.className = "text";
      } else if (typeof d === "boolean") {
        input = Object.assign(document.createElement("input"), { type: "checkbox" });
      } else {
        input = Object.assign(document.createElement("input"), { type: "number", inputMode: "decimal", step: String(f.step ?? 1) });
      }
      row.innerHTML = `<span class="name">${f.label}${f.hint ? `<small>${f.hint}</small>` : ""}</span>`;
      const ctl = document.createElement("span");
      ctl.className = "ctl";
      ctl.appendChild(input);
      if (f.unit) ctl.insertAdjacentHTML("beforeend", `<em>${f.unit}</em>`);
      row.appendChild(ctl);
      s.appendChild(row);
      inputs.set(f.key, input);
      input.addEventListener("change", async () => {
        const value = input instanceof HTMLSelectElement ? input.value
          : input.type === "checkbox" ? input.checked : input.type === "text" ? input.value : Number(input.value);
        const err = await saveSettings({ [f.key]: value } as Partial<Settings>);
        msg.textContent = err ? `Not saved: ${err}` : `Saved ${f.label.toLowerCase()}.`;
        msg.className = "settings-msg" + (err ? " err" : "");
      });
    }
    inner.appendChild(s);
  }
  mountVehicle(inner, msg);
  mountSimRoutes(inner, msg);
  const reset = document.createElement("button");
  reset.textContent = "Reset all to defaults";
  reset.onclick = async () => {
    if (!confirm("Reset every setting to its default?")) return;
    const { anthropic_key: _k, vehicle: _v, ...rest } = DEFAULTS; // keep the API key and the vehicle
    const err = await saveSettings(rest);
    msg.textContent = err ? `Not saved: ${err}` : "Defaults restored.";
  };
  inner.appendChild(reset);

  onSettings((c) => {
    for (const [k, input] of inputs) {
      if (document.activeElement === input) continue; // don't clobber what's being typed
      if (input instanceof HTMLInputElement && input.type === "checkbox") input.checked = Boolean(c[k]);
      else input.value = String(c[k]);
    }
  });
}

// Simulator route picker. Only shown when dashd runs with --sim.
async function mountSimRoutes(inner: HTMLElement, msg: HTMLElement): Promise<void> {
  let info: { sim: boolean; current: string | null; routes: { id: string; name: string }[] };
  try { info = await (await fetch("/api/sim/routes")).json(); } catch { return; }
  if (!info.sim) return;
  const s = document.createElement("section");
  s.innerHTML = `<h3>Simulator</h3>`;
  const row = document.createElement("label");
  row.className = "setting";
  row.innerHTML = `<span class="name">Route<small>The sim drives it at road speeds, parks at the end, then starts over. Make more with: just route NAME "from" "to"</small></span>`;
  const select = document.createElement("select");
  select.innerHTML = (info.current ? "" : `<option value="">Scripted loop</option>`) +
    info.routes.map((r) => `<option value="${r.id}">${r.name.replace(/</g, "&lt;")}</option>`).join("");
  select.value = info.current ?? "";
  select.addEventListener("change", async () => {
    const r = await fetch("/api/sim/route", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: select.value }) });
    const err = r.ok ? "" : (await r.json().catch(() => ({}))).detail ?? r.statusText;
    msg.textContent = err ? `Route not changed: ${err}` : `Driving ${select.selectedOptions[0].text}.`;
    msg.className = "settings-msg" + (err ? " err" : "");
  });
  const ctl = document.createElement("span");
  ctl.className = "ctl";
  ctl.appendChild(select);
  row.appendChild(ctl);
  s.appendChild(row);
  inner.insertBefore(s, inner.querySelector("button"));
}

// Vehicle profile picker, with what the profile covers and how much of it has been checked on a real vehicle.
async function mountVehicle(inner: HTMLElement, msg: HTMLElement): Promise<void> {
  type Row = { id: string; name: string; error?: string; verified?: number; signals?: number };
  let info: { current: string | null; profiles: Row[] };
  try { info = await (await fetch("/api/vehicles")).json(); } catch { return; }
  const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const s = document.createElement("section");
  s.innerHTML = `<h3>Vehicle</h3>`;
  const row = document.createElement("label");
  row.className = "setting";
  row.innerHTML = `<span class="name">Vehicle profile<small class="vinfo"></small></span>`;
  const select = document.createElement("select");
  select.className = "wide";
  select.innerHTML = info.profiles.map((p) => `<option value="${esc(p.id)}" ${p.error ? "disabled" : ""}>${esc(p.name)}${p.error ? " (broken)" : ""}</option>`).join("");
  select.value = info.current ?? "";
  const vinfo = row.querySelector<HTMLElement>(".vinfo")!;
  const describe = () => {
    const p = info.profiles.find((x) => x.id === select.value);
    vinfo.textContent = p && !p.error ? `${p.signals} signals, ${p.verified} verified on a real vehicle. Profile file: vehicles/${p.id}.yaml` : "";
  };
  describe();
  select.addEventListener("change", async () => {
    const err = await saveSettings({ vehicle: select.value });
    msg.textContent = err ? `Vehicle not changed: ${err}` : `Now reading the ${select.selectedOptions[0].text}.`;
    msg.className = "settings-msg" + (err ? " err" : "");
    if (err) select.value = info.current ?? ""; else info.current = select.value;
    describe();
  });
  const ctl = document.createElement("span");
  ctl.className = "ctl";
  ctl.appendChild(select);
  row.appendChild(ctl);
  s.appendChild(row);
  const learn = document.createElement("div");
  learn.className = "setting";
  learn.innerHTML = `<span class="name">Map a signal<small>Find which CAN frame and bit follows a door, lock or light on your vehicle, and get the profile lines to add it. See CONTRIBUTING.md to share it.</small></span><span class="ctl"><a class="btn" href="#learn">Open</a></span>`;
  s.appendChild(learn);
  inner.insertBefore(s, inner.children[1] ?? null);
}
