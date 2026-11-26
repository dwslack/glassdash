# Pi setup and bench bring-up

From a blank SD card to a running dash on the bench in about an hour, most of it waiting on downloads.
One script, `pi/setup.sh`, does the install; `glassdash-selfcheck` tells you what works.

> **Status: written before the hardware arrived.** The script has been dry-run on a Linux box with
> the hardware-facing commands stubbed out, but nothing here has run on a real Pi 5, CAN HAT or GPS yet.
> See [What's untested](#whats-untested) at the end.

## What you need

- Raspberry Pi 5 (4 GB is plenty) with the Active Cooler
- Waveshare **2-CH CAN HAT+** (two MCP2515 controllers, 16 MHz crystal, SN65HVD230 transceivers)
- VK-162 USB GPS (u-blox 7) or similar
- 64 GB high-endurance microSD card
- On the bench: a USB-C supply that can do 5 A (or your 12 V buck on a bench supply), and Ethernet if you can, so you can stay connected over SSH when the WiFi turns into a hotspot
- On the Mac: the repo, and ideally the offline map and nav data already built (`just map-bc`, `just map-assets`, `just nav-data`; see the README)

## 1. Flash the card (Mac, Raspberry Pi Imager)

Install Imager if you don't have it:

```sh
brew install --cask raspberry-pi-imager
```

In Imager:

1. **Device:** Raspberry Pi 5. **OS:** Raspberry Pi OS (other) > **Raspberry Pi OS Lite (64-bit)**. Use the Trixie-based release: offline navigation needs its Python 3.13. Bookworm works but without Navigate.
2. **Edit settings:**
   - Hostname: `glassdash`
   - Username `pi` and a password you'll remember (the commands below assume `pi`)
   - WiFi: your home network, country **CA**
   - Locale: your time zone
   - Services: **Enable SSH** (password, or paste your public key)
3. Write the card.

## 2. Hardware on the bench

Do this with everything unplugged.

1. Fit the Active Cooler, then the CAN HAT on the 40-pin header.
2. On the HAT, set the logic-level jumper to **3.3 V** (the Pi's GPIO are 3.3 V).
3. **Terminator jumpers (120 Ω):** leave them as they come for now; nothing is wired to the CAN terminals on the bench. Before the truck, both come **off** (step 8).
4. Plug the GPS into a blue USB 3 port, and put the dongle near a window.
5. Ethernet in if you have it, then power.

The first boot takes a minute or two (it expands the filesystem and reboots once).

## 3. Copy the repo and data to the Pi (Mac)

From your glassdash working copy on the Mac:

```sh
ssh-keygen -R glassdash.local 2>/dev/null; ssh pi@glassdash.local true   # accept the host key once
just pi-push
```

`just pi-push` rsyncs the code and `data/` (map, map fonts, nav data, your settings and saved places) to `~/glassdash` on the Pi. It skips the venv, `node_modules`, the 1.25 GB raw OSM download and the Mac's HTTPS certificate. The first copy of the map and nav data (about 1.5 GB) takes a few minutes over WiFi. Your Anthropic key travels inside `data/settings.json`, which is what you want for the voice assistant; it's never committed.

If you don't have `just` handy, it's the same as:

```sh
rsync -av --exclude .git/ --exclude .venv/ --exclude frontend/node_modules/ --exclude frontend/dist/ \
  --exclude 'data/nav/*.osm.pbf' --exclude data/tls/ --exclude 'data/trips*.db*' ./ pi@glassdash.local:glassdash/
```

## 4. Run the setup (on the Pi)

```sh
ssh pi@glassdash.local
cd ~/glassdash
sudo ./pi/setup.sh
```

Takes about 10 to 15 minutes on a Pi 5, mostly apt and npm. It is safe to re-run any time, and it's also how you update (step 9). To pick the hotspot name and password yourself instead of a generated one:

```sh
sudo ./pi/setup.sh --ssid GlassDash --psk 'something-8-or-more'
```

What it does, in order:

| Step | What | Where |
|---|---|---|
| 1 | apt packages: Python, Node, can-utils, gpsd, chrony, mkcert | |
| 2 | Turns on SPI and both HAT channels (`mcp2515` on SPI1 CS1/INT GPIO22 and CS2/INT GPIO13, 16 MHz), plus `usb_max_current_enable=1` because the buck has no USB-PD | a marked block at the end of `/boot/firmware/config.txt` (backup next to it) |
| 3 | Names the channels by SPI chip select so they can't swap between boots: **`hscan`** = HAT CAN_0 = OBD HS-CAN (pins 6/14), **`swcan`** = HAT CAN_1 = GMLAN single-wire (pin 1) | `/etc/udev/rules.d/70-glassdash-can.rules`, settings in `/etc/glassdash/can.env` |
| 4 | Copies the app to `/opt/glassdash`, makes a Python venv, builds the UI, copies map/nav data and settings | data in `/var/lib/glassdash` (owned by a `glassdash` system user) |
| 5 | gpsd on the USB GPS; chrony sets the clock from GPS (no internet in the truck, and the Pi's RTC has no battery) | `/etc/default/gpsd`, `/etc/chrony/conf.d/glassdash-gps.conf` |
| 6 | HTTPS certificate for `glassdash.local`, `10.42.0.1` and the Pi's current IPs, from a CA made on the Pi | `/var/lib/glassdash/tls`; the CA key stays in `/etc/glassdash/ca` (root only) |
| 7 | WiFi hotspot for the iPad, **created but not switched on** | NetworkManager connection `glassdash-ap`; name and password in `/etc/glassdash/hotspot.txt` |
| 8 | systemd: `glassdash-can` (brings up hscan at 500k and swcan at 33.3k **listen-only**), then `glassdash` (dashd as the `glassdash` user, restarts on crash). The key-off shutdown `glassdash-powerwatch` is installed but **off** | `/etc/systemd/system/glassdash*.service` |

The first run changes `config.txt`, so it ends by asking for a reboot:

```sh
sudo reboot
```

## 5. Bench self-check

```sh
ssh pi@glassdash.local
sudo glassdash-selfcheck --loopback
```

`--loopback` puts each MCP2515 into its internal loopback mode (frames never leave the chip), sends one frame, and checks it came back. That proves SPI, the chip select and the interrupt line for both channels with no wiring at all. It stops dashd for a few seconds and puts everything back afterwards. Without `--loopback` the check only reads.

What you should see on the bench, with nothing plugged into the CAN terminals:

| Check | Bench result |
|---|---|
| Power: no under-voltage, `usb_max_current_enable=1` | PASS (an under-voltage FAIL means the supply or cable can't keep up) |
| MCP2515 driver: 2 chips initialized | PASS |
| `hscan` up, 500000, not listen-only; `swcan` up, 33333, listen-only | PASS |
| `hscan -> spi1.1`, `swcan -> spi1.2` | printed; confirms which chip got which name |
| Controller state | `hscan` WARN (ERROR-PASSIVE or BUS-OFF: dashd is sending OBD requests and nothing ACKs them; normal on the bench) |
| Frames in 3 s | WARN, 0 frames on both (nothing connected) |
| Loopback on both channels | PASS |
| gpsd running, device present | PASS |
| GPS fix | PASS for a 3D fix at a window; a cold start can take a few minutes the first time |
| Clock from GPS | PASS once there's a fix (chrony prefers internet NTP while it has it) |
| Dash answers on https, UI served, certificate valid, CA download | PASS |
| dashd has a GPS position | PASS once there's a fix |
| Live OBD signals | WARN, none (no truck) |
| Offline map / routing data | PASS if you built and pushed them |
| WiFi | on your home WiFi, hotspot configured |

Useful while you poke at it:

```sh
journalctl -u glassdash -f            # dashd log
journalctl -u glassdash-can -b        # what the CAN bring-up did this boot
ip -details -statistics link show hscan
candump -tz hscan                     # watch frames (truck only)
cgps                                  # live GPS view (q to quit)
chronyc sources                       # is GPS a time source
```

## 6. iPad: trust the Pi's certificate (once)

Safari only lets the page use the microphone over HTTPS, so the iPad has to trust the Pi's own CA. Do this while the iPad and the Pi are both on your home WiFi:

1. Safari: open **https://glassdash.local:8000/glassdash-ca.crt**. Safari warns the site isn't trusted (that's what we're fixing): Show Details > visit this website, then **Allow** the profile download.
2. Settings > General > **VPN & Device Management**: tap the downloaded profile, **Install**.
3. Settings > General > About > **Certificate Trust Settings**: turn on full trust for the **mkcert** certificate.
4. Open **https://glassdash.local:8000**. No warning this time. Share > **Add to Home Screen** for a full-screen dash.

This CA is separate from the one on your Mac (`just https-cert`), so the iPad ends up with two mkcert profiles. That's fine. If you ever re-flash the Pi, it makes a new CA and you repeat these steps.

## 7. Switch to the hotspot (the truck setup)

In the truck there's no home WiFi, so the Pi becomes the access point and the iPad joins it. Read the password first, because switching drops a WiFi SSH session:

```sh
sudo cat /etc/glassdash/hotspot.txt
sudo glassdash-wifi hotspot
```

Then on the iPad: Settings > WiFi > join **GlassDash** (or your name) with that password, and open **https://glassdash.local:8000** (or **https://10.42.0.1:8000** if `.local` doesn't resolve). iOS will say "No Internet Connection" and stay connected anyway.

The hotspot stays on across reboots. To get back on your home WiFi for updates (over Ethernet, or plug a keyboard and screen in):

```sh
sudo glassdash-wifi home
```

Online extras (Satellite, Street photos, the Claude part of the voice assistant, and the iPad's own speech recognition) need the Pi to have internet. The hotspot shares any uplink the Pi has, so a phone tethered by USB or an LTE dongle makes them work in the truck.

## 8. Truck install (later)

Before plugging anything into the truck:

1. **Both terminator jumpers off.** The truck's HS-CAN already has its two 120 Ω terminators; a third is out of spec. On the single-wire bus a terminator is a 120 Ω load from pin 1 to ground and can upset the body modules. With the HAT unpowered, measure CAN_H to CAN_L on each channel: both must read well above 120 Ω.
2. Wiring from the OBD splitter pigtail:
   - CAN_0 (`hscan`): **H = pin 6, L = pin 14**
   - CAN_1 (`swcan`): **H = pin 1, L = pin 5** (signal ground)
   - Ground: pin 5 to the HAT's ground terminal (GND)
3. Run `sudo glassdash-selfcheck` with the key on: both buses should now show frames, `hscan` should be ERROR-ACTIVE, and the dash should show live gauges.
4. Turn on the key-off shutdown. It waits until it has seen the truck talk once since boot, then powers the Pi off after 180 s of silence (change in `/etc/glassdash/power.env`). Set the ACC delay-off relay to **at least 4 minutes** so it never cuts power before the Pi has shut down.

   ```sh
   sudo systemctl enable --now glassdash-powerwatch
   ```

## 9. Updating

On the Mac, after pulling or changing code:

```sh
just pi-push
ssh pi@glassdash.local 'cd ~/glassdash && sudo ./pi/setup.sh'
```

Settings, saved places and trips on the Pi are kept; the script never overwrites them. If the hotspot is on, the Mac needs to be on the Pi's hotspot or on Ethernet to reach it.

## Troubleshooting

- **No MCP2515 in `dmesg`:** HAT not seated, or you haven't rebooted since setup. `dmesg | grep -i -e mcp251 -e spi1` shows what the kernel saw. `grep -A12 '>>> glassdash' /boot/firmware/config.txt` shows the block setup wrote.
- **Loopback fails on one channel:** that channel's interrupt line. The HAT ships with INT_0 on GPIO22 and INT_1 on GPIO13; if your board's solder bridges differ, change the two `dtoverlay=mcp2515` lines in `pi/setup.sh` to match and re-run it.
- **Loopback passes but the truck shows only errors:** loopback doesn't test the bit rate against the outside world. A board with a 12 MHz or 8 MHz crystal instead of 16 MHz would be at the wrong speed: check the crystal marking and the `oscillator=` value.
- **`hscan` and `swcan` the wrong way round** (body frames on `hscan`): the udev rule maps `spi1.1` to `hscan`. Swap the two names in `/etc/udev/rules.d/70-glassdash-can.rules` and reboot.
- **No GPS fix:** `cgps` shows satellites seen. Indoors it can take a long time or never fix; try a window or outside. If gpsd sees no device, plug the dongle in and re-run setup so it pins it by id.
- **Pi rebooting or the under-voltage warning:** the supply or cable can't do 5 A. The Pi 5 also caps USB current without `usb_max_current_enable=1`.
- **Re-run is always safe:** `sudo ./pi/setup.sh` from `~/glassdash`.

## What's untested

Written and dry-run before the parts arrived. Tested so far: shellcheck on every script; a full run of `setup.sh` on Linux (twice, to check it's re-runnable) with apt, systemd, NetworkManager, udev and mkcert stubbed, then dashd running as the `glassdash` user with the generated HTTPS certificate and `glassdash-selfcheck` passing its dash-server checks against it; the self-check's CAN parsing against sample `ip -details` output; the new gpsd reader against a fake gpsd; and the poller surviving failed CAN sends.

Not yet run on real hardware:

- The `dtoverlay` lines and interrupt pins for the 2-CH CAN HAT+ (taken from Waveshare's wiki), and which SPI device each channel lands on.
- 33.333 kbit/s listen-only on the MCP2515, and the loopback test.
- gpsd with the VK-162, and chrony taking time from it.
- The NetworkManager hotspot, `.local` name resolution from the iPad over it, and the iPad trusting the Pi's CA.
- `usb_max_current_enable` with the buck, and the key-off shutdown with the delay relay.
- Building the UI on the Pi, and whether `pyvalhalla` installs on Pi OS (if it doesn't, setup carries on without Navigate and says so).
