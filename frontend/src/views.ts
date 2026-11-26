import { record } from "./history";
import { onSettings, saveSettings, settings } from "./settings";
import type { Snapshot } from "./types";
import { hasSignal, onVehicle } from "./vehicle";
import { WIDGETS, widget } from "./widgets";

// Dash views you swipe between on the left panel (or tap the dots, or say "show tow view").
// Each view is a layout plus a hero widget and a set of tiles. Long-press any widget to swap what that spot shows;
// changes are saved on dashd so the iPad and the Pi screen agree. The last view is remembered on this screen.

export type Layout = "split" | "full" | "map";
export interface ViewDef { id: string; name: string; layout: Layout; hero: string; tiles: string[]; }

export const VIEWS: ViewDef[] = [
  { id: "drive", name: "Drive", layout: "split", hero: "speedtach", tiles: ["coolant", "trans_temp", "fuel", "voltage", "economy", "drive"] },
  { id: "tow", name: "Tow", layout: "split", hero: "speedtach", tiles: ["trans_temp", "coolant", "engine_load", "oil_temp", "economy", "voltage"] },
  { id: "economy", name: "Economy", layout: "split", hero: "economy", tiles: ["speed", "fuel", "drive", "throttle", "clock", "voltage"] },
  { id: "engine", name: "All gauges", layout: "full", hero: "speedtach",
    tiles: ["coolant", "trans_temp", "oil_temp", "oil_pressure", "voltage", "fuel", "engine_load", "throttle", "intake_temp", "timing", "maf", "economy"] },
  { id: "map", name: "Big map", layout: "map", hero: "speed", tiles: ["coolant", "fuel"] },
];

const VIEW_KEY = "glassdash.view";
const store = {
  get(): string | null { try { return localStorage.getItem(VIEW_KEY); } catch { return null; } },
  set(v: string) { try { localStorage.setItem(VIEW_KEY, v); } catch { /* private mode: not remembered */ } },
};

/** A view with the user's saved swaps applied; unknown widget keys fall back to the default. */
export function resolved(v: ViewDef): ViewDef {
  const custom = settings.dash_views?.[v.id];
  if (!custom) return v;
  const ok = (k: unknown, d: string) => (typeof k === "string" && widget(k) ? k : d);
  return { ...v, hero: ok(custom.hero, v.hero), tiles: v.tiles.map((d, i) => ok(custom.tiles?.[i], d)) };
}

export function mountViews(app: HTMLElement, left: HTMLElement, toast: (text: string) => void) {
  left.innerHTML = `<div id="dashview"></div><div id="dots" role="tablist"></div>`;
  const host = left.querySelector<HTMLElement>("#dashview")!;
  const dots = left.querySelector<HTMLElement>("#dots")!;
  dots.innerHTML = VIEWS.map((v) => `<button role="tab" data-v="${v.id}" aria-label="${v.name} view"><i></i></button>`).join("");
  let index = Math.max(0, VIEWS.findIndex((v) => v.id === store.get()));
  let updaters: ((s: Snapshot) => void)[] = [];
  let last: Snapshot | null = null;
  let shownKey = "";

  function render() {
    const v = resolved(VIEWS[index]);
    const key = JSON.stringify(v);
    if (key === shownKey) return;
    shownKey = key;
    app.dataset.layout = v.layout;
    host.className = `dv dv-${v.layout} dv-n${v.tiles.length}`;
    host.innerHTML = "";
    updaters = [];
    const add = (k: string, slot: number) => {
      const el = document.createElement("div");
      el.className = "slot" + (slot < 0 ? " hero" : "");
      el.dataset.slot = String(slot);
      host.appendChild(el);
      const inner = document.createElement("div");
      inner.className = "w";
      el.appendChild(inner);
      const w = widget(k)!;
      updaters.push(w.mount(inner));
    };
    add(v.hero, -1);
    v.tiles.forEach((k, i) => add(k, i));
    dots.querySelectorAll<HTMLButtonElement>("button").forEach((b, i) => b.classList.toggle("on", i === index));
    if (last) updaters.forEach((u) => u(last!));
    window.dispatchEvent(new Event("resize")); // the map's box changes with the layout
  }

  function go(i: number, announce = true) {
    const n = (i + VIEWS.length) % VIEWS.length;
    if (n === index && shownKey) return;
    index = n;
    store.set(VIEWS[index].id);
    render();
    if (announce) toast(VIEWS[index].name);
  }

  dots.onclick = (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (b) go(VIEWS.findIndex((v) => v.id === b.dataset.v));
  };
  window.addEventListener("keydown", (e) => {
    if ((e.target as HTMLElement).closest("input, textarea, select") || !app.offsetParent) return;
    if (e.key === "ArrowRight") go(index + 1);
    if (e.key === "ArrowLeft") go(index - 1);
  });
  window.addEventListener("glassdash:view", (e) => {
    const id = (e as CustomEvent<string>).detail;
    const i = VIEWS.findIndex((v) => v.id === id);
    if (i >= 0) go(i);
  });

  // Swipe left/right on the gauge panel; long-press a widget to change it.
  let start: { x: number; y: number; t: number; id: number } | null = null;
  let pressTimer: number | undefined;
  left.addEventListener("pointerdown", (e) => {
    if ((e.target as HTMLElement).closest("button, input, #picker")) return;
    start = { x: e.clientX, y: e.clientY, t: performance.now(), id: e.pointerId };
    const slot = (e.target as HTMLElement).closest<HTMLElement>(".slot");
    clearTimeout(pressTimer);
    if (slot) pressTimer = window.setTimeout(() => { start = null; pick(Number(slot.dataset.slot)); }, 650);
  });
  left.addEventListener("pointermove", (e) => {
    if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 12) clearTimeout(pressTimer);
  });
  const end = (e: PointerEvent) => {
    clearTimeout(pressTimer);
    if (!start || e.pointerId !== start.id) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y, dt = performance.now() - start.t;
    start = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy) && dt < 800) go(index + (dx < 0 ? 1 : -1));
  };
  left.addEventListener("pointerup", end);
  left.addEventListener("pointercancel", () => { clearTimeout(pressTimer); start = null; });
  left.addEventListener("contextmenu", (e) => e.preventDefault()); // long-press on iPad otherwise opens a menu

  // The picker: which widget goes in this spot.
  function pick(slot: number) {
    const base = VIEWS[index], cur = resolved(base);
    const current = slot < 0 ? cur.hero : cur.tiles[slot];
    const sheet = document.createElement("div");
    sheet.id = "picker";
    sheet.innerHTML = `<div class="sheet"><div class="hd"><b>Show here</b><span>${base.name} view</span></div>
      <div class="grid">${WIDGETS.filter((w) => !w.needs || hasSignal(w.needs) || w.key === current).map((w) => `<button data-k="${w.key}" class="${w.key === current ? "on" : ""}">${w.name}</button>`).join("")}</div>
      <div class="ft"><button data-act="reset">Reset ${base.name} view</button><button data-act="close">Done</button></div></div>`;
    document.body.appendChild(sheet);
    sheet.onclick = async (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (e.target === sheet || b?.dataset.act === "close") { sheet.remove(); return; }
      if (!b) return;
      const views = { ...(settings.dash_views ?? {}) };
      if (b.dataset.act === "reset") delete views[base.id];
      else {
        const next = { hero: cur.hero, tiles: [...cur.tiles] };
        if (slot < 0) next.hero = b.dataset.k!; else next.tiles[slot] = b.dataset.k!;
        views[base.id] = next;
      }
      sheet.remove();
      const err = await saveSettings({ dash_views: views });
      if (err) toast(`Not saved: ${err}`);
    };
  }

  onSettings(() => render());
  onVehicle(() => { shownKey = ""; render(); }); // tach ranges are drawn at mount

  // Towing: when the transmission gets hot and it isn't on screen, bring up the Tow view (once, until it cools).
  let towArmed = true;
  function autoTow(s: Snapshot) {
    const t = s.signals.trans_temp;
    if (!t || t.stale) return;
    if (t.value < settings.trans_warn - 8) { towArmed = true; return; }
    if (!towArmed || !settings.auto_tow_view || t.value < settings.trans_warn) return;
    towArmed = false;
    const v = resolved(VIEWS[index]);
    if (v.layout !== "map" && (v.hero === "trans_temp" || v.tiles.includes("trans_temp"))) return;
    go(VIEWS.findIndex((x) => x.id === "tow"), false);
    toast(`Trans ${t.value.toFixed(0)}°C: switched to Tow view`);
  }

  render();
  return (s: Snapshot) => {
    last = s;
    record(s);
    autoTow(s);
    for (const u of updaters) u(s);
  };
}
