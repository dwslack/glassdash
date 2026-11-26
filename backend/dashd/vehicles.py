"""Vehicle profiles: what to ask the car for, how to read its body bus, and its gauge ranges.

Profiles are YAML files under vehicles/, one per vehicle, with an id equal to the path
without .yaml (vehicles/gm/silverado-1500-2011-5.3.yaml is "gm/silverado-1500-2011-5.3").
A profile can `extends:` another, so a platform's shared PIDs and body frames live in one
base (gm/gmt900) and each model only adds what differs. Merging is per key: a child's PID or
frame replaces the parent's of the same name, and `name: null` removes an inherited one.

Each file is checked against vehicles/schema.json, then for things a schema can't express:
formulas that parse, a base that exists, no loops, bits that fit. See CONTRIBUTING.md.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import jsonschema
import yaml

from .body import BodyFrame
from .pids import Pid, _compile

VEHICLES_DIR = Path(__file__).resolve().parents[2] / "vehicles"
DEFAULT_VEHICLE = "gm/silverado-3500hd-2011-6.0"
GAUGE_DEFAULTS = {"rpm_max": 7000, "shift_rpm": 5500, "redline_rpm": 6500, "speed_max": 200}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    id: str
    name: str
    vehicle: dict
    gauges: dict
    pids: list[Pid]
    frames: list[BodyFrame]
    bus: dict | None
    chain: list[str] = field(default_factory=list)  # this profile and the ones it extends, nearest first
    abstract: bool = False

    def summary(self) -> dict:
        """What the UI needs: names, gauge ranges, and which signals exist and who verified them."""
        return {
            "id": self.id, "name": self.name, "vehicle": self.vehicle, "gauges": self.gauges, "chain": self.chain,
            "signals": {p.name: {"unit": p.unit, "verified": p.verified} for p in self.pids},
            "body": {"bus": self.bus, "frames": {f.name: {"signals": sorted(f.signals), "verified": f.verified}
                                                 for f in self.frames}},
        }


def _plain(x):
    """YAML reads 2026-11-26 as a date; the schema wants text."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()[:10]
    return x


@lru_cache(maxsize=1)
def _validator(root: Path) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads((root / "schema.json").read_text()))


def _read(pid: str, root: Path) -> dict:
    path = root / f"{pid}.yaml"
    if not path.is_file() or ".." in pid.split("/"):
        raise ProfileError(f"no vehicle profile {pid!r} (looked for {path})")
    try:
        raw = _plain(yaml.safe_load(path.read_text()))
    except yaml.YAMLError as e:
        raise ProfileError(f"{pid}: not valid YAML: {e}") from None
    if not isinstance(raw, dict):
        raise ProfileError(f"{pid}: expected a mapping at the top level")
    e = jsonschema.exceptions.best_match(_validator(root).iter_errors(raw))
    if e is not None:
        where = ".".join(str(p) for p in e.absolute_path) or "(top level)"
        raise ProfileError(f"{pid}: {where}: {e.message}")
    return raw


def _merged(pid: str, root: Path, seen: tuple[str, ...] = ()) -> tuple[dict, list[str]]:
    if pid in seen:
        raise ProfileError(f"{pid}: extends loop: {' -> '.join(seen + (pid,))}")
    raw = _read(pid, root)
    if "extends" not in raw:
        base, chain = {"vehicle": {}, "gauges": {}, "pids": {}, "body": {"frames": {}}}, []
    else:
        base, chain = _merged(raw["extends"], root, seen + (pid,))
    out = copy.deepcopy(base)
    out["name"] = raw["name"]
    out["abstract"] = raw.get("abstract", False)
    out["vehicle"].update(raw.get("vehicle", {}))
    out["gauges"].update(raw.get("gauges", {}))
    for k, v in raw.get("pids", {}).items():
        out["pids"].pop(k, None) if v is None else out["pids"].__setitem__(k, v)
    body = raw.get("body", {})
    if "bus" in body:
        out["body"]["bus"] = body["bus"]
    for k, v in body.get("frames", {}).items():
        out["body"]["frames"].pop(k, None) if v is None else out["body"]["frames"].__setitem__(k, v)
    return out, [pid] + chain


def load_profile(pid: str = DEFAULT_VEHICLE, root: Path = VEHICLES_DIR) -> Profile:
    m, chain = _merged(pid, root)
    pids = []
    for name, p in m["pids"].items():
        try:
            _compile(p["formula"])
        except (SyntaxError, ValueError) as e:
            raise ProfileError(f"{pid}: pids.{name}.formula: {e}") from None
        pids.append(Pid(name=name, mode=p["mode"], pid=p["pid"], ecu=p["ecu"], formula=p["formula"], unit=p["unit"],
                        hz=p["hz"], confirmed="verified" in p, verified=p.get("verified")))
    bus = m["body"].get("bus")
    frames, used = [], {}
    for name, f in m["body"]["frames"].items():
        if ("arb" in f) == ("id" in f):
            raise ProfileError(f"{pid}: body.frames.{name}: give exactly one of `arb` (GMLAN) or `id` (plain CAN)")
        key = ("arb", f["arb"]) if "arb" in f else ("id", f["id"])
        if key in used:
            raise ProfileError(f"{pid}: body.frames.{name}: same {key[0]} as {used[key]}")
        used[key] = name
        if "arb" in f and (bus or {}).get("type") != "gmlan-swcan":
            raise ProfileError(f"{pid}: body.frames.{name}: `arb` is for GMLAN; use `id` on a plain CAN bus")
        signals = {s: (v[0], v[1]) for s, v in f["signals"].items()}
        inverted = frozenset(s for s, v in f["signals"].items() if len(v) == 3)
        frames.append(BodyFrame(name, f.get("arb", -1), signals, "verified" in f, can_id=f.get("id"),
                                inverted=inverted, verified=f.get("verified")))
    if frames and not bus:
        raise ProfileError(f"{pid}: body.frames given but no body.bus")
    g = {**GAUGE_DEFAULTS, **m["gauges"]}
    if not g["shift_rpm"] <= g["redline_rpm"] <= g["rpm_max"]:
        raise ProfileError(f"{pid}: gauges: need shift_rpm <= redline_rpm <= rpm_max")
    return Profile(pid, m["name"], m["vehicle"], g, pids, frames, bus, chain, m["abstract"])


def list_profiles(root: Path = VEHICLES_DIR, include_abstract: bool = False) -> list[dict]:
    """Every profile that loads, for the picker; broken ones are listed with their error."""
    out = []
    for path in sorted(root.rglob("*.yaml")):
        pid = path.relative_to(root).with_suffix("").as_posix()
        try:
            p = load_profile(pid, root)
        except ProfileError as e:
            out.append({"id": pid, "name": pid, "error": str(e)})
            continue
        if p.abstract and not include_abstract:
            continue
        out.append({"id": pid, "name": p.name, "make": p.vehicle.get("make", ""), "model": p.vehicle.get("model", ""),
                    "years": p.vehicle.get("years"), "verified": sum(1 for x in p.pids if x.verified)
                    + sum(1 for f in p.frames if f.verified), "signals": len(p.pids) + sum(len(f.signals) for f in p.frames)})
    return out


def main() -> int:
    """python -m dashd.vehicles: check every profile (what CI runs on a pull request)."""
    bad = 0
    for row in list_profiles(include_abstract=True):
        if "error" in row:
            bad += 1
            print(f"FAIL {row['error']}")
        else:
            print(f"ok   {row['id']}: {row['name']} ({row['signals']} signals, {row['verified']} verified)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
