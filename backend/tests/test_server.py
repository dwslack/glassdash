from fastapi.testclient import TestClient

from dashd.bus import SignalBus
from dashd.server import create_app
from dashd.sim.gps import DEFAULT_TRACK


def test_snapshot_and_websocket():
    sig = SignalBus()
    sig.set("rpm", 1500.0, "rpm")
    sig.set_gps({"lat": 49.1, "lon": -122.6, "heading": 90, "speed": 50})
    client = TestClient(create_app(sig, track=DEFAULT_TRACK))
    snap = client.get("/api/snapshot").json()
    assert snap["signals"]["rpm"]["value"] == 1500.0
    assert snap["signals"]["rpm"]["stale"] is False
    assert len(client.get("/api/track").json()["points"]) > 2
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["gps"]["lat"] == 49.1


def test_staleness():
    sig = SignalBus(stale_after=2)
    sig.set("coolant", 90, "C", ts=100.0)
    assert sig.snapshot(now=101)["signals"]["coolant"]["stale"] is False
    assert sig.snapshot(now=103)["signals"]["coolant"]["stale"] is True
