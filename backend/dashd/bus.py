"""Signal bus: latest value of every signal, with timestamps for staleness."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Reading:
    value: float
    unit: str
    ts: float
    stale_after: float | None = None  # slow-polled signals get a longer window


@dataclass
class SignalBus:
    stale_after: float = 2.0
    signals: dict[str, Reading] = field(default_factory=dict)
    gps: dict | None = None
    errors: dict[str, int] = field(default_factory=dict)
    trip: dict | None = None  # live trip state from TripRecorder
    mil: dict | None = None  # check-engine light {mil, count}, from Diagnostics
    body: dict | None = None  # doors/locks/lights from the body bus, None if not heard
    nav: dict | None = None  # turn-by-turn guidance while navigating

    def set(self, name: str, value: float, unit: str, ts: float | None = None, stale_after: float | None = None) -> None:
        self.signals[name] = Reading(value, unit, ts if ts is not None else time.time(), stale_after)

    def is_stale(self, r: Reading, now: float) -> bool:
        return now - r.ts > (r.stale_after or self.stale_after)

    def set_gps(self, fix: dict) -> None:
        self.gps = {**fix, "ts": time.time()}

    def error(self, name: str) -> None:
        self.errors[name] = self.errors.get(name, 0) + 1

    def snapshot(self, now: float | None = None) -> dict:
        now = now if now is not None else time.time()
        return {
            "ts": now,
            "signals": {
                k: {
                    "value": round(r.value, 2),
                    "unit": r.unit,
                    "age": round(now - r.ts, 2),
                    "stale": self.is_stale(r, now),
                }
                for k, r in self.signals.items()
            },
            "gps": self.gps,
            "errors": dict(self.errors),
            "trip": self.trip,
            "mil": self.mil,
            "body": self.body,
            "nav": self.nav,
        }
