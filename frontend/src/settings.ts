// Settings live on dashd (data/settings.json) so the Pi screen and the iPad agree.
export interface Settings {
  vehicle: string;
  tank_l: number; fuel_cal: number;
  night: "auto" | "on" | "off"; night_brightness: number; alert_sound: boolean; voice: boolean;
  coolant_warn: number; coolant_bad: number; trans_warn: number; trans_bad: number;
  volts_low: number; volts_bad: number; volts_high: number;
  oil_pressure_min: number; oil_temp_warn: number;
  basemap: "street" | "satellite"; esri_key: string; mapillary_token: string;
  anthropic_key: string; assistant_model: string;
  dash_views: Record<string, { hero: string; tiles: string[] }>; auto_tow_view: boolean;
}

export const DEFAULTS: Settings = {
  vehicle: "gm/silverado-3500hd-2011-6.0",
  tank_l: 136, fuel_cal: 1, night: "auto", night_brightness: 0.55, alert_sound: true, voice: true,
  coolant_warn: 108, coolant_bad: 115, trans_warn: 110, trans_bad: 125,
  volts_low: 13.2, volts_bad: 12.5, volts_high: 15.2, oil_pressure_min: 70, oil_temp_warn: 135,
  basemap: "street", esri_key: "", mapillary_token: "", anthropic_key: "", assistant_model: "claude-haiku-5-5",
  dash_views: {}, auto_tow_view: true,
};

export let settings: Settings = { ...DEFAULTS };
const listeners: ((s: Settings) => void)[] = [];

export function onSettings(fn: (s: Settings) => void): void { listeners.push(fn); fn(settings); }

function apply(s: Settings) { settings = s; listeners.forEach((fn) => fn(s)); }

export async function loadSettings(): Promise<void> {
  try {
    const res = await fetch("/api/settings");
    if (res.ok) apply({ ...DEFAULTS, ...(await res.json()) });
  } catch { /* mock mode: defaults */ }
}

export async function saveSettings(changes: Partial<Settings>): Promise<string | null> {
  try {
    const res = await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
    const body = await res.json().catch(() => null);
    if (!body) return `the dash computer didn't answer (HTTP ${res.status}). Is just sim / just run still running? If you just ran just https-cert, restart both it and just dev.`;
    if (!res.ok) return body.detail ?? res.statusText;
    apply({ ...DEFAULTS, ...body });
    return null;
  } catch (e) { return (e as Error).message; }
}

// Other screens may change settings; pick that up now and then.
setInterval(loadSettings, 30_000);
