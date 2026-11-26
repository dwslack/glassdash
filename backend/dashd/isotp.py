"""Minimal ISO-TP (ISO 15765-2) for diagnostic requests whose reply can span frames.

Enough for OBD: single-frame requests (up to 7 bytes) and single- or multi-frame
replies. We answer a First Frame with a Flow Control "continue, no block limit,
no gap" and reassemble Consecutive Frames. Mode 03 with three or more DTCs needs this.
"""
from __future__ import annotations

import asyncio
import time

import can

FC_CONTINUE = bytes([0x30, 0x00, 0x00]).ljust(8, b"\x00")


def single_frame(payload: bytes) -> bytes:
    if not 1 <= len(payload) <= 7:
        raise ValueError("single frame payload must be 1-7 bytes")
    return (bytes([len(payload)]) + payload).ljust(8, b"\x00")


def segment(payload: bytes) -> list[bytes]:
    """Split a payload into CAN frames: [SF] or [FF, CF1, CF2, ...]. Used by the ECU sim."""
    if len(payload) <= 7:
        return [single_frame(payload)]
    n = len(payload)
    frames = [bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6]]
    rest, seq = payload[6:], 1
    while rest:
        frames.append((bytes([0x20 | (seq & 0x0F)]) + rest[:7]).ljust(8, b"\x00"))
        rest, seq = rest[7:], seq + 1
    return frames


async def transact(
    can_bus: can.BusABC,
    reader: can.AsyncBufferedReader,
    tx_id: int,
    payload: bytes,
    timeout: float = 1.0,
) -> bytes | None:
    """Send a request to tx_id and return the reassembled reply payload from tx_id + 8.

    A negative response 0x7F xx 0x78 ("response pending") extends the wait; any other
    negative response is returned as-is for the caller to interpret.
    """
    rx_id = tx_id + 8
    while not reader.buffer.empty():
        reader.buffer.get_nowait()
    can_bus.send(can.Message(arbitration_id=tx_id, data=single_frame(payload), is_extended_id=False))
    deadline = time.monotonic() + timeout
    buf = bytearray()
    total = 0
    next_seq = 1
    while (left := deadline - time.monotonic()) > 0:
        try:
            msg = await asyncio.wait_for(reader.get_message(), left)
        except asyncio.TimeoutError:
            return None
        if msg.arbitration_id != rx_id or not msg.data:
            continue
        d = bytes(msg.data)
        kind = d[0] >> 4
        if kind == 0:  # single frame
            body = d[1 : 1 + (d[0] & 0x0F)]
            if len(body) >= 3 and body[0] == 0x7F and body[2] == 0x78:
                deadline = time.monotonic() + timeout
                continue
            return body
        if kind == 1:  # first frame
            total = ((d[0] & 0x0F) << 8) | d[1]
            buf = bytearray(d[2:8])
            next_seq = 1
            can_bus.send(can.Message(arbitration_id=tx_id, data=FC_CONTINUE, is_extended_id=False))
            deadline = time.monotonic() + timeout
        elif kind == 2 and total:  # consecutive frame
            if (d[0] & 0x0F) != (next_seq & 0x0F):
                return None  # lost a frame
            buf += d[1:8]
            next_seq += 1
            if len(buf) >= total:
                return bytes(buf[:total])
    return None
