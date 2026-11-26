"""OBD poller: sends Mode 01/22 requests on a schedule and decodes replies."""
from __future__ import annotations

import asyncio
import logging
import time

import can

from . import isotp
from .bus import SignalBus
from .pids import Pid

log = logging.getLogger(__name__)


class Poller:
    """Polls one PID at a time. Total request rate is the sum of each PID's hz."""

    def __init__(self, can_bus: can.BusABC, pids: list[Pid], sig: SignalBus, timeout: float = 0.1):
        self.can_bus = can_bus
        self.pids = pids
        self.sig = sig
        self.timeout = timeout
        self._next_due = {p.name: 0.0 for p in pids}
        self._lock = asyncio.Lock()  # one request on the bus at a time
        self._reader: can.AsyncBufferedReader | None = None

    def set_pids(self, pids: list[Pid]) -> None:
        """Switch vehicle profile without restarting."""
        self._next_due = {p.name: self._next_due.get(p.name, 0.0) for p in pids}
        self.pids = pids

    async def request(self, ecu: int, payload: bytes, timeout: float = 1.0) -> bytes | None:
        """One-off diagnostic request (DTCs etc.), slotted between polls. Returns the reply payload."""
        if self._reader is None:
            return None
        async with self._lock:
            return await isotp.transact(self.can_bus, self._reader, ecu, payload, timeout)

    def _due(self, now: float) -> Pid | None:
        due = [p for p in self.pids if self._next_due[p.name] <= now]
        if not due:
            return None
        return min(due, key=lambda p: self._next_due[p.name])

    async def query(self, pid: Pid, reader: can.AsyncBufferedReader) -> float | None:
        while not reader.buffer.empty():  # drop late replies from earlier requests
            reader.buffer.get_nowait()
        self.can_bus.send(can.Message(arbitration_id=pid.ecu, data=pid.request(), is_extended_id=False))
        deadline = time.monotonic() + self.timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                msg = await asyncio.wait_for(reader.get_message(), left)
            except asyncio.TimeoutError:
                break
            if msg.arbitration_id != pid.response_id:
                continue
            data = pid.match(bytes(msg.data))
            if data is not None:
                return pid.decode(data)
        return None

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        reader = self._reader = can.AsyncBufferedReader()
        notifier = can.Notifier(self.can_bus, [reader], loop=asyncio.get_running_loop())
        try:
            while not stop.is_set():
                now = time.monotonic()
                pid = self._due(now)
                if pid is None:
                    await asyncio.sleep(0.005)
                    continue
                self._next_due[pid.name] = now + 1.0 / pid.hz
                async with self._lock:
                    value = await self.query(pid, reader)
                if value is None:
                    self.sig.error(pid.name)
                else:
                    self.sig.set(pid.name, value, pid.unit, stale_after=max(self.sig.stale_after, 2.5 / pid.hz))
        finally:
            self._reader = None
            notifier.stop()
