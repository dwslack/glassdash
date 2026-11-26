import { isNight } from "./night";
import { settings as cfg } from "./settings";
import type { Snapshot } from "./types";

export type Level = "warn" | "bad";
export interface Alert { id: string; level: Level; text: string; }

// Thresholds come from Settings (defaults are starting points for a 5.3 L / 6L80; tune once real data is in).
// Electrical and oil-pressure rules only apply with the engine running (key-on/engine-off reads low by design).
const blinkerSince: { L?: number; R?: number } = {};

export function evaluate(s: Snapshot): Alert[] {
  const v = (k: string) => { const x = s.signals[k]; return x && !x.stale ? x.value : undefined; };
  const out: Alert[] = [];
  const rpm = v("rpm");
  const running = rpm !== undefined && rpm > 300;
  const add = (id: string, level: Level, text: string) => out.push({ id, level, text });

  const ct = v("coolant");
  if (ct !== undefined && ct >= cfg.coolant_bad) add("coolant", "bad", `Engine overheating: coolant ${ct.toFixed(0)}°C. Pull over and idle or shut off.`);
  else if (ct !== undefined && ct >= cfg.coolant_warn) add("coolant", "warn", `Coolant hot: ${ct.toFixed(0)}°C`);

  const tt = v("trans_temp");
  if (tt !== undefined && tt >= cfg.trans_bad) add("trans", "bad", `Transmission overheating: ${tt.toFixed(0)}°C. Ease off and let it cool.`);
  else if (tt !== undefined && tt >= cfg.trans_warn) add("trans", "warn", `Transmission hot: ${tt.toFixed(0)}°C`);

  const volts = v("voltage");
  if (running && volts !== undefined) {
    if (volts < cfg.volts_bad) add("volts", "bad", `Not charging: ${volts.toFixed(1)} V with the engine running. Check alternator/belt.`);
    else if (volts < cfg.volts_low) add("volts", "warn", `Low charging voltage: ${volts.toFixed(1)} V`);
    else if (volts > cfg.volts_high) add("volts", "warn", `High voltage: ${volts.toFixed(1)} V (regulator?)`);
  }

  const op = v("oil_pressure");
  if (running && op !== undefined && op < cfg.oil_pressure_min) add("oil", "bad", `Low oil pressure: ${op.toFixed(0)} kPa. Stop the engine. (Unverified PID: confirm on the cluster gauge.)`);

  const ot = v("oil_temp");
  if (ot !== undefined && ot >= cfg.oil_temp_warn) add("oiltemp", "warn", `Oil hot: ${ot.toFixed(0)}°C`);

  // Body bus (doors, belts, lights). Placeholder frame ids until mapped on the truck.
  const b = s.body;
  const speed = v("speed") ?? s.gps?.speed ?? 0;
  if (b && speed > 5) {
    const open = (["door_driver", "door_passenger", "door_rear_left", "door_rear_right"] as const).filter((k) => b[k]);
    if (open.length) add("door", "bad", `Door open while moving (${open.map((k) => k.replace("door_", "").replace("_", " ")).join(", ")})`);
    if (b.tailgate) add("tailgate", "warn", "Tailgate open");
    if (b.hood) add("hood", "bad", "Hood open while moving");
    if (b.parking_brake) add("pbrake", "warn", "Parking brake on while moving");
    if (speed > 15 && b.belt_driver === false) add("belt", "warn", "Driver seatbelt unbuckled");
    if (isNight() && b.low_beam === false) add("lights", "warn", "Headlights are off");
  }
  if (b && speed > 50 && (b.turn_left || b.turn_right) && !b.hazard) {
    const k = b.turn_left ? "L" : "R";
    blinkerSince[k] ??= Date.now();
    if (Date.now() - blinkerSince[k]! > 30_000) add("blinker", "warn", `${k === "L" ? "Left" : "Right"} turn signal still on`);
  } else { blinkerSince.L = blinkerSince.R = undefined; }

  if (s.mil?.mil) add("mil", "warn", `Check engine light is on (${s.mil.count} code${s.mil.count === 1 ? "" : "s"}). See Diagnostics.`);
  return out;
}

// Banners above the footer on every page. Tapping hides a warning for 10 minutes; "bad" alerts can't be hidden.
export function mountAlerts(root: HTMLElement): (s: Snapshot) => void {
  const snoozed = new Map<string, number>();
  let last = "";
  let badBefore = new Set<string>();
  return (s) => {
    const now = Date.now();
    const onDiag = location.hash === "#diag"; // the check-engine alert is redundant on its own page
    const active = evaluate(s).filter((a) => (a.level === "bad" || (snoozed.get(a.id) ?? 0) < now) && !(onDiag && a.id === "mil"));
    const bad = new Set(active.filter((a) => a.level === "bad").map((a) => a.id));
    if ([...bad].some((id) => !badBefore.has(id))) chime();
    badBefore = bad;
    const key = active.map((a) => a.id + a.level + a.text).join("|");
    if (key === last) return;
    last = key;
    root.innerHTML = "";
    for (const a of active) {
      const el = document.createElement("div");
      el.className = `alert ${a.level}`;
      el.textContent = a.text;
      if (a.level === "warn") el.onclick = () => { snoozed.set(a.id, Date.now() + 10 * 60_000); last = ""; el.remove(); };
      root.appendChild(el);
    }
  };
}

// Three short beeps when a critical alert first appears. iPad Safari only allows audio after
// a touch, so the AudioContext is unlocked on the first tap anywhere.
let audio: AudioContext | null = null;
const unlock = () => { audio ??= new AudioContext(); audio.resume(); };
window.addEventListener("pointerdown", unlock, { once: true });

function chime() {
  if (!cfg.alert_sound || !audio) return;
  const t0 = audio.currentTime;
  for (let i = 0; i < 3; i++) {
    const osc = audio.createOscillator(), gain = audio.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, t0 + i * 0.25);
    gain.gain.exponentialRampToValueAtTime(0.4, t0 + i * 0.25 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.25 + 0.18);
    osc.connect(gain).connect(audio.destination);
    osc.start(t0 + i * 0.25);
    osc.stop(t0 + i * 0.25 + 0.2);
  }
}
