import json

import pytest
from fastapi.testclient import TestClient

from dashd.bus import SignalBus
from dashd.server import create_app
from dashd.sim.route import TRACKS, Route, dist, track_path
from dashd.sim.vehicle import PARK_S, Vehicle

# About 2 km east along a parallel, then a sharp left turn north into a slow 400 m street.
EAST = [(49.0, -122.0 + i * 0.00275) for i in range(11)]  # ~200 m legs
NORTH = [(49.0 + i * 0.0009, EAST[-1][1]) for i in range(1, 5)]  # ~100 m legs
POINTS = EAST + NORTH
SPEEDS = [90.0] * (len(EAST) - 1) + [30.0] * len(NORTH)


def make_route():
    return Route(POINTS, SPEEDS, name="Test road")


def test_geometry_and_position():
    r = make_route()
    assert r.length == pytest.approx(sum(dist(a, b) for a, b in zip(POINTS, POINTS[1:])))
    lat, lon, heading = r.position(r.cum[3] + 10)
    assert lat == pytest.approx(49.0, abs=1e-6) and heading == pytest.approx(90, abs=1)
    _, _, heading = r.position(r.length - 1)
    assert heading == pytest.approx(0, abs=1) or heading == pytest.approx(360, abs=1)
    assert r.position(r.length + 500)[:2] == pytest.approx(POINTS[-1])


def test_brakes_before_slow_road_and_end():
    r = make_route()
    corner = r.cum[len(EAST) - 1]
    assert r.target_speed(corner - 1000, 90) == 90  # far away: road speed
    assert r.target_speed(corner - 60, 90) < 60  # close: slowing for the 30 zone
    assert r.target_speed(corner + 10, 30) <= 30
    assert r.target_speed(r.length - 2, 10) == 0  # stopping at the end


def test_turn_signal_ahead_of_corner():
    r = make_route()
    corner = r.cum[len(EAST) - 1]
    assert r.turn_ahead(corner - 500) is None
    assert r.turn_ahead(corner - 80) == "L"


def test_speeds_must_match_segments():
    with pytest.raises(ValueError):
        Route(POINTS, SPEEDS[:-1])


def test_vehicle_drives_route_parks_and_restarts():
    v = Vehicle()
    v.set_route(make_route())
    assert v.route_mode
    top, slow_zone = 0.0, []
    corner = v.route.cum[len(EAST) - 1]
    for _ in range(int(600 / 0.1)):
        v.step(0.1)
        top = max(top, v.speed)
        if v.route_d > corner + 50 and not v.parked():
            slow_zone.append(v.speed)
        if v.parked():
            break
    assert v.parked() and not v.engine_on
    assert 85 <= top <= 90.01
    assert max(slow_zone) <= 30.5
    assert v.route_d == pytest.approx(v.route.length, abs=5)
    for _ in range(int((PARK_S + 2) / 0.1)):
        v.step(0.1)
    assert v.engine_on and v.route_d < 50  # keyed on again at the start


def test_route_without_speeds_keeps_scripted_profile():
    v = Vehicle()
    v.set_route(Route(POINTS))
    assert not v.route_mode
    v.step(10)
    assert v.speed == v.target_speed()


def test_track_names_are_checked():
    with pytest.raises(ValueError):
        track_path("../etc/passwd")
    with pytest.raises(FileNotFoundError):
        track_path("no_such_route")


def test_sim_route_api(tmp_path):
    name = "zz_test_route"
    f = TRACKS / f"{name}.json"
    f.write_text(json.dumps({"name": "Test road", "points": POINTS, "speeds": SPEEDS}))
    try:
        app = create_app(SignalBus())
        client = TestClient(app)
        assert client.get("/api/sim/routes").json()["sim"] is False
        assert client.post("/api/sim/route", json={"id": name}).status_code == 409

        app.state.vehicle = Vehicle()
        routes = client.get("/api/sim/routes").json()
        assert {"id": name, "name": "Test road"} in routes["routes"]
        r = client.post("/api/sim/route", json={"id": name})
        assert r.status_code == 200 and r.json()["current"] == name
        assert client.get("/api/sim/routes").json()["current"] == name
        assert len(client.get("/api/track").json()["points"]) == len(POINTS)
        assert client.post("/api/sim/route", json={"id": "nope"}).status_code == 404
    finally:
        f.unlink()
