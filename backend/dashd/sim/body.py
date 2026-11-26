"""Fake body bus: broadcasts door, lock, light and belt frames from the Vehicle's state.

Frames are encoded from the same vehicle-profile table the decoder uses, so the
sim exercises the decoder end to end. A real truck's ids will differ (see vehicles/gm/gmt900.yaml).
"""
from __future__ import annotations

import asyncio

import can

from ..body import BodyFrame, gmlan_id
from .vehicle import DRIVE_LOOPS, PARK_S, PROFILE, Vehicle

BLINK_S = 0.36  # GM flasher half-period, roughly


def body_state(v: Vehicle) -> dict[str, bool]:
    parked_for = v.parked_for
    blink = int(v.t / BLINK_S) % 2 == 0
    driving = not v.parked()
    if v.route_mode:
        # Signal for real turns in the road; high beams on fast open road.
        turn = v.route.turn_ahead(v.route_d) if driving and v.speed < 70 else None
        high = driving and v.speed >= 90
        left, right = turn == "L", turn == "R"
    else:
        # Scripted loop: 0-8 idle, 8-28 accel, 28-68 cruise 100, 68-78 slow to 60, 78-103 at 60, 103-118 stop.
        loop_t = (v.t % (v.cycle_s * DRIVE_LOOPS + PARK_S)) % v.cycle_s
        edges = [sum(d for d, _ in PROFILE[:i]) for i in range(len(PROFILE) + 1)]
        high = driving and edges[2] + 5 <= loop_t < edges[3] - 5
        left = driving and edges[3] - 3 <= loop_t < edges[4]
        right = driving and edges[5] <= loop_t < edges[5] + 6
    return {**{
        "door_driver": 3 <= parked_for < 15,
        "door_passenger": False,
        "door_rear_left": False,
        "door_rear_right": False,
        "tailgate": 8 <= parked_for < 20,
        "hood": False,
        "locked": parked_for >= 25,
        "parking_lights": driving,
        "low_beam": driving,
        "high_beam": high,
        "fog": False,
        "turn_left": left and blink,
        "turn_right": right and blink,
        "brake": driving and (v.accel < -0.5 or v.speed < 1),
        "reverse": False,
        "belt_driver": driving or 0 <= parked_for < 2,
        "belt_passenger": False,
        "passenger_seated": False,
        "parking_brake": parked_for >= 1,
    }, **v.body_overrides}


def encode(frame: BodyFrame, state: dict[str, bool]) -> can.Message:
    data = bytearray(8)
    for name, (byte, bit) in frame.signals.items():
        if bool(state.get(name)) != (name in frame.inverted):
            data[byte] |= 1 << bit
    if frame.can_id is not None:
        return can.Message(arbitration_id=frame.can_id, data=bytes(data), is_extended_id=frame.can_id > 0x7FF)
    return can.Message(arbitration_id=gmlan_id(frame.arb), data=bytes(data), is_extended_id=True)


async def broadcast(can_bus: can.BusABC, frames, v: Vehicle, stop: asyncio.Event, period: float = 0.1) -> None:
    """Body modules keep talking with the key off (doors, locks), so this runs even when parked.
    frames is a list, or a function returning one (so a vehicle switch takes effect live)."""
    while not stop.is_set():
        state = body_state(v)
        for f in (frames() if callable(frames) else frames):
            can_bus.send(encode(f, state))
        await asyncio.sleep(period)
