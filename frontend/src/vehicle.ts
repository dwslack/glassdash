import { onSettings } from "./settings";

// The vehicle profile dashd is using (vehicles/…yaml): gauge ranges and which signals exist.
export interface VehicleInfo {
  id: string; name: string;
  vehicle: { make?: string; model?: string; years?: [number, number]; engine?: string; transmission?: string };
  gauges: { rpm_max: number; shift_rpm: number; redline_rpm: number; speed_max: number; tank_l?: number };
  chain: string[];
  signals: Record<string, { unit: string; verified: { by: string; date: string } | null }>;
  body: { bus: { type: string; bitrate: number } | null; frames: Record<string, { signals: string[]; verified: { by: string; date: string } | null }> };
}

// Until dashd answers (or in ?mock=1): the project's own truck.
export let vehicle: VehicleInfo = {
  id: "gm/silverado-1500-2011-5.3", name: "Chevrolet Silverado 1500 2011, 5.3 L V8", vehicle: {}, chain: [],
  gauges: { rpm_max: 6000, shift_rpm: 4800, redline_rpm: 5600, speed_max: 160 }, signals: {}, body: { bus: null, frames: {} },
};
const listeners: ((v: VehicleInfo) => void)[] = [];
export function onVehicle(fn: (v: VehicleInfo) => void): void { listeners.push(fn); fn(vehicle); }

export async function loadVehicle(): Promise<void> {
  try {
    const r = await fetch("/api/vehicle");
    if (!r.ok) return;
    vehicle = await r.json();
    listeners.forEach((fn) => fn(vehicle));
  } catch { /* mock mode */ }
}

/** True when the profile reads this signal (or nothing is known yet, so don't hide anything). */
export const hasSignal = (key: string) => Object.keys(vehicle.signals).length === 0 || key in vehicle.signals;

let shown = "";
onSettings((s) => { if (s.vehicle && s.vehicle !== shown) { shown = s.vehicle; loadVehicle(); } });
