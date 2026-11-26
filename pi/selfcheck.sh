#!/usr/bin/env bash
# shellcheck disable=SC2015  # pass/warn always return 0, so "A && pass || fail" is safe here
# Bench self-check for the glass dash Pi. Installed as glassdash-selfcheck by pi/setup.sh.
#
#   sudo glassdash-selfcheck              # everything, read-only
#   sudo glassdash-selfcheck --loopback   # also test each CAN chip in its internal loopback mode
#
# --loopback stops dashd for a few seconds, puts each MCP2515 in loopback (frames never leave the
# chip, so it's safe even when plugged into the truck), sends one frame, checks it came back, then
# restores normal / listen-only mode and restarts dashd. It proves SPI, the crystal and the
# interrupt pin all work without any wiring.
#
# Prints PASS / WARN / FAIL per check; exit status is the number of FAILs.
set -uo pipefail

LOOPBACK=0
[ "${1:-}" = --loopback ] && LOOPBACK=1
HS_IF=hscan HS_BITRATE=500000 SW_IF=swcan SW_BITRATE=33333 SW_ENABLE=1
# shellcheck disable=SC1091
[ -f /etc/glassdash/can.env ] && . /etc/glassdash/can.env
# shellcheck disable=SC1091
[ -f /etc/glassdash/power.env ] && . /etc/glassdash/power.env
DATA=/var/lib/glassdash
PORT=8000
fails=0

pass() { printf '\033[32mPASS\033[0m %s\n' "$*"; }
warn() { printf '\033[33mWARN\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*"; fails=$((fails + 1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "run with sudo: sudo $0 $*" >&2; exit 1; }

# ---------------------------------------------------------------------------------------------
head_ "Board and power"
model=$(tr -d '\0' 2>/dev/null </proc/device-tree/model)
case "$model" in *"Pi 5"*) pass "$model" ;; *) warn "board: ${model:-unknown} (expected a Pi 5)" ;; esac
if command -v vcgencmd >/dev/null; then
  t=$(vcgencmd get_throttled | cut -d= -f2)
  # bit 0 = under-voltage now, bit 16 = under-voltage since boot
  if [ "$t" = 0x0 ]; then pass "power: no under-voltage or throttling since boot"
  elif (( t & 0x1 )); then fail "power: UNDER-VOLTAGE NOW ($t). Check the 5 V supply and cable"
  else warn "power: under-voltage or throttling happened since boot ($t)"; fi
  temp=$(vcgencmd measure_temp | cut -d= -f2)
  pass "CPU temperature $temp"
  [ "$(vcgencmd get_config usb_max_current_enable | cut -d= -f2)" = 1 ] \
    && pass "usb_max_current_enable=1" || fail "usb_max_current_enable not set (re-run setup, reboot)"
fi

# ---------------------------------------------------------------------------------------------
head_ "CAN HAT"
if dmesg 2>/dev/null | grep -qi 'mcp251x.*successfully initialized'; then
  pass "MCP2515 driver: $(dmesg | grep -ci 'mcp251x.*successfully initialized') chip(s) initialized"
else
  fail "no MCP2515 found in dmesg. HAT seated? Rebooted after setup? Try: dmesg | grep -i -e mcp251 -e spi1"
fi

check_if() {  # name bitrate want_listen_only
  local ifc=$1 rate=$2 lo=$3 info
  if [ ! -e "/sys/class/net/$ifc" ]; then
    fail "$ifc missing (ip -br link shows: $(ip -br link | awk '/can|hscan|swcan/{print $1}' | xargs))"
    return 1
  fi
  info=$(ip -details link show "$ifc")
  local up=0 got_rate state
  echo "$info" | grep -q '<.*UP.*>' && up=1
  got_rate=$(echo "$info" | sed -n 's/.*bitrate \([0-9]*\).*/\1/p' | head -1)
  state=$(echo "$info" | sed -n 's/.*can \(<[^>]*> \)\?state \([A-Z-]*\).*/\2/p' | head -1)
  [ $up = 1 ] && pass "$ifc is up" || fail "$ifc is down: journalctl -u glassdash-can"
  # The kernel picks the closest rate the 16 MHz crystal allows; 33333 can show as 33333 or 33334.
  if [ -n "$got_rate" ] && [ $(( got_rate > rate ? got_rate - rate : rate - got_rate )) -le $(( rate / 100 )) ]; then
    pass "$ifc bitrate $got_rate"
  else
    fail "$ifc bitrate ${got_rate:-unset}, want $rate"
  fi
  if [ "$lo" = 1 ]; then
    echo "$info" | grep -q 'LISTEN-ONLY' && pass "$ifc is listen-only (can't transmit on the body bus)" \
      || fail "$ifc is NOT listen-only. Don't plug into the truck: sudo ip link set $ifc down"
  else
    echo "$info" | grep -q 'LISTEN-ONLY' && fail "$ifc is listen-only but dashd needs to send OBD requests"
  fi
  case "$state" in
    ERROR-ACTIVE) pass "$ifc controller state $state" ;;
    "") warn "$ifc controller state unknown" ;;
    *) warn "$ifc controller state $state (normal on the bench with nothing ACKing; should be ERROR-ACTIVE on the truck)" ;;
  esac
}
check_if "$HS_IF" "$HS_BITRATE" 0
[ "$SW_ENABLE" = 1 ] && check_if "$SW_IF" "$SW_BITRATE" 1

# Which SPI chip select each name sits on, so you can confirm CAN_0 = hscan, CAN_1 = swcan.
for ifc in "$HS_IF" "$SW_IF"; do
  [ -e "/sys/class/net/$ifc/device" ] && echo "     $ifc -> $(basename "$(readlink -f "/sys/class/net/$ifc/device")")"
done

traffic() {  # count frames for 3 s
  timeout 3 candump -n 10000 "$1" 2>/dev/null | wc -l
}
for ifc in "$HS_IF" "$SW_IF"; do
  [ -e "/sys/class/net/$ifc" ] || continue
  n=$(traffic "$ifc")
  if [ "$n" -gt 0 ]; then pass "$ifc: $n frames in 3 s"
  else warn "$ifc: no frames in 3 s (expected on the bench; on the truck with the key on there should be traffic)"; fi
done

if [ $LOOPBACK = 1 ]; then
  head_ "CAN chip loopback (frames stay inside the MCP2515)"
  systemctl stop glassdash.service
  for ifc in "$HS_IF" "$SW_IF"; do
    [ -e "/sys/class/net/$ifc" ] || continue
    rate=$HS_BITRATE; [ "$ifc" = "$SW_IF" ] && rate=$SW_BITRATE
    ip link set "$ifc" down
    ip link set "$ifc" type can bitrate "$rate" loopback on listen-only off
    ip link set "$ifc" up
    before=$(cat "/sys/class/net/$ifc/statistics/rx_packets")
    cansend "$ifc" 7DF#0201050000000000
    sleep 0.5
    after=$(cat "/sys/class/net/$ifc/statistics/rx_packets")
    if [ "$after" -gt "$before" ]; then pass "$ifc loopback: frame sent and received (SPI + interrupt OK)"
    else fail "$ifc loopback: frame didn't come back. Check the overlay's interrupt GPIO and oscillator in config.txt"; fi
    ip link set "$ifc" down
    ip link set "$ifc" type can loopback off
  done
  /usr/local/bin/glassdash-can-up >/dev/null || fail "restoring CAN settings failed: run sudo glassdash-can-up"
  systemctl start glassdash.service
  pass "CAN settings restored, dashd restarted"
fi

# ---------------------------------------------------------------------------------------------
head_ "GPS"
if systemctl is-active -q gpsd.service; then pass "gpsd running"; else fail "gpsd not running: systemctl status gpsd"; fi
dev=$(sed -n 's/^DEVICES="\(.*\)"/\1/p' /etc/default/gpsd 2>/dev/null)
[ -n "$dev" ] && [ -e "$dev" ] && pass "GPS device $dev present" || fail "GPS device ${dev:-?} not found. Plugged in? ls /dev/ttyACM* /dev/serial/by-id"
if command -v gpspipe >/dev/null; then
  mode=$(timeout 12 gpspipe -w -n 40 2>/dev/null | jq -r 'select(.class=="TPV") | .mode // 0' 2>/dev/null | sort -n | tail -1)
  case "${mode:-}" in
    3) pass "GPS 3D fix" ;;
    2) warn "GPS 2D fix only (give it a clearer sky view)" ;;
    *) warn "no GPS fix yet. Put the dongle at a window; a cold start can take a few minutes" ;;
  esac
fi
if chronyc -n sources 2>/dev/null | grep -q 'GPS'; then
  sel=$(chronyc -n sources | awk '$2=="GPS"{print $1}')
  case "$sel" in
    '#*') pass "clock is following GPS" ;;
    *) pass "chrony sees GPS as a time source (status $sel; it takes over when there's no internet)" ;;
  esac
else
  warn "chrony has no GPS time source yet (needs a fix, then a minute)"
fi

# ---------------------------------------------------------------------------------------------
head_ "Dash server"
systemctl is-active -q glassdash.service && pass "glassdash service running" \
  || fail "glassdash service not running: journalctl -u glassdash -n 50"
scheme=http curlopt=(--noproxy "*")
if [ -f "$DATA/tls/cert.pem" ]; then scheme=https curlopt+=(--cacert "$DATA/tls/glassdash-ca.crt"); fi
snap=""
for _ in 1 2 3 4 5; do
  snap=$(curl -fsS "${curlopt[@]}" "$scheme://localhost:$PORT/api/snapshot" 2>/dev/null) && break
  sleep 2
done
if [ -n "$snap" ]; then
  pass "$scheme://localhost:$PORT answers"
  if echo "$snap" | jq -e '.gps.lat' >/dev/null 2>&1; then
    pass "dashd has a GPS position: $(echo "$snap" | jq -r '"\(.gps.lat), \(.gps.lon)"')"
  else
    warn "dashd has no GPS position yet"
  fi
  n=$(echo "$snap" | jq '[.signals[] | select(.stale == false)] | length' 2>/dev/null || echo 0)
  if [ "${n:-0}" -gt 0 ]; then pass "$n live OBD signals"
  else warn "no live OBD signals (expected on the bench; on the truck, key on, they should appear)"; fi
else
  fail "nothing answering on $scheme://localhost:$PORT: journalctl -u glassdash -n 50"
fi
curl -fsS -o /dev/null "${curlopt[@]}" "$scheme://localhost:$PORT/" && pass "UI is served" \
  || fail "UI not served (frontend/dist missing? re-run setup without --skip-build)"
host="$(hostname).local"
if [ "$scheme" = https ]; then
  curl -fsS -o /dev/null "${curlopt[@]}" --resolve "$host:$PORT:127.0.0.1" "https://$host:$PORT/api/snapshot" \
    && pass "certificate is valid for $host" || fail "certificate doesn't cover $host (re-run setup)"
  curl -fsS -o /dev/null "${curlopt[@]}" "https://localhost:$PORT/glassdash-ca.crt" \
    && pass "iPad CA download at https://$host:$PORT/glassdash-ca.crt" || warn "CA certificate download not served"
else
  warn "serving plain HTTP: the iPad mic won't work (re-run setup to make a certificate)"
fi
[ -f "$DATA/maps/bc.pmtiles" ] && pass "offline map $(du -h "$DATA/maps/bc.pmtiles" | cut -f1)" \
  || warn "no offline map ($DATA/maps/bc.pmtiles)"
[ -f "$DATA/nav/tiles.tar" ] && pass "offline routing data present" || warn "no offline routing data ($DATA/nav)"

# ---------------------------------------------------------------------------------------------
head_ "WiFi"
active=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: '$2=="wlan0"{print $1}')
if [ "$active" = glassdash-ap ]; then
  pass "hotspot is on: iPad joins $(sed -n 's/^SSID=//p' /etc/glassdash/hotspot.txt 2>/dev/null), then opens $scheme://10.42.0.1:$PORT"
  curl -fsS -o /dev/null "${curlopt[@]}" "$scheme://10.42.0.1:$PORT/api/snapshot" && pass "dash reachable on the hotspot address" \
    || fail "dash not reachable at 10.42.0.1"
elif [ -n "$active" ]; then
  ip4=$(ip -4 -br addr show wlan0 | awk '{print $3}' | cut -d/ -f1)
  pass "on WiFi '$active' ($ip4): iPad on the same WiFi opens $scheme://$host:$PORT"
  nmcli -t -f NAME connection show | grep -qx glassdash-ap && pass "hotspot configured (sudo glassdash-wifi hotspot to switch)" \
    || warn "no hotspot configured (re-run setup)"
else
  warn "wlan0 not connected to anything"
fi

# ---------------------------------------------------------------------------------------------
head_ "Key-off shutdown"
if systemctl is-enabled -q glassdash-powerwatch.service 2>/dev/null; then
  pass "enabled (powers off ${IDLE_S:-180} s after the truck bus goes quiet)"
else
  pass "disabled (right for the bench; enable at truck install)"
fi

echo
if [ $fails = 0 ]; then printf '\033[32mAll checks passed\033[0m (WARNs are expected on the bench)\n'
else printf '\033[31m%d check(s) failed\033[0m\n' "$fails"; fi
exit $fails
