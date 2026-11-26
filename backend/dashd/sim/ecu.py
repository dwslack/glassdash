"""Fake ECUs: answer OBD requests on a CAN bus the way the truck would.

Values come from a shared Vehicle. Encodes using the inverse of each PID's
formula for the PIDs below; unknown PIDs get a negative response (0x7F),
like a real module. Also answers trouble-code requests (Modes 03/07/0A, with
multi-frame ISO-TP replies), clears codes (Mode 04), and goes silent at key-off.
"""
from __future__ import annotations

import threading

import can

from ..dtc import encode_dtc
from ..isotp import segment
from .vehicle import Vehicle

ECM, TCM = 0x7E0, 0x7E2
MODULE = {ECM: "ECM", TCM: "TCM"}
DTC_MODES = {0x03: "stored", 0x07: "pending", 0x0A: "permanent"}


def _u16(x: float) -> bytes:
    return int(max(0, min(65535, round(x)))).to_bytes(2, "big")


def _u8(x: float) -> bytes:
    return bytes([int(max(0, min(255, round(x))))])


def _pct(x: float) -> bytes:
    return _u8(x * 255 / 100)


def _trim(x: float) -> bytes:
    return _u8(x * 128 / 100 + 128)


def encode(v: Vehicle, ecu: int, mode: int, pid: int) -> bytes | None:
    if ecu == ECM and mode == 0x01:
        return {
            0x01: lambda: bytes([(0x80 if v.mil else 0) | len(v.dtcs["ECM"]["stored"]), 0x07, 0x65, 0x00]),
            0x0C: lambda: _u16(v.rpm * 4),
            0x0D: lambda: _u8(v.speed),
            0x05: lambda: _u8(v.coolant + 40),
            0x42: lambda: _u16(v.voltage * 1000),
            0x10: lambda: _u16(v.maf * 100),
            0x2F: lambda: _pct(v.fuel_level),
            0x04: lambda: _pct(v.engine_load),
            0x11: lambda: _pct(v.throttle),
            0x0F: lambda: _u8(v.iat + 40),
            0x46: lambda: _u8(v.ambient + 40),
            0x33: lambda: _u8(101),
            0x06: lambda: _trim(v.stft),
            0x07: lambda: _trim(v.ltft),
            0x0E: lambda: _u8((v.timing + 64) * 2),
        }.get(pid, lambda: None)()
    if ecu == ECM and mode == 0x22:
        return {
            0x1154: lambda: _u8(v.oil_temp + 40),
            0x115C: lambda: _u8(v.oil_pressure / 4),
        }.get(pid, lambda: None)()
    if ecu == TCM and mode == 0x22 and pid == 0x1940:
        return _u8(v.trans_temp + 40)
    return None


def reply_payload(v: Vehicle, ecu: int, d: bytes) -> bytes | None:
    """The reply payload for a single-frame request, or None to stay silent."""
    length = d[0]
    if not 1 <= length <= 7:
        return None  # not a single-frame request (e.g. our own flow control)
    mode = d[1]
    module = MODULE[ecu]
    if mode in DTC_MODES and length == 1:
        codes = v.dtcs[module][DTC_MODES[mode]]
        return bytes([mode + 0x40, len(codes)]) + b"".join(encode_dtc(c) for c in codes)
    if mode == 0x04 and length == 1:
        v.clear_dtcs(module)
        return bytes([0x44])
    if length < 2:
        return bytes([0x7F, mode, 0x12])  # subFunctionNotSupported / invalid format
    pid_len = 2 if mode == 0x22 else 1
    pid = int.from_bytes(d[2 : 2 + pid_len], "big")
    data = encode(v, ecu, mode, pid)
    if data is None:
        return bytes([0x7F, mode, 0x31])  # requestOutOfRange
    return bytes([mode + 0x40]) + d[2 : 2 + pid_len] + data


class EcuSim:
    """Runs in a background thread on its own CAN bus handle."""

    def __init__(self, can_bus: can.BusABC, vehicle: Vehicle):
        self.can_bus = can_bus
        self.vehicle = vehicle
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ecu-sim")

    def start(self) -> "EcuSim":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _send(self, ecu: int, body: bytes) -> None:
        frames = segment(body)
        tx = lambda data: self.can_bus.send(can.Message(arbitration_id=ecu + 8, data=data, is_extended_id=False))
        tx(frames[0])
        if len(frames) == 1:
            return
        # Multi-frame: wait for the tester's flow control before the consecutive frames.
        while True:
            fc = self.can_bus.recv(timeout=1.0)
            if fc is None:
                return
            if fc.arbitration_id == ecu and fc.data and fc.data[0] >> 4 == 3:
                break
        for f in frames[1:]:
            tx(f)

    def _run(self) -> None:
        while not self._stop.is_set():
            msg = self.can_bus.recv(timeout=0.05)
            if msg is None or msg.arbitration_id not in MODULE or not self.vehicle.engine_on:
                continue  # key off: modules are asleep and the bus is quiet
            body = reply_payload(self.vehicle, msg.arbitration_id, bytes(msg.data))
            if body is not None:
                self._send(msg.arbitration_id, body)
