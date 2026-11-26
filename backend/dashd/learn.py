""""Map a signal": find which CAN frame bit follows a switch, door or lamp.

The user records the body bus while telling us, with two buttons, when the thing is on
and when it's off. Every bit of every frame seen is then compared with that on/off timeline;
a bit that matches it (or its inverse) and doesn't flicker otherwise is the signal.
The result is a ready-to-paste profile snippet for vehicles/…yaml.
"""
from __future__ import annotations

import bisect
import datetime as dt
import time
from dataclasses import dataclass, field

import can

from .body import BodyFrame, gmlan_arb

MAX_FRAMES = 400_000    # about 10 minutes of a busy body bus; recording stops there
SETTLE_S = 0.4          # ignore samples this soon after a button press (reaction time, slow modules)
MIN_CHANGES = 2         # on and off at least once each


@dataclass
class Learner:
    signal: str
    gmlan: bool = True
    t0: float = field(default_factory=time.time)
    frames: dict[tuple[int, bool], list[tuple[float, bytes]]] = field(default_factory=dict)
    marks: list[tuple[float, bool]] = field(default_factory=list)
    count: int = 0
    full: bool = False

    def __post_init__(self):
        self.marks.append((self.t0, False))  # the user starts with it off

    def feed(self, msg: can.Message, now: float | None = None) -> None:
        if self.count >= MAX_FRAMES:
            self.full = True
            return
        self.frames.setdefault((msg.arbitration_id, msg.is_extended_id), []).append(
            (now if now is not None else time.time(), bytes(msg.data)))
        self.count += 1

    def mark(self, on: bool, now: float | None = None) -> None:
        if self.marks[-1][1] != on:
            self.marks.append((now if now is not None else time.time(), on))

    @property
    def changes(self) -> int:
        return len(self.marks) - 1

    def status(self) -> dict:
        return {"signal": self.signal, "frames": self.count, "ids": len(self.frames), "changes": self.changes,
                "on": self.marks[-1][1], "seconds": round(time.time() - self.t0, 1), "full": self.full}

    def _state(self, t: float) -> bool | None:
        """The marked state at time t, or None while settling after a change."""
        times = [m[0] for m in self.marks]
        i = bisect.bisect_right(times, t) - 1
        if i < 0:
            return None
        if i > 0 and t - times[i] < SETTLE_S:
            return None
        return self.marks[i][1]

    def analyze(self, top: int = 5) -> list[dict]:
        if self.changes < MIN_CHANGES:
            raise ValueError("Turn it on and off at least once each first.")
        out = []
        for (cid, ext), samples in self.frames.items():
            labelled = [(self._state(t), d) for t, d in samples]
            labelled = [(s, d) for s, d in labelled if s is not None]
            if len(labelled) < 4 or len({s for s, _ in labelled}) < 2:
                continue  # never seen in both states
            width = min(len(d) for _, d in labelled)
            for byte in range(width):
                for bit in range(8):
                    vals = [(d[byte] >> bit) & 1 for _, d in labelled]
                    if len(set(vals)) < 2:
                        continue
                    agree = sum(v == s for v, (s, _) in zip(vals, labelled)) / len(vals)
                    inverted = agree < 0.5
                    match = 1 - agree if inverted else agree
                    toggles = sum(a != b for a, b in zip(vals, vals[1:]))
                    out.append({
                        "can_id": cid, "extended": ext, "arb": gmlan_arb(cid) if ext and self.gmlan else None,
                        "byte": byte, "bit": bit, "inverted": inverted, "match": round(match, 3),
                        "toggles": toggles, "state_changes": self.changes, "samples": len(vals),
                    })
        # Best agreement first; among equals, the bit whose toggles track the button presses (not a blinker or counter).
        out.sort(key=lambda c: (-c["match"], abs(c["toggles"] - c["state_changes"])))
        return out[:top]


def snippet(c: dict, signal: str, frames: list[BodyFrame], gmlan: bool, by: str = "YOUR_GITHUB_NAME") -> str:
    """Profile YAML for one candidate. If the profile already has a frame with that id, its other
    signals are kept: a child profile's frame replaces the inherited one whole."""
    use_arb = gmlan and c["arb"] is not None
    key = c["arb"] if use_arb else c["can_id"]
    existing = next((f for f in frames if (f.can_id is None and use_arb and f.arb == key)
                     or (f.can_id is not None and not use_arb and f.can_id == key)), None)
    name = existing.name if existing else f"frame_{key:x}"
    signals = dict(existing.signals) if existing else {}
    inv = set(existing.inverted) if existing else set()
    signals[signal] = (c["byte"], c["bit"])
    inv.discard(signal)
    if c["inverted"]:
        inv.add(signal)
    hexw = 3 if key <= 0xFFF else 8
    sig_lines = "\n".join(
        f"        {s}: [{b}, {bit}{', inverted' if s in inv else ''}]" for s, (b, bit) in signals.items())
    today = dt.date.today().isoformat()
    head = f"arb: 0x{key:0{hexw}X}" if use_arb else f"id: 0x{key:0{hexw}X}"
    others = [s for s in signals if s != signal]
    note = (f"# `verified` covers the whole frame: map {', '.join(others)} too before keeping it,\n"
            f"# or leave `verified` out.\n") if others else ""
    return (f"# Learned with glassdash \"Map a signal\" on {today}: {signal} matched {c['match']:.0%} "
            f"over {c['state_changes']} on/off changes.\n{note}"
            f"body:\n  frames:\n    {name}:\n      {head}\n      signals:\n{sig_lines}\n"
            f"      verified: {{by: {by}, date: {today}, notes: \"checked {signal}\"}}\n")


class Tap:
    """Lets a Learner see every frame the body listener reads, without a second bus connection."""

    def __init__(self):
        self.learner: Learner | None = None

    def __call__(self, msg: can.Message) -> None:
        if self.learner is not None:
            self.learner.feed(msg)
