# Contributing

The most useful thing you can add is **your vehicle**: a profile that tells the dash which OBD
PIDs your car answers, how to read its doors and lights, and where its tach redline is.
You don't need to write any code for that.

## Add or improve a vehicle profile

Profiles live in `vehicles/`, one YAML file per vehicle, checked against `vehicles/schema.json`.
The id is the path without `.yaml`: `vehicles/gm/silverado-1500-2011-5.3.yaml` is `gm/silverado-1500-2011-5.3`.

### 1. Start from the closest existing profile

Profiles inherit with `extends`. Put what a whole platform shares in a base and keep the
model file small:

```
generic/obd2                  standard Mode 01 PIDs every OBD-II car should answer
└── gm/gmt900                 GM Mode 22 PIDs + GMLAN body bus (abstract: a base, not picked directly)
    └── gm/silverado-1500-2011-5.3   names, years, engine, tach ranges
```

- **Same platform, different model or engine** (a 2012 Sierra 6.2, a Tahoe): make a new file that
  `extends: gm/gmt900` and set `name`, `vehicle` and `gauges`.
- **A new make or platform**: make a base for the platform (e.g. `ford/p552.yaml` with `abstract: true`,
  `extends: generic/obd2`), then a model file that extends it.
- **Any OBD-II car**: pick "Any OBD-II car" in Settings. It reads the standard PIDs with no profile work.

Rules for merging: a child's PID or frame **replaces** the parent's of the same name, whole.
`name: null` removes an inherited one. `vehicle` and `gauges` merge key by key.

Folder and file names: lower case, `make/model-years-engine.yaml`.

### 2. Signals

```yaml
pids:
  trans_temp: {mode: 0x22, pid: 0x1940, ecu: 0x7E2, formula: A-40, unit: C, hz: 1}
```

- Use the **standard names** so gauges pick them up: `rpm speed coolant voltage maf fuel_level engine_load
  throttle intake_temp ambient_temp baro timing trans_temp oil_temp oil_pressure`. New names are fine
  too; they show on the Diagnostics page.
- `mode` is `0x01` (standard) or `0x22` (manufacturer, 2-byte id). `ecu` is the request id; the reply
  comes from `ecu + 8`.
- `formula` uses the reply bytes `A B C D` with `+ - * /` and parentheses. Nothing else is allowed.
- `hz` is how often to ask. Keep the total under about 40 per second.

### 3. Doors, locks and lights (body bus)

These frames aren't published by most makers, so they have to be found on a vehicle. The dash
has a tool for it:

1. Connect the body bus (on GM GMT900 that's channel 2 of the CAN HAT on OBD pin 1; see README).
2. **Settings › Vehicle › Map a signal**. Type the signal name (e.g. `door_driver`), make sure
   it's off, and tap Start.
3. Open and close the door three or more times, tapping **It's ON now** / **It's OFF now** each time,
   with a couple of seconds in between.
4. Tap **Find it**. A match of 97% or better with flips equal to your on/off changes is a solid
   find. A blinker or a counter shows up as a lower match or far more flips.
5. Copy the profile lines into your profile.

```yaml
body:
  bus: {type: can, bitrate: 500000}        # or gmlan-swcan, 33333 on GM single-wire
  frames:
    doors:
      id: 0x3C1                            # plain CAN: the exact id. GMLAN: `arb:` (13-bit) instead
      signals:
        door_driver: [2, 6]                # [byte, bit], bit 0 = least significant
        door_passenger: [2, 7, inverted]   # inverted: a 0 bit means open/on
```

Body signal names the Truck page knows: `door_driver door_passenger door_rear_left door_rear_right
tailgate hood locked parking_lights low_beam high_beam fog turn_left turn_right brake reverse
belt_driver belt_passenger passenger_seated parking_brake`.

dashd only ever **listens** on the body bus. Never add anything that transmits on it.

### 4. Mark what you checked

Anything not checked on a real vehicle stays unverified, and the dash says so. When you've
checked a PID against the factory gauge or a scan tool, or mapped a frame with the tool above, add:

```yaml
  verified: {by: your-github-name, date: 2026-11-26, vehicle: 2012 Sierra 1500 5.3, notes: matched cluster gauge}
```

Only mark what **you** checked, on the vehicle you name. A wrong "verified" is worse than none:
people trust alerts built on it.

### 5. Check and send

```
just vehicles-check     # every profile loads and passes the schema
just test               # the full test suite
just sim --vehicle your/profile-id   # see it on the dash with simulated data
```

Then open a pull request. The template asks what you checked and how.

## Code changes

- Backend: Python 3.12, FastAPI, python-can (`backend/`). Frontend: TypeScript + Vite, no framework (`frontend/`).
- `just setup` once, then `just sim` + `just dev`. `just test` before a pull request.
- Keep the dash usable at arm's length while driving: big targets, few words, colour only when something is wrong.
- Never commit keys or tokens. They belong in `data/settings.json`, which git ignores.

## Privacy and safety

- Don't commit anything that identifies you or your vehicle: VINs, plate numbers, your home or work
  location, saved places (`data/favorites.json`), trip logs, or sim routes that start at your house.
  CAN captures (`candump` logs) can contain the VIN, so trim them to the frames you need before attaching them.
- Use public places for test data and examples.
- dashd must never transmit on the body bus, and nothing should clear codes or send Mode 04 while the vehicle is moving.
  Changes that touch either get extra review.
