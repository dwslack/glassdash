"""A navigation route parsed from Valhalla's OSRM-format reply.

OSRM steps work like this: step i *starts* with its maneuver ("turn left onto X"),
and the voice and banner instructions listed on step i announce the maneuver of
step i + 1, triggered when the remaining distance in step i drops below each
instruction's distanceAlongGeometry.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from ..geo import decode_polyline, dist


@dataclass
class Voice:
    at_m: float  # speak when this far (or less) from the end of the step
    text: str


@dataclass
class Banner:
    at_m: float
    text: str
    sub: str = ""  # secondary line, e.g. "toward Vancouver"


@dataclass
class Step:
    type: str  # depart, turn, continue, merge, on ramp, off ramp, fork, roundabout, exit roundabout, arrive, ...
    modifier: str  # left, slight left, sharp left, straight, uturn, right, ...
    instruction: str
    name: str
    ref: str
    exit: int | None  # roundabout exit number
    exit_number: str  # highway exit, e.g. "92"
    begin: int  # index into route points
    start_m: float = 0.0
    end_m: float = 0.0
    duration: float = 0.0
    voice: list[Voice] = field(default_factory=list)
    banners: list[Banner] = field(default_factory=list)

    @property
    def length(self) -> float:
        return self.end_m - self.start_m


@dataclass
class LaneSpot:
    at_m: float  # distance along the route of the intersection
    lanes: list[dict]  # [{"indications": [...], "valid": bool}]


class NavRoute:
    def __init__(self, points: list[tuple[float, float]], steps: list[Step], duration: float,
                 lanes: list[LaneSpot] | None = None, dest: dict | None = None):
        if len(points) < 2:
            raise ValueError("route needs at least two points")
        self.points = points
        self.cum = [0.0]
        for a, b in zip(points, points[1:]):
            self.cum.append(self.cum[-1] + dist(a, b))
        self.length = self.cum[-1]
        self.steps = steps
        self.duration = duration
        self.lanes = lanes or []
        self.dest = dest or {}
        for i, s in enumerate(steps):
            s.start_m = self.cum[min(s.begin, len(points) - 1)]
            nxt = steps[i + 1].begin if i + 1 < len(steps) else len(points) - 1
            s.end_m = self.cum[min(nxt, len(points) - 1)]

    @classmethod
    def from_osrm(cls, reply: dict, dest: dict | None = None) -> "NavRoute":
        route = reply["routes"][0]
        points = decode_polyline(route["geometry"], 6)
        steps: list[Step] = []
        lane_idx: list[tuple[int, list]] = []
        for leg in route["legs"]:
            for s in leg["steps"]:
                m = s["maneuver"]
                ints = s.get("intersections") or [{}]
                exit_number = ""
                for b in s.get("bannerInstructions") or []:
                    for c in b.get("primary", {}).get("components", []):
                        if c.get("type") == "exit-number":
                            exit_number = c.get("text", "")
                steps.append(Step(
                    type=m.get("type", ""), modifier=m.get("modifier", ""), instruction=m.get("instruction", ""),
                    name=s.get("name", ""), ref=s.get("ref", ""), exit=m.get("exit"),
                    exit_number=s.get("exits", "") or exit_number,
                    begin=ints[0].get("geometry_index", 0), duration=s.get("duration", 0.0),
                    voice=[Voice(v["distanceAlongGeometry"], v["announcement"]) for v in s.get("voiceInstructions") or []],
                    banners=[Banner(b["distanceAlongGeometry"], b["primary"].get("text", ""),
                                    (b.get("secondary") or {}).get("text", ""))
                             for b in s.get("bannerInstructions") or []],
                ))
                for it in ints:
                    if it.get("lanes") and "geometry_index" in it:
                        lane_idx.append((it["geometry_index"], it["lanes"]))
        # Multi-leg replies repeat an arrive/depart pair at each via point; we only route A to B, so that's moot.
        r = cls(points, steps, route.get("duration", 0.0), dest=dest)
        r.lanes = [LaneSpot(r.cum[min(i, len(points) - 1)],
                            [{"indications": ln.get("indications", []), "valid": bool(ln.get("valid"))} for ln in lanes])
                   for i, lanes in lane_idx]
        return r

    def step_at(self, d: float) -> int:
        starts = [s.start_m for s in self.steps]
        return max(0, min(len(self.steps) - 1, bisect.bisect_right(starts, d) - 1))

    def remaining_time(self, d: float) -> float:
        """Seconds to the destination from distance d, using Valhalla's per-step times."""
        i = self.step_at(d)
        s = self.steps[i]
        frac = (s.end_m - d) / s.length if s.length > 0 else 0.0
        return max(0.0, frac) * s.duration + sum(x.duration for x in self.steps[i + 1:])

    def segment_speeds(self, floor: float = 15.0, cap: float = 110.0) -> list[float]:
        """km/h for each point-to-point segment, from each step's length and time. Used by the sim."""
        speeds = [floor] * (len(self.points) - 1)
        for s in self.steps:
            v = s.length / s.duration * 3.6 if s.duration > 0 else floor
            end = next((t.begin for t in self.steps if t.begin > s.begin), len(self.points) - 1)
            for k in range(s.begin, min(end, len(speeds))):
                speeds[k] = min(cap, max(floor, v))
        return speeds
