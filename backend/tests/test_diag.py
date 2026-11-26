"""Trouble codes end to end: poller + ISO-TP + fake ECUs on a virtual bus."""
import asyncio

import can
import pytest
from fastapi.testclient import TestClient

from dashd.bus import SignalBus
from dashd.diag import Diagnostics
from dashd.dtc import decode_dtc, encode_dtc, parse_dtc_reply, parse_mil
from dashd.isotp import segment
from dashd.obd import Poller
from dashd.pids import load_pids
from dashd.server import create_app
from dashd.sim.ecu import EcuSim
from dashd.sim.vehicle import Vehicle


@pytest.mark.parametrize("code", ["P0300", "P0420", "C1234", "B0A1F", "U0100", "P1101"])
def test_dtc_roundtrip(code):
    assert decode_dtc(*encode_dtc(code)) == code


def test_parse_replies():
    assert parse_dtc_reply(0x03, bytes([0x43, 0])) == []
    assert parse_dtc_reply(0x03, bytes([0x43, 1, 0x01, 0x71])) == ["P0171"]
    assert parse_dtc_reply(0x03, bytes([0x7F, 0x03, 0x31])) is None
    assert parse_mil(bytes([0x41, 0x01, 0x83, 0, 0, 0])) == {"mil": True, "count": 3}


def test_segment_multiframe():
    frames = segment(bytes(range(20)))
    assert frames[0][:2] == bytes([0x10, 20])
    assert [f[0] for f in frames[1:]] == [0x21, 0x22]


async def _with_sim(fn, vehicle=None):
    vehicle = vehicle or Vehicle()
    a = can.Bus(interface="virtual", channel="test-diag")
    b = can.Bus(interface="virtual", channel="test-diag")
    ecu = EcuSim(b, vehicle).start()
    sig = SignalBus()
    poller = Poller(a, load_pids(), sig)
    stop = asyncio.Event()
    task = asyncio.create_task(poller.run(stop))
    await asyncio.sleep(0.3)
    try:
        return await fn(Diagnostics(poller, sig), vehicle)
    finally:
        stop.set()
        await task
        ecu.stop()
        a.shutdown()
        b.shutdown()


def test_read_codes_multiframe_while_polling():
    async def go(diag, vehicle):
        return await diag.read()

    r = asyncio.run(_with_sim(go))
    ecm = r["ecus"]["ECM"]
    # three stored codes need a multi-frame reply
    assert [c["code"] for c in ecm["stored"]] == ["P0171", "P0300", "P0420"]
    assert ecm["stored"][1]["desc"].startswith("Random")
    assert [c["code"] for c in ecm["pending"]] == ["P0442"]
    assert [c["code"] for c in ecm["permanent"]] == ["P0420"]
    assert r["ecus"]["TCM"]["stored"] == []
    assert r["mil"] == {"mil": True, "count": 3}


def test_clear_keeps_permanent_codes():
    async def go(diag, vehicle):
        cleared = await diag.clear()
        return cleared, await diag.read()

    cleared, r = asyncio.run(_with_sim(go))
    assert cleared == {"ECM": True, "TCM": True}
    assert r["ecus"]["ECM"]["stored"] == []
    assert [c["code"] for c in r["ecus"]["ECM"]["permanent"]] == ["P0420"]
    assert r["mil"]["mil"] is False


def test_clear_api_refuses_while_moving():
    sig = SignalBus()
    sig.set("speed", 50, "km/h")
    client = TestClient(create_app(sig, diag=Diagnostics(poller=None, sig=sig)))
    assert client.post("/api/dtcs/clear").status_code == 409
    sig.set("speed", 0, "km/h")
    assert Diagnostics(poller=None, sig=sig).stopped()
