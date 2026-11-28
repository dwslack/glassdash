# glassdash

A DIY glass dash for a 2011 Chevy Silverado: gauges from the truck's CAN bus (OBD Mode 01 + GM Mode 22) and an offline BC moving map, running on a Raspberry Pi 5. The UI is a web page, shown on the Pi touchscreen (Chromium kiosk) or an iPad in Safari over the Pi's WiFi.

> **Safety.** This is a hobby project, not automotive-grade software. Don't look at, tap or talk to the dash in ways that take your attention off the road: set routes and views while parked, and keep your eyes on traffic. Readings, alerts and PID formulas can be wrong (many signals are unverified guesses), so never rely on the dash instead of the truck's own gauges and warning lights. Clearing trouble codes erases freeze-frame data and resets emissions monitors; do it at your own risk, only when stopped, and only if you know what the code was. Wiring anything to the OBD port or the body bus is also at your own risk: a wrong connection or a terminator left on can upset the vehicle's modules. See [LICENSE](LICENSE): there is no warranty.

Licensed under the [MIT License](LICENSE). Want to add your vehicle? See [CONTRIBUTING.md](CONTRIBUTING.md).

## Status: M0, simulator skeleton

Runs entirely on a laptop, no hardware:

- `dashd` (Python) polls OBD PIDs over CAN and streams values over a WebSocket at 20 Hz.
- A fake ECM and TCM answer those requests on a python-can virtual bus, driven by a scripted drive (idle, accelerate, cruise, slow, stop).
- A fake GPS moves along Hwy 1 from Langley to Abbotsford at the simulated speed.
- The UI shows speed, RPM, coolant, trans temp and voltage gauges, plus the route and a moving truck marker on the map.
- Trips: every drive (engine on to engine off) is logged with distance, time, fuel used, L/100 km and its route, and there are resettable Trip A / Trip B counters. The dash shows instant and per-drive economy and range to empty. Every third loop the sim parks with the engine off for 40 s, so drives start and end.

- Diagnostics page: check-engine status, stored / pending / permanent trouble codes from the ECM and TCM with short descriptions, a Clear button, and every live signal with value, age, missed polls and a 60 s trend. The sim reports P0171, P0300, P0420 (stored), P0442 (pending) and P0420 (permanent).
- Alerts: banners for overheating coolant or trans, not charging / high voltage, low oil pressure, hot oil, and the check-engine light. Warnings can be tapped away for 10 minutes; critical ones stay. Test them in the sim with `just sim --scenario lowvolt` (or `overheat`, `oilpressure`).

- Settings page: night mode (auto at dusk for the truck's GPS position, or on/off) with adjustable dimming, a beep for critical alerts, tank size, fuel calibration factor, and every alert threshold. Saved by dashd in `data/settings.json`, so the Pi screen and the iPad share them.

### Truck view (doors, locks, lights)

The **Truck** page (and a small copy on the dash map) shows a top-down Silverado: doors and tailgate opening, lock state, headlights, high beams, blinking turn signals, brake and reverse lights, driver belt and parking brake. Alerts fire for a door or hood open while moving, an unbuckled driver belt, the parking brake on, headlights off at night, and a turn signal left on.

These signals aren't on the OBD diagnostic bus. They're on GM's **single-wire CAN** (GMLAN SWCAN, 33.333 kbit/s, OBD pin 1), which dashd only listens to and never transmits on.

- **Hardware:** channel 2 (CAN_1) of the Waveshare 2-CH CAN HAT+, named `swcan` on the Pi (channel 1, CAN_0, is `hscan`, the OBD bus). OBD pin 1 goes to CAN_H and pin 5 (signal ground) goes to CAN_L. An ordinary high-speed transceiver can *receive* single-wire CAN this way, because the dominant level (~4 V) reads as dominant across H/L.
  - **Turn off the 120 Ω terminator on channel 2 before plugging in.** On the single-wire bus it would be a 120 Ω load from pin 1 to ground, which can disrupt the truck's body modules (locks, lights, cluster). With the HAT unpowered, measure CAN_H to CAN_L on channel 2: it must read well above 120 Ω.
  - It has to be listen-only, so the HAT never ACKs or transmits. `pi/setup.sh` sets that up at every boot, and refuses to leave `swcan` up if listen-only didn't take. By hand it's:
    ```sh
    sudo ip link set swcan type can bitrate 33333 listen-only on
    sudo ip link set swcan up
    ```
- **Frame ids:** `vehicles/gm/gmt900.yaml` holds the frame/bit table, and **every entry is a placeholder**. GM doesn't publish them. Map them on the truck with **Settings › Vehicle › Map a signal** (record, toggle the door or light, it finds the bit and writes the YAML), then add `verified:` to each frame. Until then the Truck page says it's a sim demo.
- `dashd --source socketcan` listens on `can1` by default; the Pi service passes `--body-channel swcan`. Use `--body-channel ''` to turn it off. A missing interface just disables the Truck view.

### Maps

- **Street map (offline):** OpenStreetMap data for all of BC as one Protomaps PMTiles file, served by dashd. One-time setup on the Mac (and copy `data/` to the Pi later):

  ```sh
  brew install pmtiles
  just map-bc-size   # shows the download size first
  just map-bc        # writes data/maps/bc.pmtiles
  just map-assets    # label fonts + icons into data/map-assets
  ```

  Without the file the map shows just the route; without the fonts it draws without street names.
- **Satellite (online only):** the Satellite button switches to Esri World Imagery, keeping road and place labels on top. It needs internet on whatever is showing the page. In the truck that means the Pi needs a connection (phone hotspot/tether or an LTE dongle), because the iPad is on the Pi's WiFi. Esri's terms don't allow bulk offline caching, so there's no offline satellite. An optional ArcGIS key goes in Settings.

- **Street photos (online only):** the Street photos button shows the nearest Mapillary photo to the truck, or to wherever you tap the map. Needs a free Mapillary client token in Settings. Coverage outside towns is patchy, and there's no Google Street View (by choice).

### How trips work

- Distance is integrated from OBD vehicle speed; fuel from MAF assuming a stoichiometric mix (MAF / 14.7 / 745 g/L). That's accurate at cruise and reads somewhat low under heavy load or towing, when the engine runs rich. Calibrate against a few fill-ups once the truck is connected.
- A drive ends 20 s after the ECM stops answering (or RPM drops under 300). Drives that never moved are dropped.
- Everything is checkpointed to SQLite every 15 s, so a power cut at key-off loses at most 15 s. Real drives go to `data/trips.db`, sim drives to `data/trips-sim.db` (`--data` to move them).
- To calibrate fuel: after a fill-up, divide litres pumped by litres the dash logged since the last fill, and enter that as Fuel calibration in Settings.
- Range = fuel level x tank (98 L, set in Settings) / economy, using this drive once it has 5 km, else Trip A, else 14 L/100 km.

## On the Pi

[docs/pi-setup.md](docs/pi-setup.md) turns a fresh Raspberry Pi OS card into a running dash with one script (`pi/setup.sh`): CAN HAT, GPS, HTTPS, a WiFi hotspot for the iPad and autostart, plus a bench self-check.

## Run it

Needs `just`, Python 3.11+ and Node 18+. On a Mac (the built-in `python3` is 3.9, which is too old):

```sh
brew install just python@3.12 node
just setup    # creates .venv with Python 3.11+, installs backend and frontend deps
just sim      # terminal 1: backend on :8000
just dev      # terminal 2: UI on http://localhost:5173
```

`just setup` is safe to re-run; it replaces a `.venv` made with a too-old Python.

Or build the UI once and let `dashd` serve it: `just build && just sim`, then open http://localhost:8000. Open the same URL from an iPad on the same WiFi to check Safari.

`just mock` runs the UI with in-browser fake data and no backend.

### Navigation (turn by turn, offline)

Tap **Navigate** on the dash, type a town, business, street or address, and pick a result. The map turns heading-up with a next-turn banner (distance, street, highway exit number, lane arrows where OSM has them), a "then" hint for quick follow-up turns, and an ETA bar. Prompts are spoken by the iPad's built-in voice ("In 400 meters, turn right onto…"). Leave the route and it recalculates within a couple of seconds.

Everything runs on the Pi with no internet: routing is [Valhalla](https://github.com/valhalla/valhalla) run inside dashd (pip package `pyvalhalla`, no Docker), and search is a SQLite index of OpenStreetMap places. One-time setup, on the Mac:

```sh
just nav-data     # installs the engine, downloads BC (about 1.25 GB), builds data/nav (about 430 MB)
just sim          # restart dashd; Navigate now works, and the sim truck drives the route you pick
```

Copy `data/nav` (`tiles.tar` and `places.db`) to the Pi along with `data/maps`. The engine needs Python 3.12+ (on the Pi that means Raspberry Pi OS Trixie). Without the data, dashd runs normally and the Navigate panel says what's missing.

Saved places: with the Navigate panel open, tap the map to drop a pin and save it (e.g. Home), or tap ☆ on a search result. Saved places show first and match by name. They live in `data/favorites.json`.

Limits: OpenStreetMap has house numbers for only part of BC (none on many small-town streets), so a number it doesn't know falls back to the street, flagged in the results; save Home with a pin instead. No live traffic, so ETAs are typical speeds. Lane arrows only appear where OSM mappers tagged turn lanes. Search matches word starts ("tim hor chil"), not misspellings.

### Voice assistant

The round microphone button on the dash: tap, talk, and the answer is spoken back (and shown, with a box to type instead).

- **Works with no internet and no key** (handled on the dash computer): "navigate home", "take me to Tim Hortons", "stop navigation", "how far is it", "recenter", "zoom in", "show diagnostics", "what's the coolant temp", "how's the battery".
- **Everything else goes to Claude** with an Anthropic API key (Settings > Voice assistant): "is anything wrong with the truck?", "why is the trans running hot?", "what were my last three trips like?", "find a gas station on the way to Hope". Claude reads live gauges, trouble codes and trips, and can start or stop navigation and switch screens. It cannot clear trouble codes.
- **Cost:** with Claude Haiku 5.5 (the default) a typical question is well under a cent. Claude Opus 5.5 handles open-ended questions better but is slower and about 3 to 5 cents. Pick the model in Settings.
- **Speech-to-text is the iPad's own** (Safari speech recognition), which usually needs a connection. It also only works on HTTPS pages, so run once on the Mac:

```sh
brew install mkcert
just https-cert   # makes data/tls, prints the 4 steps to trust it on the iPad
```

Then use `https://<your-mac>.local:8000` (or `:5173` with `just dev`). On the Pi, `pi/setup.sh` makes its own certificate (see [docs/pi-setup.md](docs/pi-setup.md)).

On the Pi's own screen, voice works through a USB speakerphone instead: `sudo ./pi/setup.sh --pi-voice` makes the Pi listen (Whisper) and speak (Piper) itself, offline. See [docs/pi-setup.md](docs/pi-setup.md#5b-optional-voice-on-the-pi-through-a-usb-speakerphone).

### Sim routes

The sim can drive real roads. A route is road geometry from OSRM (OpenStreetMap routing) with a speed for each stretch. The truck accelerates and brakes like a loaded half-ton, slows for curves and towns, signals before sharp turns, parks with the engine off for 40 s at the destination, then starts over.

```sh
just route-presets                 # once, needs internet: Hwy 1 both ways, Sea to Sky, Fraser Canyon
just sim --route sea_to_sky        # or pick one in Settings > Simulator while the sim runs
just route chilliwack_hope "Chilliwack, BC" "Hope, BC"            # any start and destination
just route harrison_loop "Chilliwack, BC" "Chilliwack, BC" --via "Harrison Hot Springs, BC"
```

Routes are saved in `sim/tracks/NAME.json`, so after generating them the sim works offline. Places are looked up with Nominatim; `"49.157,-121.951"` works too. The old `langley_abbotsford` track has no speeds, so it runs the scripted speed loop.

## Dash views

The gauge panel has five views. Swipe left or right on it to change views, tap the dots, or say "show the tow view".
The last view is remembered on each screen.

- **Drive:** speed inside a tach arc, coolant, trans, fuel and range, battery, economy trend, this drive.
- **Tow:** trans temp first, then coolant, engine load bars, oil temp, economy and battery.
- **Economy:** a 5-minute L/100 km trend against the drive average, plus speed, fuel and throttle.
- **All gauges:** full screen, no map.
- **Big map:** a narrow column with speed, coolant and fuel.

Gauges stay grey while they read normal. Amber and red only appear when a value leaves its normal band; thresholds come
from Settings. Temperatures show "warming up" in blue when cold, plus this drive's min and max on the bar.

**Long-press any gauge** to pick what that spot shows. Choices are saved in `data/settings.json`, so the iPad and
the Pi agree, and "Reset view" puts a view back. When the transmission passes its warning temperature and trans temp
isn't on screen, the dash switches to the Tow view once. You can turn that off in Settings.
`just sim --scenario towing` heats the trans to try it.

## Vehicles

Everything specific to a vehicle lives in a profile under `vehicles/`: which OBD PIDs to read and
how to decode them, the body-bus frames for doors, locks and lights, and the tach and speed ranges.
Profiles inherit (`generic/obd2` → `gm/gmt900` → `gm/silverado-1500-2011-5.3`), so a new model on a
known platform is a few lines. Pick yours in **Settings › Vehicle**; it switches without a restart.
`just sim --vehicle ID` runs the sim with one.

**Map a signal** (Settings › Vehicle) finds a door or light on the body bus. Record, toggle the thing
a few times while tapping On and Off, and it shows the frame bit that followed it, plus the profile
lines to add. See [CONTRIBUTING.md](CONTRIBUTING.md) for adding a vehicle and marking what's verified.

## Layout

```
backend/dashd/      pids.py (PID table + decoding), obd.py (poller), bus.py (latest values),
                    trips.py (drive log + Trip A/B), fuel.py (MAF to fuel flow),
                    dtc.py + diag.py (trouble codes), isotp.py (multi-frame replies),
                    body.py (single-wire CAN body signals), gpsd.py (real GPS on the Pi),
                    server.py (HTTP + WebSocket), sim/ (vehicle model, fake ECUs, fake GPS)
backend/tests/      unit + simulator round-trip tests
vehicles/           vehicle profiles: PIDs, body-bus frames, gauge ranges; schema.json; see CONTRIBUTING.md
frontend/           Vite + TypeScript + MapLibre GL
sim/tracks/         GPS routes for the simulator
pi/                 setup.sh + selfcheck.sh for the Pi, systemd units, helper scripts (docs/pi-setup.md)
```

Adding a signal = add it to the vehicle profile (`vehicles/…yaml`). Gauges are widgets in `frontend/src/widgets.ts`.

## Safety

`dashd` sends diagnostic *read* requests (Modes 01, 03, 07, 0A, 22), plus one write: **Clear codes** (Mode 04) when you press it. Clearing erases stored codes and freeze frames and resets the readiness monitors; the API refuses unless OBD speed reads 0, and the UI asks first. Codes are read over ISO-TP so replies longer than one CAN frame work. A PID counts as unverified until its profile entry has `verified: {by, date}` from someone who checked it on a real vehicle. GM Mode 22 PIDs (trans temp 0x1940, oil temp 0x1154, oil pressure 0x115C) come from forum lists, not GM documentation; the oil pressure formula in particular is a guess, so don't trust its alert until it matches the cluster gauge.
