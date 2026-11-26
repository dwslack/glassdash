import { layers, namedFlavor } from "@protomaps/basemaps";
import type { LayerSpecification, StyleSpecification } from "maplibre-gl";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";

// Offline BC street map: data/maps/bc.pmtiles served by dashd at /tiles (see `just map-bc`).
// Labels need fonts in data/map-assets (see `just map-assets`); without them the map draws unlabelled.
const PMTILES = "/tiles/bc.pmtiles";
const FONT_PROBE = "/map-assets/fonts/Noto Sans Regular/0-255.pbf";
const SPRITE_PROBE = "/map-assets/sprites/v4/dark.json";

export const SAT_ATTRIBUTION = "Imagery © Esri, Maxar, Earthstar Geographics";
const OSM_ATTRIBUTION = '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · Protomaps';

let protocolAdded = false;
const exists = async (url: string) => {
  try { return (await fetch(url, { method: "HEAD" })).ok; } catch { return false; }
};

export interface Basemap { style: StyleSpecification; vectorIds: string[]; hasStreet: boolean; }

export function satelliteUrl(key: string): string {
  // With an ArcGIS Location Platform key use the keyed endpoint; otherwise the public one.
  return key
    ? `https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token=${encodeURIComponent(key)}`
    : "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
}

export async function buildBasemap(esriKey: string): Promise<Basemap> {
  if (!protocolAdded) { maplibregl.addProtocol("pmtiles", new Protocol().tile); protocolAdded = true; }
  const [hasStreet, hasFonts, hasSprite] = await Promise.all([exists(PMTILES), exists(FONT_PROBE), exists(SPRITE_PROBE)]);
  const origin = location.origin;
  const style: StyleSpecification = {
    version: 8,
    sources: {
      satellite: { type: "raster", tiles: [satelliteUrl(esriKey)], tileSize: 256, maxzoom: 19, attribution: SAT_ATTRIBUTION },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#0b1118" } },
      { id: "satellite", type: "raster", source: "satellite", layout: { visibility: "none" } },
    ],
  };
  let vectorIds: string[] = [];
  if (hasStreet) {
    style.sources.protomaps = { type: "vector", url: `pmtiles://${origin}${PMTILES}`, attribution: OSM_ATTRIBUTION };
    const vl = layers("protomaps", namedFlavor("dark"), hasFonts ? { lang: "en" } : undefined) as LayerSpecification[];
    vectorIds = vl.map((l) => l.id);
    style.layers.push(...vl);
    if (hasFonts) style.glyphs = `${origin}/map-assets/fonts/{fontstack}/{range}.pbf`;
    if (hasSprite) style.sprite = `${origin}/map-assets/sprites/v4/dark`;
  }
  return { style, vectorIds, hasStreet };
}

export function showBasemap(map: maplibregl.Map, bm: Basemap, kind: "street" | "satellite"): void {
  const sat = kind === "satellite";
  map.setLayoutProperty("satellite", "visibility", sat ? "visible" : "none");
  for (const id of bm.vectorIds) {
    if (!map.getLayer(id)) continue;
    // Over satellite keep only roads' labels and place names, drop fills.
    const keep = !sat || /label|roads_shield|places/.test(id);
    map.setLayoutProperty(id, "visibility", keep ? "visible" : "none");
  }
}
