#!/usr/bin/env bash
# Turn a fresh Raspberry Pi OS (64-bit, Trixie or Bookworm) install into a running glass dash.
#
#   sudo ./pi/setup.sh                          # first run, and again after every update
#   sudo ./pi/setup.sh --ssid GlassDash --psk 'at least 8 chars'   # choose the hotspot name/password
#   sudo ./pi/setup.sh --skip-build             # don't rebuild the UI (it's already in frontend/dist)
#
# Run it from the copy of the repo you rsynced to the Pi (see docs/pi-setup.md). It is safe to
# re-run: each step checks what's there first. What it does:
#   1. apt packages (python, node, can-utils, gpsd, chrony, mkcert)
#   2. /boot/firmware/config.txt: SPI + both MCP2515 channels of the Waveshare 2-CH CAN HAT+,
#      and full USB current (the 12 V buck has no USB-PD)
#   3. udev names pinned to the SPI chip select: hscan = HAT CAN_0 (OBD HS-CAN), swcan = HAT CAN_1 (GMLAN)
#   4. the app in /opt/glassdash (Python venv + built UI), data in /var/lib/glassdash
#   5. gpsd for the USB GPS, and chrony taking the time from GPS (no internet in the truck)
#   6. an HTTPS certificate for the Pi (the iPad mic needs HTTPS)
#   7. a WiFi hotspot for the iPad, created but NOT switched on (glassdash-wifi hotspot does that)
#   8. systemd: CAN bring-up + dashd at boot; the key-off auto shutdown is installed but disabled
set -euo pipefail

SSID="" PSK="" SKIP_BUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ssid) SSID="$2"; shift 2 ;;
    --psk) PSK="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

APP=/opt/glassdash
DATA=/var/lib/glassdash
ETC=/etc/glassdash
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REBOOT=0

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run with sudo: sudo $0 $*"
[ -f "$SRC/backend/pyproject.toml" ] || die "run this from the glassdash repo (no backend/ next to $0)"
[ "$(uname -m)" = aarch64 ] || warn "expected 64-bit Pi OS (aarch64), got $(uname -m)"
# shellcheck disable=SC1091
. /etc/os-release
case "${VERSION_CODENAME:-}" in
  trixie) ;;
  bookworm) warn "Bookworm has Python 3.11: the dash runs, but offline navigation needs Trixie (Python 3.12+)" ;;
  *) warn "untested OS: ${PRETTY_NAME:-unknown}" ;;
esac
grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null || warn "not a Pi 5: $(tr -d '\0' 2>/dev/null </proc/device-tree/model || echo unknown)"

# ---------------------------------------------------------------------------------------------
step "1/8 packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  python3 python3-venv python3-dev build-essential git rsync curl jq \
  nodejs npm can-utils iproute2 gpsd gpsd-clients chrony mkcert libnss3-tools avahi-daemon network-manager

# ---------------------------------------------------------------------------------------------
step "2/8 boot config (CAN HAT + USB power)"
CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || CFG=/boot/config.txt
[ -f "$CFG" ] || die "can't find config.txt in /boot/firmware or /boot"
BLOCK="# >>> glassdash (pi/setup.sh manages this block; edits inside it are replaced)
[all]
dtparam=spi=on
# Waveshare 2-CH CAN HAT+: two MCP2515s on SPI1, 16 MHz crystal. CAN_0 = CS1/INT GPIO22, CAN_1 = CS2/INT GPIO13
dtoverlay=spi1-3cs
dtoverlay=mcp2515,spi1-1,oscillator=16000000,interrupt=22
dtoverlay=mcp2515,spi1-2,oscillator=16000000,interrupt=13
# The 12 V to 5 V buck can supply 5 A but can't say so over USB-PD; without this the Pi caps USB at 600 mA.
usb_max_current_enable=1
# <<< glassdash"
current="$(sed -n '/^# >>> glassdash/,/^# <<< glassdash/p' "$CFG")"
if [ "$current" != "$BLOCK" ]; then
  cp -a "$CFG" "$CFG.glassdash-backup"
  sed -i '/^# >>> glassdash/,/^# <<< glassdash/d' "$CFG"
  printf '\n%s\n' "$BLOCK" >>"$CFG"
  echo "updated $CFG (backup: $CFG.glassdash-backup)"
  REBOOT=1
else
  echo "$CFG already set"
fi

# ---------------------------------------------------------------------------------------------
step "3/8 CAN interface names + settings"
install -d -m 755 "$ETC"
install -d /etc/udev/rules.d
cat >/etc/udev/rules.d/70-glassdash-can.rules <<'EOF'
# glassdash: name the HAT's CAN channels by SPI chip select so they can't swap between boots.
# A swap would put the body bus in normal mode at 500k and the OBD bus listen-only.
SUBSYSTEM=="net", ACTION=="add", KERNELS=="spi1.1", NAME="hscan"
SUBSYSTEM=="net", ACTION=="add", KERNELS=="spi1.2", NAME="swcan"
# Keep ModemManager (if installed) off the u-blox GPS.
SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", ENV{ID_MM_DEVICE_IGNORE}="1"
EOF
if [ ! -f "$ETC/can.env" ]; then
  cat >"$ETC/can.env" <<'EOF'
# Read by glassdash-can-up and glassdash-powerwatch.
HS_IF=hscan
HS_BITRATE=500000
SW_IF=swcan
SW_BITRATE=33333
# 0 leaves the single-wire body bus down (no doors/lights, nothing on OBD pin 1)
SW_ENABLE=1
EOF
fi
[ -f "$ETC/power.env" ] || printf '# Seconds of CAN silence before the Pi powers off (relay delay must be longer)\nIDLE_S=180\n' >"$ETC/power.env"
[ -f "$ETC/dashd.env" ] || printf '# Extra dashd arguments, e.g. DASHD_ARGS="--port 8443"\nDASHD_ARGS=\n' >"$ETC/dashd.env"
udevadm control --reload-rules

# ---------------------------------------------------------------------------------------------
step "4/8 app in $APP, data in $DATA"
id glassdash >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin glassdash
install -d -o glassdash -g glassdash -m 755 "$DATA"
if [ "$SRC" != "$APP" ]; then
  rsync -a --delete --exclude /.git/ --exclude /.venv/ --exclude /data/ --exclude /frontend/node_modules/ \
    --exclude /frontend/dist/ "$SRC/" "$APP/"
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "need Python 3.11+, have $PYV"
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
   && "$APP/.venv/bin/pip" install -q -e "$APP/backend[nav]"; then
  echo "dashd installed with offline navigation (Python $PYV)"
else
  warn "offline navigation engine didn't install (Python $PYV); installing the dash without it"
  "$APP/.venv/bin/pip" install -q -e "$APP/backend"
fi
"$APP/.venv/bin/python" -m dashd.vehicles >/dev/null || die "vehicle profiles don't validate"

if [ "$SKIP_BUILD" = 1 ] && [ -f "$SRC/frontend/dist/index.html" ]; then
  rsync -a --delete "$SRC/frontend/dist/" "$APP/frontend/dist/"
  echo "using the UI already built in $SRC/frontend/dist"
else
  echo "building the UI (a minute or two on a Pi 5)"
  (cd "$APP/frontend" && npm ci --no-fund --no-audit --loglevel=error && npm run build --silent)
fi

# Map, map fonts and nav data come from the Mac (they're big and not in git). Copy them in if the
# repo copy has them; never overwrite settings, saved places or trips already on the Pi.
if [ -d "$SRC/data" ]; then
  rsync -a --exclude tls/ --exclude 'trips*.db*' --exclude settings.json --exclude favorites.json \
    --exclude 'nav/*.osm.pbf' "$SRC/data/" "$DATA/"
  for f in settings.json favorites.json; do
    [ -f "$SRC/data/$f" ] && [ ! -f "$DATA/$f" ] && cp "$SRC/data/$f" "$DATA/$f"
  done
fi
chown -R glassdash:glassdash "$DATA"
[ -f "$DATA/maps/bc.pmtiles" ] || warn "no $DATA/maps/bc.pmtiles: the map will show only the route (see docs/pi-setup.md)"
[ -f "$DATA/nav/tiles.tar" ] || warn "no $DATA/nav/tiles.tar: Navigate will say routing data is missing"

# ---------------------------------------------------------------------------------------------
step "5/8 GPS (gpsd) + clock from GPS (chrony)"
# shellcheck disable=SC2012
GPSDEV=$(ls /dev/serial/by-id/*u-blox* 2>/dev/null | head -1 || true)
if [ -z "$GPSDEV" ]; then
  GPSDEV=/dev/ttyACM0
  [ -e "$GPSDEV" ] || warn "GPS not plugged in? using $GPSDEV; re-run setup after plugging it in to pin it by id"
fi
cat >/etc/default/gpsd <<EOF
# glassdash: VK-162 (u-blox 7) USB GPS. -n = keep reading without a client, so chrony gets the time.
START_DAEMON="true"
DEVICES="$GPSDEV"
GPSD_OPTIONS="-n"
USBAUTO="true"
EOF
install -d /etc/chrony/conf.d
cat >/etc/chrony/conf.d/glassdash-gps.conf <<'EOF'
# glassdash: the truck has no internet, so take time from the GPS via gpsd's shared memory.
# USB GPS without PPS is good to about 0.1 s, plenty for trip logs. Internet NTP still wins when present.
refclock SHM 0 refid GPS poll 4 precision 1e-1 offset 0.1 delay 0.5
# The Pi 5 RTC has no battery fitted, so the clock can be far off at boot: step it any time.
makestep 1 -1
EOF
systemctl enable gpsd.socket gpsd.service chrony.service >/dev/null 2>&1 || true
systemctl restart gpsd.service chrony.service || warn "gpsd/chrony didn't restart; the self-check will say more"

# ---------------------------------------------------------------------------------------------
step "6/8 HTTPS certificate (iPad mic needs HTTPS)"
TLS="$DATA/tls"
CA="$ETC/ca"   # outside DATA, which gets chowned to the glassdash user
install -d -o root -g glassdash -m 750 "$TLS"
install -d -o root -g root -m 700 "$CA"
HOST="$(hostname)"
NAMES="$HOST.local $HOST localhost 127.0.0.1 10.42.0.1 $(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v ':' | xargs || true)"
if [ ! -f "$TLS/cert.pem" ] || [ "$(cat "$TLS/names" 2>/dev/null)" != "$NAMES" ]; then
  # shellcheck disable=SC2086
  CAROOT="$CA" mkcert -cert-file "$TLS/cert.pem" -key-file "$TLS/key.pem" $NAMES
  cp "$CA/rootCA.pem" "$TLS/glassdash-ca.crt"   # public half only; rootCA-key.pem stays in $CA, root only
  echo "$NAMES" >"$TLS/names"
  echo "made a certificate for: $NAMES"
else
  echo "certificate already covers: $NAMES"
fi
chown root:glassdash "$TLS/cert.pem" "$TLS/key.pem" "$TLS/glassdash-ca.crt" "$TLS/names"
chmod 640 "$TLS/cert.pem" "$TLS/key.pem"
chmod 644 "$TLS/glassdash-ca.crt"

# ---------------------------------------------------------------------------------------------
step "7/8 WiFi hotspot for the iPad (created, not switched on)"
if command -v raspi-config >/dev/null && [ -z "$(raspi-config nonint get_wifi_country 2>/dev/null)" ]; then
  raspi-config nonint do_wifi_country CA && echo "WiFi country set to CA"
fi
if ! systemctl is-active -q NetworkManager; then
  warn "NetworkManager isn't running; skipping the hotspot"
else
  if [ -n "$SSID" ] || [ -n "$PSK" ] || ! nmcli -t -f NAME connection show | grep -qx glassdash-ap; then
    # Recreating it keeps the old name and password unless new ones were given, so the iPad stays paired.
    [ -n "$SSID" ] || SSID="$(sed -n 's/^SSID=//p' "$ETC/hotspot.txt" 2>/dev/null)"
    [ -n "$PSK" ] || PSK="$(sed -n 's/^PSK=//p' "$ETC/hotspot.txt" 2>/dev/null)"
    SSID="${SSID:-GlassDash}"
    [ -n "$PSK" ] || PSK="$(python3 -c 'import secrets; print("".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(12)))')"
    [ ${#PSK} -ge 8 ] || die "--psk must be at least 8 characters"
    nmcli connection delete glassdash-ap >/dev/null 2>&1 || true
    # ipv4.method shared: NetworkManager runs DHCP for the iPad, and NATs to eth0/USB tether if the
    # Pi has internet (needed for Satellite, Street photos and the Claude part of the voice assistant).
    nmcli connection add type wifi ifname wlan0 con-name glassdash-ap autoconnect no ssid "$SSID" \
      802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
      ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled \
      wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
      wifi-sec.psk "$PSK" >/dev/null
    printf 'SSID=%s\nPSK=%s\n' "$SSID" "$PSK" >"$ETC/hotspot.txt"
    chmod 600 "$ETC/hotspot.txt"
    echo "hotspot '$SSID' created (password in $ETC/hotspot.txt)"
  else
    echo "hotspot already set up: $(grep SSID= "$ETC/hotspot.txt" 2>/dev/null || echo glassdash-ap)"
  fi
fi

# ---------------------------------------------------------------------------------------------
step "8/8 services"
install -m 755 "$APP/pi/bin/glassdash-can-up" "$APP/pi/bin/glassdash-wifi" "$APP/pi/bin/glassdash-powerwatch" /usr/local/bin/
install -m 755 "$APP/pi/selfcheck.sh" /usr/local/bin/glassdash-selfcheck
install -m 644 "$APP"/pi/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable glassdash-can.service glassdash.service >/dev/null
# Key-off shutdown stays off on the bench; enable it at truck install (docs/pi-setup.md).
if [ "$REBOOT" = 0 ]; then
  systemctl restart glassdash-can.service || warn "CAN bring-up failed: journalctl -u glassdash-can"
  systemctl restart glassdash.service
fi

step "done"
SCHEME=https
echo "Dash:       $SCHEME://$HOST.local:8000   (on the hotspot: $SCHEME://10.42.0.1:8000)"
echo "iPad cert:  $SCHEME://$HOST.local:8000/glassdash-ca.crt  (steps in docs/pi-setup.md)"
echo "Hotspot:    sudo glassdash-wifi hotspot   (sudo glassdash-wifi home to go back)"
echo "Check:      sudo glassdash-selfcheck"
if [ "$REBOOT" = 1 ]; then
  printf '\n\033[1mReboot now so the CAN HAT is detected:\033[0m sudo reboot\n'
  echo "then: sudo glassdash-selfcheck"
fi
