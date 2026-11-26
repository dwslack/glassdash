import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildBasemap, satelliteUrl, showBasemap } from "./basemap";
import { onSettings, saveSettings, settings } from "./settings";
import { mountNav } from "./nav";
import { mountStreetView } from "./streetview";
import { mountVoice } from "./voice";
import type { Fix, NavState } from "./types";

export async function mountMap(container: HTMLElement, note: HTMLElement, toggle: HTMLButtonElement): Promise<(fix: Fix | null, nav?: NavState | null) => void> {
  const bm = await buildBasemap(settings.esri_key);
  const map = new maplibregl.Map({
    container, style: bm.style, center: [-122.5, 49.1], zoom: 10,
    attributionControl: { compact: true },
  });
  await new Promise<void>((r) => map.on("load", () => r()));

  // Satellite is online only. Note failures so the driver knows why the map went blank.
  let satErrors = 0;
  map.on("error", (e) => { if ((e as { sourceId?: string }).sourceId === "satellite") { satErrors++; render(); } });
  map.on("sourcedata", (e) => { if (e.sourceId === "satellite" && e.isSourceLoaded) satErrors = 0; });

  let key = settings.esri_key;
  const render = () => {
    if (settings.esri_key !== key) {
      key = settings.esri_key;
      (map.getSource("satellite") as maplibregl.RasterTileSource).setTiles([satelliteUrl(key)]);
      satErrors = 0;
    }
    const kind = settings.basemap;
    showBasemap(map, bm, kind);
    toggle.textContent = kind === "satellite" ? "Map" : "Satellite";
    note.textContent =
      kind === "satellite" && satErrors > 3 ? "Satellite needs internet. No connection to the imagery server." :
      kind === "street" && !bm.hasStreet ? "No offline map installed yet (just map-bc). Showing the route only." : "";
  };
  toggle.onclick = () => { saveSettings({ basemap: settings.basemap === "satellite" ? "street" : "satellite" }); };
  onSettings(render);

  // The route line. In the simulator it changes when a different route is picked.
  const loadRoute = async () => {
    try {
      const track = await (await fetch("/api/track")).json();
      const coords = (track.points ?? []).map(([lat, lon]: [number, number]) => [lon, lat]);
      const data = { type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: coords } };
      const src = map.getSource("route") as maplibregl.GeoJSONSource | undefined;
      if (src) src.setData(data);
      else if (coords.length) {
        map.addSource("route", { type: "geojson", data });
        map.addLayer({ id: "route", type: "line", source: "route", paint: { "line-color": "#3fb6ff", "line-width": 4, "line-opacity": 0.8 } });
      }
    } catch { /* no backend (mock mode) */ }
  };
  await loadRoute();
  let routeName: string | undefined;

  const el = document.createElement("div");
  el.innerHTML = `<svg class="truck" viewBox="0 0 20 20"><path d="M10 1 L17 18 L10 14 L3 18 Z" fill="#3fb6ff" stroke="#fff" stroke-width="1.2"/></svg>`;
  const marker = new maplibregl.Marker({ element: el, rotationAlignment: "map" });
  let placed = false;
  let last: [number, number] | null = null;
  mountStreetView(map, document.getElementById("sv-toggle") as HTMLButtonElement, document.getElementById("sv-panel")!, () => last);
  const updateNav = mountNav(map, container.parentElement!, document.getElementById("nav-toggle") as HTMLButtonElement, () => last);
  let following = false;

  mountVoice(container.parentElement!);

  // Free look: any pan, zoom, rotate or tilt by hand stops the map following the truck until Recenter.
  let free = false;
  let lastFix: Fix | null = null;
  let lastNav: NavState | null | undefined = null;
  const recenter = document.createElement("button");
  recenter.id = "recenter";
  recenter.textContent = "Recenter";
  recenter.hidden = true;
  container.parentElement!.appendChild(recenter);
  const setFree = (on: boolean) => {
    free = on;
    recenter.hidden = !on;
    if (!on && lastFix) { following = !(lastNav && lastNav.status !== "arrived"); follow(lastFix, lastNav, true); }
  };
  for (const ev of ["dragstart", "zoomstart", "rotatestart", "pitchstart"] as const) {
    map.on(ev, (e: { originalEvent?: Event }) => { if (e.originalEvent && !free) setFree(true); });
  }
  recenter.onclick = () => setFree(false);
  // Voice commands and other modules drive the map through this event.
  window.addEventListener("glassdash:map", (e) => {
    const action = (e as CustomEvent<string>).detail;
    if (action === "recenter") setFree(false);
    else if (action === "zoom_in" || action === "zoom_out") {
      if (!free) setFree(true);
      map.easeTo({ zoom: map.getZoom() + (action === "zoom_in" ? 1 : -1) });
    }
  });

  function follow(fix: Fix, nav: NavState | null | undefined, now = false) {
    const duration = now ? 600 : undefined;
    if (nav && nav.status !== "arrived") {
      // Navigating: heading-up, tilted, truck low on the screen, zoomed out as speed rises.
      following = true;
      const zoom = fix.speed > 80 ? 14.5 : fix.speed > 45 ? 15.5 : 16.5;
      map.easeTo({ center: [fix.lon, fix.lat], bearing: fix.speed > 3 ? fix.heading : map.getBearing(), pitch: 50, zoom,
        padding: { top: container.clientHeight * 0.45, bottom: 0, left: 0, right: 0 }, duration: duration ?? 250, easing: (t) => t });
    } else if (following) {
      following = false;
      map.easeTo({ center: [fix.lon, fix.lat], bearing: 0, pitch: 0, zoom: 14, padding: { top: 0, bottom: 0, left: 0, right: 0 }, duration: 600 });
    } else map.easeTo({ center: [fix.lon, fix.lat], duration: duration ?? 200, easing: (t) => t });
  }

  return (fix, nav) => {
    updateNav(nav, fix);
    lastNav = nav;
    if (!fix) return;
    lastFix = fix;
    if (fix.route !== undefined && fix.route !== routeName) {
      if (routeName !== undefined) { loadRoute(); map.jumpTo({ center: [fix.lon, fix.lat] }); }
      routeName = fix.route;
    }
    last = [fix.lon, fix.lat];
    marker.setLngLat([fix.lon, fix.lat]).setRotation(fix.heading);
    if (!placed) { marker.addTo(map); placed = true; map.jumpTo({ center: [fix.lon, fix.lat], zoom: 14 }); }
    else if (!free) follow(fix, nav);
  };
}
