# Glass dash dev commands. Needs: just, Python 3.11+, Node 18+.
# On a Mac: brew install just python@3.12 node

venv := justfile_directory() / ".venv"
py := venv / "bin" / "python"

# Create .venv with Python 3.11+, install backend + frontend deps
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    found=""
    for c in python3.13 python3.12 python3.11 python3; do
      if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        found="$(command -v "$c")"; break
      fi
    done
    if [ -z "$found" ]; then
      echo "error: need Python 3.11 or newer; found: $(python3 --version 2>&1 || echo none)" >&2
      echo "       on a Mac: brew install python@3.12, then re-run: just setup" >&2
      exit 1
    fi
    if [ -x "{{py}}" ] && ! "{{py}}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      echo "removing old .venv (Python too old)"; rm -rf "{{venv}}"
    fi
    [ -x "{{py}}" ] || { echo "creating .venv with $("$found" --version)"; "$found" -m venv "{{venv}}"; }
    "{{py}}" -m pip install -q --upgrade pip
    "{{py}}" -m pip install -q -e './backend[dev]'
    if ! command -v npm >/dev/null 2>&1; then
      echo "error: npm not found; on a Mac: brew install node, then re-run: just setup" >&2
      exit 1
    fi
    (cd frontend && npm install --no-fund --no-audit)
    echo "setup done. next: 'just sim' in one terminal, 'just dev' in another"

[private]
check-venv:
    @test -x "{{py}}" || { echo "error: no .venv yet; run: just setup" >&2; exit 1; }

# Backend with fake ECUs + fake GPS on :8000. Extra args pass through, e.g. just sim --scenario overheat
# Pick a route: just sim --route sea_to_sky (list: ls sim/tracks)
sim *args: check-venv
    cd backend && "{{py}}" -m dashd --source sim {{args}}

# Frontend with hot reload on :5173 (proxies /api and /ws to :8000)
dev:
    cd frontend && npm run dev

# UI only, fake data generated in the browser
mock:
    @echo "open http://localhost:5173/?mock=1"
    cd frontend && npm run dev

# Backend tests + TypeScript check
test: check-venv
    cd backend && "{{py}}" -m pytest -q
    cd frontend && npx tsc --noEmit

# Check every vehicle profile in vehicles/ (CI runs this on pull requests)
vehicles-check: check-venv
    cd backend && "{{py}}" -m dashd.vehicles

# Build the UI so dashd serves it on :8000
build:
    cd frontend && npm run build

# BC bounding box (lon/lat) for the offline map
bc_bbox := "-139.1,48.2,-114.0,60.0"

[private]
check-pmtiles:
    @command -v pmtiles >/dev/null || { echo "error: needs the pmtiles CLI; on a Mac: brew install pmtiles" >&2; exit 1; }

# Show how big the offline BC map download would be, without downloading
map-bc-size build="": check-pmtiles
    #!/usr/bin/env bash
    set -euo pipefail
    b="{{build}}"; [ -n "$b" ] || b=$(python3 -c 'import datetime as d; print((d.date.today()-d.timedelta(days=2)).strftime("%Y%m%d"))')
    pmtiles extract "https://build.protomaps.com/$b.pmtiles" /tmp/bc-dryrun.pmtiles --bbox={{bc_bbox}} --dry-run

# Download the offline BC street map (OpenStreetMap via Protomaps) to data/maps/bc.pmtiles
map-bc build="": check-pmtiles
    #!/usr/bin/env bash
    set -euo pipefail
    b="{{build}}"; [ -n "$b" ] || b=$(python3 -c 'import datetime as d; print((d.date.today()-d.timedelta(days=2)).strftime("%Y%m%d"))')
    mkdir -p data/maps
    echo "extracting BC from Protomaps build $b"
    pmtiles extract "https://build.protomaps.com/$b.pmtiles" data/maps/bc.pmtiles.part --bbox={{bc_bbox}}
    mv data/maps/bc.pmtiles.part data/maps/bc.pmtiles
    ls -lh data/maps/bc.pmtiles

# Download map label fonts and icons to data/map-assets (needed for street names)
map-assets:
    #!/usr/bin/env bash
    set -euo pipefail
    tmp=$(mktemp -d)
    curl -fL https://github.com/protomaps/basemaps-assets/archive/refs/heads/main.zip -o "$tmp/a.zip"
    unzip -q "$tmp/a.zip" -d "$tmp"
    mkdir -p data/map-assets
    rm -rf data/map-assets/fonts data/map-assets/sprites
    cp -R "$tmp/basemaps-assets-main/fonts" "$tmp/basemaps-assets-main/sprites" data/map-assets/
    rm -rf "$tmp"
    du -sh data/map-assets

# Make a sim route along real roads, e.g. just route sea_to_sky "Horseshoe Bay, BC" "Whistler, BC" (needs internet)
[positional-arguments]
route name start end *via: check-venv
    "{{py}}" tools/make_route.py "$@"

# Fetch the preset BC sim routes (needs internet, about 10 s)
route-presets: check-venv
    "{{py}}" tools/make_route.py hwy1_langley_abbotsford "49.1427,-122.6713" "49.0545,-122.2580"
    "{{py}}" tools/make_route.py hwy1_abbotsford_vancouver "Abbotsford, BC" "Canada Place, Vancouver, BC"
    "{{py}}" tools/make_route.py sea_to_sky "Horseshoe Bay, West Vancouver, BC" "Whistler Village, BC"
    "{{py}}" tools/make_route.py fraser_canyon "Hope, BC" "Lytton, BC"

# Install the offline navigation engine (Valhalla routing + OSM tools; Python 3.12+, about 60 MB)
nav-setup: check-venv
    "{{py}}" -m pip install -q -e './backend[dev,nav]'
    @echo "nav engine installed. next: just nav-data"

# Download BC from OpenStreetMap (about 1.25 GB) and build offline routing + place search into data/nav.
# Takes roughly 15-40 min on a Mac and a few GB of RAM; re-run to refresh the map data.
nav-data: nav-setup
    mkdir -p data/nav
    curl -L --fail -C - -o data/nav/bc.osm.pbf https://download.geofabrik.de/north-america/canada/british-columbia-latest.osm.pbf
    cd backend && "{{py}}" -m dashd.nav.build ../data/nav/bc.osm.pbf ../data/nav
    du -sh data/nav/*

# HTTPS for the dash: the iPad only lets a page use the microphone over HTTPS.
# Needs mkcert (brew install mkcert). Re-run if the Mac's name changes. Then restart dashd.
https-cert:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v mkcert >/dev/null || { echo "error: install mkcert first: brew install mkcert" >&2; exit 1; }
    mkcert -install
    mkdir -p data/tls
    host="$(scutil --get LocalHostName 2>/dev/null || hostname -s).local"
    ips="$(ipconfig getifaddr en0 2>/dev/null || true) $(ipconfig getifaddr en1 2>/dev/null || true)"
    mkcert -cert-file data/tls/cert.pem -key-file data/tls/key.pem "$host" localhost 127.0.0.1 $ips
    cp "$(mkcert -CAROOT)/rootCA.pem" data/tls/glassdash-ca.crt   # the public CA cert only, never rootCA-key.pem
    echo
    echo "Done. Restart dashd, then on the iPad:"
    echo "  1. Safari: open https://$host:8000/glassdash-ca.crt and allow the download"
    echo "  2. Settings > General > VPN & Device Management: install the profile"
    echo "  3. Settings > General > About > Certificate Trust Settings: turn on full trust for mkcert"
    echo "  4. Open the dash at https://$host:8000 (or :5173 with just dev)"

# Copy the repo plus the map/nav data to the Pi (then on the Pi: cd glassdash && sudo ./pi/setup.sh).
# Skips the venv, node_modules, the 1.25 GB OSM download and the Mac's HTTPS certificate.
pi-push host="pi@glassdash.local":
    rsync -av --exclude .git/ --exclude .venv/ --exclude frontend/node_modules/ --exclude frontend/dist/ \
      --exclude 'data/nav/*.osm.pbf' --exclude data/tls/ --exclude 'data/trips*.db*' ./ "{{host}}:glassdash/"
