"""Small geodesy helpers shared by the sim and navigation. Points are (lat, lon) in degrees."""
from __future__ import annotations

import math

R = 6371000.0


def dist(a, b) -> float:
    """Great-circle distance in metres."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, h)))


def bearing(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    """Signed smallest difference b - a in degrees, -180..180."""
    return (b - a + 540) % 360 - 180


def decode_polyline(s: str, precision: int = 6) -> list[tuple[float, float]]:
    """Google encoded polyline (OSRM/Valhalla use precision 6) to [(lat, lon)]."""
    pts, i, lat, lon, f = [], 0, 0, 0, 10.0 ** precision
    while i < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        pts.append((lat / f, lon / f))
    return pts


def offset(p, metres_north: float, metres_east: float) -> tuple[float, float]:
    """Move a point by a small number of metres."""
    return (p[0] + math.degrees(metres_north / R),
            p[1] + math.degrees(metres_east / (R * math.cos(math.radians(p[0])))))


def project(p, a, b) -> tuple[float, float]:
    """Project p onto segment a-b. Returns (fraction 0..1 along a-b, distance from p in metres)."""
    k = math.cos(math.radians(a[0]))
    ax, ay = a[1] * k, a[0]
    bx, by = b[1] * k, b[0]
    px, py = p[1] * k, p[0]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return t, math.hypot(px - qx, py - qy) * math.radians(1) * R
