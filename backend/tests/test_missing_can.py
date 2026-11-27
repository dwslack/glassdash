"""dashd on a Pi with no CAN HAT (bench, or the HAT not detected) must still serve the dash."""
import pytest
from fastapi.testclient import TestClient

from dashd.__main__ import build
from dashd.sim.gps import DEFAULT_TRACK


@pytest.mark.parametrize("gps", ["", "127.0.0.1:1"])  # no GPS, and a gpsd that isn't there
def test_starts_without_can_interfaces(tmp_path, gps):
    app = build("socketcan", "glassdash-nope0", None, DEFAULT_TRACK, tmp_path,
                body_channel="glassdash-nope1", nav_dir=tmp_path / "nav", gps=gps)
    with TestClient(app) as client:
        assert client.get("/api/settings").status_code == 200
        assert app.state.body_bus_up is False
