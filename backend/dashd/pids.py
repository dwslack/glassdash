"""PIDs: build OBD requests and decode responses. The table comes from a vehicle profile (vehicles.py)."""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field

import yaml

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
}


def _compile(formula: str):
    """Compile a formula like '(A*256+B)/4' into a function of the data bytes.

    Only + - * / unary minus, numbers and the names A-D are allowed.
    """
    tree = ast.parse(formula, mode="eval")

    def ev(node, v):
        if isinstance(node, ast.Expression):
            return ev(node.body, v)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in "ABCD":
            return v[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left, v), ev(node.right, v))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand, v))
        raise ValueError(f"unsupported expression in formula {formula!r}")

    ev(tree, {k: 0 for k in "ABCD"})  # validate now, not mid-drive

    def fn(data: bytes) -> float:
        padded = bytes(data) + bytes(4)
        return float(ev(tree, dict(zip("ABCD", padded))))

    return fn


@dataclass(frozen=True)
class Pid:
    name: str
    mode: int
    pid: int
    ecu: int
    formula: str
    unit: str
    hz: float
    confirmed: bool = False
    verified: dict | None = field(default=None, compare=False, hash=False)  # {by, date, ...} once checked on a car

    @property
    def response_id(self) -> int:
        return self.ecu + 8

    @property
    def pid_bytes(self) -> bytes:
        # Mode 01 PIDs are 1 byte, Mode 22 identifiers are 2 bytes.
        return self.pid.to_bytes(2 if self.mode == 0x22 else 1, "big")

    def request(self) -> bytes:
        """ISO-TP single frame: [length, mode, pid...] padded to 8 bytes."""
        payload = bytes([self.mode]) + self.pid_bytes
        return (bytes([len(payload)]) + payload).ljust(8, b"\x00")

    def match(self, frame: bytes) -> bytes | None:
        """Return the data bytes if frame is a positive single-frame reply to this PID."""
        if not frame:
            return None
        length = frame[0]
        if length >> 4 != 0 or not 1 <= length <= 7:
            return None  # not a single frame
        body = frame[1 : 1 + length]
        head = bytes([self.mode + 0x40]) + self.pid_bytes
        if not body.startswith(head):
            return None
        return body[len(head) :]

    def decode(self, data: bytes) -> float:
        return _compile(self.formula)(data)


def load_pids(vehicle: str | None = None) -> list[Pid]:
    """The PID table of a vehicle profile (default: the project's own truck)."""
    from .vehicles import DEFAULT_VEHICLE, load_profile
    return load_profile(vehicle or DEFAULT_VEHICLE).pids
