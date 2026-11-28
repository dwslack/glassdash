"""HTTP + WebSocket server: pushes signal snapshots to the browser UI."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .bus import SignalBus
from .diag import Diagnostics
from .geo import dist
from .nav.service import NavError, NavService
from .pids import Pid
from .assistant import Assistant
from .learn import Learner, Tap, snippet
from .vehicles import Profile, ProfileError, list_profiles, load_profile
from .settings import Settings, mask
from .sim.route import Route, list_tracks, track_path
from .trips import COUNTERS, TripLog

PUSH_HZ = 20


def create_app(
    sig: SignalBus,
    lifespan=None,
    frontend_dist: Path | None = None,
    track: Path | None = None,
    trips: TripLog | None = None,
    diag: Diagnostics | None = None,
    pids: list[Pid] | None = None,
    settings: Settings | None = None,
    map_dir: Path | None = None,
    nav: NavService | None = None,
    profile: Profile | None = None,
    tap: Tap | None = None,
) -> FastAPI:
    app = FastAPI(title="dashd", lifespan=lifespan)
    settings = settings or Settings(None)
    app.state.profile = profile
    tap = tap or Tap()

    @app.get("/api/settings")
    def get_settings():
        return mask(settings.values)

    @app.put("/api/settings")
    def put_settings(changes: dict):
        new_profile = None
        if "vehicle" in changes:
            try:
                new_profile = load_profile(str(changes["vehicle"]))
            except ProfileError as e:
                raise HTTPException(422, str(e))
            if new_profile.abstract:
                raise HTTPException(422, f"{new_profile.id} is a base for other profiles; pick a specific vehicle")
        try:
            out = mask(settings.update(changes))
        except ValueError as e:
            raise HTTPException(422, str(e))
        if new_profile is not None:
            app.state.profile = new_profile
            switch = getattr(app.state, "switch_vehicle", None)
            if switch is not None:
                switch(new_profile)
        return out

    # ---- Vehicle profiles ----

    @app.get("/api/vehicles")
    def vehicles():
        return {"current": getattr(app.state.profile, "id", None), "profiles": list_profiles()}

    @app.get("/api/vehicle")
    def vehicle():
        p = app.state.profile
        if p is None:
            raise HTTPException(503, "no vehicle profile loaded")
        return p.summary()

    # ---- "Map a signal": record the body bus while the user marks on/off, then find the bit ----

    def need_learner() -> Learner:
        if tap.learner is None:
            raise HTTPException(409, "not recording; start first")
        return tap.learner

    @app.post("/api/learn/start")
    def learn_start(body: dict):
        name = str(body.get("signal", "")).strip().lower().replace(" ", "_")
        if not name or len(name) > 32 or not name.replace("_", "").isalnum() or not name[0].isalpha():
            raise HTTPException(422, "signal name: letters, digits and _ only, starting with a letter")
        p = app.state.profile
        gmlan = bool(p and p.bus and p.bus.get("type") == "gmlan-swcan")
        tap.learner = Learner(name, gmlan=gmlan)
        return {**tap.learner.status(), "body_bus": getattr(app.state, "body_bus_up", False)}

    @app.post("/api/learn/mark")
    def learn_mark(body: dict):
        lr = need_learner()
        on = bool(body.get("on"))
        lr.mark(on)
        v = getattr(app.state, "vehicle", None)  # in the sim, "toggle it for me" flips the simulated signal
        if body.get("sim") and v is not None:
            v.body_overrides[lr.signal] = on
        return lr.status()

    @app.get("/api/learn/status")
    def learn_status():
        return need_learner().status()

    @app.post("/api/learn/analyze")
    def learn_analyze():
        lr = need_learner()
        try:
            found = lr.analyze()
        except ValueError as e:
            raise HTTPException(422, str(e))
        p = app.state.profile
        frames = p.frames if p else []
        for c in found:
            c["snippet"] = snippet(c, lr.signal, frames, lr.gmlan)
        return {**lr.status(), "candidates": found}

    @app.post("/api/learn/stop")
    def learn_stop():
        lr, tap.learner = tap.learner, None
        v = getattr(app.state, "vehicle", None)
        if lr is not None and v is not None:
            v.body_overrides.pop(lr.signal, None)
        return {"ok": True}

    assistant = Assistant(sig, settings, diag_fn=lambda: diag or getattr(app.state, "diag", None), trips=trips,
                          vehicle_fn=lambda: app.state.profile.name if app.state.profile else "vehicle")
    app.state.assistant = assistant

    # ---- Voice on the Pi itself (USB speakerphone; dashd --pi-voice). The kiosk uses it. ----

    def need_pivoice():
        pv = getattr(app.state, "pivoice", None)
        if pv is None:
            raise HTTPException(404, "voice on the Pi is off (dashd --pi-voice)")
        return pv

    @app.get("/api/pivoice")
    def pivoice_status():
        pv = getattr(app.state, "pivoice", None)
        return pv.status() if pv is not None else {"enabled": False}

    @app.post("/api/pivoice/listen")
    async def pivoice_listen():
        return await need_pivoice().listen()

    @app.post("/api/pivoice/stop")
    async def pivoice_stop():
        pv = need_pivoice()
        await pv.stop_listening()
        await pv.stop_speaking()
        return {"ok": True}

    @app.post("/api/pivoice/say")
    async def pivoice_say(body: dict):
        return await need_pivoice().say(str(body.get("text", "")))

    @app.post("/api/assistant")
    async def ask(body: dict):
        assistant.nav = nav or getattr(app.state, "nav", None)
        return await assistant.handle(str(body.get("text", "")))

    def need_trips() -> TripLog:
        if trips is None:
            raise HTTPException(503, "trip log not enabled")
        return trips

    @app.get("/api/pids")
    def list_pids():
        return {"pids": [{"name": p.name, "mode": p.mode, "pid": p.pid, "ecu": p.ecu, "unit": p.unit,
                          "hz": p.hz, "confirmed": p.confirmed, "verified": p.verified}
                         for p in (app.state.profile.pids if app.state.profile else pids or [])]}

    def need_diag() -> Diagnostics:
        d = diag or getattr(app.state, "diag", None)  # tests pass one in; dashd sets it at startup
        if d is None:
            raise HTTPException(503, "diagnostics not available")
        return d

    @app.get("/api/dtcs")
    async def read_dtcs():
        return await need_diag().read()

    @app.post("/api/dtcs/clear")
    async def clear_dtcs():
        d = need_diag()
        if not d.stopped():
            raise HTTPException(409, "Stop the truck first: codes can only be cleared when stopped.")
        cleared = await d.clear()
        return {"cleared": cleared, **(await d.read())}

    @app.get("/api/trips")
    def list_trips(limit: int = 100):
        return {"trips": need_trips().list(limit)}

    @app.get("/api/trips/{trip_id}")
    def get_trip(trip_id: int):
        t = need_trips().get(trip_id)
        if t is None:
            raise HTTPException(404, "no such trip")
        return t

    @app.delete("/api/trips/{trip_id}")
    def delete_trip(trip_id: int):
        if not need_trips().delete(trip_id):
            raise HTTPException(404, "no such trip")
        return {"ok": True}

    @app.post("/api/counters/{name}/reset")
    def reset_counter(name: str):
        if name not in COUNTERS:
            raise HTTPException(404, "no such counter")
        need_trips().reset_counter(name)
        return {"ok": True}

    @app.get("/glassdash-ca.crt")
    def ca_cert():
        # The public certificate of the local CA from `just https-cert`, for installing on the iPad.
        f = map_dir / "tls" / "glassdash-ca.crt" if map_dir else None
        if f is None or not f.exists():
            raise HTTPException(404, "no certificate yet (run: just https-cert)")
        return Response(f.read_bytes(), media_type="application/x-x509-ca-cert")

    @app.get("/api/snapshot")
    def snapshot():
        return sig.snapshot()

    @app.get("/api/track")
    def get_track():
        v = getattr(app.state, "vehicle", None)
        if v is not None and v.route is not None:  # the sim's current route
            return {"name": v.route.name, "points": [list(p) for p in v.route.points]}
        if track is None or not track.exists():
            return {"points": []}
        return json.loads(track.read_text())

    @app.get("/api/sim/routes")
    def sim_routes():
        v = getattr(app.state, "vehicle", None)
        if v is None:
            return {"sim": False, "routes": [], "current": None}
        current = v.route.file if v.route is not None else None
        return {"sim": True, "current": current,
                "routes": [{"id": n, "name": Route.load(track_path(n)).name} for n in list_tracks()]}

    @app.post("/api/sim/route")
    def set_sim_route(body: dict):
        v = getattr(app.state, "vehicle", None)
        if v is None:
            raise HTTPException(409, "not running the simulator")
        try:
            route = Route.load(track_path(str(body.get("id", ""))))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(404, str(e))
        v.set_route(route)
        return {"ok": True, "current": route.file, "name": route.name}

    def need_nav() -> NavService:
        n = nav or getattr(app.state, "nav", None)
        if n is None:
            raise HTTPException(503, "navigation not available")
        return n

    @app.get("/api/nav/status")
    def nav_status():
        n = nav or getattr(app.state, "nav", None)
        return n.status() if n else {"routing": False, "search": False, "problems": {"nav": "not started"}}

    @app.get("/api/places")
    def search_places(q: str, lat: float | None = None, lon: float | None = None, limit: int = 10):
        n = need_nav()
        near = (lat, lon) if lat is not None and lon is not None else (
            (sig.gps["lat"], sig.gps["lon"]) if sig.gps else None)
        saved = [{"name": x["name"], "city": "", "kind": "saved", "lat": x["lat"], "lon": x["lon"], "id": x["id"],
                  "distance_m": round(dist(near, (x["lat"], x["lon"]))) if near else None}
                 for x in n.favorites.match(q)]
        if n.places is None:
            if saved:
                return {"results": saved}
            raise HTTPException(503, n.problems.get("search", "place search not available"))
        return {"results": (saved + n.places.search(q, near, min(limit, 30)))[:max(limit, len(saved))]}

    @app.get("/api/favorites")
    def list_favorites():
        return {"favorites": need_nav().favorites.items}

    @app.post("/api/favorites")
    def add_favorite(body: dict):
        try:
            item = need_nav().favorites.add(str(body.get("name", "")), float(body["lat"]), float(body["lon"]))
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(422, str(e) or "need name, lat and lon")
        return item

    @app.delete("/api/favorites/{item_id}")
    def delete_favorite(item_id: str):
        if not need_nav().favorites.remove(item_id):
            raise HTTPException(404, "no such saved place")
        return {"ok": True}

    @app.post("/api/nav")
    async def start_nav(body: dict):
        n = need_nav()
        try:
            dest = {"lat": float(body["lat"]), "lon": float(body["lon"]), "name": str(body.get("name", ""))[:120]}
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, "need lat, lon and optional name")
        try:
            route = await n.start(dest)
        except NavError as e:
            raise HTTPException(409, str(e))
        return {"ok": True, "route_id": n.route_id, "distance_m": round(route.length), "duration_s": round(route.duration)}

    @app.delete("/api/nav")
    def stop_nav():
        need_nav().stop()
        return {"ok": True}

    @app.get("/api/nav/route")
    def nav_route():
        n = need_nav()
        return {"route_id": n.route_id if n.nav else None, "points": n.route_points()}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        try:
            while True:
                await socket.send_json(sig.snapshot())
                await asyncio.sleep(1 / PUSH_HZ)
        except (WebSocketDisconnect, RuntimeError):
            pass

    # Offline map files (see `just map-bc` / `just map-assets`). Served with HTTP Range
    # support, which the PMTiles reader needs.
    if map_dir is not None:
        for sub, route in (("maps", "/tiles"), ("map-assets", "/map-assets")):
            (map_dir / sub).mkdir(parents=True, exist_ok=True)
            app.mount(route, StaticFiles(directory=map_dir / sub), name=sub)

    if frontend_dist is not None and frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="ui")
    return app
