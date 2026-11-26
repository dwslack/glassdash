import maplibregl from "maplibre-gl";
import { settings } from "./settings";
import type { Fix, NavState } from "./types";

// Turn-by-turn navigation: destination search, next-turn banner, lane arrows, ETA bar and spoken prompts.
// Routing and timing of prompts happen in dashd; this only shows and speaks what it sends.

interface Place { name: string; city: string; kind: string; lat: number; lon: number; distance_m: number | null; note?: string; id?: string; }

const ANGLE: Record<string, number> = {
  straight: 0, "slight right": 45, right: 90, "sharp right": 135, uturn: 180,
  "slight left": -45, left: -90, "sharp left": -135,
};
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

export function fmtDist(m: number): string {
  if (m >= 10000) return `${Math.round(m / 1000)} km`;
  if (m >= 950) return `${(m / 1000).toFixed(1)} km`;
  if (m >= 300) return `${Math.round(m / 50) * 50} m`;
  return `${Math.max(0, Math.round(m / 10) * 10)} m`;
}

function fmtDuration(s: number): string {
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

// Arrow for a maneuver, drawn in a 48x48 box.
export function arrowSvg(type: string, modifier: string, exit: number | null, size = 48): string {
  const wrap = (inner: string) => `<svg viewBox="0 0 48 48" width="${size}" height="${size}" class="arrow" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  if (type === "arrive") {
    return wrap(`<path d="M24 44 C24 44 12 30 12 20 a12 12 0 0 1 24 0 C36 30 24 44 24 44Z" stroke-width="4"/><circle cx="24" cy="20" r="4" fill="currentColor" stroke="none"/>`);
  }
  if (type === "roundabout" || type === "rotary") {
    const a = ((ANGLE[modifier] ?? 0) * Math.PI) / 180;
    const x = 24 + 18 * Math.sin(a), y = 20 - 18 * Math.cos(a);
    return wrap(`<circle cx="24" cy="20" r="9" stroke-width="4"/><path d="M24 44 V29"/><path d="M${24 + 9 * Math.sin(a)} ${20 - 9 * Math.cos(a)} L${x} ${y}"/>` +
      (exit ? `<text x="24" y="24.5" text-anchor="middle" font-size="12" font-weight="700" fill="currentColor" stroke="none">${exit}</text>` : ""));
  }
  const left = modifier.includes("left");
  let inner: string;
  if (modifier === "uturn") {
    inner = `<path d="M32 44 V20 a8 8 0 0 0 -16 0 V30"/><path d="M10 26 L16 33 L22 26"/>`;
  } else {
    const deg = Math.abs(ANGLE[modifier] ?? 0);
    const a = (deg * Math.PI) / 180;
    const tx = 24 + 16 * Math.sin(a), ty = 22 - 16 * Math.cos(a);
    // Arrow head: two strokes back from the tip, 35 degrees either side of the direction of travel.
    const h = (off: number) => `${tx - 9 * Math.sin(a + off)} ${ty + 9 * Math.cos(a + off)}`;
    inner = `<path d="M24 44 V${deg ? 22 : 8} ${deg ? `L${tx} ${ty}` : ""}"/><path d="M${h(0.6)} L${tx} ${deg ? ty : 6} L${h(-0.6)}"/>`;
  }
  return wrap(left ? `<g transform="translate(48 0) scale(-1 1)">${inner}</g>` : inner);
}

export function mountNav(
  map: maplibregl.Map, wrap: HTMLElement, button: HTMLButtonElement, here: () => [number, number] | null,
): (nav: NavState | null | undefined, fix: Fix | null) => void {
  wrap.insertAdjacentHTML("beforeend", `
    <div id="nav-panel" hidden>
      <input id="nav-q" type="search" placeholder="Where to? Town, business, street or address" autocomplete="off" autocapitalize="off" spellcheck="false">
      <div id="nav-msg"></div>
      <div id="nav-pin" hidden><div class="where"></div>
        <div class="row"><button class="go">Go here</button><input class="pin-name" value="Home" maxlength="60" autocapitalize="words"><button class="save">Save</button></div></div>
      <ul id="nav-results"></ul>
      <div class="hint">Or tap the map to drop a pin.</div>
    </div>
    <div id="nav-banner" hidden></div>
    <div id="nav-bar" hidden><span class="eta"></span><span class="left"></span>
      <button class="mute" title="Voice prompts">Voice on</button><button class="end">End</button></div>`);
  const panel = wrap.querySelector<HTMLElement>("#nav-panel")!;
  const input = wrap.querySelector<HTMLInputElement>("#nav-q")!;
  const msg = wrap.querySelector<HTMLElement>("#nav-msg")!;
  const list = wrap.querySelector<HTMLUListElement>("#nav-results")!;
  const banner = wrap.querySelector<HTMLElement>("#nav-banner")!;
  const bar = wrap.querySelector<HTMLElement>("#nav-bar")!;
  const muteBtn = bar.querySelector<HTMLButtonElement>(".mute")!;
  let muted = false;
  let routeId: number | null = null;
  let lastSpeak: number | null = null;
  let seq = 0;
  const pinBox = wrap.querySelector<HTMLElement>("#nav-pin")!;
  const pinName = pinBox.querySelector<HTMLInputElement>(".pin-name")!;
  const pinMarker = new maplibregl.Marker({ color: "#34d17a" });
  let pin: Place | null = null;

  function setPin(p: Place | null) {
    pin = p;
    pinBox.hidden = !p;
    if (!p) { pinMarker.remove(); return; }
    pinBox.querySelector(".where")!.textContent = p.kind === "pin" ? `Dropped pin ${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}` : p.name;
    pinMarker.setLngLat([p.lon, p.lat]).addTo(map);
  }
  pinBox.querySelector<HTMLButtonElement>(".go")!.onclick = () => pin && go({ ...pin, name: pin.kind === "pin" ? "Dropped pin" : pin.name });
  pinBox.querySelector<HTMLButtonElement>(".save")!.onclick = async () => {
    if (!pin) return;
    const r = await fetch("/api/favorites", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: pinName.value, lat: pin.lat, lon: pin.lon }) });
    msg.textContent = r.ok ? `Saved "${pinName.value.trim()}". Type its name or open this panel to go there.` :
      (await r.json().catch(() => ({}))).detail ?? "Couldn't save";
    if (r.ok) { setPin(null); input.value = ""; search(); }
  };
  map.on("click", (e) => {
    if (panel.hidden) return;
    setPin({ name: "Dropped pin", city: "", kind: "pin", lat: e.lngLat.lat, lon: e.lngLat.lng, distance_m: null });
  });

  function render(results: Place[], empty: string) {
    list.innerHTML = results.length ? "" : empty;
    for (const p of results) {
      const li = document.createElement("li");
      li.innerHTML = `<b>${p.kind === "saved" ? "★ " : ""}${esc(p.name)}</b><small>${esc(p.kind)}${p.city ? ` · ${esc(p.city)}` : ""}</small>` +
        (p.distance_m != null ? `<em>${fmtDist(p.distance_m)}</em>` : "") +
        (p.note ? `<small class="note">${esc(p.note)}</small>` : "") +
        (p.kind === "saved" ? `<button class="del" title="Remove">×</button>` : `<button class="pick" title="Save or check on the map">☆</button>`);
      li.onclick = (e) => {
        const t = e.target as HTMLElement;
        if (t.classList.contains("del")) {
          e.stopPropagation();
          fetch(`/api/favorites/${p.id}`, { method: "DELETE" }).then(() => search());
        } else if (t.classList.contains("pick")) {
          e.stopPropagation();
          setPin(p);
          pinName.value = /^\d/.test(p.name) || p.kind === "street" ? "Home" : p.name;
          map.easeTo({ center: [p.lon, p.lat], zoom: 16 });
        } else go(p);
      };
      list.appendChild(li);
    }
  }

  const say = (text: string) => {
    if (muted || !settings.voice || !("speechSynthesis" in window) || !text) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-CA";
    u.rate = 1.0;
    speechSynthesis.speak(u);
  };

  const setPanel = async (open: boolean) => {
    panel.hidden = !open;
    button.classList.toggle("on", open);
    if (!open) { setPin(null); return; }
    input.focus();
    search();
    try {
      const st = await (await fetch("/api/nav/status")).json();
      const problems = Object.values(st.problems ?? {}) as string[];
      msg.textContent = st.routing && st.search ? "" : `Offline navigation isn't set up: ${problems.join("; ")}`;
    } catch { msg.textContent = "Navigation needs dashd running."; }
  };
  button.onclick = () => setPanel(panel.hidden);

  let timer: number | undefined;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = window.setTimeout(search, 200);
  });
  async function search() {
    const q = input.value.trim();
    const mine = ++seq;
    if (q.length < 2) {
      const favs = await (await fetch("/api/favorites")).json().catch(() => ({ favorites: [] }));
      if (mine !== seq) return;
      render((favs.favorites ?? []).map((f: Place) => ({ ...f, kind: "saved", city: "", distance_m: null })), "");
      return;
    }
    const pos = here();
    const params = new URLSearchParams({ q, ...(pos ? { lon: String(pos[0]), lat: String(pos[1]) } : {}) });
    const r = await fetch(`/api/places?${params}`);
    if (mine !== seq) return;
    if (!r.ok) { msg.textContent = (await r.json().catch(() => ({}))).detail ?? "Search failed"; return; }
    render((await r.json()).results, `<li class="none">Nothing found. Try fewer words, or tap the map to drop a pin.</li>`);
  }

  async function go(p: Place) {
    // iPad Safari only speaks after something was spoken inside a tap; this tap counts.
    if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance(""));
    msg.textContent = `Finding a route to ${p.name}…`;
    const r = await fetch("/api/nav", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ lat: p.lat, lon: p.lon, name: p.name }) });
    if (!r.ok) { msg.textContent = (await r.json().catch(() => ({}))).detail ?? "Couldn't start navigation"; return; }
    msg.textContent = "";
    input.value = "";
    list.innerHTML = "";
    setPin(null);
    setPanel(false);
  }

  muteBtn.onclick = () => {
    muted = !muted;
    muteBtn.textContent = muted ? "Voice off" : "Voice on";
    muteBtn.classList.toggle("off", muted);
    if (muted && "speechSynthesis" in window) speechSynthesis.cancel();
  };
  bar.querySelector<HTMLButtonElement>(".end")!.onclick = () => fetch("/api/nav", { method: "DELETE" });

  async function loadRoute(id: number | null) {
    routeId = id;
    let coords: [number, number][] = [];
    if (id != null) {
      const r = await (await fetch("/api/nav/route")).json();
      coords = (r.points ?? []).map(([lat, lon]: [number, number]) => [lon, lat]);
    }
    const data = { type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: coords } };
    const src = map.getSource("nav-route") as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData(data);
    else {
      map.addSource("nav-route", { type: "geojson", data });
      map.addLayer({ id: "nav-route-casing", type: "line", source: "nav-route", layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#0b3d1f", "line-width": 11 } });
      map.addLayer({ id: "nav-route", type: "line", source: "nav-route", layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#34d17a", "line-width": 7 } });
    }
    if (map.getLayer("route")) map.setLayoutProperty("route", "visibility", id != null ? "none" : "visible");
  }

  return (nav) => {
    const id = nav ? nav.route_id : null;
    if (id !== routeId) loadRoute(id);
    banner.hidden = bar.hidden = !nav;
    if (!nav) { lastSpeak = null; return; }

    if (lastSpeak === null) lastSpeak = nav.speak.id; // joining mid-route: don't replay the last prompt
    else if (nav.speak.id !== lastSpeak) { lastSpeak = nav.speak.id; say(nav.speak.text); }

    const n = nav.next;
    if (nav.status === "arrived") {
      banner.innerHTML = `<div class="icon">${arrowSvg("arrive", "", null)}</div><div class="main"><div class="text">Arrived${nav.dest.name ? ` at ${esc(nav.dest.name)}` : ""}</div></div>`;
    } else if (nav.status === "offroute" || nav.rerouting) {
      banner.innerHTML = `<div class="main"><div class="text">${nav.error ? esc(nav.error) : "Off route. Finding a new one…"}</div></div>`;
    } else {
      const lanes = nav.lanes?.length
        ? `<div class="lanes">${nav.lanes.map((l) => `<span class="${l.valid ? "ok" : ""}">${arrowSvg("turn", l.indications[0] === "none" ? "straight" : l.indications[0] ?? "straight", null, 26)}</span>`).join("")}</div>`
        : "";
      const then = nav.then ? `<div class="then">Then ${arrowSvg(nav.then.type, nav.then.modifier, nav.then.exit, 22)}</div>` : "";
      banner.innerHTML = `<div class="icon">${arrowSvg(n.type, n.modifier, n.exit)}</div>` +
        `<div class="main"><div class="dist">${fmtDist(n.distance_m)}</div>` +
        `<div class="text">${n.exit_number ? `<span class="exitno">Exit ${esc(n.exit_number)}</span>` : ""}${esc(n.text || n.instruction)}</div>` +
        (n.sub ? `<div class="sub">${esc(n.sub)}</div>` : "") + `${lanes}</div>${then}`;
    }
    const eta = new Date(nav.eta_ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    bar.querySelector(".eta")!.textContent = nav.status === "arrived" ? "Arrived" : `Arrive ${eta}`;
    bar.querySelector(".left")!.textContent = nav.status === "arrived" ? "" : `${fmtDuration(nav.remaining_s)} · ${fmtDist(nav.remaining_m)}`;
  };
}
