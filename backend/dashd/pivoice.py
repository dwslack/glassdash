"""Voice on the Pi itself: a USB speakerphone is the mic and the speaker.

The browser on the Pi's own screen (Chromium) has no speech recognition, so for the
kiosk the Pi does it: dashd records from the speakerphone, Whisper (faster-whisper)
turns it into text on the Pi with no internet, the existing assistant answers, and
Piper speaks the reply through the speakerphone. The iPad keeps using its own mic
and voice; none of this runs unless dashd is started with --pi-voice.

Half duplex: it never listens while speaking (a tap while speaking stops the speech).
A conference speakerphone also cancels its own echo in hardware.

Audio goes through ALSA directly (arecord/aplay), so it needs no desktop sound server.
"""
from __future__ import annotations

import array
import asyncio
import io
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import wave
from pathlib import Path

log = logging.getLogger("dashd.pivoice")

RATE = 16000              # Whisper wants 16 kHz mono
CHUNK_S = 0.03            # 30 ms frames for the speech detector
# Whisper does better with the words it should expect.
PROMPT = ("Glass dash in a Silverado pickup. Navigate home. Stop navigation. Recenter. "
          "What's the coolant temperature? Transmission temp. Show trips. Tow view. Trouble codes.")


def find_card(cards_text: str | None = None) -> str | None:
    """ALSA card id of the first USB audio device (the speakerphone), from /proc/asound/cards."""
    if cards_text is None:
        try:
            cards_text = Path("/proc/asound/cards").read_text()
        except OSError:
            return None
    # " 2 [Sync20         ]: USB-Audio - Poly Sync 20"
    for m in re.finditer(r"^\s*\d+\s+\[(\S+)\s*\]:\s*(\S+)", cards_text, re.M):
        if m.group(2).lower().startswith("usb-audio"):
            return m.group(1)
    return None


def rms(frame: bytes) -> float:
    a = array.array("h", frame)
    return math.sqrt(sum(x * x for x in a) / len(a)) if a else 0.0


class Endpointer:
    """Decides when an utterance starts and ends from 30 ms frames of 16-bit audio.

    The first few frames set the room's noise floor; speech is anything well above it.
    Ends after `trail_s` of quiet following speech, or at `max_s`; gives up if nothing
    is said within `wait_s`.
    """

    def __init__(self, wait_s=5.0, trail_s=0.9, max_s=10.0, min_level=300.0):
        self.wait_n, self.trail_n, self.max_n = (int(s / CHUNK_S) for s in (wait_s, trail_s, max_s))
        self.min_level = min_level
        self.n = 0
        self.floor: list[float] = []
        self.started = False
        self.quiet = 0
        self.loud = 0

    def feed(self, level: float) -> str:
        """'wait', 'speech', 'done' (said something, now finished) or 'nothing'."""
        self.n += 1
        if len(self.floor) < 8:
            self.floor.append(level)
            return "wait"
        thresh = max(self.min_level, 3.0 * sorted(self.floor)[len(self.floor) // 2])
        if level > thresh:
            self.loud += 1
            self.quiet = 0
            if self.loud >= 3:  # 90 ms of sound, not a click
                self.started = True
        else:
            self.loud = 0
            self.quiet += 1
        if self.started and (self.quiet >= self.trail_n or self.n >= self.max_n):
            return "done"
        if not self.started and self.n >= self.wait_n:
            return "nothing"
        return "speech" if self.started else "wait"


class PiVoice:
    def __init__(self, data_dir: Path, device: str = "auto", whisper_model: str = "base.en",
                 piper_voice: Path | None = None, load: bool = True):
        self.dir = data_dir / "voice"
        self.device_setting = device
        self.whisper_model = whisper_model
        self.piper_voice = piper_voice or next(iter(sorted(self.dir.glob("*.onnx"))), None)
        self.state = "idle"          # idle | listening | thinking | speaking
        self.problems: dict[str, str] = {}
        self._stt = None
        self._tts = None
        self._lock = asyncio.Lock()
        self._rec: asyncio.subprocess.Process | None = None
        self._play: asyncio.subprocess.Process | None = None
        self._stop_listen = asyncio.Event()
        self._loading = threading.Thread(target=self._load, daemon=True)
        if load:
            self._loading.start()

    # ---- setup ----

    def device(self) -> str | None:
        if self.device_setting != "auto":
            return self.device_setting
        card = find_card()
        return f"plughw:CARD={card}" if card else None

    def _load(self) -> None:
        """Load the speech models in the background so dashd starts at once (Whisper takes a few seconds)."""
        for tool in ("arecord", "aplay"):
            if shutil.which(tool) is None:
                self.problems["audio"] = f"{tool} not installed (apt install alsa-utils)"
        try:
            from faster_whisper import WhisperModel
            self._stt = WhisperModel(self.whisper_model, device="cpu", compute_type="int8",
                                     download_root=str(self.dir / "whisper"), cpu_threads=4)
            log.info("speech recognition ready (whisper %s)", self.whisper_model)
        except Exception as e:  # missing package, no model and no internet, ...
            self.problems["listen"] = f"speech recognition not loaded: {e}"
            log.warning("pi voice: %s", self.problems["listen"])
        try:
            if self.piper_voice is None or not self.piper_voice.exists():
                raise FileNotFoundError(f"no Piper voice (.onnx) in {self.dir}")
            from piper import PiperVoice
            self._tts = PiperVoice.load(str(self.piper_voice))
            log.info("speech output ready (piper %s)", self.piper_voice.stem)
        except Exception as e:
            self.problems["speak"] = f"voice output not loaded: {e}"
            log.warning("pi voice: %s", self.problems["speak"])

    def status(self) -> dict:
        dev = self.device()
        problems = dict(self.problems)
        if dev is None:
            problems["device"] = "no USB speakerphone found (plug it in; check with: arecord -l)"
        loading = self._loading.is_alive()
        return {"enabled": True, "device": dev, "state": self.state, "loading": loading,
                "listen": self._stt is not None and dev is not None, "speak": self._tts is not None and dev is not None,
                "problems": problems}

    # ---- listening ----

    async def listen(self) -> dict:
        """Record one utterance and return {"text": ...}; empty text if nothing was said."""
        await self.stop_speaking()
        async with self._lock:
            dev = self.device()
            if dev is None or self._stt is None:
                st = self.status()
                why = "still loading speech recognition" if st["loading"] else "; ".join(st["problems"].values())
                return {"text": "", "error": why or "voice not ready"}
            self._stop_listen.clear()
            self.state = "listening"
            try:
                pcm, said = await self._record(dev)
            finally:
                self.state = "idle"
            if not said:
                return {"text": "", "error": "I didn't hear anything."}
            self.state = "thinking"
            try:
                text = await asyncio.to_thread(self._transcribe, pcm)
            finally:
                self.state = "idle"
            return {"text": text}

    async def stop_listening(self) -> None:
        """Finish the current recording now (tap the mic again)."""
        self._stop_listen.set()

    async def _record(self, dev: str) -> tuple[bytes, bool]:
        self._rec = await asyncio.create_subprocess_exec(
            "arecord", "-q", "-D", dev, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        frame = int(RATE * CHUNK_S) * 2
        ep = Endpointer()
        buf = bytearray()
        try:
            while True:
                try:
                    chunk = await self._rec.stdout.readexactly(frame)
                except asyncio.IncompleteReadError:
                    err = (await self._rec.stderr.read()).decode(errors="replace").strip()
                    if err:
                        log.warning("arecord: %s", err)
                    break
                buf += chunk
                verdict = ep.feed(rms(chunk))
                if verdict in ("done", "nothing"):
                    break
                if self._stop_listen.is_set():
                    break
        finally:
            if self._rec.returncode is None:
                self._rec.terminate()
            await self._rec.wait()
            self._rec = None
        return bytes(buf), ep.started or (self._stop_listen.is_set() and len(buf) > RATE)

    def _transcribe(self, pcm: bytes) -> str:
        audio = io.BytesIO()   # faster-whisper decodes a WAV file object itself (no numpy needed here)
        with wave.open(audio, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
        audio.seek(0)
        segments, _ = self._stt.transcribe(audio, language="en", beam_size=1, initial_prompt=PROMPT,
                                           condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("heard: %r", text)
        return text

    # ---- speaking ----

    async def say(self, text: str) -> dict:
        text = (text or "").strip()[:1000]
        dev = self.device()
        if not text:
            return {"ok": True}
        if dev is None or self._tts is None:
            return {"ok": False, "error": "voice output not ready"}
        await self.stop_speaking()
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="glassdash-say-")
        os.close(fd)
        try:
            await asyncio.to_thread(self._synth, text, path)
            self.state = "speaking"
            self._play = await asyncio.create_subprocess_exec("aplay", "-q", "-D", dev, path,
                                                              stderr=asyncio.subprocess.PIPE)
            _, err = await self._play.communicate()
            if self._play.returncode not in (0, -15) and err:
                log.warning("aplay: %s", err.decode(errors="replace").strip())
        finally:
            self._play = None
            if self.state == "speaking":
                self.state = "idle"
            Path(path).unlink(missing_ok=True)
        return {"ok": True}

    def _synth(self, text: str, path: str) -> None:
        with wave.open(path, "wb") as w:
            self._tts.synthesize_wav(text, w)

    async def stop_speaking(self) -> None:
        p = self._play
        if p is not None and p.returncode is None:
            p.terminate()
