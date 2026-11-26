"""Body state (doors, locks, lights, belts) from GM's single-wire CAN, listened to passively.

The frame table comes from the vehicle profile (vehicles/…yaml); GM's ids there are
placeholders until mapped on a truck. Turn signals blink, so a lamp counts as "on" for TURN_HOLD_S after its
bit was last seen set, which gives a steady indicator instead of a flicker.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import can
from .bus import SignalBus

TURN_HOLD_S = 1.2
STALE_S = 5.0  # body frames repeat about every second; silence means bus asleep or unplugged
BLINKERS = ("turn_left", "turn_right")


@dataclass(frozen=True)
class BodyFrame:
    name: str
    arb: int                      # GMLAN 13-bit arbitration id (any sender), or -1 when matched by can_id
    signals: dict[str, tuple[int, int]]
    confirmed: bool = False
    can_id: int | None = None     # exact id, for ordinary CAN buses
    inverted: frozenset[str] = frozenset()  # signals where a 0 bit means on
    verified: dict | None = field(default=None, compare=False, hash=False)


def load_body(vehicle: str | None = None) -> list[BodyFrame]:
    """The body frames of a vehicle profile (default: the project's own truck)."""
    from .vehicles import DEFAULT_VEHICLE, load_profile
    return load_profile(vehicle or DEFAULT_VEHICLE).frames


def gmlan_arb(can_id: int) -> int:
    """13-bit arbitration id from a 29-bit GMLAN id."""
    return (can_id >> 13) & 0x1FFF


def gmlan_id(arb: int, sender: int = 0x40, priority: int = 6) -> int:
    return (priority << 26) | (arb << 13) | sender


class BodyDecoder:
    def __init__(self, frames: list[BodyFrame]):
        self.set_frames(frames)

    def set_frames(self, frames: list[BodyFrame]) -> None:
        self.by_arb = {f.arb: f for f in frames if f.can_id is None}
        self.by_id = {f.can_id: f for f in frames if f.can_id is not None}
        self.state: dict[str, bool] = {}
        self.seen: dict[str, float] = {}      # when each signal was last decoded
        self.blink_on: dict[str, float] = {}  # when each blinker bit was last set

    def feed(self, msg: can.Message, now: float | None = None) -> bool:
        frame = self.by_id.get(msg.arbitration_id)
        if frame is None and msg.is_extended_id:
            frame = self.by_arb.get(gmlan_arb(msg.arbitration_id))
        if frame is None:
            return False
        now = now if now is not None else time.time()
        data = bytes(msg.data)
        for name, (byte, bit) in frame.signals.items():
            if byte >= len(data):
                continue
            on = bool(data[byte] >> bit & 1) != (name in frame.inverted)
            self.seen[name] = now
            if name in BLINKERS:
                if on:
                    self.blink_on[name] = now
            else:
                self.state[name] = on
        return True

    def snapshot(self, now: float | None = None) -> dict | None:
        now = now if now is not None else time.time()
        fresh = {k for k, t in self.seen.items() if now - t <= STALE_S}
        if not fresh:
            return None
        out = {k: v for k, v in self.state.items() if k in fresh}
        for b in BLINKERS:
            if b in fresh:
                out[b] = now - self.blink_on.get(b, 0) <= TURN_HOLD_S
        out["hazard"] = bool(out.get("turn_left") and out.get("turn_right"))
        frames = [*self.by_arb.values(), *self.by_id.values()]
        out["confirmed"] = bool(frames) and all(f.confirmed for f in frames)
        return out


class BodyListener:
    """Reads a CAN bus (receive only) and publishes body state on the signal bus."""

    def __init__(self, can_bus: can.BusABC, decoder: BodyDecoder, sig: SignalBus, tap=None):
        self.can_bus = can_bus
        self.decoder = decoder
        self.sig = sig
        self.tap = tap  # sees every frame (the "Map a signal" recorder)

    async def run(self, stop: asyncio.Event) -> None:
        reader = can.AsyncBufferedReader()
        notifier = can.Notifier(self.can_bus, [reader], loop=asyncio.get_running_loop())
        try:
            while not stop.is_set():
                try:
                    msg = await asyncio.wait_for(reader.get_message(), 0.25)
                    if self.tap is not None:
                        self.tap(msg)
                    self.decoder.feed(msg)
                except asyncio.TimeoutError:
                    pass
                self.sig.body = self.decoder.snapshot()
        finally:
            notifier.stop()
