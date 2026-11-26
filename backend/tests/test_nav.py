import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashd.bus import SignalBus
from dashd.geo import bearing, offset
from dashd.nav.guidance import OFF_ROUTE_FIXES, Navigator
from dashd.nav.places import Places
from dashd.nav.route import NavRoute
from dashd.nav.service import NavService
from dashd.server import create_app

# A real Valhalla reply (OSRM format, banner + voice instructions) for an 11.9 km drive in Utrecht,
# from Valhalla's own test extract. BC data gives the same structure.
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "valhalla_utrecht_osrm.json").read_text())
DEST = {"lat": 52.06, "lon": 5.07, "name": "Test destination"}


def route():
    return NavRoute.from_osrm(FIXTURE, dest=DEST)


def fix_at(r: NavRoute, d: float, speed=50.0):
    i = max(0, min(len(r.points) - 2, next(k for k in range(len(r.cum)) if r.cum[k] >= d) - 1))
    a, b = r.points[i], r.points[i + 1]
    seg = r.cum[i + 1] - r.cum[i]
    f = (d - r.cum[i]) / seg if seg else 0
    lat, lon = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
    return {"lat": lat, "lon": lon, "heading": bearing(a, b) if a != b else 0, "speed": speed}


def test_parse_route():
    r = route()
    assert r.length == pytest.approx(FIXTURE["routes"][0]["distance"], rel=0.01)
    assert r.steps[0].type == "depart" and r.steps[-1].type == "arrive"
    assert [s.start_m for s in r.steps] == sorted(s.start_m for s in r.steps)
    rb = next(s for s in r.steps if s.type == "roundabout")
    assert rb.exit == 2
    assert any(s.voice for s in r.steps) and any(s.banners for s in r.steps)
    assert r.lanes and all("valid" in ln for spot in r.lanes for ln in spot.lanes)
    assert r.remaining_time(0) == pytest.approx(FIXTURE["routes"][0]["duration"], rel=0.02)
    assert len(r.segment_speeds()) == len(r.points) - 1


def test_drive_the_route():
    r = route()
    nav = Navigator(r)
    spoken, seen_next, last_eta = [], [], None
    now = 1000.0
    d = 0.0
    while d < r.length + 20:
        assert not nav.update(fix_at(r, min(d, r.length)), now)
        st = nav.state(now)
        if st["speak"]["text"] and (not spoken or spoken[-1][0] != st["speak"]["id"]):
            spoken.append((st["speak"]["id"], st["speak"]["text"]))
        if not seen_next or seen_next[-1] != st["next"]["instruction"]:
            seen_next.append(st["next"]["instruction"])
        assert st["next"]["distance_m"] >= 0
        if last_eta is not None and st["status"] == "navigating":
            assert st["remaining_s"] <= last_eta + 1
        last_eta = st["remaining_s"]
        d += 10
        now += 1
    assert nav.status == "arrived"
    texts = [t for _, t in spoken]
    assert texts[-1] == "You have arrived at Test destination."
    assert any("roundabout" in t for t in texts)
    assert any(t.startswith("In ") for t in texts)  # early warnings like "In 400 meters, ..."
    assert len(texts) == len(set(spoken))  # every prompt once
    # Every maneuver shows up as "next" in order.
    instr = [s.instruction for s in r.steps[1:]]
    assert [x for x in seen_next if x in instr] == instr[:len([x for x in seen_next if x in instr])]
    assert len(seen_next) >= len(instr) - 2


def test_off_route_triggers_reroute_once_per_gap():
    r = route()
    nav = Navigator(r)
    now = 0.0
    for d in range(0, 600, 20):
        nav.update(fix_at(r, d), now)
        now += 1
    on = fix_at(r, 600)
    away = {**on, **dict(zip(("lat", "lon"), offset((on["lat"], on["lon"]), 150, 150)))}
    results = []
    for _ in range(OFF_ROUTE_FIXES + 4):
        results.append(nav.update(away, now))
        now += 0.5
    assert results.count(True) == 1 and results[OFF_ROUTE_FIXES - 1] is True
    assert nav.status == "offroute"


def test_wrong_way_counts_as_off_route():
    r = route()
    nav = Navigator(r)
    for d in range(0, 1500, 25):
        nav.update(fix_at(r, d), 0)
    f = fix_at(r, 1500)
    back = {**f, "heading": (f["heading"] + 180) % 360, "speed": 40}
    assert any(nav.update(back, 100 + k) for k in range(OFF_ROUTE_FIXES))


def test_lane_hint_shown_only_before_its_intersection():
    r = route()
    spot = next(s for s in r.lanes if not all(x["valid"] for x in s.lanes))
    nav = Navigator(r)
    for d in range(0, int(spot.at_m) - 100, 50):
        nav.update(fix_at(r, d), 0)
    nav.update(fix_at(r, spot.at_m - 100), 0)
    st = nav.state(0)
    if r.steps[r.step_at(spot.at_m - 100)].end_m + 5 >= spot.at_m:
        assert st["lanes"] == spot.lanes
    nav.update(fix_at(r, spot.at_m + 40), 0)
    assert nav.state(0)["lanes"] != spot.lanes or len([s for s in r.lanes if s.lanes == spot.lanes]) > 1


class FakeRouter:
    def __init__(self):
        self.calls = []

    def route(self, origin, dest, heading=None):
        self.calls.append((origin, dest, heading))
        return NavRoute.from_osrm(FIXTURE, dest=dest)


def make_places():
    p = Places(":memory:")
    p.add([("Chilliwack", "", "city", 49.157, -121.951), ("Chilliwack Lake", "", "suburb", 49.08, -121.42),
           ("Tim Hortons", "Chilliwack", "cafe", 49.16, -121.95), ("Tim Hortons", "Hope", "cafe", 49.38, -121.44),
           ("Lougheed Highway", "Maple Ridge", "street", 49.22, -122.60), ("45900 Wellington Avenue", "Chilliwack", "address", 49.16, -121.96),
           ("Harrison Hot Springs", "", "village", 49.30, -121.78), ("Mountain Road", "Abbotsford", "street", 49.0, -122.2)])
    p.commit()
    return p


def test_place_search_ranking():
    p = make_places()
    near = (49.157, -121.951)
    assert p.search("tim hor", near)[0]["city"] == "Chilliwack"  # the closer one first
    assert p.search("chilliwack", near)[0]["name"] == "Chilliwack"
    assert p.search("harrison hot")[0]["kind"] == "village"
    assert p.search("lougheed hwy")[0]["name"] == "Lougheed Highway"  # abbreviation
    assert p.search("mountain rd")[0]["name"] == "Mountain Road"
    assert p.search("45900 wellington")[0]["kind"] == "address"
    assert p.search('"; drop') == [] and p.search("") == []


def test_service_and_api():
    sig = SignalBus()
    router = FakeRouter()
    followed = []
    nav = NavService(sig, None, router=router, places=make_places(), on_route=lambda r, re: followed.append(re))
    client = TestClient(create_app(sig, nav=nav))
    assert client.get("/api/nav/status").json()["routing"] is True
    assert client.get("/api/places", params={"q": "tim"}).json()["results"][0]["name"] == "Tim Hortons"
    assert client.post("/api/nav", json=DEST).status_code == 409  # no GPS yet

    start = fix_at(route(), 0, speed=30)
    sig.set_gps(start)
    r = client.post("/api/nav", json=DEST)
    assert r.status_code == 200 and r.json()["distance_m"] > 10000
    assert router.calls[-1][2] is not None  # moving, so the heading is passed on
    assert followed == [False]
    snap = client.get("/api/snapshot").json()
    assert snap["nav"]["dest"]["name"] == "Test destination" and snap["nav"]["route_id"] == 1
    assert len(client.get("/api/nav/route").json()["points"]) == len(route().points)

    # Leave the route: the service asks for a reroute and the sim follows the new one.
    on = fix_at(route(), 800)
    away = {**on, **dict(zip(("lat", "lon"), offset((on["lat"], on["lon"]), 200, 0)))}
    need = False
    for _ in range(OFF_ROUTE_FIXES):
        sig.set_gps(away)
        need = nav.tick() or need
    assert need
    asyncio.run(nav._reroute())
    assert followed == [False, True] and nav.route_id == 2
    assert client.delete("/api/nav").json()["ok"] and client.get("/api/snapshot").json()["nav"] is None


def test_api_without_nav_data():
    sig = SignalBus()
    nav = NavService(sig, Path("/nonexistent/nav"))
    client = TestClient(create_app(sig, nav=nav))
    st = client.get("/api/nav/status").json()
    assert st["routing"] is False and st["search"] is False and st["problems"]
    assert client.get("/api/places", params={"q": "x"}).status_code == 503
    sig.set_gps({"lat": 49.1, "lon": -122.3, "heading": 0, "speed": 0})
    assert client.post("/api/nav", json=DEST).status_code == 409


def test_real_valhalla():
    """Runs only with real tiles: GLASSDASH_NAV_TEST_DIR=path/with/tiles.tar (Valhalla's Utrecht test extract)."""
    import os

    d = os.environ.get("GLASSDASH_NAV_TEST_DIR")
    if not d:
        pytest.skip("set GLASSDASH_NAV_TEST_DIR to run against real routing tiles")
    from dashd.nav.router import RouteError, Router

    router = Router(Path(d))
    t = time.time()
    r = router.route((52.0907, 5.1214), DEST, heading=340)
    assert time.time() - t < 2 and r.length > 5000 and r.steps[-1].type == "arrive"
    with pytest.raises(RouteError):
        router.route((52.0907, 5.1214), {"lat": 49.157, "lon": -121.951})  # Chilliwack, BC: not in these tiles


def test_unmapped_house_number_falls_back_to_street():
    p = make_places()
    p.add([("Yale Road", "Chilliwack", "street", 49.165, -121.96), ("Yale Road", "Hope", "street", 49.38, -121.44),
           ("650 Yale Road", "Hope", "address", 49.381, -121.441)])
    p.commit()
    near = (49.157, -121.951)
    r = p.search("46000 Yale Rd", near)
    assert r[0]["name"] == "Yale Road" and r[0]["city"] == "Chilliwack" and "46000" in r[0]["note"]
    r = p.search("46000 yale road chilliwack", near)
    assert r[0]["city"] == "Chilliwack" and r[0]["kind"] == "street"
    r = p.search("650 Yale", near)  # a mapped number is still found exactly, no note
    assert r[0]["kind"] == "address" and "note" not in r[0]
    assert "note" not in p.search("45900 wellington", near)[0]


def test_saved_places(tmp_path):
    from dashd.nav.favorites import Favorites

    sig = SignalBus()
    favs = Favorites(tmp_path / "favorites.json")
    nav = NavService(sig, None, router=FakeRouter(), places=make_places(), favorites=favs)
    client = TestClient(create_app(sig, nav=nav))
    home = client.post("/api/favorites", json={"name": "Home", "lat": 49.17, "lon": -121.96}).json()
    client.post("/api/favorites", json={"name": "home", "lat": 49.18, "lon": -121.97})  # same name replaces
    assert [f["lat"] for f in client.get("/api/favorites").json()["favorites"]] == [49.18]
    assert client.post("/api/favorites", json={"name": " ", "lat": 1, "lon": 1}).status_code == 422
    r = client.get("/api/places", params={"q": "hom", "lat": 49.157, "lon": -121.951}).json()["results"]
    assert r[0]["kind"] == "saved" and r[0]["name"] == "home" and r[0]["distance_m"] > 0
    assert Favorites(tmp_path / "favorites.json").items[0]["name"] == "home"  # persisted
    fid = client.get("/api/favorites").json()["favorites"][0]["id"]
    assert client.delete(f"/api/favorites/{fid}").json()["ok"]
    assert client.delete(f"/api/favorites/{home['id']}").status_code == 404
