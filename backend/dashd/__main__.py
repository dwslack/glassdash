"""Run dashd.

  python -m dashd --source sim          # laptop: fake ECUs + fake GPS, no hardware
  python -m dashd --source socketcan    # Pi: real can0 (GPS via gpsd arrives in M1)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from pathlib import Path

import can
import uvicorn

from .body import BodyDecoder, BodyListener
from .bus import SignalBus
from .diag import Diagnostics
from .nav.favorites import Favorites
from .nav.service import NavService
from .obd import Poller
from .learn import Tap
from .vehicles import ProfileError, load_profile
from .server import create_app
from .sim import body as body_sim
from .sim.ecu import EcuSim
from .sim.gps import DEFAULT_TRACK, GpsSim
from .sim.route import Route, list_tracks, track_path
from .sim.vehicle import Vehicle
from .settings import Settings
from .trips import TripLog, TripRecorder

log = logging.getLogger("dashd")
ROOT = Path(__file__).resolve().parents[2]
SIM_CHANNEL = "glassdash-sim"
SIM_BODY_CHANNEL = "glassdash-sim-body"


def build(source: str, channel: str, vehicle: str | None, track: Path, data_dir: Path, scenario: str = "",
          body_channel: str | None = None, nav_dir: Path | None = None):
    sig = SignalBus()
    # Sim drives go in their own file so they never mix with real ones.
    trip_log = TripLog(data_dir / ("trips-sim.db" if source == "sim" else "trips.db"))
    settings = Settings(data_dir / "settings.json")
    # --vehicle wins for this run; otherwise the one picked in Settings.
    try:
        profile = load_profile(vehicle or settings["vehicle"])
    except ProfileError as e:
        if vehicle:
            raise
        log.error("vehicle profile %r doesn't load (%s); using the default", settings["vehicle"], e)
        profile = load_profile()
    log.info("vehicle: %s (%s)", profile.name, profile.id)
    pids, body_frames = profile.pids, profile.frames
    decoder = BodyDecoder(body_frames)
    tap = Tap()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        tasks = []
        sim_parts = []
        if source == "sim":
            app_bus = can.Bus(interface="virtual", channel=SIM_CHANNEL)
            ecu_bus = can.Bus(interface="virtual", channel=SIM_CHANNEL)
            vehicle = Vehicle(scenario=scenario)
            vehicle.set_route(Route.load(track))
            app.state.vehicle = vehicle  # lets /api/sim/route switch routes live
            ecu = EcuSim(ecu_bus, vehicle).start()
            gps = GpsSim(vehicle)
            sim_parts = [ecu, ecu_bus]

            async def drive():
                while not stop.is_set():
                    vehicle.step(0.05)
                    await asyncio.sleep(0.05)

            async def gps_feed():
                while not stop.is_set():
                    sig.set_gps(gps.fix())
                    await asyncio.sleep(0.2)

            tasks += [asyncio.create_task(drive()), asyncio.create_task(gps_feed())]
            body_bus = can.Bus(interface="virtual", channel=SIM_BODY_CHANNEL, receive_own_messages=False)
            body_tx = can.Bus(interface="virtual", channel=SIM_BODY_CHANNEL)
            sim_parts.append(body_tx)
            tasks.append(asyncio.create_task(body_sim.broadcast(body_tx, lambda: app.state.profile.frames, vehicle, stop)))
        else:
            app_bus = can.Bus(interface="socketcan", channel=channel)
            # Channel 2 of the HAT on the single-wire body bus, listen-only at 33.333 kbit/s
            # (set up by the OS, see README). Missing interface = no body data, not a crash.
            body_bus = None
            if body_channel:
                try:
                    body_bus = can.Bus(interface="socketcan", channel=body_channel)
                except (OSError, can.CanError) as e:
                    log.warning("body bus %s not available (%s); doors/lights disabled", body_channel, e)
        if body_bus is not None:
            tasks.append(asyncio.create_task(BodyListener(body_bus, decoder, sig, tap).run(stop)))
        app.state.body_bus_up = body_bus is not None
        def follow(route, reroute):
            # In the sim the truck drives whatever route navigation picked, so guidance can be demoed.
            if source == "sim":
                speeds = route.segment_speeds()
                vehicle.set_route(Route(route.points, speeds, name=route.dest.get("name") or "Navigation"),
                                  keep_speed=True, loop=False)

        app.state.nav = NavService(sig, nav_dir if nav_dir is not None else data_dir / "nav", on_route=follow,
                                   favorites=Favorites(data_dir / "favorites.json"))
        for what, why in app.state.nav.problems.items():
            log.warning("navigation: %s unavailable: %s", what, why)
        tasks.append(asyncio.create_task(app.state.nav.run(stop)))
        poller = Poller(app_bus, pids, sig)

        def switch_vehicle(p):
            poller.set_pids(p.pids)
            decoder.set_frames(p.frames)
            log.info("vehicle switched to %s (%s)", p.name, p.id)
        app.state.switch_vehicle = switch_vehicle
        app.state.diag = Diagnostics(poller, sig)
        tasks.append(asyncio.create_task(poller.run(stop)))
        tasks.append(asyncio.create_task(app.state.diag.watch_mil(stop)))
        tasks.append(asyncio.create_task(TripRecorder(sig, trip_log, settings).run(stop)))
        try:
            yield
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            for part in sim_parts:
                part.stop() if isinstance(part, EcuSim) else part.shutdown()
            app_bus.shutdown()
            if body_bus is not None:
                body_bus.shutdown()
            app.state.diag = None

    app = create_app(sig, lifespan=lifespan, frontend_dist=ROOT / "frontend" / "dist", track=track, trips=trip_log, pids=pids,
                     settings=settings, map_dir=data_dir, profile=profile, tap=tap)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(prog="dashd")
    ap.add_argument("--source", choices=["sim", "socketcan"], default="sim")
    ap.add_argument("--channel", default="can0")
    ap.add_argument("--vehicle", default=None, help="vehicle profile id under vehicles/ (default: the one picked in Settings)")
    ap.add_argument("--track", type=Path, default=DEFAULT_TRACK, help="sim route file")
    ap.add_argument("--route", default=None, help=f"sim route by name, one of: {', '.join(list_tracks())}")
    ap.add_argument("--data", type=Path, default=ROOT / "data", help="where trips are stored")
    ap.add_argument("--body-channel", default="can1",
                    help="socketcan interface on GM single-wire CAN for doors/lights; '' to disable (sim has its own)")
    ap.add_argument("--scenario", choices=["", "overheat", "lowvolt", "oilpressure", "towing"], default="",
                    help="sim only: inject a fault to test alerts")
    ap.add_argument("--nav-dir", type=Path, default=None, help="offline routing + search data (default DATA/nav)")
    ap.add_argument("--tls-dir", type=Path, default=None,
                    help="serve HTTPS with cert.pem + key.pem from here (default DATA/tls if present; the iPad mic needs it)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    if args.route:
        try:
            args.track = track_path(args.route)
        except (ValueError, FileNotFoundError) as e:
            ap.error(str(e))
    logging.basicConfig(level=logging.INFO)
    try:
        app = build(args.source, args.channel, args.vehicle, args.track, args.data, args.scenario,
                    args.body_channel, args.nav_dir)
    except ProfileError as e:
        ap.error(str(e))
    tls = args.tls_dir or args.data / "tls"
    ssl = {}
    if (tls / "cert.pem").exists() and (tls / "key.pem").exists():
        ssl = {"ssl_certfile": str(tls / "cert.pem"), "ssl_keyfile": str(tls / "key.pem")}
        log.info("serving HTTPS with %s", tls / "cert.pem")
    elif args.tls_dir:
        ap.error(f"no cert.pem/key.pem in {args.tls_dir}")
    # Generous header limit: browsers send every cookie set on 127.0.0.1/localhost by other apps.
    uvicorn.run(app, host=args.host, port=args.port, h11_max_incomplete_event_size=64 * 1024, **ssl)


if __name__ == "__main__":
    main()
