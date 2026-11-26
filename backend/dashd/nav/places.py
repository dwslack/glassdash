"""Offline place search: towns, businesses, streets and addresses from OpenStreetMap.

A SQLite full-text index (data/nav/places.db) built from the same OSM extract as
the routing tiles. Search is word-prefix matching ("tim hor chil" finds Tim
Hortons in Chilliwack), ranked by text match, kind of place and distance from the truck.
"""
from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path

from ..geo import dist

SCHEMA = """
create virtual table if not exists places using fts5(
  name, city, kind unindexed, lat unindexed, lon unindexed,
  tokenize = "unicode61 remove_diacritics 2", prefix = '2 3'
);
"""
# Lower is better: how much a kind of result is pushed up the list.
KIND_BONUS = {"city": 4.0, "town": 3.5, "village": 3.0, "suburb": 2.0, "hamlet": 1.5, "neighbourhood": 1.0,
              "locality": 0.8, "street": 0.8, "address": 0.6}
POI_BONUS = 1.2
# Abbreviations people type that aren't prefixes of the full word in OSM.
ABBREV = {"rd": "road", "hwy": "highway", "blvd": "boulevard", "ct": "court", "hts": "heights",
          "mt": "mount", "pkwy": "parkway", "fwy": "freeway", "ln": "lane", "sq": "square", "tce": "terrace"}


def _match_expr(q: str) -> str | None:
    words = re.findall(r"\w+", q.lower())
    if not words:
        return None
    terms = []
    for w in words:
        w = ABBREV.get(w, w)
        terms.append(f'"{w}"*')
    return " AND ".join(terms)


class Places:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)

    def add(self, rows) -> None:
        """rows of (name, city, kind, lat, lon)."""
        self.db.executemany("insert into places (name, city, kind, lat, lon) values (?, ?, ?, ?, ?)", rows)

    def commit(self) -> None:
        self.db.commit()

    def count(self) -> int:
        return self.db.execute("select count(*) from places").fetchone()[0]

    def search(self, q: str, near: tuple[float, float] | None = None, limit: int = 10) -> list[dict]:
        """Best matches for q. If q starts with a house number OSM doesn't have (common in BC outside
        the cities), the street itself is offered first, flagged with a note."""
        results = self._search(q, near, limit)
        m = re.match(r"\s*(\d+[a-z]?)\s+(.+)", q.lower())
        if not m:
            return results
        number, rest = m.groups()
        if any(r["kind"] == "address" and r["name"].lower().startswith(number + " ") for r in results):
            return results
        streets = [r for r in self._search(rest, near, 30) if r["kind"] == "street"][:3]
        for r in streets:
            r["note"] = f"No house numbers mapped on this street, so this goes to the street, not number {number}."
        return (streets + [r for r in results if r not in streets])[:limit]

    def _search(self, q: str, near: tuple[float, float] | None, limit: int) -> list[dict]:
        expr = _match_expr(q)
        if expr is None:
            return []
        try:
            rows = self.db.execute(
                "select name, city, kind, lat, lon, bm25(places, 4.0, 1.0) from places where places match ?"
                " order by bm25(places, 4.0, 1.0) limit 400", (expr,)).fetchall()
        except sqlite3.OperationalError:
            return []
        ql = q.strip().lower()
        scored, seen = [], set()
        for name, city, kind, lat, lon, bm in rows:
            key = (name.lower(), city.lower(), kind)
            if key in seen:  # e.g. the same street split into many ways
                continue
            seen.add(key)
            score = bm - KIND_BONUS.get(kind, POI_BONUS)
            if name.lower() == ql:
                score -= 3.0
            elif name.lower().startswith(ql):
                score -= 2.0
            d = dist(near, (lat, lon)) if near else None
            if d is not None:
                score += 1.5 * math.log10(1 + d / 1000)
            scored.append((score, {"name": name, "city": city, "kind": kind, "lat": lat, "lon": lon,
                                   "distance_m": round(d) if d is not None else None}))
        scored.sort(key=lambda x: x[0])
        return [r for _, r in scored[:limit]]
