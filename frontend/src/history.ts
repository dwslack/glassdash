import type { Snapshot } from "./types";

// Recent history for trend lines, and min/max for the current drive.
// One sample per second per signal, kept for 15 minutes. Gaps (no reading) are null.
const KEEP = 900;
const series = new Map<string, (number | null)[]>();
const extremes = new Map<string, { min: number; max: number }>();
let lastSample = 0;
let driveId: number | null | undefined;

function push(key: string, v: number | null) {
  let s = series.get(key);
  if (!s) { s = []; series.set(key, s); }
  s.push(v);
  if (s.length > KEEP) s.shift();
}

export function record(snap: Snapshot): void {
  const id = snap.trip?.current?.id ?? null;
  if (id !== driveId) { driveId = id; extremes.clear(); }
  for (const [k, s] of Object.entries(snap.signals)) {
    if (s.stale) continue;
    const e = extremes.get(k);
    if (!e) extremes.set(k, { min: s.value, max: s.value });
    else { e.min = Math.min(e.min, s.value); e.max = Math.max(e.max, s.value); }
  }
  const now = snap.ts;
  if (now - lastSample < 1) return;
  lastSample = now;
  const keys = new Set([...series.keys(), ...Object.keys(snap.signals)]);
  for (const k of keys) {
    if (k === "economy") continue;
    const s = snap.signals[k];
    push(k, s && !s.stale ? s.value : null);
  }
  push("economy", snap.trip?.instant_l_per_100km ?? null);
}

/** The last `seconds` samples of a signal, oldest first. */
export function recent(key: string, seconds: number): (number | null)[] {
  const s = series.get(key) ?? [];
  return s.slice(Math.max(0, s.length - seconds));
}

/** Lowest and highest reading this drive (or since the page loaded, with no drive). */
export function driveRange(key: string): { min: number; max: number } | undefined {
  return extremes.get(key);
}
