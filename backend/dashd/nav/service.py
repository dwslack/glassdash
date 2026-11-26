"""Navigation service: start/stop a route, follow the GPS, reroute, publish guidance on the bus."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable

from ..bus import SignalBus
from .favorites import Favorites
from .guidance import Navigator
from .places import Places
from .route import NavRoute
from .router import RouteError, Router

log = logging.getLogger("dashd.nav")
TICK_S = 0.5
GPS_MAX_AGE_S = 3.0


class NavError(Exception):
    pass


class NavService:
    def __init__(self, sig: SignalBus, nav_dir: Path | None, router=None, places: Places | None = None,
                 on_route: Callable[[NavRoute, bool], None] | None = None, favorites: Favorites | None = None):
        self.sig = sig
        self.favorites = favorites or Favorites(None)
        self.problems: dict[str, str] = {}
        self.router = router
        self.places = places
        if nav_dir is not None and router is None:
            try:
                self.router = Router(nav_dir)
            except ImportError:
                self.problems["routing"] = "pyvalhalla not installed (run: just nav-setup)"
            except (FileNotFoundError, RuntimeError, ValueError) as e:
                self.problems["routing"] = str(e)
        if nav_dir is not None and places is None:
            db = nav_dir / "places.db"
            if db.exists():
                self.places = Places(db)
            else:
                self.problems["search"] = f"no place index in {nav_dir} (run: just nav-data)"
        self.on_route = on_route
        self.nav: Navigator | None = None
        self.route_id = 0
        self.rerouting = False
        self.error = ""

    def status(self) -> dict:
        return {"routing": self.router is not None, "search": self.places is not None, "problems": self.problems}

    def _fix(self) -> dict | None:
        g = self.sig.gps
        if not g or time.time() - g.get("ts", 0) > GPS_MAX_AGE_S:
            return None
        return g

    async def _route_from_here(self, dest: dict) -> NavRoute:
        if self.router is None:
            raise NavError(self.problems.get("routing", "routing not available"))
        fix = self._fix()
        if fix is None:
            raise NavError("No GPS fix yet.")
        heading = fix.get("heading") if (fix.get("speed") or 0) > 5 else None
        try:
            return await asyncio.to_thread(self.router.route, (fix["lat"], fix["lon"]), dest, heading)
        except RouteError as e:
            raise NavError(f"No route: {e}") from e

    def _use(self, route: NavRoute, reroute: bool) -> None:
        self.route_id += 1
        self.nav = Navigator(route, self.nav.speak_id if self.nav else self.route_id * 1000)
        if reroute:
            self.nav.last_reroute = time.time()
        self.error = ""
        if self.on_route is not None:
            self.on_route(route, reroute)

    async def start(self, dest: dict) -> NavRoute:
        route = await self._route_from_here(dest)
        self._use(route, reroute=False)
        self.publish()
        return route

    def stop(self) -> None:
        self.nav = None
        self.sig.nav = None

    def route_points(self) -> list[list[float]]:
        return [list(p) for p in self.nav.route.points] if self.nav else []

    async def _reroute(self) -> None:
        self.rerouting = True
        try:
            dest = self.nav.route.dest
            route = await self._route_from_here(dest)
            if self.nav is not None and self.nav.route.dest is dest:  # not stopped or replaced meanwhile
                self._use(route, reroute=True)
        except NavError as e:
            self.error = str(e)
            log.warning("reroute failed: %s", e)
        finally:
            self.rerouting = False

    def publish(self, now: float | None = None) -> None:
        if self.nav is None:
            self.sig.nav = None
            return
        self.sig.nav = {**self.nav.state(now or time.time()), "route_id": self.route_id,
                        "rerouting": self.rerouting, "error": self.error}

    def tick(self, now: float | None = None) -> bool:
        """Advance guidance with the latest fix. Returns True if a reroute is needed."""
        now = now or time.time()
        need = False
        fix = self._fix()
        if self.nav is not None and fix is not None:
            need = self.nav.update(fix, now) and not self.rerouting
        self.publish(now)
        return need

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if self.tick():
                asyncio.create_task(self._reroute())
            await asyncio.sleep(TICK_S)
