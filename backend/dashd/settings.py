"""User settings, stored as JSON next to the trip log so the Pi screen and the iPad share them."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS: dict = {
    "vehicle": "gm/silverado-3500hd-2011-6.0",  # profile id under vehicles/ (checked by the server)
    "tank_l": 136.0,          # 36 US gal, 2011 Silverado 3500HD long box (unverified)
    "fuel_cal": 1.0,          # multiply the MAF fuel estimate; set from fill-ups (pumped / estimated)
    "night": "auto",          # auto (by sunset at the GPS position) | on | off
    "night_brightness": 0.55,  # 0.2-1.0, how bright night mode is
    "alert_sound": True,
    "voice": True,            # spoken turn-by-turn prompts
    "coolant_warn": 108, "coolant_bad": 115,
    "trans_warn": 110, "trans_bad": 125,
    "volts_low": 13.2, "volts_bad": 12.5, "volts_high": 15.2,
    "oil_pressure_min": 70,
    "oil_temp_warn": 135,
    "basemap": "street",      # street (offline vector) | satellite (online)
    "esri_key": "",           # optional ArcGIS Location Platform key for satellite imagery
    "mapillary_token": "",    # Mapillary client token for street-level photos
    "anthropic_key": "",      # Claude API key for the voice assistant (never sent back to the browser in full)
    "assistant_model": "claude-haiku-5-5",
    "dash_views": {},         # per dash view, the widgets the user swapped in: {view: {hero, tiles: [...]}}
    "auto_tow_view": True,    # switch to the Tow view when the transmission runs hot
}
LIMITS = {
    "tank_l": (20, 250), "fuel_cal": (0.5, 1.5), "night_brightness": (0.2, 1.0),
    "coolant_warn": (80, 130), "coolant_bad": (80, 135), "trans_warn": (60, 140), "trans_bad": (60, 150),
    "volts_low": (11, 15), "volts_bad": (10, 14), "volts_high": (14, 17),
    "oil_pressure_min": (0, 300), "oil_temp_warn": (90, 160),
}
CHOICES = {"night": ("auto", "on", "off"), "basemap": ("street", "satellite"),
           "assistant_model": ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")}
SECRETS = ("anthropic_key",)


def mask(values: dict) -> dict:
    """Settings as the browser sees them: secrets shown only by their last 4 characters."""
    out = dict(values)
    for k in SECRETS:
        if out.get(k):
            out[k] = "…" + out[k][-4:]
    return out
MAX_STR = 300
VIEW_IDS = ("drive", "tow", "economy", "engine", "map")
MAX_TILES = 16


def _check_views(v) -> dict:
    """dash_views: only known view ids, each {hero: str, tiles: [str]}; widget keys are checked by the UI."""
    if not isinstance(v, dict):
        raise ValueError("dash_views must be an object")
    out = {}
    for view, spec in v.items():
        if view not in VIEW_IDS:
            raise ValueError(f"dash_views: unknown view {view!r}")
        if not isinstance(spec, dict) or not isinstance(spec.get("hero"), str) or not isinstance(spec.get("tiles"), list):
            raise ValueError(f"dash_views.{view} must be {{hero, tiles}}")
        tiles = spec["tiles"]
        if len(tiles) > MAX_TILES or not all(isinstance(t, str) and 0 < len(t) <= 32 for t in tiles) or not 0 < len(spec["hero"]) <= 32:
            raise ValueError(f"dash_views.{view}: bad widget names")
        out[view] = {"hero": spec["hero"], "tiles": list(tiles)}
    return out


class Settings:
    def __init__(self, path: Path | None):
        self.path = path
        self.values = dict(DEFAULTS)
        if path is not None and path.exists():
            try:
                self.values.update({k: v for k, v in json.loads(path.read_text()).items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass  # a corrupt file falls back to defaults rather than stopping the dash

    def __getitem__(self, key: str):
        return self.values[key]

    def update(self, changes: dict) -> dict:
        """Validate and apply; raises ValueError naming the bad key."""
        clean = {}
        for k, v in changes.items():
            if k in SECRETS and isinstance(v, str) and v.startswith("…"):
                continue  # the masked value coming back unchanged
            if k not in DEFAULTS:
                raise ValueError(f"unknown setting {k!r}")
            default = DEFAULTS[k]
            if isinstance(default, bool):
                if not isinstance(v, bool):
                    raise ValueError(f"{k} must be true or false")
            elif isinstance(default, (int, float)):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise ValueError(f"{k} must be a number")
                lo, hi = LIMITS.get(k, (float("-inf"), float("inf")))
                if not lo <= v <= hi:
                    raise ValueError(f"{k} must be between {lo} and {hi}")
            elif k == "dash_views":
                v = _check_views(v)
            elif k in CHOICES:
                if v not in CHOICES[k]:
                    raise ValueError(f"{k} must be one of {', '.join(CHOICES[k])}")
            elif isinstance(default, str):
                if not isinstance(v, str) or len(v) > MAX_STR:
                    raise ValueError(f"{k} must be text up to {MAX_STR} characters")
                v = v.strip()
            clean[k] = v
        self.values.update(clean)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.values, indent=1))
            tmp.replace(self.path)  # atomic, so a power cut can't leave half a file
        return self.values
