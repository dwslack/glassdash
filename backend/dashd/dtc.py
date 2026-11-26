"""Diagnostic trouble codes: read (Modes 03/07/0A), clear (Mode 04), MIL status (01 01).

Clearing is the one thing dashd does that changes state in the truck: it erases
stored and pending codes and freeze frames, turns off the check-engine light and
resets the readiness monitors (permanent codes stay until the ECU clears them
itself). The API only allows it with the truck stopped, and the UI asks first.
"""
from __future__ import annotations

ECUS = {"ECM": 0x7E0, "TCM": 0x7E2}
KINDS = {"stored": 0x03, "pending": 0x07, "permanent": 0x0A}
CLEAR = 0x04
_LETTER = "PCBU"

# Short descriptions for common codes on a 2011 Silverado (5.3 L Gen IV + 6L80).
# Generic SAE meanings; unknown codes just show the code.
DESCRIPTIONS = {
    "P0016": "Crank/cam position correlation",
    "P0068": "Throttle body airflow performance",
    "P0101": "MAF sensor performance",
    "P0102": "MAF sensor low",
    "P0103": "MAF sensor high",
    "P0113": "Intake air temp sensor high",
    "P0117": "Coolant temp sensor low",
    "P0118": "Coolant temp sensor high",
    "P0128": "Coolant below thermostat temp (thermostat stuck open?)",
    "P0171": "System too lean, bank 1",
    "P0172": "System too rich, bank 1",
    "P0174": "System too lean, bank 2",
    "P0175": "System too rich, bank 2",
    "P0300": "Random/multiple cylinder misfire",
    **{f"P030{n}": f"Cylinder {n} misfire" for n in range(1, 9)},
    "P0325": "Knock sensor circuit",
    "P0335": "Crank position sensor circuit",
    "P0340": "Cam position sensor circuit",
    "P0401": "EGR flow insufficient",
    "P0420": "Catalyst efficiency below threshold, bank 1",
    "P0430": "Catalyst efficiency below threshold, bank 2",
    "P0440": "EVAP system",
    "P0442": "EVAP small leak",
    "P0446": "EVAP vent control",
    "P0449": "EVAP vent solenoid circuit",
    "P0455": "EVAP large leak (check gas cap)",
    "P0456": "EVAP very small leak",
    "P0496": "EVAP flow during non-purge",
    "P0506": "Idle speed lower than expected",
    "P0507": "Idle speed higher than expected",
    "P0521": "Oil pressure sensor performance",
    "P0562": "System voltage low",
    "P0563": "System voltage high",
    "P0700": "Transmission control system (see TCM codes)",
    "P0711": "Trans fluid temp sensor performance",
    "P0716": "Input speed sensor performance",
    "P0751": "Shift solenoid A performance",
    "P0796": "Pressure control solenoid C performance",
    "P1101": "MAF/MAP intake airflow out of range (GM)",
    "P2138": "Accelerator pedal position sensor correlation",
}


def decode_dtc(hi: int, lo: int) -> str:
    return f"{_LETTER[hi >> 6]}{(hi >> 4) & 0x3}{hi & 0xF:X}{lo >> 4:X}{lo & 0xF:X}"


def encode_dtc(code: str) -> bytes:
    hi = (_LETTER.index(code[0]) << 6) | (int(code[1]) << 4) | int(code[2], 16)
    return bytes([hi, int(code[3:5], 16)])


def parse_dtc_reply(mode: int, body: bytes | None) -> list[str] | None:
    """Parse a Mode 03/07/0A reply. None means no answer or a negative response."""
    if not body or body[0] != mode + 0x40:
        return None
    # ISO 15765 replies carry a count byte after the mode; pairs of zeros are padding.
    data = body[2:]
    codes = []
    for i in range(0, len(data) - 1, 2):
        if data[i] == 0 and data[i + 1] == 0:
            continue
        codes.append(decode_dtc(data[i], data[i + 1]))
    return codes


def parse_mil(body: bytes | None) -> dict | None:
    """Mode 01 PID 01: bit 7 of A is the check-engine light, the low 7 bits the code count."""
    if not body or len(body) < 3 or body[0] != 0x41 or body[1] != 0x01:
        return None
    return {"mil": bool(body[2] & 0x80), "count": body[2] & 0x7F}


def describe(code: str) -> str:
    return DESCRIPTIONS.get(code, "")
