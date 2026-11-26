"""A scripted drive: speed profile plus derived RPM, temperatures, airflow and fuel.

Shared by the ECU simulator and the GPS simulator so the gauges and the map agree.
The drive loops DRIVE_LOOPS times, then the truck is parked with the engine off for
PARK_S seconds (the ECUs stop answering, like a real key-off), so trips start and end.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..fuel import fuel_lph
from .route import ACCEL, DECEL, Route

# (duration s, target speed km/h) segments, looped.
PROFILE = [(8, 0), (20, 100), (40, 100), (10, 60), (25, 60), (15, 0)]
DRIVE_LOOPS = 3
PARK_S = 40.0
GEARS = [(0, 4.03), (20, 2.36), (40, 1.53), (60, 1.15), (80, 0.85), (95, 0.67)]  # 6L80-ish
AXLE = 3.42
TIRE_CIRC_M = 2.55
TANK_L = 98.0  # 26 US gal, standard 2011 Silverado 1500 tank


@dataclass
class Vehicle:
    t: float = 0.0
    speed: float = 0.0  # km/h
    accel: float = 0.0  # km/h per s
    coolant: float = 20.0
    trans_temp: float = 15.0
    voltage: float = 14.2
    distance_m: float = 0.0
    fuel_l: float = TANK_L * 0.75
    engine_on: bool = True
    oil_temp: float = 20.0
    ambient: float = 8.0  # a BC November day
    # Fault scenario for testing alerts: "", "overheat", "lowvolt", "oilpressure", "towing" (hot trans, high load).
    scenario: str = ""
    # Body signals forced on/off from the UI (the "Map a signal" page can toggle them in the sim).
    body_overrides: dict = field(default_factory=dict)
    # Route mode: drive this route at its road speeds, park at the end, start over.
    # Without one (or without speeds) the scripted PROFILE loop runs.
    route: Route | None = None
    route_d: float = 0.0
    park_started: float | None = None
    loop: bool = True  # False for a navigated route: stay parked at the destination
    # Trouble codes per module, as the fake ECUs report them.
    dtcs: dict = field(default_factory=lambda: {
        "ECM": {"stored": ["P0171", "P0300", "P0420"], "pending": ["P0442"], "permanent": ["P0420"]},
        "TCM": {"stored": [], "pending": [], "permanent": []},
    })

    def clear_dtcs(self, module: str) -> None:
        """Mode 04: stored and pending go; permanent codes stay, like a real ECU."""
        self.dtcs[module]["stored"] = []
        self.dtcs[module]["pending"] = []

    @property
    def mil(self) -> bool:
        return bool(self.dtcs["ECM"]["stored"] or self.dtcs["TCM"]["stored"])

    @property
    def engine_load(self) -> float:
        tow = 30 if self.scenario == "towing" else 0
        return 0.0 if not self.engine_on else min(100.0, 18 + tow + 0.25 * self.speed + 8 * max(0.0, self.accel))

    @property
    def throttle(self) -> float:
        return 0.0 if not self.engine_on else min(100.0, 14 + 0.18 * self.speed + 9 * max(0.0, self.accel))

    @property
    def iat(self) -> float:
        return self.ambient + (12 if self.speed < 20 else 5)

    @property
    def stft(self) -> float:
        return 0.0 if not self.engine_on else 1.5 * math.sin(self.t * 1.3)

    @property
    def ltft(self) -> float:
        return 3.9 if self.engine_on else 0.0  # slightly lean, matches the P0171 the sim reports

    @property
    def timing(self) -> float:
        return 0.0 if not self.engine_on else 14 + 0.2 * self.speed - 2 * max(0.0, self.accel)

    @property
    def oil_pressure(self) -> float:
        """kPa"""
        if not self.engine_on:
            return 0.0
        p = min(380.0, 140 + 0.06 * self.rpm) * (1.25 - 0.25 * min(1.0, self.oil_temp / 100))
        return p * 0.25 if self.scenario == "oilpressure" else p

    @property
    def cycle_s(self) -> float:
        return sum(d for d, _ in PROFILE)

    def target_speed(self) -> float:
        t = self.t % (self.cycle_s * DRIVE_LOOPS + PARK_S)
        if t >= self.cycle_s * DRIVE_LOOPS:
            return 0.0
        t %= self.cycle_s
        start_speed = PROFILE[-1][1]
        for dur, target in PROFILE:
            if t < dur:
                return start_speed + (target - start_speed) * (t / dur)
            t -= dur
            start_speed = target
        return 0.0

    @property
    def route_mode(self) -> bool:
        return self.route is not None and self.route.speeds is not None

    def parked(self) -> bool:
        if self.route_mode:
            return self.park_started is not None
        return self.t % (self.cycle_s * DRIVE_LOOPS + PARK_S) >= self.cycle_s * DRIVE_LOOPS

    @property
    def parked_for(self) -> float:
        """Seconds since key-off, or -1 while driving."""
        if self.route_mode:
            return self.t - self.park_started if self.park_started is not None else -1.0
        t = self.t % (self.cycle_s * DRIVE_LOOPS + PARK_S)
        return t - self.cycle_s * DRIVE_LOOPS if self.parked() else -1.0

    def set_route(self, route: Route | None, keep_speed: bool = False, loop: bool = True) -> None:
        self.route = route
        self.route_d = 0.0
        self.park_started = None
        self.loop = loop
        if not keep_speed:
            self.speed = 0.0

    def _route_speed(self, dt: float) -> float:
        r = self.route
        if self.park_started is not None:
            if self.loop and self.t - self.park_started >= PARK_S:
                self.park_started = None  # key on, back to the start of the route
                self.route_d = 0.0
            return 0.0
        if self.route_d >= r.length - 3 and self.speed < 1:
            self.park_started = self.t
            return 0.0
        target = r.target_speed(self.route_d, self.speed)
        if target > self.speed:
            return min(target, self.speed + ACCEL * dt)
        return max(target, self.speed - DECEL * 1.5 * dt, 0.0)

    @property
    def rpm(self) -> float:
        if not self.engine_on:
            return 0.0
        if self.speed < 1:
            return 650.0
        ratio = [r for s, r in GEARS if self.speed >= s][-1]
        wheel_rpm = (self.speed / 3.6) / TIRE_CIRC_M * 60
        return max(650.0, wheel_rpm * AXLE * ratio)

    @property
    def maf(self) -> float:
        """Mass airflow, g/s. Tuned for about 1.5 L/h at idle and 12 L/100 km at 100 km/h."""
        if not self.engine_on:
            return 0.0
        return 4.5 + 0.30 * self.speed + 6.0 * max(0.0, self.accel)

    @property
    def fuel_level(self) -> float:
        return 100.0 * self.fuel_l / TANK_L

    def step(self, dt: float) -> None:
        self.t += dt
        prev = self.speed
        if self.route_mode:
            self.speed = self._route_speed(dt)
            self.route_d = min(self.route.length, self.route_d + self.speed / 3.6 * dt)
        else:
            self.speed = self.target_speed()
        self.engine_on = not self.parked()
        self.accel = (self.speed - prev) / dt if dt > 0 else 0.0
        self.distance_m += self.speed / 3.6 * dt
        self.fuel_l -= fuel_lph(self.maf) / 3600 * dt
        if self.fuel_l < TANK_L * 0.1:
            self.fuel_l = TANK_L * 0.95  # sim fill-up
        if self.engine_on:
            coolant_target = 92 + (min(30.0, self.t / 4) if self.scenario == "overheat" else 0)
            self.coolant += (coolant_target - self.coolant) * (1 - math.exp(-dt / 90))
            if self.scenario == "towing":  # a trailer up the Coquihalla: the trans heats in a couple of minutes
                self.trans_temp += (122 - self.trans_temp) * (1 - math.exp(-dt / 50))
            else:
                self.trans_temp += (82 - self.trans_temp) * (1 - math.exp(-dt / 240))
            self.oil_temp += (self.coolant + 8 - self.oil_temp) * (1 - math.exp(-dt / 300))
            self.voltage = (12.1 if self.scenario == "lowvolt" else 14.2) + 0.05 * math.sin(self.t / 3)
        else:
            self.coolant += (self.ambient - self.coolant) * (1 - math.exp(-dt / 1800))
            self.trans_temp += (self.ambient - self.trans_temp) * (1 - math.exp(-dt / 2400))
            self.oil_temp += (self.ambient - self.oil_temp) * (1 - math.exp(-dt / 2400))
            self.voltage = 12.6
