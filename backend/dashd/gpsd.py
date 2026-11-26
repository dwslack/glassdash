"""Real GPS: position fixes from gpsd (the USB GPS on the Pi).

gpsd speaks newline-delimited JSON on TCP 2947. We ask it to stream reports
(?WATCH) and turn each TPV (time-position-velocity) report into the same fix
dict the sim's GpsSim produces, so the map, trips and nav don't care where the
fix came from. Reconnects forever, so gpsd starting late or the dongle being
unplugged just pauses the map.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

log = logging.getLogger("dashd.gpsd")

WATCH = b'?WATCH={"enable":true,"json":true};\n'
MS_TO_KMH = 3.6


def tpv_to_fix(report: dict) -> dict | None:
    """A gpsd TPV report as a dashd fix, or None if it has no usable position (mode 0/1 = no fix)."""
    if report.get("class") != "TPV" or report.get("mode", 0) < 2:
        return None
    if "lat" not in report or "lon" not in report:
        return None
    fix = {"lat": report["lat"], "lon": report["lon"], "mode": "3d" if report["mode"] >= 3 else "2d",
           "source": "gpsd"}
    if "track" in report:
        fix["heading"] = round(report["track"], 1)
    if "speed" in report:
        fix["speed"] = round(report["speed"] * MS_TO_KMH, 1)
    for k in ("alt", "eph", "time"):
        if k in report:
            fix[k] = report[k]
    return fix


async def follow(on_fix: Callable[[dict], None], stop: asyncio.Event, host: str = "127.0.0.1", port: int = 2947,
                 retry_s: float = 3.0) -> None:
    """Stream fixes from gpsd into on_fix until stop is set."""
    warned = False
    while not stop.is_set():
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
            writer.write(WATCH)
            await writer.drain()
            log.info("gpsd connected at %s:%d", host, port)
            warned = False
            while not stop.is_set():
                line = await asyncio.wait_for(reader.readline(), timeout=30)
                if not line:
                    raise ConnectionError("gpsd closed the connection")
                try:
                    fix = tpv_to_fix(json.loads(line))
                except (ValueError, TypeError):
                    continue
                if fix:
                    on_fix(fix)
        except (OSError, asyncio.TimeoutError, ConnectionError) as e:
            if not warned:
                log.warning("gpsd at %s:%d not available (%s); retrying every %.0f s", host, port, e, retry_s)
                warned = True
        finally:
            if writer is not None:
                writer.close()
        try:
            await asyncio.wait_for(stop.wait(), timeout=retry_s)
        except asyncio.TimeoutError:
            pass
