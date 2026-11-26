"""Simulated GPS: the truck's position along its route."""
from __future__ import annotations

from pathlib import Path

from .route import TRACKS, Route
from .vehicle import Vehicle

DEFAULT_ROUTE = "langley_abbotsford"
DEFAULT_TRACK = TRACKS / f"{DEFAULT_ROUTE}.json"


class GpsSim:
    def __init__(self, vehicle: Vehicle, track: Path | str | None = None):
        self.vehicle = vehicle
        if vehicle.route is None:
            vehicle.set_route(Route.load(track or DEFAULT_TRACK))

    def fix(self) -> dict:
        v = self.vehicle
        r = v.route
        # Route mode tracks distance along the route; the scripted loop just wraps the odometer.
        d = v.route_d if v.route_mode else v.distance_m % r.length
        lat, lon, heading = r.position(d)
        return {"lat": lat, "lon": lon, "heading": round(heading, 1), "speed": round(v.speed, 1),
                "mode": "3d", "source": "sim", "route": r.name}
