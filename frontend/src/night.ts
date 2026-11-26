import { onSettings, settings } from "./settings";
import type { Fix } from "./types";

// Sun altitude in degrees (NOAA-style approximation, good to well under a degree).
export function sunAltitude(lat: number, lon: number, date: Date): number {
  const rad = Math.PI / 180;
  const d = date.getTime() / 86400000 + 2440587.5 - 2451545.0; // days since J2000
  const g = (357.529 + 0.98560028 * d) * rad;
  const q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * rad;
  const e = (23.439 - 0.00000036 * d) * rad;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = (18.697374558 + 24.06570982441908 * d) % 24;
  const ha = ((gmst * 15 + lon) * rad) - ra;
  return Math.asin(Math.sin(lat * rad) * Math.sin(dec) + Math.cos(lat * rad) * Math.cos(dec) * Math.cos(ha)) / rad;
}

let nightNow = false;
export const isNight = () => nightNow;

// Night mode: a dimming overlay plus a warmer, lower-contrast palette (class on <body>).
// The overlay is cheaper on an iPad than a CSS filter over the WebGL map.
export function mountNight(overlay: HTMLElement): (fix: Fix | null) => void {
  let lastFix: Fix | null = null;
  const update = () => {
    let night = settings.night === "on";
    if (settings.night === "auto") {
      // Without a GPS fix, fall back to Lower Mainland coordinates.
      const lat = lastFix?.lat ?? 49.1, lon = lastFix?.lon ?? -122.6;
      night = sunAltitude(lat, lon, new Date()) < -3; // civil dusk-ish: headlights on
    }
    nightNow = night;
    document.body.classList.toggle("night", night);
    overlay.style.opacity = night ? String(1 - settings.night_brightness) : "0";
  };
  onSettings(update);
  setInterval(update, 60_000);
  let haveFix = false;
  return (fix) => {
    if (!fix) return;
    lastFix = fix;
    if (!haveFix) { haveFix = true; update(); } // re-evaluate once we know where we are
  };
}
