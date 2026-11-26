import json
import shutil
import time

import can
import pytest
import yaml
from fastapi.testclient import TestClient
import jsonschema

from dashd.body import BodyDecoder, gmlan_id
from dashd.bus import SignalBus
from dashd.learn import Learner, Tap, snippet
from dashd.server import create_app
from dashd.settings import Settings
from dashd.vehicles import DEFAULT_VEHICLE, VEHICLES_DIR, ProfileError, list_profiles, load_profile


def test_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(json.loads((VEHICLES_DIR / "schema.json").read_text()))


def test_every_profile_in_the_repo_loads():
    rows = list_profiles(include_abstract=True)
    assert rows and not [r for r in rows if "error" in r], [r["error"] for r in rows if "error" in r]


def test_inheritance_merges_platform_and_generic():
    p = load_profile(DEFAULT_VEHICLE)
    names = {x.name for x in p.pids}
    assert {"rpm", "speed", "coolant", "trans_temp", "oil_pressure"} <= names
    assert p.chain == ["gm/silverado-3500hd-2011-6.0", "gm/gmt900", "generic/obd2"]
    assert p.gauges["redline_rpm"] == 5600 and p.gauges["speed_max"] == 160
    assert p.bus["type"] == "gmlan-swcan" and {f.name for f in p.frames} == {"doors", "locks", "lights", "occupants"}
    assert not any(x.verified for x in p.pids)  # nothing checked on a truck yet


def test_abstract_bases_are_not_offered():
    ids = {r["id"] for r in list_profiles()}
    assert DEFAULT_VEHICLE in ids and "generic/obd2" in ids and "gm/gmt900" not in ids


@pytest.fixture
def root(tmp_path):
    shutil.copytree(VEHICLES_DIR, tmp_path / "v")
    return tmp_path / "v"


def write(root, pid, data):
    path = root / f"{pid}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_child_can_override_remove_and_verify(root):
    write(root, "test/car", {
        "schema": 1, "name": "Test car", "extends": "generic/obd2",
        "pids": {"baro": None, "coolant": {"mode": 1, "pid": 5, "ecu": 0x7E0, "formula": "A-40", "unit": "C", "hz": 2,
                                           "verified": {"by": "someone", "date": "2026-11-26"}}},
        "body": {"bus": {"type": "can", "bitrate": 500000},
                 "frames": {"doors": {"id": 0x3C1, "signals": {"door_driver": [2, 6, "inverted"]}}}},
    })
    p = load_profile("test/car", root)
    by = {x.name: x for x in p.pids}
    assert "baro" not in by and by["coolant"].hz == 2 and by["coolant"].verified["by"] == "someone"
    dec = BodyDecoder(p.frames)
    dec.feed(can.Message(arbitration_id=0x3C1, data=bytes([0, 0, 0b0000_0000, 0, 0, 0, 0, 0]), is_extended_id=False), now=1.0)
    assert dec.snapshot(now=1.0)["door_driver"] is True  # inverted: 0 = open
    dec.feed(can.Message(arbitration_id=0x3C1, data=bytes([0, 0, 0b0100_0000, 0, 0, 0, 0, 0]), is_extended_id=False), now=1.1)
    assert dec.snapshot(now=1.1)["door_driver"] is False


@pytest.mark.parametrize("data,msg", [
    ({"schema": 1, "name": "X car", "extends": "nope/nothing"}, "no vehicle profile"),
    ({"schema": 1, "name": "X car", "colour": "red"}, "Additional properties"),
    ({"schema": 1, "name": "X car", "extends": "generic/obd2",
      "pids": {"rpm": {"mode": 1, "pid": 12, "ecu": 0x7E0, "formula": "__import__('os')", "unit": "rpm", "hz": 1}}}, "formula"),
    ({"schema": 1, "name": "X car", "pids": {"Bad-Name": None}}, "Bad-Name"),
    ({"schema": 1, "name": "X car", "body": {"bus": {"type": "can", "bitrate": 500000},
                                              "frames": {"a": {"arb": 1, "signals": {"x": [0, 0]}}}}}, "`arb` is for GMLAN"),
    ({"schema": 1, "name": "X car", "body": {"bus": {"type": "can", "bitrate": 500000},
                                              "frames": {"a": {"id": 1, "signals": {"x": [0, 0]}},
                                                         "b": {"id": 1, "signals": {"y": [0, 1]}}}}}, "same id"),
    ({"schema": 1, "name": "X car", "body": {"bus": {"type": "can", "bitrate": 500000},
                                              "frames": {"a": {"id": 1, "signals": {"x": [0, 9]}}}}}, "body.frames.a.signals.x"),
    ({"schema": 1, "name": "X car", "gauges": {"shift_rpm": 7000, "redline_rpm": 6000}}, "shift_rpm <= redline_rpm"),
    ({"schema": 1, "name": "X car", "pids": {"rpm": {"mode": 1, "pid": 12, "ecu": 0x7E0, "formula": "A", "unit": "rpm", "hz": 1,
                                                     "verified": {"by": "me"}}}}, "date"),
])
def test_bad_profiles_are_rejected_with_a_useful_message(root, data, msg):
    write(root, "test/bad", data)
    with pytest.raises(ProfileError, match=msg):
        load_profile("test/bad", root)
    assert any(r["id"] == "test/bad" and "error" in r for r in list_profiles(root, include_abstract=True))


def test_extends_loop_is_caught(root):
    write(root, "test/a", {"schema": 1, "name": "A car", "extends": "test/b"})
    write(root, "test/b", {"schema": 1, "name": "B car", "extends": "test/a"})
    with pytest.raises(ProfileError, match="loop"):
        load_profile("test/a", root)


def test_path_escape_is_refused(root):
    with pytest.raises(ProfileError):
        load_profile("../schema", root)


# ---- Map a signal ----

def record(lr, arb_hidden, steps, blinker=True):
    """A body bus with three frames: the hidden signal, a blinking lamp, and a rolling counter."""
    t = lr.t0
    on = False
    n = 0
    for duration, state in steps:
        if state != on:
            lr.mark(state, now=t)
            on = state
        end = t + duration
        while t < end:
            n += 1
            lr.feed(can.Message(arbitration_id=gmlan_id(arb_hidden), is_extended_id=True,
                                data=bytes([0x10 | (0x04 if on else 0), 0, 0, 0])), now=t)
            if blinker:
                lr.feed(can.Message(arbitration_id=gmlan_id(0x0F3), is_extended_id=True,
                                    data=bytes([0x10 if int(t / 0.36) % 2 else 0])), now=t)
            lr.feed(can.Message(arbitration_id=0x200, is_extended_id=False, data=bytes([n & 0xFF, 0])), now=t)
            t += 0.1


def test_learner_finds_the_bit_and_writes_a_snippet_that_loads(root):
    lr = Learner("door_passenger", gmlan=True)
    record(lr, 0x0F1, [(3, False), (3, True), (3, False), (3, True), (3, False)])
    found = lr.analyze()
    best = found[0]
    assert (best["arb"], best["byte"], best["bit"], best["inverted"], best["match"]) == (0x0F1, 0, 2, False, 1.0)
    assert best["toggles"] == best["state_changes"] == 4
    frames = load_profile(DEFAULT_VEHICLE, root).frames
    text = snippet(best, "door_passenger", frames, gmlan=True, by="tester")
    # 0x0F1 already exists ("doors"): the snippet keeps its other signals and moves door_passenger to bit 2.
    doors = yaml.safe_load(text)["body"]["frames"]["doors"]
    assert doors["arb"] == 0x0F1 and doors["signals"]["door_passenger"] == [0, 2] and "door_driver" in doors["signals"]
    child = {"schema": 1, "name": "Tester's truck", "extends": DEFAULT_VEHICLE, **yaml.safe_load(text)}
    write(root, "gm/test-truck", child)
    p = load_profile("gm/test-truck", root)
    doors = next(f for f in p.frames if f.name == "doors")
    assert doors.signals["door_passenger"] == (0, 2) and doors.verified["by"] == "tester"


def test_learner_handles_inverted_bits_and_needs_both_states():
    lr = Learner("locked", gmlan=False)
    with pytest.raises(ValueError):
        lr.analyze()
    t = lr.t0
    for i, state in enumerate([False, True, False, True]):
        if i:
            lr.mark(state, now=t)
        for _ in range(30):
            lr.feed(can.Message(arbitration_id=0x3C1, is_extended_id=False, data=bytes([0, 0 if state else 0x80])), now=t)
            t += 0.1
    best = lr.analyze()[0]
    assert (best["can_id"], best["byte"], best["bit"], best["inverted"], best["arb"]) == (0x3C1, 1, 7, True, None)
    assert "id: 0x3C1" in snippet(best, "locked", [], gmlan=False) and "[1, 7, inverted]" in snippet(best, "locked", [], gmlan=False)


def test_vehicle_and_learn_api(tmp_path):
    switched = []
    tap = Tap()
    app = create_app(SignalBus(), settings=Settings(tmp_path / "s.json"), profile=load_profile(), tap=tap)
    app.state.switch_vehicle = switched.append
    c = TestClient(app)
    assert c.get("/api/vehicle").json()["gauges"]["redline_rpm"] == 5600
    listed = c.get("/api/vehicles").json()
    assert listed["current"] == DEFAULT_VEHICLE and {p["id"] for p in listed["profiles"]} >= {"generic/obd2", DEFAULT_VEHICLE}
    assert c.put("/api/settings", json={"vehicle": "gm/gmt900"}).status_code == 422  # abstract base
    assert c.put("/api/settings", json={"vehicle": "nope/x"}).status_code == 422
    assert c.put("/api/settings", json={"vehicle": "generic/obd2"}).json()["vehicle"] == "generic/obd2"
    assert switched[0].id == "generic/obd2" and c.get("/api/vehicle").json()["id"] == "generic/obd2"
    assert "trans_temp" not in {p["name"] for p in c.get("/api/pids").json()["pids"]}

    assert c.post("/api/learn/analyze").status_code == 409
    assert c.post("/api/learn/start", json={"signal": "rm -rf"}).status_code == 422
    assert c.post("/api/learn/start", json={"signal": "Hood"}).json()["signal"] == "hood"
    for state in (False, True, False, True, False):
        if state != c.get("/api/learn/status").json()["on"]:
            c.post("/api/learn/mark", json={"on": state})
        time.sleep(0.45)  # past the settle time after a press
        for _ in range(10):
            tap(can.Message(arbitration_id=0x400, is_extended_id=False, data=bytes([0x03 if state else 0x01])))
    out = c.post("/api/learn/analyze").json()
    assert out["changes"] == 4 and out["candidates"][0]["byte"] == 0 and out["candidates"][0]["bit"] == 1
    assert "hood: [0, 1]" in out["candidates"][0]["snippet"]
    assert c.post("/api/learn/stop").json() == {"ok": True}
