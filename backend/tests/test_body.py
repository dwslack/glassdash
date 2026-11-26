import asyncio

import can

from dashd.body import BodyDecoder, BodyListener, gmlan_arb, gmlan_id, load_body
from dashd.bus import SignalBus
from dashd.sim import body as body_sim
from dashd.sim.vehicle import Vehicle


def test_gmlan_id_roundtrip():
    assert gmlan_arb(gmlan_id(0x0F1, sender=0x58)) == 0x0F1


def test_decode_and_staleness():
    frames = load_body()
    dec = BodyDecoder(frames)
    doors = next(f for f in frames if f.name == "doors")
    dec.feed(body_sim.encode(doors, {"door_driver": True, "tailgate": True}), now=100)
    s = dec.snapshot(now=100)
    assert s["door_driver"] and s["tailgate"] and not s["door_passenger"]
    assert s["confirmed"] is False  # all placeholders
    assert dec.snapshot(now=200) is None  # bus went quiet


def test_ignores_standard_ids_and_unknown_frames():
    dec = BodyDecoder(load_body())
    assert not dec.feed(can.Message(arbitration_id=0x0F1, data=b"\xff", is_extended_id=False))
    assert not dec.feed(can.Message(arbitration_id=gmlan_id(0x123), data=b"\xff", is_extended_id=True))


def test_blinker_holds_between_flashes():
    frames = load_body()
    lights = next(f for f in frames if f.name == "lights")
    dec = BodyDecoder(frames)
    dec.feed(body_sim.encode(lights, {"turn_left": True}), now=10.0)
    dec.feed(body_sim.encode(lights, {"turn_left": False}), now=10.4)  # lamp off phase
    assert dec.snapshot(now=10.5)["turn_left"] is True
    dec.feed(body_sim.encode(lights, {}), now=12.0)
    assert dec.snapshot(now=12.0)["turn_left"] is False


def test_sim_parks_with_door_open_then_locks():
    v = Vehicle()
    while not v.parked():
        v.step(0.1)
    for _ in range(50):  # 5 s into the park
        v.step(0.1)
    st = body_sim.body_state(v)
    assert st["door_driver"] and not st["locked"] and not st["low_beam"]
    for _ in range(250):  # 30 s
        v.step(0.1)
    st = body_sim.body_state(v)
    assert st["locked"] and not st["door_driver"]


def test_listener_end_to_end():
    async def go():
        frames = load_body()
        rx = can.Bus(interface="virtual", channel="test-body")
        tx = can.Bus(interface="virtual", channel="test-body")
        v = Vehicle()
        for _ in range(400):  # 20 s in: driving, lights on
            v.step(0.05)
        sig = SignalBus()
        stop = asyncio.Event()
        tasks = [asyncio.create_task(BodyListener(rx, BodyDecoder(frames), sig).run(stop)),
                 asyncio.create_task(body_sim.broadcast(tx, frames, v, stop, period=0.05))]
        await asyncio.sleep(0.6)
        stop.set()
        await asyncio.gather(*tasks)
        rx.shutdown()
        tx.shutdown()
        return sig

    body = asyncio.run(go()).body
    assert body["low_beam"] and body["belt_driver"] and not body["locked"]
    assert "body" in SignalBus().snapshot()
