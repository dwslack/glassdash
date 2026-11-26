"""A drivable route for the sim: road geometry plus a speed for each segment.

Tracks are JSON files in sim/tracks: {"name", "points": [[lat, lon], ...], "speeds": [km/h per segment]}.
"speeds" is optional; without it the sim uses its scripted speed profile instead.
Make new ones with `just route NAME "from" "to"` (tools/make_route.py, needs internet once).
"""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

from ..geo import bearing, dist  # noqa: F401  (re-exported for the sim and tests)

TRACKS = Path(__file__).resolve().parents[3] / "sim" / "tracks"
ACCEL = 2.2   # km/h per s, a loaded half-ton pulling away
DECEL = 3.5   # km/h per s, ordinary braking
CRUISE_CAP = 110.0


def list_tracks(folder: Path = TRACKS) -> list[str]:
    return sorted(p.stem for p in folder.glob("*.json"))


def track_path(name: str, folder: Path = TRACKS) -> Path:
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"bad route name {name!r}")
    p = folder / f"{name}.json"
    if not p.exists():
        raise FileNotFoundError(f"no route {name!r}; have: {', '.join(list_tracks(folder))}")
    return p


class Route:
    def __init__(self, points: list[tuple[float, float]], speeds: list[float] | None = None, name: str = "",
                 file: str = ""):
        if len(points) < 2:
            raise ValueError("a route needs at least two points")
        self.name = name or file
        self.file = file
        self.points = [tuple(p) for p in points]
        legs = [dist(a, b) for a, b in zip(self.points, self.points[1:])]
        self.cum = [0.0]
        for leg in legs:
            self.cum.append(self.cum[-1] + leg)
        self.length = self.cum[-1]
        self.speeds = [min(CRUISE_CAP, max(15.0, s)) for s in speeds] if speeds else None
        if self.speeds is not None and len(self.speeds) != len(legs):
            raise ValueError("speeds must have one entry per segment")

    @classmethod
    def load(cls, path: Path | str) -> "Route":
        raw = json.loads(Path(path).read_text())
        return cls([tuple(p) for p in raw["points"]], raw.get("speeds"), raw.get("name", ""), Path(path).stem)

    def _seg(self, d: float) -> int:
        return min(len(self.points) - 2, max(0, bisect.bisect_right(self.cum, d) - 1))

    def position(self, d: float) -> tuple[float, float, float]:
        """(lat, lon, heading) at distance d along the route."""
        d = min(max(d, 0.0), self.length)
        i = self._seg(d)
        a, b = self.points[i], self.points[i + 1]
        leg = self.cum[i + 1] - self.cum[i]
        f = (d - self.cum[i]) / leg if leg > 0 else 0.0
        return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, bearing(a, b)

    def speed_limit(self, d: float) -> float:
        return self.speeds[self._seg(d)] if self.speeds else 0.0

    def target_speed(self, d: float, current: float) -> float:
        """Fastest speed allowed now so the truck can still brake for slower road ahead and stop at the end."""
        target = self.speed_limit(d)
        brake_m = (current / 3.6) ** 2 / (2 * DECEL / 3.6) + 30
        i = self._seg(d)
        while i < len(self.points) - 1 and self.cum[i] < d + brake_m:
            ahead = max(0.0, self.cum[i] - d)
            # v^2 = v_seg^2 + 2*a*s: what we can be doing now and still reach that segment's speed
            v_seg = self.speeds[i] / 3.6
            allowed = math.sqrt(v_seg ** 2 + 2 * (DECEL / 3.6) * ahead) * 3.6
            target = min(target, allowed)
            i += 1
        to_end = self.length - d
        target = min(target, math.sqrt(2 * (DECEL / 3.6) * max(0.0, to_end - 2)) * 3.6)
        return target

    def turn_ahead(self, d: float, within: float = 120.0) -> str | None:
        """'L' or 'R' if the road turns more than 50 degrees in the next `within` metres."""
        i = self._seg(d)
        h0 = bearing(self.points[i], self.points[i + 1])
        j = i + 1
        while j < len(self.points) - 1 and self.cum[j] < d + within:
            delta = (bearing(self.points[j], self.points[j + 1]) - h0 + 540) % 360 - 180
            if abs(delta) > 50:
                return "R" if delta > 0 else "L"
            j += 1
        return None
