"""Trip recording: a log of every drive plus resettable Trip A / Trip B counters.

A drive starts when the engine is running (fresh RPM reading above 300) and ends
once it has been off, or the ECM silent, for END_GRACE_S. Distance comes from the
OBD vehicle speed, fuel from MAF at a stoichiometric mix (a good estimate at
cruise; it reads low under heavy load, when the engine runs rich).

Everything is checkpointed to SQLite every CHECKPOINT_S, because on the truck the
Pi may lose power at key-off before the end of the drive is detected. On start-up
any drive left open is closed at its last checkpoint.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .bus import SignalBus
from .fuel import fuel_lph
from .settings import Settings

END_GRACE_S = 20.0
CHECKPOINT_S = 15.0
RUNNING_RPM = 300.0
TRACK_EVERY_M = 50.0
TICK_S = 0.2
DEFAULT_L_PER_100KM = 14.0  # 5.3 L Silverado, mixed driving
COUNTERS = ("A", "B")

SCHEMA = """
create table if not exists trips (
  id integer primary key,
  start_ts real not null,
  end_ts real,
  last_ts real not null,
  distance_km real not null default 0,
  fuel_l real not null default 0,
  moving_s real not null default 0,
  idle_s real not null default 0,
  max_speed real not null default 0,
  track text not null default '[]'
);
create table if not exists counters (
  name text primary key,
  since_ts real not null,
  distance_km real not null default 0,
  fuel_l real not null default 0,
  moving_s real not null default 0,
  idle_s real not null default 0
);
"""


def _economy(fuel_l: float, distance_km: float) -> float | None:
    """L/100 km, or None until there's enough distance to mean anything."""
    return round(fuel_l / distance_km * 100, 1) if distance_km >= 0.5 else None


def _haversine_m(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


@dataclass
class Totals:
    distance_km: float = 0.0
    fuel_l: float = 0.0
    moving_s: float = 0.0
    idle_s: float = 0.0

    def add(self, km: float, litres: float, dt: float, moving: bool) -> None:
        self.distance_km += km
        self.fuel_l += litres
        if moving:
            self.moving_s += dt
        else:
            self.idle_s += dt

    def summary(self) -> dict:
        hours = self.moving_s / 3600
        return {
            "distance_km": round(self.distance_km, 2),
            "fuel_l": round(self.fuel_l, 2),
            "moving_s": round(self.moving_s),
            "idle_s": round(self.idle_s),
            "l_per_100km": _economy(self.fuel_l, self.distance_km),
            "avg_speed": round(self.distance_km / hours, 1) if hours > 0.01 else None,
        }


@dataclass
class Trip:
    id: int
    start_ts: float
    last_ts: float
    totals: Totals = field(default_factory=Totals)
    max_speed: float = 0.0
    track: list[list[float]] = field(default_factory=list)


class TripLog:
    def __init__(self, db_path: Path | str):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("update trips set end_ts = last_ts where end_ts is null")
        for name in COUNTERS:
            self.db.execute("insert or ignore into counters (name, since_ts) values (?, ?)", (name, time.time()))
        self.db.commit()
        self.counters = {r["name"]: (r["since_ts"], Totals(r["distance_km"], r["fuel_l"], r["moving_s"], r["idle_s"]))
                         for r in self.db.execute("select * from counters")}

    def open_trip(self, ts: float) -> Trip:
        cur = self.db.execute("insert into trips (start_ts, last_ts) values (?, ?)", (ts, ts))
        self.db.commit()
        return Trip(cur.lastrowid, ts, ts)

    def save(self, trip: Trip, end: bool = False) -> None:
        t = trip.totals
        self.db.execute(
            "update trips set last_ts=?, end_ts=?, distance_km=?, fuel_l=?, moving_s=?, idle_s=?, max_speed=?, track=? where id=?",
            (trip.last_ts, trip.last_ts if end else None, t.distance_km, t.fuel_l, t.moving_s, t.idle_s,
             trip.max_speed, json.dumps(trip.track), trip.id),
        )
        for name, (_, c) in self.counters.items():
            self.db.execute("update counters set distance_km=?, fuel_l=?, moving_s=?, idle_s=? where name=?",
                            (c.distance_km, c.fuel_l, c.moving_s, c.idle_s, name))
        self.db.commit()

    def reset_counter(self, name: str, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        self.counters[name] = (ts, Totals())
        self.db.execute("update counters set since_ts=?, distance_km=0, fuel_l=0, moving_s=0, idle_s=0 where name=?", (ts, name))
        self.db.commit()

    def drop_short(self, trip: Trip) -> None:
        self.db.execute("delete from trips where id=?", (trip.id,))
        self.db.commit()

    @staticmethod
    def _row(r: sqlite3.Row, with_track: bool) -> dict:
        t = Totals(r["distance_km"], r["fuel_l"], r["moving_s"], r["idle_s"])
        out = {"id": r["id"], "start_ts": r["start_ts"], "end_ts": r["end_ts"],
               "max_speed": round(r["max_speed"], 1), **t.summary()}
        if with_track:
            out["track"] = json.loads(r["track"])
        return out

    def list(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("select * from trips order by start_ts desc limit ?", (limit,))
        return [self._row(r, False) for r in rows]

    def get(self, trip_id: int) -> dict | None:
        r = self.db.execute("select * from trips where id=?", (trip_id,)).fetchone()
        return self._row(r, True) if r else None

    def delete(self, trip_id: int) -> bool:
        cur = self.db.execute("delete from trips where id=?", (trip_id,))
        self.db.commit()
        return cur.rowcount > 0


class TripRecorder:
    """Watches the signal bus, integrates distance and fuel, publishes live trip state."""

    def __init__(self, sig: SignalBus, log: TripLog, settings: Settings | None = None):
        self.sig = sig
        self.log = log
        self.settings = settings or Settings(None)
        self.trip: Trip | None = None
        self._last_tick: float | None = None
        self._off_since: float | None = None
        self._last_checkpoint = 0.0
        self._last_track_pt: tuple[float, float] | None = None

    def _fresh(self, name: str, now: float) -> float | None:
        r = self.sig.signals.get(name)
        if r is None or self.sig.is_stale(r, now):
            return None
        return r.value

    def tick(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        dt = 0.0 if self._last_tick is None else min(now - self._last_tick, 2.0)
        self._last_tick = now
        rpm = self._fresh("rpm", now)
        running = rpm is not None and rpm > RUNNING_RPM
        speed = self._fresh("speed", now) or 0.0
        maf = self._fresh("maf", now)
        lph = fuel_lph(maf) * self.settings["fuel_cal"] if maf is not None and running else 0.0

        if running:
            self._off_since = None
            if self.trip is None:
                self.trip = self.log.open_trip(now)
                self._last_track_pt = None
                dt = 0.0
        elif self.trip is not None:
            self._off_since = self._off_since or now
            if now - self._off_since >= END_GRACE_S:
                self._end_trip()

        if self.trip is not None and running and dt > 0:
            km = speed * dt / 3600
            litres = lph * dt / 3600
            moving = speed >= 2
            self.trip.totals.add(km, litres, dt, moving)
            for _, c in self.log.counters.values():
                c.add(km, litres, dt, moving)
            self.trip.max_speed = max(self.trip.max_speed, speed)
            self.trip.last_ts = now
            self._add_track_point()
            if now - self._last_checkpoint >= CHECKPOINT_S:
                self._last_checkpoint = now
                self.log.save(self.trip)

        self.sig.trip = self.state(speed, lph, running, now)

    def _add_track_point(self) -> None:
        gps = self.sig.gps
        if not gps or self.trip is None:
            return
        pt = (round(gps["lat"], 5), round(gps["lon"], 5))
        if self._last_track_pt is None or _haversine_m(self._last_track_pt, pt) >= TRACK_EVERY_M:
            self.trip.track.append(list(pt))
            self._last_track_pt = pt

    def _end_trip(self) -> None:
        trip, self.trip = self.trip, None
        self._off_since = None
        if trip.totals.distance_km < 0.05 and trip.totals.moving_s < 5:
            self.log.drop_short(trip)  # started and stopped without moving: not a drive
        else:
            self.log.save(trip, end=True)

    def flush(self) -> None:
        if self.trip is not None:
            self.log.save(self.trip)

    def state(self, speed: float, lph: float, running: bool, now: float) -> dict:
        fuel_level = self._fresh("fuel_level", now)
        trip = self.trip
        # Range uses this drive's economy once it has 5 km, else Trip A's, else a typical figure.
        avg = DEFAULT_L_PER_100KM
        for basis in (trip.totals if trip else None, self.log.counters["A"][1]):
            if basis is not None and basis.distance_km >= 5:
                avg = basis.fuel_l / basis.distance_km * 100
                break
        fuel_left = fuel_level / 100 * self.settings["tank_l"] if fuel_level is not None else None
        return {
            "active": trip is not None,
            "running": running,
            "fuel_lph": round(lph, 2),
            # Instant economy only makes sense when moving; at a stop show L/h instead.
            "instant_l_per_100km": round(lph / speed * 100, 1) if speed >= 15 else None,
            "fuel_left_l": round(fuel_left, 1) if fuel_left is not None else None,
            "range_km": round(fuel_left / avg * 100) if fuel_left is not None and avg else None,
            "current": ({"id": trip.id, "start_ts": trip.start_ts, "max_speed": round(trip.max_speed, 1),
                         **trip.totals.summary()} if trip else None),
            "counters": {name: {"since_ts": since, **c.summary()} for name, (since, c) in self.log.counters.items()},
        }

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                self.tick()
                await asyncio.sleep(TICK_S)
        finally:
            self.flush()
