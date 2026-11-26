import maplibregl from "maplibre-gl";
import { buildBasemap, showBasemap } from "./basemap";
import { dur, num, when } from "./fmt";
import { settings } from "./settings";
import type { Snapshot, TripRecord, TripTotals } from "./types";

// Trips page: Trip A/B counters, a log of drives, and the selected drive's route.
export function mountTrips(root: HTMLElement): { show: () => void; update: (s: Snapshot) => void } {
  root.innerHTML = `
    <div class="trips-side">
      <div class="counters"></div>
      <table class="trip-list">
        <thead><tr><th>Drive</th><th>km</th><th>time</th><th>L/100</th><th>L</th></tr></thead>
        <tbody></tbody>
      </table>
      <p class="empty" hidden>No drives yet. One is logged each time the engine runs and the truck moves.</p>
    </div>
    <div class="trip-detail">
      <div class="trip-map"></div>
      <div class="trip-facts"></div>
    </div>`;
  const counters = root.querySelector<HTMLElement>(".counters")!;
  const tbody = root.querySelector("tbody")!;
  const empty = root.querySelector<HTMLElement>(".empty")!;
  const facts = root.querySelector<HTMLElement>(".trip-facts")!;
  const mapEl = root.querySelector<HTMLElement>(".trip-map")!;
  let map: maplibregl.Map | null = null;
  let selected: number | null = null;

  const counterCard = (name: string) => {
    const el = document.createElement("div");
    el.className = "counter";
    el.innerHTML = `<div class="head"><b>Trip ${name}</b><button>Reset</button></div><div class="body"></div>`;
    el.querySelector("button")!.onclick = async () => {
      if (!confirm(`Reset Trip ${name}?`)) return;
      await fetch(`/api/counters/${name}/reset`, { method: "POST" });
    };
    counters.appendChild(el);
    return el.querySelector<HTMLElement>(".body")!;
  };
  const cards: Record<string, HTMLElement> = { A: counterCard("A"), B: counterCard("B") };

  const totals = (t: TripTotals) =>
    `<span><b>${num(t.distance_km)}</b> km</span><span><b>${num(t.l_per_100km)}</b> L/100km</span>` +
    `<span><b>${num(t.fuel_l)}</b> L</span><span><b>${dur(t.moving_s + t.idle_s)}</b></span>`;

  async function load() {
    let trips: TripRecord[] = [];
    try { trips = (await (await fetch("/api/trips")).json()).trips ?? []; } catch { /* mock mode */ }
    empty.hidden = trips.length > 0;
    tbody.innerHTML = "";
    for (const t of trips) {
      const tr = document.createElement("tr");
      tr.className = (t.end_ts === null ? "live " : "") + (t.id === selected ? "sel" : "");
      tr.innerHTML = `<td>${when(t.start_ts)}${t.end_ts === null ? " <i>now</i>" : ""}</td><td>${num(t.distance_km)}</td>` +
        `<td>${dur(t.moving_s + t.idle_s)}</td><td>${num(t.l_per_100km)}</td><td>${num(t.fuel_l)}</td>`;
      tr.onclick = () => select(t.id);
      tbody.appendChild(tr);
    }
    if (selected === null && trips.length) select(trips[0].id);
    else if (selected !== null && trips.find((t) => t.id === selected)?.end_ts === null) showDetail(selected);
  }

  function select(id: number) {
    selected = id;
    showDetail(id);
    load();
  }

  async function showDetail(id: number) {
    const t: TripRecord = await (await fetch(`/api/trips/${id}`)).json();
    facts.innerHTML = `<h3>${when(t.start_ts)}</h3><div class="facts">${totals(t)}` +
      `<span>avg <b>${num(t.avg_speed, 0)}</b> km/h</span><span>max <b>${num(t.max_speed, 0)}</b> km/h</span>` +
      `<span>idle <b>${dur(t.idle_s)}</b></span></div>` +
      `<button class="del">Delete drive</button>`;
    facts.querySelector<HTMLButtonElement>(".del")!.onclick = async () => {
      if (!confirm("Delete this drive from the log?")) return;
      await fetch(`/api/trips/${id}`, { method: "DELETE" });
      selected = null; load();
    };
    drawRoute(t.track ?? []);
  }

  let creating = false;
  async function drawRoute(track: [number, number][]) {
    const coords = track.map(([lat, lon]) => [lon, lat]);
    const data = { type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: coords } };
    const fit = () => {
      if (coords.length < 2) return;
      const b = coords.reduce((bb, c) => bb.extend(c as [number, number]), new maplibregl.LngLatBounds(coords[0] as [number, number], coords[0] as [number, number]));
      map!.fitBounds(b, { padding: 30, duration: 0 });
    };
    if (!map) {
      if (creating) return;
      creating = true;
      const bm = await buildBasemap(settings.esri_key);
      map = new maplibregl.Map({ container: mapEl, attributionControl: { compact: true }, center: [-122.5, 49.1], zoom: 9, style: bm.style });
      map.on("load", () => {
        showBasemap(map!, bm, "street");
        map!.addSource("trip", { type: "geojson", data });
        map!.addLayer({ id: "trip", type: "line", source: "trip", paint: { "line-color": "#3fb6ff", "line-width": 5 } });
        fit();
      });
    } else {
      (map.getSource("trip") as maplibregl.GeoJSONSource | undefined)?.setData(data);
      map.resize();
      fit();
    }
  }

  // Keep the list (and the live drive's row) current while the page is open.
  setInterval(() => { if (!root.hidden) load(); }, 5000);

  return {
    show: () => { load(); map?.resize(); },
    update: (s) => {
      const c = s.trip?.counters;
      if (!c) return;
      for (const k of Object.keys(cards)) if (c[k]) cards[k].innerHTML = totals(c[k]);
    },
  };
}
