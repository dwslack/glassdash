import { num, when } from "./fmt";
import type { DtcRead, PidInfo, Snapshot } from "./types";

const HISTORY_S = 60;
const KIND_LABEL = { stored: "Stored", pending: "Pending", permanent: "Permanent" } as const;
const KIND_NOTE = {
  stored: "Confirmed faults. These turn the check-engine light on.",
  pending: "Seen once; becomes stored if it happens again.",
  permanent: "Can't be cleared; the ECU removes them after the fault passes its self-test.",
} as const;

// Diagnostics page: check-engine status and trouble codes, plus every live signal with a 60 s trend.
export function mountDiag(root: HTMLElement): { show: () => void; update: (s: Snapshot) => void } {
  root.innerHTML = `
    <div class="diag-codes">
      <div class="mil-card"><div class="mil-light"></div><div class="mil-text">Check engine: --</div></div>
      <div class="diag-actions"><button class="read">Read codes</button><button class="clear">Clear codes</button></div>
      <div class="diag-msg"></div>
      <div class="dtc-list"></div>
    </div>
    <div class="diag-signals">
      <table><thead><tr><th>Signal</th><th>Value</th><th></th><th>Last 60 s</th><th>Age</th><th>Misses</th><th>PID</th></tr></thead><tbody></tbody></table>
      <p class="note">PIDs marked <span class="unv">?</span> haven't been confirmed on the truck yet.</p>
    </div>`;
  const milLight = root.querySelector<HTMLElement>(".mil-light")!;
  const milText = root.querySelector<HTMLElement>(".mil-text")!;
  const msg = root.querySelector<HTMLElement>(".diag-msg")!;
  const list = root.querySelector<HTMLElement>(".dtc-list")!;
  const tbody = root.querySelector("tbody")!;
  const readBtn = root.querySelector<HTMLButtonElement>(".read")!;
  const clearBtn = root.querySelector<HTMLButtonElement>(".clear")!;

  let pids: PidInfo[] = [];
  const rows = new Map<string, { val: HTMLElement; unit: HTMLElement; spark: SVGPolylineElement; age: HTMLElement; err: HTMLElement; tr: HTMLElement }>();
  const hist = new Map<string, [number, number][]>();
  let lastSample = 0;
  let lastRender = 0;

  function render(r: DtcRead) {
    list.innerHTML = "";
    for (const [ecu, kinds] of Object.entries(r.ecus)) {
      const sec = document.createElement("section");
      sec.innerHTML = `<h3>${ecu === "ECM" ? "Engine (ECM)" : "Transmission (TCM)"}</h3>`;
      for (const kind of ["stored", "pending", "permanent"] as const) {
        const codes = kinds[kind];
        const block = document.createElement("div");
        block.className = "dtc-kind";
        block.innerHTML = `<div class="kh"><b>${KIND_LABEL[kind]}</b> <span title="${KIND_NOTE[kind]}">${codes === null ? "no answer" : codes.length ? "" : "none"}</span></div>`;
        for (const c of codes ?? []) {
          const row = document.createElement("div");
          row.className = `dtc ${kind}`;
          row.innerHTML = `<code>${c.code}</code><span>${c.desc || "No description on file"}</span>`;
          block.appendChild(row);
        }
        sec.appendChild(block);
      }
      list.appendChild(sec);
    }
    msg.textContent = `Read ${when(r.ts)}.` + (r.cleared ? ` Clear sent: ${Object.entries(r.cleared).map(([k, ok]) => `${k} ${ok ? "OK" : "no reply"}`).join(", ")}.` : "");
  }

  async function read() {
    readBtn.disabled = true; msg.textContent = "Reading…";
    try {
      const res = await fetch("/api/dtcs");
      if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
      render(await res.json());
    } catch (e) { msg.textContent = `Couldn't read codes: ${(e as Error).message}`; }
    finally { readBtn.disabled = false; }
  }

  readBtn.onclick = read;
  clearBtn.onclick = async () => {
    if (!confirm("Clear trouble codes?\n\nThis turns off the check-engine light, erases freeze-frame data and resets the emissions readiness monitors. If the fault is still there, the code will come back.\n\nOnly works with the truck stopped.")) return;
    clearBtn.disabled = true; msg.textContent = "Clearing…";
    try {
      const res = await fetch("/api/dtcs/clear", { method: "POST" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? res.statusText);
      render(body);
    } catch (e) { msg.textContent = (e as Error).message; }
    finally { clearBtn.disabled = false; }
  };

  async function loadPids() {
    try { pids = (await (await fetch("/api/pids")).json()).pids; } catch { pids = []; }
    tbody.innerHTML = "";
    rows.clear();
    for (const p of pids) {
      const tr = document.createElement("tr");
      const id = `${p.mode.toString(16).padStart(2, "0")} ${p.pid.toString(16).toUpperCase().padStart(p.mode === 0x22 ? 4 : 2, "0")}`;
      tr.innerHTML = `<td>${p.name.replace(/_/g, " ")}${p.confirmed ? "" : ' <span class="unv" title="not confirmed on the truck">?</span>'}</td>` +
        `<td class="v">--</td><td class="u">${p.unit}</td>` +
        `<td><svg viewBox="0 0 120 24" preserveAspectRatio="none"><polyline fill="none" stroke-width="1.5"/></svg></td>` +
        `<td class="age">--</td><td class="err">0</td><td class="pid">${id} @${p.ecu.toString(16).toUpperCase()} ${p.hz}Hz</td>`;
      tbody.appendChild(tr);
      rows.set(p.name, { tr, val: tr.querySelector(".v")!, unit: tr.querySelector(".u")!, spark: tr.querySelector("polyline")!, age: tr.querySelector(".age")!, err: tr.querySelector(".err")! });
    }
  }

  function sample(s: Snapshot) {
    for (const [k, sig] of Object.entries(s.signals)) {
      const h = hist.get(k) ?? [];
      if (!sig.stale) h.push([s.ts, sig.value]);
      while (h.length && h[0][0] < s.ts - HISTORY_S) h.shift();
      hist.set(k, h);
    }
  }

  function draw(s: Snapshot) {
    for (const [name, r] of rows) {
      const sig = s.signals[name];
      r.val.textContent = sig ? num(sig.value, Math.abs(sig.value) >= 100 ? 0 : 1) : "--";
      r.age.textContent = sig ? `${num(sig.age, 1)}s` : "--";
      r.err.textContent = String(s.errors[name] ?? 0);
      r.tr.classList.toggle("stale", !sig || sig.stale);
      const h = hist.get(name) ?? [];
      if (h.length > 1) {
        const lo = Math.min(...h.map((p) => p[1])), hi = Math.max(...h.map((p) => p[1]));
        const span = hi - lo || 1;
        r.spark.setAttribute("points", h.map(([t, v]) => `${((t - (s.ts - HISTORY_S)) / HISTORY_S * 120).toFixed(1)},${(22 - (v - lo) / span * 20).toFixed(1)}`).join(" "));
      }
    }
  }

  return {
    show: () => { if (!pids.length) loadPids(); if (!list.children.length) read(); },
    update: (s) => {
      if (s.mil === undefined || s.mil === null) { milText.textContent = "Check engine: no answer (key off?)"; milLight.className = "mil-light"; }
      else {
        milLight.className = "mil-light" + (s.mil.mil ? " on" : " off");
        milText.textContent = s.mil.mil ? `Check engine light ON (${s.mil.count} stored)` : "Check engine light off";
      }
      const now = performance.now();
      if (now - lastSample > 500) { lastSample = now; sample(s); }   // 2 Hz history, always
      if (!root.hidden && now - lastRender > 250) { lastRender = now; draw(s); }
    },
  };
}
