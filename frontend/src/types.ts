export interface Signal { value: number; unit: string; age: number; stale: boolean; }
export interface Fix { lat: number; lon: number; heading: number; speed: number; source?: string; route?: string; }

export interface TripTotals {
  distance_km: number; fuel_l: number; moving_s: number; idle_s: number;
  l_per_100km: number | null; avg_speed: number | null;
}
export interface TripLive {
  active: boolean; running: boolean; fuel_lph: number;
  instant_l_per_100km: number | null; fuel_left_l: number | null; range_km: number | null;
  current: (TripTotals & { id: number; start_ts: number; max_speed: number }) | null;
  counters: Record<string, TripTotals & { since_ts: number }>;
}
export interface TripRecord extends TripTotals {
  id: number; start_ts: number; end_ts: number | null; max_speed: number; track?: [number, number][];
}

export interface Snapshot {
  ts: number; signals: Record<string, Signal>; gps: Fix | null; errors: Record<string, number>;
  trip?: TripLive | null;
  mil?: { mil: boolean; count: number } | null;
  body?: Body | null;
  nav?: NavState | null;
}

export interface NavState {
  status: "navigating" | "offroute" | "arrived";
  dest: { lat: number; lon: number; name?: string };
  road: string;
  next: { type: string; modifier: string; exit: number | null; exit_number: string; text: string; sub: string; instruction: string; distance_m: number };
  then: { type: string; modifier: string; exit: number | null } | null;
  lanes: { indications: string[]; valid: boolean }[] | null;
  remaining_m: number; remaining_s: number; eta_ts: number; off_m: number;
  speak: { id: number; text: string };
  route_id: number; rerouting: boolean; error: string;
}

export interface Body {
  door_driver?: boolean; door_passenger?: boolean; door_rear_left?: boolean; door_rear_right?: boolean;
  tailgate?: boolean; hood?: boolean; locked?: boolean;
  parking_lights?: boolean; low_beam?: boolean; high_beam?: boolean; fog?: boolean;
  turn_left?: boolean; turn_right?: boolean; hazard?: boolean; brake?: boolean; reverse?: boolean;
  belt_driver?: boolean; belt_passenger?: boolean; passenger_seated?: boolean; parking_brake?: boolean;
  confirmed?: boolean;
}

export interface PidInfo { name: string; mode: number; pid: number; ecu: number; unit: string; hz: number; confirmed: boolean; }
export interface Dtc { code: string; desc: string; }
export interface DtcRead {
  ts: number; mil: { mil: boolean; count: number } | null;
  ecus: Record<string, Record<"stored" | "pending" | "permanent", Dtc[] | null>>;
  cleared?: Record<string, boolean>;
}
