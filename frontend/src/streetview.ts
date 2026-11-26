import type maplibregl from "maplibre-gl";
import { settings } from "./settings";

// Street-level photos from Mapillary (crowd-sourced, online only, coverage is patchy outside towns).
// Shows the photo nearest a point: the truck by default, or wherever you tap the map while the panel is open.
const SEARCH_DEG = 0.0015; // about 150 m

interface Img { id: string; thumb_1024_url: string; captured_at: number; computed_geometry: { coordinates: [number, number] }; }

export function mountStreetView(map: maplibregl.Map, button: HTMLButtonElement, panel: HTMLElement, here: () => [number, number] | null): void {
  let open = false;
  let seq = 0;

  async function show(lon: number, lat: number) {
    const mine = ++seq;
    if (!settings.mapillary_token) {
      panel.innerHTML = `<p>Street photos need a free Mapillary token. Add it in <a href="#settings">Settings</a>.</p>`;
      return;
    }
    panel.innerHTML = `<p>Looking for photos near here…</p>`;
    const bbox = [lon - SEARCH_DEG, lat - SEARCH_DEG, lon + SEARCH_DEG, lat + SEARCH_DEG].join(",");
    const url = `https://graph.mapillary.com/images?access_token=${encodeURIComponent(settings.mapillary_token)}` +
      `&fields=id,thumb_1024_url,captured_at,computed_geometry&bbox=${bbox}&limit=50`;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(res.status === 401 ? "Mapillary rejected the token" : `Mapillary error ${res.status}`);
      const imgs: Img[] = (await res.json()).data ?? [];
      if (mine !== seq) return;
      if (!imgs.length) { panel.innerHTML = `<p>No street photos within about 150 m. Tap somewhere else on the map.</p>`; return; }
      const d2 = (i: Img) => (i.computed_geometry.coordinates[0] - lon) ** 2 + (i.computed_geometry.coordinates[1] - lat) ** 2;
      const best = imgs.reduce((a, b) => (d2(b) < d2(a) ? b : a));
      const taken = new Date(best.captured_at).toLocaleDateString(undefined, { year: "numeric", month: "short" });
      panel.innerHTML = `<img alt="Street photo" src="${best.thumb_1024_url}">` +
        `<div class="sv-foot"><span>Photo from ${taken} · © Mapillary, CC BY-SA</span>` +
        `<a target="_blank" rel="noopener" href="https://www.mapillary.com/app/?pKey=${best.id}">Open in Mapillary</a></div>`;
    } catch (e) {
      if (mine === seq) panel.innerHTML = `<p>${(e as Error).message === "Failed to fetch" ? "Street photos need internet." : (e as Error).message}</p>`;
    }
  }

  const setOpen = (v: boolean) => {
    open = v;
    panel.hidden = !v;
    button.classList.toggle("on", v);
    if (v) { const p = here() ?? [map.getCenter().lng, map.getCenter().lat]; show(p[0], p[1]); }
  };
  button.onclick = () => setOpen(!open);
  map.on("click", (e) => { if (open) show(e.lngLat.lng, e.lngLat.lat); });
}
