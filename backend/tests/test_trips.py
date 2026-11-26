from fastapi.testclient import TestClient

from dashd.bus import SignalBus
from dashd.fuel import fuel_lph
from dashd.server import create_app
from dashd.sim.vehicle import DRIVE_LOOPS, PARK_S, PROFILE, Vehicle
from dashd.trips import END_GRACE_S, TripLog, TripRecorder


def drive(sig, rec, t0, seconds, speed, rpm=1500.0, maf=30.0, dt=0.2, gps=None):
    t = t0
    while t < t0 + seconds:
        sig.set("rpm", rpm, "rpm", ts=t)
        sig.set("speed", speed, "km/h", ts=t)
        sig.set("maf", maf, "g/s", ts=t)
        sig.set("fuel_level", 50.0, "%", ts=t)
        if gps:
            sig.gps = gps(t)
        rec.tick(t)
        t += dt
    return t


def key_off(rec, t0, seconds, dt=0.2):
    t = t0
    while t < t0 + seconds:
        rec.tick(t)  # no fresh readings: the ECM is silent
        t += dt
    return t


def test_fuel_math():
    # 38 g/s of air is about 12.5 L/h of gasoline
    assert 12 < fuel_lph(38) < 13


def test_one_drive_recorded(tmp_path):
    sig = SignalBus()
    log = TripLog(tmp_path / "t.db")
    rec = TripRecorder(sig, log)
    t = drive(sig, rec, 1000.0, 3600, speed=100.0, maf=38.0,
              gps=lambda t: {"lat": 49.1, "lon": -122.6 + (t - 1000) * 1e-4})
    assert sig.trip["active"]
    assert 99 < sig.trip["current"]["distance_km"] < 101
    assert 11 < sig.trip["instant_l_per_100km"] < 14
    key_off(rec, t, END_GRACE_S + 3)
    assert not sig.trip["active"]
    trips = log.list()
    assert len(trips) == 1
    trip = log.get(trips[0]["id"])
    assert 11 < trip["l_per_100km"] < 14
    assert 99 < trip["avg_speed"] < 101
    assert len(trip["track"]) > 10
    # counters ran too, and range uses the drive's economy: 49 L left / ~12.5 L/100 km
    assert 99 < sig.trip["counters"]["A"]["distance_km"] < 101


def test_brief_engine_off_does_not_split_a_drive(tmp_path):
    sig = SignalBus()
    log = TripLog(tmp_path / "t.db")
    rec = TripRecorder(sig, log)
    t = drive(sig, rec, 0.0, 60, speed=50)
    t = key_off(rec, t, END_GRACE_S / 2)
    t = drive(sig, rec, t, 60, speed=50)
    key_off(rec, t, END_GRACE_S + 3)
    assert len(log.list()) == 1


def test_idle_without_moving_is_dropped(tmp_path):
    sig = SignalBus()
    log = TripLog(tmp_path / "t.db")
    rec = TripRecorder(sig, log)
    t = drive(sig, rec, 0.0, 3, speed=0, maf=4.5)
    key_off(rec, t, END_GRACE_S + 3)
    assert log.list() == []


def test_power_cut_mid_drive_keeps_the_trip(tmp_path):
    sig = SignalBus()
    log = TripLog(tmp_path / "t.db")
    rec = TripRecorder(sig, log)
    drive(sig, rec, 0.0, 120, speed=60)  # checkpoints every 15 s, then power dies
    reopened = TripLog(tmp_path / "t.db")
    trips = reopened.list()
    assert len(trips) == 1 and trips[0]["end_ts"] is not None
    assert trips[0]["distance_km"] > 1.5


def test_counter_reset(tmp_path):
    sig = SignalBus()
    log = TripLog(tmp_path / "t.db")
    rec = TripRecorder(sig, log)
    drive(sig, rec, 0.0, 60, speed=60)
    log.reset_counter("A")
    rec.tick(61.0)
    assert sig.trip["counters"]["A"]["distance_km"] < 0.05
    assert sig.trip["counters"]["B"]["distance_km"] > 0.9


def test_sim_vehicle_parks_with_engine_off():
    v = Vehicle()
    cycle = sum(d for d, _ in PROFILE)
    t = 0.0
    seen_off = False
    while t < cycle * DRIVE_LOOPS + PARK_S / 2:
        v.step(0.1)
        t += 0.1
        seen_off |= not v.engine_on
    assert seen_off and v.rpm == 0 and v.maf == 0
    assert v.fuel_level < 75


def test_trip_api(tmp_path):
    sig = SignalBus()
    log = TripLog(tmp_path / "t.db")
    rec = TripRecorder(sig, log)
    t = drive(sig, rec, 0.0, 120, speed=60)
    key_off(rec, t, END_GRACE_S + 3)
    client = TestClient(create_app(sig, trips=log))
    trips = client.get("/api/trips").json()["trips"]
    assert len(trips) == 1
    one = client.get(f"/api/trips/{trips[0]['id']}").json()
    assert "track" in one
    assert client.post("/api/counters/A/reset").json() == {"ok": True}
    assert client.post("/api/counters/Z/reset").status_code == 404
    assert client.delete(f"/api/trips/{trips[0]['id']}").status_code == 200
    assert client.get("/api/trips").json()["trips"] == []
