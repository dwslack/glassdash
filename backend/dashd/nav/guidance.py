"""Turn-by-turn guidance: follow GPS fixes along a NavRoute.

Each fix is snapped to the route (searching a window around the last position),
which gives the distance travelled along it. From that: the next maneuver and the
distance to it, the banner text, lane hints, spoken prompts (each once, at the
distances Valhalla chose for the road's speed), time and distance left, and
whether the truck has left the route.
"""
from __future__ import annotations

import bisect

from ..geo import angle_diff, bearing, dist, project
from .route import NavRoute

OFF_ROUTE_M = 45.0      # further than this from the route line counts as off it
OFF_ROUTE_FIXES = 3     # ...for this many fixes in a row (about 1.5 s at 2 Hz) before rerouting
WRONG_WAY_DEG = 120.0   # driving the route backwards also counts, above WRONG_WAY_KMH
WRONG_WAY_KMH = 15.0
REROUTE_GAP_S = 8.0     # at most one reroute per this many seconds
ARRIVE_M = 30.0
LANE_AHEAD_M = 600.0    # show lane arrows this far before the intersection


class Navigator:
    def __init__(self, route: NavRoute, speak_id: int = 0):
        self.route = route
        self.d: float | None = None
        self.off_m = 0.0
        self.off_count = 0
        self.status = "navigating"  # navigating | offroute | arrived
        self.spoken: set[tuple[int, int]] = set()
        self.speak_id = speak_id  # carried across reroutes so the UI sees every new prompt
        self.speak_text = ""
        self.last_reroute = float("-inf")

    def locate(self, p: tuple[float, float], heading: float | None = None, speed: float = 0.0) -> tuple[float, float, bool]:
        """(distance along route, metres off the line, driving against it) for position p."""
        r = self.route
        n = len(r.points) - 1
        if self.d is None:
            lo, hi = 0, n
        else:
            lo = max(0, bisect.bisect_right(r.cum, self.d - 80) - 1)
            hi = min(n, bisect.bisect_right(r.cum, self.d + max(400.0, speed / 3.6 * 12)) + 1)
        best = (float("inf"), 0.0, 0)
        for i in range(lo, hi):
            a, b = r.points[i], r.points[i + 1]
            t, off = project(p, a, b)
            if off < best[0]:
                best = (off, r.cum[i] + t * (r.cum[i + 1] - r.cum[i]), i)
        off, d, i = best
        wrong_way = False
        if heading is not None and speed >= WRONG_WAY_KMH and r.points[i] != r.points[i + 1]:
            wrong_way = abs(angle_diff(heading, bearing(r.points[i], r.points[i + 1]))) > WRONG_WAY_DEG
        return d, off, wrong_way

    def update(self, fix: dict, now: float) -> bool:
        """Feed a GPS fix. Returns True when the caller should reroute from here."""
        p = (fix["lat"], fix["lon"])
        speed = fix.get("speed") or 0.0
        d, off, wrong_way = self.locate(p, fix.get("heading"), speed)
        self.off_m = off
        if self.status == "arrived":
            return False
        if off > OFF_ROUTE_M or wrong_way:
            self.off_count += 1
        else:
            self.off_count = 0
            self.d = d if self.d is None else max(d, self.d - 30)  # allow small GPS jitter backwards
        if self.off_count >= OFF_ROUTE_FIXES:
            self.status = "offroute"
            if now - self.last_reroute >= REROUTE_GAP_S:
                self.last_reroute = now
                return True
            return False
        self.status = "navigating"
        if self.d is None:
            return False
        if self.route.length - self.d <= ARRIVE_M or dist(p, self.route.points[-1]) <= ARRIVE_M:
            self.status = "arrived"
            name = self.route.dest.get("name")
            self.say(f"You have arrived at {name}." if name else "You have arrived.")
            return False
        self._voice()
        return False

    def _voice(self) -> None:
        i = self.route.step_at(self.d)
        step = self.route.steps[i]
        rem = step.end_m - self.d
        due = [k for k, v in enumerate(step.voice) if rem <= v.at_m and (i, k) not in self.spoken]
        if due:
            self.spoken.update((i, k) for k in due)
            self.say(step.voice[due[-1]].text)  # after a jump (reroute, GPS gap) only the latest matters

    def say(self, text: str) -> None:
        self.speak_id += 1
        self.speak_text = text

    def state(self, now: float) -> dict:
        r = self.route
        d = self.d or 0.0
        i = r.step_at(d)
        step = r.steps[i]
        rem = max(0.0, step.end_m - d)
        nxt = r.steps[i + 1] if i + 1 < len(r.steps) else step
        banner = None
        for b in step.banners:  # listed furthest first; the last one already reached wins
            if rem <= b.at_m or banner is None:
                banner = b
        then = None
        if i + 2 < len(r.steps) and nxt.length < 250 and nxt.type != "arrive":
            t = r.steps[i + 2]
            then = {"type": t.type, "modifier": t.modifier, "exit": t.exit}
        lanes = None
        for spot in r.lanes:
            if d < spot.at_m <= step.end_m + 5 and spot.at_m - d <= LANE_AHEAD_M and not all(x["valid"] for x in spot.lanes):
                lanes = spot.lanes
                break
        remaining_s = r.remaining_time(d)
        return {
            "status": self.status,
            "dest": r.dest,
            "road": step.name,
            "next": {
                "type": nxt.type, "modifier": nxt.modifier, "exit": nxt.exit, "exit_number": nxt.exit_number,
                "text": banner.text if banner else nxt.name, "sub": banner.sub if banner else "",
                "instruction": nxt.instruction, "distance_m": round(rem),
            },
            "then": then,
            "lanes": lanes,
            "remaining_m": round(max(0.0, r.length - d)),
            "remaining_s": round(remaining_s),
            "eta_ts": round(now + remaining_s),
            "off_m": round(self.off_m),
            "speak": {"id": self.speak_id, "text": self.speak_text},
        }
