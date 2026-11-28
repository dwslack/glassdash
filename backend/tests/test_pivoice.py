import asyncio
import math
import os
import stat
import sys
import wave
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from dashd.bus import SignalBus
from dashd.pivoice import CHUNK_S, RATE, Endpointer, PiVoice, find_card
from dashd.server import create_app

CARDS = """ 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 1 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1
                      vc4-hdmi-1
 2 [Sync20         ]: USB-Audio - Poly Sync 20
                      Plantronics Poly Sync 20 at usb-xhci-hcd.1-1, full speed
"""


def test_find_card():
    assert find_card(CARDS) == "Sync20"
    assert find_card(" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n") is None


def frames(*parts):
    """(seconds, level) parts -> levels, one per 30 ms frame."""
    return [lvl for s, lvl in parts for _ in range(int(s / CHUNK_S))]


def run_ep(levels, **kw):
    ep = Endpointer(**kw)
    for i, lvl in enumerate(levels):
        v = ep.feed(lvl)
        if v in ("done", "nothing"):
            return v, i * CHUNK_S
    return "eof", None


def test_endpointer():
    v, t = run_ep(frames((0.5, 50), (1.5, 3000), (2, 50)))
    assert v == "done" and 2.8 < t < 3.0          # ends ~0.9 s after speech stops
    assert run_ep(frames((6, 50)))[0] == "nothing"  # nobody spoke
    assert run_ep(frames((0.5, 50), (0.03, 9000), (6, 50)))[0] == "nothing"  # a click isn't speech
    v, t = run_ep(frames((0.3, 50), (20, 3000)))
    assert v == "done" and t < 10.1                 # capped
    # A noisy cab raises the bar: steady road noise alone isn't speech.
    assert run_ep(frames((0.3, 800), (6, 900)))[0] == "nothing"
    assert run_ep(frames((0.3, 800), (1, 4000), (2, 800)))[0] == "done"


def fake_arecord(tmp_path, monkeypatch):
    """An arecord on PATH that 'hears' 0.5 s quiet, 1 s of a tone, then quiet."""
    script = tmp_path / "bin" / "arecord"
    script.parent.mkdir()
    script.write_text(f"""#!{sys.executable}
import math, struct, sys, time
out = sys.stdout.buffer
for i in range(int({RATE} * 4)):
    t = i / {RATE}
    v = int(6000 * math.sin(2 * math.pi * 300 * t)) if 0.5 < t < 1.5 else (i % 7) - 3
    out.write(struct.pack("<h", v))
out.flush()
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "bin" / "aplay").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "bin" / "aplay").chmod(0o755)
    monkeypatch.setenv("PATH", f"{script.parent}{os.pathsep}{os.environ['PATH']}")


def make_voice(tmp_path, monkeypatch):
    fake_arecord(tmp_path, monkeypatch)
    pv = PiVoice(tmp_path, device="plughw:CARD=Fake", load=False)
    heard = {}

    def transcribe(audio, **kw):
        with wave.open(audio) as w:
            heard["seconds"] = w.getnframes() / w.getframerate()
        return [NS(text=" What's the coolant temp?")], None
    pv._stt = NS(transcribe=transcribe)

    def synth(text, w):
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b"\0\0" * 100)
        heard["said"] = text
    pv._tts = NS(synthesize_wav=synth)
    return pv, heard


def test_listen_and_say(tmp_path, monkeypatch):
    pv, heard = make_voice(tmp_path, monkeypatch)
    out = asyncio.run(pv.listen())
    assert out == {"text": "What's the coolant temp?"}
    assert 2.0 < heard["seconds"] < 3.0   # stopped after the quiet, didn't take all 4 s
    assert asyncio.run(pv.say("Coolant is 92 degrees.")) == {"ok": True}
    assert heard["said"] == "Coolant is 92 degrees." and pv.state == "idle"


def test_not_ready(tmp_path):
    pv = PiVoice(tmp_path, device="plughw:CARD=Fake", load=False)
    out = asyncio.run(pv.listen())
    assert out["text"] == "" and out["error"]
    assert asyncio.run(pv.say("hi"))["ok"] is False
    st = pv.status()
    assert st["enabled"] and not st["listen"] and not st["speak"]


def test_endpoints(tmp_path, monkeypatch):
    app = create_app(SignalBus())
    c = TestClient(app)
    assert c.get("/api/pivoice").json() == {"enabled": False}
    assert c.post("/api/pivoice/listen").status_code == 404

    app.state.pivoice, heard = make_voice(tmp_path, monkeypatch)
    st = c.get("/api/pivoice").json()
    assert st["enabled"] and st["listen"] and st["speak"] and st["device"] == "plughw:CARD=Fake"
    assert c.post("/api/pivoice/listen").json()["text"] == "What's the coolant temp?"
    assert c.post("/api/pivoice/say", json={"text": "92 degrees"}).json() == {"ok": True}
    assert c.post("/api/pivoice/stop").json() == {"ok": True}
