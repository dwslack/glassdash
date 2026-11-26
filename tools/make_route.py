"""Make a sim route that follows real roads: python tools/make_route.py NAME "FROM" "TO" [--via "PLACE"]...

Looks up place names with OpenStreetMap Nominatim, gets a driving route from the public
OSRM demo server, and writes sim/tracks/NAME.json with the road geometry and a speed for
every segment. Needs internet once; the sim then runs offline. Both services are free
community servers: fine for making a handful of routes, not for heavy use.

Places can be names ("Whistler, BC") or "lat,lon".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "glassdash-sim-route-maker/1.0"}
LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def get(url: str) -> dict | list:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read())


def geocode(place: str) -> tuple[float, float, str]:
    if m := LATLON.match(place):
        return float(m[1]), float(m[2]), place
    q = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1, "countrycodes": "ca"})
    hits = get(f"https://nominatim.openstreetmap.org/search?{q}")
    time.sleep(1.1)  # Nominatim policy: at most one request a second
    if not hits:
        sys.exit(f"couldn't find {place!r}; try adding ', BC' or give lat,lon")
    return float(hits[0]["lat"]), float(hits[0]["lon"]), hits[0]["display_name"]


def decode_polyline6(s: str) -> list[tuple[float, float]]:
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lon += vals[1]
        pts.append((lat / 1e6, lon / 1e6))
    return pts


def road_speed(mps: float) -> float:
    """OSRM's demo profile is conservative on highways (80 km/h on a 100 zone); nudge fast roads up."""
    kmh = mps * 3.6
    return round(min(110.0, kmh * 1.2) if kmh >= 65 else kmh, 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="file name, letters/digits/_ only, e.g. sea_to_sky")
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--via", action="append", default=[], help="a place to pass through (repeatable)")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", a.name):
        sys.exit("name: letters, digits, _ and - only")

    stops = [geocode(p) for p in [a.start, *a.via, a.end]]
    for lat, lon, label in stops:
        print(f"  {lat:.5f},{lon:.5f}  {label}")
    coords = ";".join(f"{lon},{lat}" for lat, lon, _ in stops)
    url = (f"https://router.project-osrm.org/route/v1/driving/{coords}"
           "?overview=full&geometries=polyline6&annotations=speed&steps=false")
    res = get(url)
    if res.get("code") != "Ok":
        sys.exit(f"routing failed: {res.get('code')} {res.get('message', '')}")
    route = res["routes"][0]
    points = decode_polyline6(route["geometry"])
    speeds = [road_speed(s) for leg in route["legs"] for s in leg["annotation"]["speed"]]
    if len(speeds) != len(points) - 1:
        sys.exit(f"unexpected OSRM reply: {len(points)} points but {len(speeds)} speeds")

    out = ROOT / "sim" / "tracks" / f"{a.name}.json"
    out.write_text(json.dumps({
        "name": f"{stops[0][2].split(',')[0]} to {stops[-1][2].split(',')[0]}" if not LATLON.match(a.start) else a.name,
        "source": "OpenStreetMap data via OSRM (router.project-osrm.org), ODbL",
        "points": [[round(la, 6), round(lo, 6)] for la, lo in points],
        "speeds": speeds,
    }, separators=(",", ":")))
    print(f"wrote {out.relative_to(ROOT)}: {route['distance'] / 1000:.1f} km, "
          f"about {route['duration'] / 60:.0f} min at OSRM speeds, {len(points)} points")
    print(f"drive it: just sim --route {a.name}")


if __name__ == "__main__":
    main()
