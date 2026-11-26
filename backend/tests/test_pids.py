import pytest

from dashd.pids import Pid, _compile, load_pids


def test_load_real_table():
    names = {p.name for p in load_pids()}
    assert {"rpm", "speed", "coolant", "voltage", "trans_temp"} <= names


def test_mode01_request_and_decode():
    rpm = Pid("rpm", 0x01, 0x0C, 0x7E0, "(A*256+B)/4", "rpm", 8)
    assert rpm.request() == bytes([2, 0x01, 0x0C, 0, 0, 0, 0, 0])
    data = rpm.match(bytes([4, 0x41, 0x0C, 0x1A, 0xF8, 0, 0, 0]))
    assert data == bytes([0x1A, 0xF8])
    assert rpm.decode(data) == 1726.0


def test_mode22_two_byte_pid():
    tt = Pid("trans_temp", 0x22, 0x1940, 0x7E2, "A-40", "C", 1)
    assert tt.request()[:4] == bytes([3, 0x22, 0x19, 0x40])
    assert tt.response_id == 0x7EA
    assert tt.decode(tt.match(bytes([4, 0x62, 0x19, 0x40, 120, 0, 0, 0]))) == 80.0


def test_rejects_wrong_pid_and_negative_response():
    speed = Pid("speed", 0x01, 0x0D, 0x7E0, "A", "km/h", 8)
    assert speed.match(bytes([3, 0x41, 0x0C, 50, 0, 0, 0, 0])) is None
    assert speed.match(bytes([3, 0x7F, 0x01, 0x31, 0, 0, 0, 0])) is None


@pytest.mark.parametrize("bad", ["__import__('os')", "A.real", "A**2", "open('x')"])
def test_formula_sandbox(bad):
    with pytest.raises(ValueError):
        _compile(bad)
