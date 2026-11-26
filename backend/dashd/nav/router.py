"""Offline routing with Valhalla, run in-process through pyvalhalla (no server, no Docker).

Data lives in data/nav/tiles.tar, built from an OSM extract by `just nav-data`.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .route import NavRoute


class RouteError(Exception):
    pass


def valhalla_config(nav_dir: Path) -> dict:
    from valhalla import get_config

    cfg = get_config(tile_extract=str((nav_dir / "tiles.tar").resolve()), tile_dir="", verbose=False)
    cfg["mjolnir"]["admin"] = ""
    cfg["mjolnir"]["timezone"] = ""
    cfg["mjolnir"]["traffic_extract"] = ""
    return cfg


class Router:
    def __init__(self, nav_dir: Path):
        from valhalla import Actor  # optional dependency: pip install -e 'backend[nav]'

        if not (nav_dir / "tiles.tar").exists():
            raise FileNotFoundError(f"no routing tiles in {nav_dir} (run: just nav-data)")
        self.actor = Actor(valhalla_config(nav_dir))
        self.lock = threading.Lock()  # one route at a time; they take well under a second

    def route(self, origin: tuple[float, float], dest: dict, heading: float | None = None) -> NavRoute:
        start = {"lat": origin[0], "lon": origin[1]}
        if heading is not None:
            # Prefer leaving in the direction the truck is already pointing (no surprise U-turn).
            start.update(heading=round(heading) % 360, heading_tolerance=60)
        req = {
            "locations": [start, {"lat": dest["lat"], "lon": dest["lon"]}],
            "costing": "auto",
            "units": "kilometers",
            "language": "en-US",
            "format": "osrm",
            "banner_instructions": True,
            "voice_instructions": True,
        }
        try:
            with self.lock:
                reply = self.actor.route(req)
        except RuntimeError as e:  # Valhalla raises with a JSON error message, e.g. no route found
            raise RouteError(str(e)) from e
        if not reply.get("routes"):
            raise RouteError("no route found")
        return NavRoute.from_osrm(reply, dest=dest)
