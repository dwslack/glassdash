export const num = (v: number | null | undefined, d = 1, dash = "--") =>
  v === null || v === undefined || Number.isNaN(v) ? dash : v.toFixed(d);

export function dur(s: number): string {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
}

export function when(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) + " " +
    d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
