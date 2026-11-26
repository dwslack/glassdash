"""ECU simulator + poller talking over a python-can virtual bus."""
import asyncio

import can

from dashd.bus import SignalBus
from dashd.obd import Poller
from dashd.pids import Pid, load_pids
from dashd.sim.ecu import EcuSim
from dashd.sim.gps import GpsSim
from dashd.sim.vehicle import Vehicle


def test_poller_reads_all_signals_from_sim():
    async def go():
        vehicle = Vehicle()
        for _ in range(600):  # 30 s into the drive: moving, engine warming
            vehicle.step(0.05)
        a = can.Bus(interface="virtual", channel="test-roundtrip")
        b = can.Bus(interface="virtual", channel="test-roundtrip")
        ecu = EcuSim(b, vehicle).start()
        sig = SignalBus()
        stop = asyncio.Event()
        task = asyncio.create_task(Poller(a, load_pids(), sig).run(stop))
        await asyncio.sleep(12)  # slowest PID (baro) polls every 10 s
        stop.set()
        await task
        ecu.stop()
        a.shutdown()
        b.shutdown()
        return vehicle, sig

    vehicle, sig = asyncio.run(go())
    s = sig.signals
    assert set(s) == {p.name for p in load_pids()}  # the sim answers every PID in the table
    assert abs(s["speed"].value - vehicle.speed) <= 2
    assert abs(s["rpm"].value - vehicle.rpm) < 100
    assert abs(s["trans_temp"].value - vehicle.trans_temp) <= 1
    assert 13.5 < s["voltage"].value < 15
    assert abs(s["maf"].value - vehicle.maf) < 3
    assert abs(s["fuel_level"].value - vehicle.fuel_level) < 1


def test_key_off_ecus_go_quiet():
    async def go():
        vehicle = Vehicle()
        vehicle.engine_on = False
        a = can.Bus(interface="virtual", channel="test-keyoff")
        b = can.Bus(interface="virtual", channel="test-keyoff")
        ecu = EcuSim(b, vehicle).start()
        sig = SignalBus()
        stop = asyncio.Event()
        task = asyncio.create_task(Poller(a, load_pids(), sig, timeout=0.05).run(stop))
        await asyncio.sleep(0.5)
        stop.set()
        await task
        ecu.stop()
        a.shutdown()
        b.shutdown()
        return sig

    sig = asyncio.run(go())
    assert sig.signals == {}
    assert sig.errors["rpm"] >= 1


def test_unknown_pid_counts_as_error():
    async def go():
        a = can.Bus(interface="virtual", channel="test-unknown")
        b = can.Bus(interface="virtual", channel="test-unknown")
        ecu = EcuSim(b, Vehicle()).start()
        sig = SignalBus()
        bogus = Pid("bogus", 0x01, 0x99, 0x7E0, "A", "", 5)
        stop = asyncio.Event()
        task = asyncio.create_task(Poller(a, [bogus], sig, timeout=0.05).run(stop))
        await asyncio.sleep(0.5)
        stop.set()
        await task
        ecu.stop()
        a.shutdown()
        b.shutdown()
        return sig

    sig = asyncio.run(go())
    assert "bogus" not in sig.signals
    assert sig.errors["bogus"] >= 1


def test_gps_follows_odometer():
    v = Vehicle()
    gps = GpsSim(v)
    start = gps.fix()
    v.distance_m = 5000
    moved = gps.fix()
    assert (start["lat"], start["lon"]) != (moved["lat"], moved["lon"])
    assert 90 < moved["heading"] < 180  # heading roughly east-southeast
