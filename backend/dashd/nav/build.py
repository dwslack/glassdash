"""Build offline navigation data from an OSM extract (.osm.pbf).

    python -m dashd.nav.build data/nav/bc.osm.pbf data/nav

Makes data/nav/tiles.tar (Valhalla routing graph) and data/nav/places.db (search).
Needs the [nav] extras: pyvalhalla and osmium. Run it on the Mac and copy data/nav
to the Pi; building on a Pi 5 works but is much slower.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .places import Places

SETTLEMENTS = {"city", "town", "village", "hamlet", "suburb", "neighbourhood", "locality", "quarter"}
CONTEXT_PLACES = {"city", "town", "village", "hamlet"}  # what "city" means for a result
POI_KEYS = ("amenity", "shop", "tourism", "leisure", "office", "craft", "healthcare", "historic",
            "aeroway", "club", "sport")
STATIONS = {"station", "halt"}  # railway=* / public_transport=* values worth finding
POI_SKIP = {"bench", "waste_basket", "parking_space", "bicycle_parking", "vending_machine", "recycling",
            "toilets", "drinking_water", "post_box", "telephone", "atm", "parking_entrance", "shelter",
            "hunting_stand", "grit_bin", "switch", "level_crossing", "crossing", "signal", "buffer_stop",
            "milestone", "survey_point", "taxiway", "gate", "holding_position", "apron", "street_cabinet", "utility_pole", "pipeline", "manhole", "yes"}
STREET_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
                   "living_street", "service", "motorway_link", "trunk_link", "primary_link", "track"}
NATURAL = {"peak", "bay", "beach", "lake", "glacier", "volcano", "cape", "spring", "waterfall"}


class Grid:
    """Nearest settlement lookup in 0.2-degree cells."""

    def __init__(self):
        self.cells: dict[tuple[int, int], list] = {}

    def add(self, name: str, lat: float, lon: float, rank: int) -> None:
        self.cells.setdefault((int(lat * 5), int(lon * 5)), []).append((name, lat, lon, rank))

    def nearest(self, lat: float, lon: float) -> str:
        ci, cj = int(lat * 5), int(lon * 5)
        best, best_d = "", 1e9
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for name, plat, plon, rank in self.cells.get((ci + di, cj + dj), ()):
                    d = ((plat - lat) ** 2 + ((plon - lon) * 0.65) ** 2) ** 0.5 / (1 + 0.6 * rank)
                    if d < best_d:
                        best, best_d = name, d
        return best


def _poi_kind(tags) -> str | None:
    if tags.get("railway") in STATIONS or tags.get("public_transport") == "station":
        return "station"
    for k in POI_KEYS:
        v = tags.get(k)
        if v and v not in POI_SKIP:
            return v.replace("_", " ")
    v = tags.get("natural")
    if v in NATURAL:
        return v
    return None


def build_places(pbf: Path, out: Path) -> int:
    import osmium

    rank = {"city": 3, "town": 2, "village": 1, "hamlet": 0}
    grid = Grid()
    for n in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(osmium.filter.KeyFilter("place")):
        p = n.tags.get("place")
        if p in CONTEXT_PLACES and n.tags.get("name"):
            grid.add(n.tags["name"], n.location.lat, n.location.lon, rank[p])

    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    places = Places(tmp)
    batch, streets = [], set()

    def where(o):
        if o.is_node():
            return (o.location.lat, o.location.lon) if o.location.valid() else None
        nodes = o.nodes
        if len(nodes) == 0:
            return None
        loc = nodes[len(nodes) // 2].location
        return (loc.lat, loc.lon) if loc.valid() else None

    fp = (osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
          .with_locations()
          .with_filter(osmium.filter.EmptyTagFilter()))
    for o in fp:
        tags = o.tags
        name = tags.get("name")
        hn = tags.get("addr:housenumber")
        if not name and not hn:
            continue
        pos = where(o)
        if pos is None:
            continue
        lat, lon = pos
        city = tags.get("addr:city") or grid.nearest(lat, lon)
        place = tags.get("place")
        if name and place in SETTLEMENTS and o.is_node():
            batch.append((name, "" if place in CONTEXT_PLACES else grid.nearest(lat, lon), place, lat, lon))
        elif name and o.is_way() and tags.get("highway") in STREET_HIGHWAYS:
            key = (name, city)
            if key not in streets:
                streets.add(key)
                batch.append((name, city, "street", lat, lon))
        elif name and (kind := _poi_kind(tags)):
            batch.append((name, city, kind, lat, lon))
        if hn and tags.get("addr:street"):
            batch.append((f"{hn} {tags['addr:street']}", city, "address", lat, lon))
        if len(batch) >= 50000:
            places.add(batch)
            batch.clear()
    places.add(batch)
    places.commit()
    places.db.execute("insert into places(places) values('optimize')")
    places.commit()
    n = places.count()
    places.db.close()
    tmp.replace(out)
    return n


def build_tiles(pbf: Path, nav_dir: Path) -> None:
    """Valhalla routing tiles, tarred into nav_dir/tiles.tar."""
    from valhalla import get_config

    tiles = nav_dir / "tiles"
    shutil.rmtree(tiles, ignore_errors=True)
    tiles.mkdir(parents=True)
    cfg = get_config(tile_extract="", tile_dir="", verbose=False)
    cfg["mjolnir"]["tile_extract"] = str((nav_dir / "tiles.tar").resolve())
    cfg["mjolnir"]["tile_dir"] = str(tiles.resolve())
    cfg["mjolnir"]["admin"] = ""
    cfg["mjolnir"]["timezone"] = ""
    cfg["mjolnir"]["concurrency"] = os.cpu_count() or 2
    cfg_path = nav_dir / "valhalla-build.json"
    cfg_path.write_text(json.dumps(cfg, indent=1))
    # pyvalhalla's wrapper scripts look for the real binaries on PATH next to python.
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
    bindir = Path(sys.executable).parent
    subprocess.run([str(bindir / "valhalla_build_tiles"), "-c", str(cfg_path), str(pbf)], check=True, env=env)
    subprocess.run([str(bindir / "valhalla_build_extract"), "-c", str(cfg_path), "--overwrite"], check=True, env=env)
    shutil.rmtree(tiles)  # the tar holds everything
    cfg_path.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf", type=Path)
    ap.add_argument("nav_dir", type=Path)
    ap.add_argument("--only", choices=["tiles", "places"])
    args = ap.parse_args()
    args.nav_dir.mkdir(parents=True, exist_ok=True)
    if args.only != "places":
        t = time.time()
        print("Building routing tiles (the long part)...", flush=True)
        build_tiles(args.pbf, args.nav_dir)
        size = (args.nav_dir / "tiles.tar").stat().st_size / 1e6
        print(f"  tiles.tar {size:.0f} MB in {(time.time() - t) / 60:.1f} min", flush=True)
    if args.only != "tiles":
        t = time.time()
        print("Building place search...", flush=True)
        n = build_places(args.pbf, args.nav_dir / "places.db")
        size = (args.nav_dir / "places.db").stat().st_size / 1e6
        print(f"  places.db {n:,} entries, {size:.0f} MB in {(time.time() - t) / 60:.1f} min", flush=True)
    print("Done. Restart dashd to use it.")


if __name__ == "__main__":
    main()
