"""Saved places (Home, Work, ...): a small JSON file next to the settings."""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path

MAX = 50


class Favorites:
    def __init__(self, path: Path | None):
        self.path = path
        self.lock = threading.Lock()
        self.items: list[dict] = []
        if path is not None and path.exists():
            try:
                self.items = [x for x in json.loads(path.read_text()) if {"id", "name", "lat", "lon"} <= x.keys()]
            except (OSError, ValueError):
                self.items = []

    def _save(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, indent=1))
            tmp.replace(self.path)

    def add(self, name: str, lat: float, lon: float) -> dict:
        name = name.strip()[:60]
        if not name:
            raise ValueError("name is required")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("bad position")
        with self.lock:
            # Same name replaces the old one, so "Home" can be moved.
            self.items = [x for x in self.items if x["name"].lower() != name.lower()]
            if len(self.items) >= MAX:
                raise ValueError(f"at most {MAX} saved places")
            item = {"id": uuid.uuid4().hex[:8], "name": name, "lat": lat, "lon": lon}
            self.items.append(item)
            self._save()
        return item

    def remove(self, item_id: str) -> bool:
        with self.lock:
            before = len(self.items)
            self.items = [x for x in self.items if x["id"] != item_id]
            self._save()
            return len(self.items) < before

    def match(self, q: str) -> list[dict]:
        words = re.findall(r"\w+", q.lower())
        out = []
        for x in self.items:
            have = re.findall(r"\w+", x["name"].lower())
            if all(any(h.startswith(w) for h in have) for w in words):
                out.append(x)
        return out
