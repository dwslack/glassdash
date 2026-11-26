import { driveRange, recent } from "./history";
import { num } from "./fmt";
import { settings } from "./settings";
import type { Snapshot } from "./types";
import { vehicle } from "./vehicle";

// The dash is built from widgets, each drawn to suit its signal rather than one round dial for everything:
// a tach arc with the speed inside it, quiet range bars for temperatures, a status pill for the battery,
// trend lines for economy and the slower engine numbers. Colour appears only when something needs attention.
// Every widget works in any slot; sizes come from CSS container units, so a widget fills whatever box it gets.

export type Level = "ok" | "warn" | "bad" | "cold" | "off";
export interface Widget {
  key: string;
  name: string;
  /** Signal it needs; the picker hides it when the vehicle profile doesn't read that signal. */
  needs?: string;
  /** Builds the widget into el; returns the per-snapshot update. */
  mount(el: HTMLElement): (snap: Snapshot) => void;
}

const sig = (snap: Snapshot, k: string) => {
  const s = snap.signals[k];
  return s && !s.stale ? s.value : undefined;
};
const running = (snap: Snapshot) => (sig(snap, "rpm") ?? 0) > 300;
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const SVGNS = "http://www.w3.org/2000/svg";

function setLevel(el: HTMLElement, level: Level) {
  el.dataset.level = level;
}

/** Points for a sparkline in a 0..300 x 0..100 box; gaps break the line. */
function sparkPath(values: (number | null)[], lo: number, hi: number, slots: number): string {
  let d = "", pen = false;
  const n = Math.max(slots, values.length);
  values.forEach((v, i) => {
    if (v === null) { pen = false; return; }
    const x = ((n - values.length + i) / Math.max(1, n - 1)) * 300;
    const y = 100 - clamp01((v - lo) / (hi - lo || 1)) * 100;
    d += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    pen = true;
  });
  return d;
}

// ---- Speed inside a tach arc: the hero of the Drive and Tow views ----

const A0 = 150, SWEEP = 240; // degrees, 0 = east, clockwise
function polar(cx: number, cy: number, r: number, deg: number) {
  const a = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}
function arc(cx: number, cy: number, r: number, from: number, to: number) {
  const [x0, y0] = polar(cx, cy, r, from), [x1, y1] = polar(cx, cy, r, to);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

const speedTach: Widget = {
  key: "speedtach", name: "Speed + tach", needs: "rpm",
  mount(el) {
    // Ranges come from the vehicle profile (gauges: rpm_max, shift_rpm, redline_rpm).
    const { rpm_max: RPM_MAX, shift_rpm: SHIFT, redline_rpm: REDLINE } = vehicle.gauges;
    const ang = (rpm: number) => A0 + SWEEP * clamp01(rpm / RPM_MAX);
    let ticks = "";
    for (let k = 0; k <= Math.floor(RPM_MAX / 1000); k++) {
      const a = ang(k * 1000);
      const [x0, y0] = polar(100, 100, 74, a), [x1, y1] = polar(100, 100, 84, a), [tx, ty] = polar(100, 100, 63, a);
      ticks += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"/><text x="${tx}" y="${ty + 4}">${k}</text>`;
    }
    el.innerHTML = `<svg class="w-tach" viewBox="0 0 200 168">
      <path class="track" d="${arc(100, 100, 86, A0, A0 + SWEEP)}" pathLength="1000"/>
      <path class="red" d="${arc(100, 100, 92, ang(REDLINE), A0 + SWEEP)}"/>
      <path class="fill" d="${arc(100, 100, 86, A0, A0 + SWEEP)}" pathLength="1000" stroke-dasharray="0 1000"/>
      <g class="ticks">${ticks}</g>
      <text class="speed" x="100" y="112">--</text>
      <text class="unit" x="100" y="132">km/h</text>
      <text class="rpm" x="100" y="160">-- rpm</text>
    </svg>`;
    const fill = el.querySelector<SVGPathElement>(".fill")!, speed = el.querySelector<SVGTextElement>(".speed")!;
    const rpmT = el.querySelector<SVGTextElement>(".rpm")!, root = el.firstElementChild as unknown as HTMLElement;
    return (snap) => {
      const v = sig(snap, "speed"), rpm = sig(snap, "rpm");
      speed.textContent = v === undefined ? "--" : v.toFixed(0);
      rpmT.textContent = rpm === undefined ? "-- rpm" : `${(Math.round(rpm / 50) * 50).toFixed(0)} rpm`;
      const f = rpm === undefined ? 0 : clamp01(rpm / RPM_MAX) * 1000;
      fill.setAttribute("stroke-dasharray", `${f.toFixed(1)} 1000`);
      setLevel(root, rpm === undefined ? "off" : rpm >= REDLINE ? "bad" : rpm >= SHIFT ? "warn" : "ok");
    };
  },
};

// ---- Big numerals ----

const speedBig: Widget = {
  key: "speed", name: "Speed (big numbers)", needs: "speed",
  mount(el) {
    el.innerHTML = `<div class="w-big"><div class="v">--</div><div class="u">km/h</div><div class="sub"></div></div>`;
    const v = el.querySelector<HTMLElement>(".v")!, sub = el.querySelector<HTMLElement>(".sub")!;
    return (snap) => {
      const s = sig(snap, "speed");
      v.textContent = s === undefined ? "--" : s.toFixed(0);
      const r = driveRange("speed");
      sub.textContent = r && r.max > 0 ? `max ${r.max.toFixed(0)} this drive` : "";
    };
  },
};

// ---- Range bars: quiet in the normal band, coloured only outside it ----

interface RangeSpec {
  key: string; name: string; label: string; unit: string; min: number; max: number; decimals?: number;
  normal: () => [number, number];
  level: (v: number, snap: Snapshot) => Level;
  note?: (v: number, level: Level, snap: Snapshot) => string;
}

function rangeWidget(spec: RangeSpec): Widget {
  return {
    key: spec.key, name: spec.name, needs: spec.key,
    mount(el) {
      el.innerHTML = `<div class="w-range">
        <div class="top"><span class="lbl">${spec.label}</span><span class="v">--<small>${spec.unit}</small></span></div>
        <div class="bar"><i class="band"></i><i class="mm"></i><i class="mark"></i></div>
        <div class="note"></div></div>`;
      const root = el.firstElementChild as HTMLElement;
      const v = root.querySelector<HTMLElement>(".v")!, band = root.querySelector<HTMLElement>(".band")!;
      const mm = root.querySelector<HTMLElement>(".mm")!, mark = root.querySelector<HTMLElement>(".mark")!;
      const note = root.querySelector<HTMLElement>(".note")!;
      const pct = (x: number) => `${(clamp01((x - spec.min) / (spec.max - spec.min)) * 100).toFixed(1)}%`;
      return (snap) => {
        const [lo, hi] = spec.normal();
        band.style.left = pct(lo);
        band.style.width = `calc(${pct(hi)} - ${pct(lo)})`;
        const x = sig(snap, spec.key);
        if (x === undefined) { v.firstChild!.textContent = "--"; setLevel(root, "off"); note.textContent = "no reading"; return; }
        v.firstChild!.textContent = x.toFixed(spec.decimals ?? 0);
        mark.style.left = pct(x);
        const r = driveRange(spec.key);
        if (r) { mm.style.left = pct(r.min); mm.style.width = `calc(${pct(r.max)} - ${pct(r.min)})`; }
        const level = spec.level(x, snap);
        setLevel(root, level);
        note.textContent = spec.note?.(x, level, snap) ?? (r ? `${r.min.toFixed(spec.decimals ?? 0)}–${r.max.toFixed(spec.decimals ?? 0)} this drive` : "");
      };
    },
  };
}

const tempNote = (warmName: string) => (v: number, level: Level) =>
  level === "cold" ? "warming up" : level === "bad" ? "too hot: ease off" : level === "warn" ? `${warmName} running hot` : "";

const coolant = rangeWidget({
  key: "coolant", name: "Coolant temp", label: "Coolant", unit: "°C", min: 40, max: 130,
  normal: () => [80, settings.coolant_warn],
  level: (v) => (v >= settings.coolant_bad ? "bad" : v >= settings.coolant_warn ? "warn" : v < 70 ? "cold" : "ok"),
  note: tempNote("engine"),
});
const trans = rangeWidget({
  key: "trans_temp", name: "Trans temp", label: "Trans", unit: "°C", min: 20, max: 140,
  normal: () => [60, settings.trans_warn],
  level: (v) => (v >= settings.trans_bad ? "bad" : v >= settings.trans_warn ? "warn" : v < 40 ? "cold" : "ok"),
  note: tempNote("transmission"),
});
const oilTemp = rangeWidget({
  key: "oil_temp", name: "Oil temp", label: "Oil temp", unit: "°C", min: 40, max: 160,
  normal: () => [80, settings.oil_temp_warn],
  level: (v) => (v >= settings.oil_temp_warn + 10 ? "bad" : v >= settings.oil_temp_warn ? "warn" : v < 60 ? "cold" : "ok"),
  note: tempNote("oil"),
});
const oilPress = rangeWidget({
  key: "oil_pressure", name: "Oil pressure", label: "Oil pressure", unit: "kPa", min: 0, max: 600,
  normal: () => [settings.oil_pressure_min * 1.5, 550],
  level: (v, snap) => (!running(snap) ? "off" : v < settings.oil_pressure_min ? "bad" : v < settings.oil_pressure_min * 1.5 ? "warn" : "ok"),
  note: (v, level) => (level === "off" ? "engine off" : level === "bad" ? "LOW: stop safely" : ""),
});
const intake = rangeWidget({
  key: "intake_temp", name: "Intake air temp", label: "Intake air", unit: "°C", min: -20, max: 80,
  normal: () => [-20, 50],
  level: (v) => (v >= 65 ? "warn" : "ok"),
});

// ---- Battery as a status pill: the voltage only matters when it's wrong ----

const volts: Widget = {
  key: "voltage", name: "Battery / charging", needs: "voltage",
  mount(el) {
    el.innerHTML = `<div class="w-pill"><i class="dot"></i><span class="txt">Battery</span><span class="v">--</span></div>`;
    const root = el.firstElementChild as HTMLElement;
    const txt = root.querySelector<HTMLElement>(".txt")!, v = root.querySelector<HTMLElement>(".v")!;
    return (snap) => {
      const x = sig(snap, "voltage");
      if (x === undefined) { txt.textContent = "Battery"; v.textContent = "--"; setLevel(root, "off"); return; }
      v.textContent = `${x.toFixed(1)} V`;
      const run = running(snap);
      const [t, l]: [string, Level] =
        x >= settings.volts_high ? ["Overcharging", "bad"]
        : run && x < settings.volts_bad ? ["Not charging", "bad"]
        : run && x < settings.volts_low ? ["Charging low", "warn"]
        : run ? ["Charging", "ok"]
        : x < 12.2 ? ["Battery low", "warn"] : ["Battery", "ok"];
      txt.textContent = t;
      setLevel(root, l);
    };
  },
};

// ---- Fuel tank and range ----

const fuel: Widget = {
  key: "fuel", name: "Fuel and range", needs: "fuel_level",
  mount(el) {
    el.innerHTML = `<div class="w-fuel"><div class="top"><span class="lbl">Fuel</span><span class="v">--</span></div>
      <div class="tank"><i class="lvl"></i><i class="q" style="left:25%"></i><i class="q" style="left:50%"></i><i class="q" style="left:75%"></i></div>
      <div class="note"></div></div>`;
    const root = el.firstElementChild as HTMLElement;
    const v = root.querySelector<HTMLElement>(".v")!, lvl = root.querySelector<HTMLElement>(".lvl")!, note = root.querySelector<HTMLElement>(".note")!;
    return (snap) => {
      const pct = sig(snap, "fuel_level");
      const range = snap.trip?.range_km;
      v.textContent = range != null ? `${range.toFixed(0)} km` : pct !== undefined ? `${pct.toFixed(0)}%` : "--";
      lvl.style.width = `${clamp01((pct ?? 0) / 100) * 100}%`;
      note.textContent = pct === undefined ? "no reading" : `${pct.toFixed(0)}% · ${num(snap.trip?.fuel_left_l, 0)} L left`;
      setLevel(root, pct === undefined ? "off" : pct < 7 ? "bad" : pct < 15 ? "warn" : "ok");
    };
  },
};

// ---- Economy: what it is now, against this drive's average, over the last 5 minutes ----

const economy: Widget = {
  key: "economy", name: "Fuel economy trend", needs: "maf",
  mount(el) {
    el.innerHTML = `<div class="w-trend">
      <div class="top"><span class="lbl">L/100 km</span><span class="v">--</span></div>
      <svg viewBox="0 0 300 100" preserveAspectRatio="none"><path class="avg"/><path class="line"/></svg>
      <div class="note"></div></div>`;
    const root = el.firstElementChild as HTMLElement;
    const v = root.querySelector<HTMLElement>(".v")!, line = root.querySelector<SVGPathElement>(".line")!;
    const avgP = root.querySelector<SVGPathElement>(".avg")!, note = root.querySelector<HTMLElement>(".note")!;
    const HI = 40;
    return (snap) => {
      const t = snap.trip;
      if (!t) { v.textContent = "--"; note.textContent = "no trip data"; return; }
      v.textContent = t.instant_l_per_100km !== null ? num(t.instant_l_per_100km) : t.running ? `${num(t.fuel_lph)} L/h` : "--";
      line.setAttribute("d", sparkPath(recent("economy", 300).map((x) => (x === null ? null : Math.min(x, HI))), 0, HI, 300));
      const avg = t.current?.l_per_100km;
      avgP.setAttribute("d", avg != null ? `M0 ${100 - clamp01(avg / HI) * 100}H300` : "");
      note.textContent = avg != null ? `drive avg ${num(avg)} · last 5 min` : "last 5 min";
    };
  },
};

// ---- Bar history for load and throttle: a minute of bars shows how hard the truck is working ----

function barsWidget(key: string, name: string, label: string): Widget {
  return {
    key, name, needs: key,
    mount(el) {
      el.innerHTML = `<div class="w-bars"><div class="top"><span class="lbl">${label}</span><span class="v">--<small>%</small></span></div>
        <svg viewBox="0 0 300 100" preserveAspectRatio="none"></svg><div class="note"></div></div>`;
      const root = el.firstElementChild as HTMLElement;
      const v = root.querySelector<HTMLElement>(".v")!, svg = root.querySelector("svg")!, note = root.querySelector<HTMLElement>(".note")!;
      const N = 60;
      const rects = Array.from({ length: N }, (_, i) => {
        const r = document.createElementNS(SVGNS, "rect");
        r.setAttribute("x", String((i * 300) / N + 0.6));
        r.setAttribute("width", String(300 / N - 1.2));
        svg.appendChild(r);
        return r;
      });
      return (snap) => {
        const x = sig(snap, key);
        v.firstChild!.textContent = x === undefined ? "--" : x.toFixed(0);
        const hist = recent(key, N);
        const pad = N - hist.length;
        let sum = 0, n = 0;
        rects.forEach((r, i) => {
          const h = i < pad ? null : hist[i - pad];
          const y = h === null ? 0 : clamp01(h / 100) * 100;
          r.setAttribute("y", String(100 - y));
          r.setAttribute("height", String(y));
          r.classList.toggle("hi", h !== null && h >= 80);
          if (h !== null) { sum += h; n++; }
        });
        note.textContent = n ? `avg ${(sum / n).toFixed(0)}% last minute` : "";
        setLevel(root, x === undefined ? "off" : "ok");
      };
    },
  };
}

// ---- A number with a 5-minute trend line, for the slower engine readings ----

function numberWidget(key: string, name: string, label: string, unit: string, decimals = 0): Widget {
  return {
    key, name, needs: key,
    mount(el) {
      el.innerHTML = `<div class="w-num"><div class="top"><span class="lbl">${label}</span><span class="v">--<small>${unit}</small></span></div>
        <svg viewBox="0 0 300 100" preserveAspectRatio="none"><path class="line"/></svg><div class="note"></div></div>`;
      const root = el.firstElementChild as HTMLElement;
      const v = root.querySelector<HTMLElement>(".v")!, line = root.querySelector<SVGPathElement>(".line")!, note = root.querySelector<HTMLElement>(".note")!;
      return (snap) => {
        const x = sig(snap, key);
        v.firstChild!.textContent = x === undefined ? "--" : x.toFixed(decimals);
        const h = recent(key, 300);
        const vals = h.filter((y): y is number => y !== null);
        if (vals.length > 1) {
          let lo = Math.min(...vals), hi = Math.max(...vals);
          if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
          line.setAttribute("d", sparkPath(h, lo, hi, 300));
          note.textContent = `${lo.toFixed(decimals)}–${hi.toFixed(decimals)} last 5 min`;
        } else { line.setAttribute("d", ""); note.textContent = ""; }
        setLevel(root, x === undefined ? "off" : "ok");
      };
    },
  };
}

// ---- This drive, and a clock ----

const drive: Widget = {
  key: "drive", name: "This drive",
  mount(el) {
    el.innerHTML = `<div class="w-trip"><div><b data-k="d">--</b><small>km</small></div><div><b data-k="t">--</b><small>time</small></div><div><b data-k="e">--</b><small>L/100</small></div></div>`;
    const q = (k: string) => el.querySelector<HTMLElement>(`[data-k="${k}"]`)!;
    const d = q("d"), t = q("t"), e = q("e");
    return (snap) => {
      const c = snap.trip?.current;
      d.textContent = num(c?.distance_km);
      const s = c ? c.moving_s + c.idle_s : null;
      t.textContent = s == null ? "--" : s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : `${Math.floor(s / 60)}m`;
      e.textContent = num(c?.l_per_100km);
    };
  },
};

const clock: Widget = {
  key: "clock", name: "Clock and outside temp",
  mount(el) {
    el.innerHTML = `<div class="w-clock"><div class="v">--</div><div class="note"></div></div>`;
    const v = el.querySelector<HTMLElement>(".v")!, note = el.querySelector<HTMLElement>(".note")!;
    return (snap) => {
      v.textContent = new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
      const a = sig(snap, "ambient_temp");
      note.textContent = a === undefined ? "" : `${a.toFixed(0)}°C outside${a <= 2 ? " · ice possible" : ""}`;
      setLevel(el.firstElementChild as HTMLElement, a !== undefined && a <= 2 ? "cold" : "ok");
    };
  },
};

export const WIDGETS: Widget[] = [
  speedTach, speedBig, coolant, trans, oilTemp, oilPress, volts, fuel, economy,
  barsWidget("engine_load", "Engine load (bars)", "Load"), barsWidget("throttle", "Throttle (bars)", "Throttle"),
  numberWidget("rpm", "RPM with trend", "Engine", "rpm"),
  intake, numberWidget("timing", "Timing advance", "Timing", "°"), numberWidget("maf", "Mass airflow", "MAF", "g/s", 1),
  numberWidget("baro", "Barometric pressure", "Baro", "kPa"), drive, clock,
];
export const widget = (key: string) => WIDGETS.find((w) => w.key === key);
