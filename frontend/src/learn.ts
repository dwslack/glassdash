import { vehicle } from "./vehicle";

// "Map a signal": record the body bus while the user says when a door/lock/light is on or off,
// then show the frame bits that follow it and the profile lines to add. Reached from Settings › Vehicle.

const KNOWN = ["door_driver", "door_passenger", "door_rear_left", "door_rear_right", "tailgate", "hood", "locked",
  "parking_lights", "low_beam", "high_beam", "fog", "turn_left", "turn_right", "brake", "reverse",
  "belt_driver", "belt_passenger", "passenger_seated", "parking_brake"];

interface Status { signal: string; frames: number; ids: number; changes: number; on: boolean; seconds: number; full: boolean; body_bus?: boolean; }
interface Candidate { can_id: number; extended: boolean; arb: number | null; byte: number; bit: number; inverted: boolean;
  match: number; toggles: number; state_changes: number; snippet: string; }

const hex = (n: number) => "0x" + n.toString(16).toUpperCase().padStart(3, "0");

async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(out.detail ?? `HTTP ${r.status}`);
  return out as T;
}

export function mountLearn(root: HTMLElement): { show: () => void } {
  root.innerHTML = `<div class="learn">
    <h2>Map a signal</h2>
    <p class="dim">Find which bit on the body bus follows one thing on your vehicle. Park, engine off is fine.
      Pick what you're mapping, make sure it's <b>off</b>, then start. Turn it on and off at least three times,
      pressing the matching button each time and waiting a couple of seconds in between.</p>
    <div class="step s1">
      <label>Signal <input list="learn-known" class="sig" placeholder="e.g. door_driver" autocapitalize="off" spellcheck="false"></label>
      <datalist id="learn-known">${KNOWN.map((k) => `<option value="${k}">`).join("")}</datalist>
      <button class="start primary">It's off. Start recording</button>
    </div>
    <div class="step s2" hidden>
      <div class="big"><button class="on">It's ON now</button><button class="off">It's OFF now</button></div>
      <div class="sim" hidden><button class="simtoggle">Simulator: toggle it for me</button></div>
      <div class="stat dim"></div>
      <div class="row"><button class="find primary" disabled>Find it</button><button class="cancel">Cancel</button></div>
    </div>
    <div class="step s3" hidden>
      <div class="results"></div>
      <pre class="snip"></pre>
      <div class="row"><button class="copy">Copy</button><button class="again">Map another</button></div>
      <p class="dim">Add these lines to your vehicle's profile in <code>vehicles/</code> (or a new one that
        <code>extends</code> it), put your GitHub name in <code>verified</code>, and open a pull request.
        CONTRIBUTING.md has the steps.</p>
    </div>
    <div class="err"></div>
  </div>`;
  const $ = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!;
  const s1 = $(".s1"), s2 = $(".s2"), s3 = $(".s3"), err = $(".err"), stat = $(".stat");
  const sigIn = $<HTMLInputElement>(".sig"), find = $<HTMLButtonElement>(".find");
  let poll: number | undefined;
  let state = false;
  let isSim = false;
  let candidates: Candidate[] = [];

  const step = (n: number) => { s1.hidden = n !== 1; s2.hidden = n !== 2; s3.hidden = n !== 3; err.textContent = ""; };
  const fail = (e: unknown) => { err.textContent = (e as Error).message; };
  const showStatus = (s: Status) => {
    state = s.on;
    stat.textContent = `${s.frames.toLocaleString()} frames from ${s.ids} ids · ${s.changes} on/off changes · now ${s.on ? "ON" : "OFF"}` +
      (s.full ? " · recording full" : "") + (s.body_bus === false ? " · no body bus connected: nothing will be recorded" : "");
    find.disabled = s.changes < 2;
    $(".on").classList.toggle("active", s.on);
    $(".off").classList.toggle("active", !s.on);
  };

  $(".start").onclick = async () => {
    try {
      const s = await api<Status>("/api/learn/start", { signal: sigIn.value });
      sigIn.value = s.signal;
      step(2);
      showStatus(s);
      clearInterval(poll);
      poll = window.setInterval(() => api<Status>("/api/learn/status").then(showStatus).catch(() => {}), 1000);
    } catch (e) { fail(e); }
  };
  const mark = (on: boolean, sim = false) => api<Status>("/api/learn/mark", { on, sim }).then(showStatus).catch(fail);
  $(".on").onclick = () => mark(true);
  $(".off").onclick = () => mark(false);
  $(".simtoggle").onclick = () => mark(!state, true);
  $(".cancel").onclick = () => { clearInterval(poll); api("/api/learn/stop", {}).catch(() => {}); step(1); };
  $(".again").onclick = $(".cancel").onclick;

  const pick = (i: number) => {
    root.querySelectorAll(".results tr[data-i]").forEach((tr) => tr.classList.toggle("on", Number((tr as HTMLElement).dataset.i) === i));
    $(".snip").textContent = candidates[i]?.snippet ?? "";
  };
  find.onclick = async () => {
    try {
      const r = await api<{ candidates: Candidate[] }>("/api/learn/analyze", {});
      clearInterval(poll);
      candidates = r.candidates;
      step(3);
      if (!candidates.length) {
        $(".results").innerHTML = `<p>No bit followed it. Check the body bus is connected (Truck page), and try again with longer on and off periods.</p>`;
        $(".snip").textContent = "";
        return;
      }
      const best = candidates[0];
      const sure = best.match >= 0.97 && (candidates[1]?.match ?? 0) < best.match - 0.05;
      $(".results").innerHTML = `<p>${sure ? "Found it." : "Best guesses below. More on/off changes make it clearer."}
        Tap a row to see its profile lines.</p>
        <table><tr><th>${vehicle.body.bus?.type === "gmlan-swcan" ? "GMLAN arb" : "CAN id"}</th><th>Byte</th><th>Bit</th><th>Match</th><th>Flips</th></tr>
        ${candidates.map((c, i) => `<tr data-i="${i}"><td>${hex(c.arb ?? c.can_id)}${c.inverted ? " (inverted)" : ""}</td><td>${c.byte}</td><td>${c.bit}</td>
          <td>${Math.round(c.match * 100)}%</td><td>${c.toggles} / ${c.state_changes}</td></tr>`).join("")}</table>`;
      root.querySelectorAll<HTMLElement>(".results tr[data-i]").forEach((tr) => { tr.onclick = () => pick(Number(tr.dataset.i)); });
      pick(0);
    } catch (e) { fail(e); }
  };
  $(".copy").onclick = async () => {
    try { await navigator.clipboard.writeText($(".snip").textContent ?? ""); $(".copy").textContent = "Copied"; }
    catch { const r = document.createRange(); r.selectNodeContents($(".snip")); getSelection()?.removeAllRanges(); getSelection()?.addRange(r); }
    setTimeout(() => { $(".copy").textContent = "Copy"; }, 1500);
  };

  return {
    show: async () => {
      try { isSim = (await api<{ sim: boolean }>("/api/sim/routes")).sim; } catch { isSim = false; }
      $<HTMLElement>(".sim").hidden = !isSim;
      try {
        showStatus(await api<Status>("/api/learn/status"));
        step(2);
        clearInterval(poll);
        poll = window.setInterval(() => api<Status>("/api/learn/status").then(showStatus).catch(() => {}), 1000);
      } catch { /* not recording */ }
    },
  };
}
