"""Voice assistant: the iPad turns speech into text, dashd answers it.

Common commands are handled right here with no internet and no cost ("navigate
home", "stop navigation", "recenter", "what's the coolant temp", "show trips").
Anything else goes to Claude, with tools that read the truck's live data and
drive navigation and the screen. The reply is short plain text for the iPad to speak.

Clearing trouble codes is deliberately not a tool: it changes the truck, so it
stays a two-tap action on the Diagnostics page.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .bus import SignalBus
from .settings import Settings

log = logging.getLogger("dashd.assistant")

# Spoken names for signals, and how to say them.
SIGNALS = {
    "speed": ("speed", "km/h"), "rpm": ("engine speed", "RPM"), "coolant": ("coolant temperature", "degrees"),
    "trans_temp": ("transmission temperature", "degrees"), "voltage": ("battery voltage", "volts"),
    "fuel_level": ("fuel level", "percent"), "oil_temp": ("oil temperature", "degrees"),
    "oil_pressure": ("oil pressure", "kPa"), "intake_temp": ("intake air temperature", "degrees"),
    "ambient_temp": ("outside temperature", "degrees"), "engine_load": ("engine load", "percent"),
    "throttle": ("throttle", "percent"), "baro": ("barometric pressure", "kPa"), "timing": ("timing advance", "degrees"),
    "maf": ("mass airflow", "grams per second"),
}
# What people say -> signal key. Checked in order, so longer phrases first.
SIGNAL_WORDS = [
    ("oil pressure", "oil_pressure"), ("oil temp", "oil_temp"), ("trans", "trans_temp"), ("transmission", "trans_temp"),
    ("coolant", "coolant"), ("engine temp", "coolant"), ("water temp", "coolant"), ("battery", "voltage"),
    ("voltage", "voltage"), ("volts", "voltage"), ("fuel", "fuel_level"), ("gas", "fuel_level"), ("rpm", "rpm"),
    ("revs", "rpm"), ("speed", "speed"), ("how fast", "speed"), ("intake", "intake_temp"),
    ("outside", "ambient_temp"), ("ambient", "ambient_temp"), ("load", "engine_load"), ("throttle", "throttle"),
    ("boost", "baro"), ("timing", "timing"), ("airflow", "maf"), ("maf", "maf"),
]
PAGES = {"dash": "dash", "map": "dash", "home screen": "dash", "gauges": "dash", "trips": "trips", "trip": "trips",
         "diagnostics": "diag", "codes": "diag", "truck": "truck", "doors": "truck", "settings": "settings"}
# Dash views (swipeable layouts of the gauge panel).
DASH_VIEWS = {"drive": "drive", "driving": "drive", "tow": "tow", "towing": "tow", "economy": "economy",
              "fuel economy": "economy", "all gauges": "engine", "engine": "engine", "big map": "map"}

SYSTEM = """You are the voice assistant built into the glass dash of the driver's {vehicle}. \
They are usually driving.

Your reply is read aloud by the iPad. Answer in one or two short sentences of plain speech: no markdown, \
no lists, no symbols that read badly aloud. Use metric units and say them as words ("92 degrees", "14.1 volts").

Never guess a reading: get live values from the tools. Say so if a value is stale or missing (the truck may be \
off). For warnings, say what matters for the drive right now. You can start or stop navigation, search places \
and change what the screen shows. Saved places such as Home come back from search_places with kind "saved". \
If a search is ambiguous, pick the nearest sensible match and say which one you chose. You cannot clear \
trouble codes; tell them to use the Diagnostics page for that."""

TOOLS: list[dict] = [
    {"name": "get_truck_status", "strict": True,
     "description": "Live readings from the truck: every engine/transmission signal with unit and whether it is stale, "
                    "GPS speed and position, check-engine light, doors/lights/belts, current drive and fuel range.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "read_trouble_codes", "strict": True,
     "description": "Read stored, pending and permanent trouble codes from the engine and transmission computers. "
                    "Takes a second or two.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "list_trips", "strict": True,
     "description": "Recent drives (newest first) with distance, time, fuel used and economy, plus the Trip A/B counters.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "How many, 1-20"}},
                      "required": ["limit"], "additionalProperties": False}},
    {"name": "search_places", "strict": True,
     "description": "Search offline map places near the truck: saved places, towns, businesses, streets, addresses.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False}},
    {"name": "start_navigation", "strict": True,
     "description": "Start turn-by-turn navigation from the truck to a place (use coordinates from search_places).",
     "input_schema": {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"},
                                                       "name": {"type": "string"}},
                      "required": ["lat", "lon", "name"], "additionalProperties": False}},
    {"name": "stop_navigation", "strict": True, "description": "End the current navigation.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "get_navigation_status", "strict": True,
     "description": "Current navigation: destination, next turn, distance and time left, arrival time.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "control_screen", "strict": True,
     "description": "Change the dash screen: show a page, recenter or zoom the map, switch satellite or street map, "
                    "or pick a gauge view (view_drive, view_tow for towing, view_economy, view_engine for all gauges, view_map for a big map).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": [
         "show_dash", "show_trips", "show_truck", "show_diagnostics", "show_settings",
         "recenter", "zoom_in", "zoom_out", "satellite_map", "street_map",
         "view_drive", "view_tow", "view_economy", "view_engine", "view_map"]}},
                      "required": ["action"], "additionalProperties": False}},
]


def _fmt_clock(ts: float) -> str:
    return time.strftime("%-I:%M %p", time.localtime(ts))


def _fmt_dist(m: float) -> str:
    return f"{m / 1000:.1f} kilometres" if m >= 1000 else f"{round(m / 10) * 10:.0f} metres"


class Assistant:
    def __init__(self, sig: SignalBus, settings: Settings, nav=None, diag_fn=None, trips=None, client=None,
                 vehicle_fn=None):
        self.sig = sig
        self.vehicle_fn = vehicle_fn or (lambda: "truck")  # the vehicle profile's name
        self.settings = settings
        self.nav = nav
        self.diag_fn = diag_fn  # returns a Diagnostics or None (set at startup)
        self.trips = trips
        self._client = client
        self._client_key = None
        self.history: list[dict] = []
        self.last_used = 0.0

    # ---------- answers without the internet ----------

    def reading(self, key: str) -> str:
        name, unit = SIGNALS[key]
        r = self.sig.signals.get(key)
        if r is None or self.sig.is_stale(r, time.time()):
            if key == "speed" and self.sig.gps:
                return f"GPS speed is {self.sig.gps.get('speed', 0):.0f} km/h."
            return f"I don't have a current {name} reading. The truck may be off."
        v = r.value
        shown = f"{v:.1f}" if key == "voltage" else f"{v:.0f}"
        return f"{name.capitalize()} is {shown} {unit}."

    async def local(self, text: str) -> dict | None:
        """Fast path for common commands. None means: not understood here."""
        t = text.lower().strip().rstrip(".!?")
        t = re.sub(r"^(hey |ok |okay )?(truck|dash|glass ?dash)[, ]+", "", t)
        t = re.sub(r"^(please |can you |could you )", "", t)

        if re.fullmatch(r"(stop|end|cancel|quit|exit)( the)? (navigation|navigating|route|directions|guidance)|stop navigating", t):
            if self.nav is None or self.nav.nav is None:
                return {"reply": "Navigation isn't running."}
            self.nav.stop()
            return {"reply": "Navigation stopped."}
        if re.fullmatch(r"(re ?cent(er|re)|center|centre)( the)?( map)?|(follow|show) (me|the truck)|where am i", t):
            return {"reply": "Recentering.", "actions": ["recenter"]}
        m = re.fullmatch(r"zoom (in|out)", t)
        if m:
            return {"reply": f"Zooming {m.group(1)}.", "actions": [f"zoom_{m.group(1)}"]}
        m = re.fullmatch(r"(show|open|go to|switch to)( me)?( the)? (.+?)( page| screen| view)?", t)
        if m and m.group(4) in DASH_VIEWS:
            return {"reply": "", "actions": [f"view_{DASH_VIEWS[m.group(4)]}"]}
        if m and m.group(4) in PAGES:
            return {"reply": "", "actions": [f"show_{PAGES[m.group(4)]}".replace("show_diag", "show_diagnostics")]}
        if re.fullmatch(r"(satellite|satellite (map|view))|(show|switch to) (the )?satellite( map| view)?", t):
            return {"reply": "Satellite map.", "actions": ["satellite_map"]}
        if re.fullmatch(r"(street map|normal map|regular map)|(show|switch to) (the )?(street|normal|regular) map", t):
            return {"reply": "Street map.", "actions": ["street_map"]}
        if re.search(r"\b(eta|how (much )?(far|long)|when (will|do) (we|i) (get|arrive)|time left|distance left)\b", t) \
                and self.nav is not None and self.nav.nav is not None:
            return {"reply": self.nav_summary()}
        m = re.fullmatch(r"(navigate|take me|drive|directions|go|get me|route|head)( me)?( to| home)?( the)? ?(.*)", t)
        if m and self.nav is not None:
            dest = (m.group(5) or "").strip() or ("home" if "home" in (m.group(3) or "") else "")
            if not dest:
                return None
            return await self.navigate_to(dest)
        if re.match(r"(what'?s|what is|how'?s|how is|check|tell me)\b", t) or t.endswith(("temp", "temperature", "pressure", "level", "voltage")):
            for word, key in SIGNAL_WORDS:
                if re.search(rf"\b{word}", t):
                    return {"reply": self.reading(key)}
        return None

    async def navigate_to(self, dest: str) -> dict:
        nav = self.nav
        g = self.sig.gps
        near = (g["lat"], g["lon"]) if g else None
        hits = [{"name": f["name"], "lat": f["lat"], "lon": f["lon"], "city": "", "kind": "saved"} for f in nav.favorites.match(dest)]
        if not hits and nav.places is not None:
            hits = nav.places.search(dest, near, 3)
        if not hits:
            if dest == "home":
                return {"reply": "You haven't saved Home yet. Open Navigate, tap your house on the map and save it as Home."}
            return {"reply": f"I couldn't find {dest}."}
        p = hits[0]
        from .nav.service import NavError
        try:
            route = await nav.start({"lat": p["lat"], "lon": p["lon"], "name": p["name"]})
        except NavError as e:
            return {"reply": str(e)}
        where = f" in {p['city']}" if p.get("city") else ""
        mins = round(route.duration / 60)
        return {"reply": f"Navigating to {p['name']}{where}. {_fmt_dist(route.length)}, about {mins} minutes.",
                "actions": ["show_dash", "recenter"]}

    def nav_summary(self) -> str:
        st = self.sig.nav or {}
        if not st:
            return "Navigation isn't running."
        if st.get("status") == "arrived":
            return "You've arrived."
        dest = st.get("dest", {}).get("name") or "the destination"
        return (f"{_fmt_dist(st['remaining_m'])} to {dest}, about {round(st['remaining_s'] / 60)} minutes. "
                f"You'll get there around {_fmt_clock(st['eta_ts'])}.")

    # ---------- Claude ----------

    def client(self):
        key = self.settings["anthropic_key"]
        if self._client is not None:
            return self._client
        if not key:
            return None
        if key != self._client_key:
            import anthropic

            self._client_key = key
            self._cached = anthropic.AsyncAnthropic(api_key=key, timeout=25.0, max_retries=1)
        return self._cached

    async def run_tool(self, name: str, args: dict) -> tuple[Any, list[str]]:
        """Returns (result for Claude, screen actions)."""
        if name == "get_truck_status":
            snap = self.sig.snapshot()
            snap.pop("errors", None)
            nav = snap.pop("nav", None)
            snap["navigating_to"] = nav["dest"].get("name") if nav else None
            return snap, []
        if name == "read_trouble_codes":
            d = self.diag_fn() if self.diag_fn else None
            if d is None:
                return {"error": "diagnostics not available"}, []
            res = await d.read()
            from .dtc import describe
            for ecu in res.get("ecus", {}).values():
                for kind, codes in ecu.items():
                    if codes:
                        ecu[kind] = [{"code": c, "desc": describe(c)} if isinstance(c, str) else c for c in codes]
            return res, []
        if name == "list_trips":
            if self.trips is None:
                return {"error": "trip log not available"}, []
            n = max(1, min(20, int(args.get("limit", 5))))
            return {"trips": self.trips.list(n), "counters": (self.sig.trip or {}).get("counters")}, []
        if name == "search_places":
            g = self.sig.gps
            near = (g["lat"], g["lon"]) if g else None
            saved = [{**f, "kind": "saved"} for f in self.nav.favorites.match(args["query"])] if self.nav else []
            found = self.nav.places.search(args["query"], near, 5) if self.nav and self.nav.places else []
            return {"results": saved + found}, []
        if name == "start_navigation":
            from .nav.service import NavError
            try:
                route = await self.nav.start({"lat": args["lat"], "lon": args["lon"], "name": args["name"]})
            except NavError as e:
                return {"error": str(e)}, []
            return {"ok": True, "distance_km": round(route.length / 1000, 1), "minutes": round(route.duration / 60)}, \
                ["show_dash", "recenter"]
        if name == "stop_navigation":
            if self.nav:
                self.nav.stop()
            return {"ok": True}, []
        if name == "get_navigation_status":
            return self.sig.nav or {"navigating": False}, []
        if name == "control_screen":
            return {"ok": True}, [args["action"]]
        return {"error": f"unknown tool {name}"}, []

    async def ask_claude(self, text: str) -> dict:
        client = self.client()
        if client is None:
            return {"reply": "I can only do basic commands until an Anthropic API key is added in Settings."}
        now = time.time()
        if now - self.last_used > 180 or len(self.history) > 30:
            self.history = []  # a new conversation after a few quiet minutes
        self.last_used = now
        messages = self.history + [{"role": "user", "content": f"[{time.strftime('%A %-I:%M %p')}] {text}"}]
        actions: list[str] = []
        import anthropic

        try:
            for _ in range(6):
                resp = await client.beta.messages.create(
                    model=self.settings["assistant_model"],
                    max_tokens=4096,
                    system=SYSTEM.format(vehicle=self.vehicle_fn()),
                    tools=TOOLS,
                    messages=messages,
                    output_config={"effort": "low"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
                messages.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason == "refusal":
                    self.history = []
                    return {"reply": "Sorry, I can't help with that one.", "actions": actions}
                if resp.stop_reason == "pause_turn":
                    continue
                uses = [b for b in resp.content if b.type == "tool_use"]
                if resp.stop_reason != "tool_use" or not uses:
                    break
                results = []
                for b in uses:
                    try:
                        out, acts = await self.run_tool(b.name, b.input)
                        actions += acts
                        results.append({"type": "tool_result", "tool_use_id": b.id,
                                        "content": json.dumps(out, default=str)[:20000]})
                    except Exception as e:  # a failing tool shouldn't kill the answer
                        log.exception("assistant tool %s failed", b.name)
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"Error: {e}",
                                        "is_error": True})
                messages.append({"role": "user", "content": results})
        except anthropic.AuthenticationError:
            return {"reply": "The Anthropic API key was rejected. Check it in Settings.", "actions": actions}
        except anthropic.RateLimitError:
            return {"reply": "Claude is rate limited right now. Try again in a minute.", "actions": actions}
        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            return {"reply": "No connection to Claude. Basic commands still work.", "actions": actions}
        except anthropic.APIStatusError as e:
            log.warning("assistant API error %s", e)
            return {"reply": "Claude had an error. Try again.", "actions": actions}
        reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
        self.history = messages if messages[-1]["role"] == "assistant" else []
        return {"reply": reply or "Done.", "actions": actions}

    async def handle(self, text: str) -> dict:
        text = (text or "").strip()[:500]
        if not text:
            return {"reply": "I didn't catch that.", "source": "local"}
        out = await self.local(text)
        if out is not None:
            return {"actions": [], **out, "source": "local"}
        return {"actions": [], **(await self.ask_claude(text)), "source": "claude"}
