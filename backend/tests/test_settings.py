import pytest
from fastapi.testclient import TestClient

from dashd.bus import SignalBus
from dashd.server import create_app
from dashd.settings import DEFAULTS, Settings
from dashd.trips import TripLog, TripRecorder


def test_defaults_and_persist(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s["tank_l"] == DEFAULTS["tank_l"]
    s.update({"tank_l": 129, "night": "on"})
    again = Settings(tmp_path / "s.json")
    assert again["tank_l"] == 129 and again["night"] == "on"


@pytest.mark.parametrize("bad", [{"nope": 1}, {"tank_l": 5}, {"tank_l": "big"}, {"night": "dusk"}, {"alert_sound": 1},
                                 {"dash_views": []}, {"dash_views": {"nope": {"hero": "speed", "tiles": []}}},
                                 {"dash_views": {"tow": {"hero": "speed"}}}, {"dash_views": {"tow": {"hero": "x" * 40, "tiles": []}}},
                                 {"dash_views": {"tow": {"hero": "speed", "tiles": ["a"] * 17}}}, {"auto_tow_view": "yes"}])
def test_rejects_bad_values(tmp_path, bad):
    with pytest.raises(ValueError):
        Settings(tmp_path / "s.json").update(bad)


def test_corrupt_file_falls_back(tmp_path):
    (tmp_path / "s.json").write_text("{not json")
    assert Settings(tmp_path / "s.json")["tank_l"] == DEFAULTS["tank_l"]


def test_api(tmp_path):
    client = TestClient(create_app(SignalBus(), settings=Settings(tmp_path / "s.json")))
    assert client.get("/api/settings").json()["night"] == "auto"
    assert client.put("/api/settings", json={"fuel_cal": 1.1}).json()["fuel_cal"] == 1.1
    r = client.put("/api/settings", json={"fuel_cal": 9})
    assert r.status_code == 422 and "fuel_cal" in r.json()["detail"]


def test_fuel_cal_and_tank_apply_to_trips(tmp_path):
    sig = SignalBus()
    settings = Settings(None)
    settings.update({"fuel_cal": 1.2, "tank_l": 129})
    rec = TripRecorder(sig, TripLog(tmp_path / "t.db"), settings)
    for i in range(10):
        t = 100.0 + i * 0.2
        sig.set("rpm", 1500, "rpm", ts=t)
        sig.set("speed", 100, "km/h", ts=t)
        sig.set("maf", 38, "g/s", ts=t)
        sig.set("fuel_level", 50, "%", ts=t)
        rec.tick(t)
    assert 14.5 < sig.trip["fuel_lph"] < 15.5  # 12.5 L/h x 1.2
    assert sig.trip["fuel_left_l"] == 64.5


def test_string_settings(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.update({"mapillary_token": "  MLY|123|abc  ", "basemap": "satellite"})
    assert s["mapillary_token"] == "MLY|123|abc"
    with pytest.raises(ValueError):
        s.update({"esri_key": 5})
    with pytest.raises(ValueError):
        s.update({"basemap": "terrain"})


def test_tiles_served_with_range(tmp_path):
    (tmp_path / "maps").mkdir()
    (tmp_path / "maps" / "bc.pmtiles").write_bytes(bytes(range(256)) * 4)
    client = TestClient(create_app(SignalBus(), map_dir=tmp_path))
    r = client.get("/tiles/bc.pmtiles", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20))
    assert client.head("/tiles/missing.pmtiles").status_code == 404


def test_dash_views_round_trip(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.update({"dash_views": {"tow": {"hero": "speedtach", "tiles": ["trans_temp", "clock"], "extra": 1}}, "auto_tow_view": False})
    again = Settings(tmp_path / "s.json")
    assert again["dash_views"] == {"tow": {"hero": "speedtach", "tiles": ["trans_temp", "clock"]}}
    assert again["auto_tow_view"] is False
    assert DEFAULTS["dash_views"] == {}  # the default dict is never mutated
