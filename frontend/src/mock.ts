import type { Snapshot } from "./types";

// ?mock=1: generate fake data in the browser so UI work needs no backend.
export function mockStream(onSnap: (s: Snapshot) => void): void {
  const t0 = performance.now();
  setInterval(() => {
    const t = (performance.now() - t0) / 1000;
    const speed = 60 + 40 * Math.sin(t / 8);
    const sig = (value: number, unit: string) => ({ value, unit, age: 0, stale: false });
    onSnap({
      ts: Date.now() / 1000,
      signals: {
        speed: sig(speed, "km/h"),
        rpm: sig(800 + speed * 22, "rpm"),
        coolant: sig(Math.min(92, 20 + t * 2), "C"),
        trans_temp: sig(Math.min(85, 15 + t), "C"),
        voltage: sig(14.1 + 0.1 * Math.sin(t), "V"),
      },
      gps: { lat: 49.12 + 0.01 * Math.sin(t / 30), lon: -122.6 + 0.02 * Math.cos(t / 30), heading: (t * 6) % 360, speed },
      errors: {},
      trip: {
        active: true, running: true, fuel_lph: 1.5 + speed * 0.12,
        instant_l_per_100km: speed > 5 ? (1.5 + speed * 0.12) / speed * 100 : null,
        fuel_left_l: 60, range_km: 470,
        current: { id: 1, start_ts: Date.now() / 1000 - t, max_speed: 100, distance_km: t * 0.02, fuel_l: t * 0.0025,
          moving_s: t, idle_s: 0, l_per_100km: t > 25 ? 12.5 : null, avg_speed: 72 },
        counters: {},
      },
    });
  }, 50);
}
