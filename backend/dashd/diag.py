"""Diagnostics service: trouble codes and check-engine status over the shared poller."""
from __future__ import annotations

import asyncio
import time

from .bus import SignalBus
from .dtc import CLEAR, ECUS, KINDS, describe, parse_dtc_reply, parse_mil
from .obd import Poller

MIL_EVERY_S = 30.0


class Diagnostics:
    def __init__(self, poller: Poller, sig: SignalBus):
        self.poller = poller
        self.sig = sig
        self.last_read: dict | None = None

    async def mil(self) -> dict | None:
        return parse_mil(await self.poller.request(ECUS["ECM"], bytes([0x01, 0x01]), timeout=0.3))

    async def read(self) -> dict:
        ecus = {}
        for name, ecu in ECUS.items():
            out = {}
            for kind, mode in KINDS.items():
                codes = parse_dtc_reply(mode, await self.poller.request(ecu, bytes([mode]), timeout=1.0))
                out[kind] = None if codes is None else [{"code": c, "desc": describe(c)} for c in codes]
            ecus[name] = out
        status = await self.mil()
        if status is not None:
            self.sig.mil = status
        self.last_read = {"ts": time.time(), "mil": status, "ecus": ecus}
        return self.last_read

    def stopped(self) -> bool:
        r = self.sig.signals.get("speed")
        return r is not None and not self.sig.is_stale(r, time.time()) and r.value < 1

    async def clear(self) -> dict:
        """Mode 04 to each module. Caller must check stopped() first."""
        result = {}
        for name, ecu in ECUS.items():
            body = await self.poller.request(ecu, bytes([CLEAR]), timeout=2.0)
            result[name] = bool(body) and body[0] == CLEAR + 0x40
        return result

    async def watch_mil(self, stop: asyncio.Event) -> None:
        """Check the check-engine light now and then so the dash can alert when it comes on."""
        await asyncio.sleep(3)
        while not stop.is_set():
            status = await self.mil()
            self.sig.mil = status  # None when the ECM is quiet (key off)
            try:
                await asyncio.wait_for(stop.wait(), MIL_EVERY_S)
            except asyncio.TimeoutError:
                pass
