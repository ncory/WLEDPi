# WLED-Pi

A [WLED](https://github.com/wled/WLED)-compatible LED controller that runs as a service on a Raspberry Pi 4 or 5
(Raspberry Pi OS Trixie). It drives WS2812/WS2811/SK6812 strips straight from a GPIO pin and serves WLED's own
web interface, JSON API and WebSocket, so the WLED UI, your existing `presets.json`, and most tools that talk to
WLED keep working.

What you get:

- WLED's web UI (version 0.15.3, downloaded from the WLED project at install time)
- 118 of WLED's 1D effects with the same effect IDs, all 71 palettes, custom palettes
- Segments with grouping, spacing, offset, mirror, reverse, freeze, per-segment brightness and individual-LED control
- Presets, playlists, quick-load labels, nightlight, transitions, live "Peek" view
- The WLED JSON API (`/json`, `/json/state`, `/json/si`, …), WebSocket (`/ws`) and the common `/win&…` HTTP API
- Same `presets.json` format: export from an ESP32 WLED and restore it here, or the reverse
- A settings page for LED outputs, pins, power limiting, startup behaviour and the web port

Not included (by design): Wi-Fi setup and access point, Home Assistant/MQTT, usermods, E1.31/DDP/UDP sync,
serial control, 2D matrix effects and audio-reactive effects.

> **Status:** tested in a virtual-LED mode (every effect, the API, presets, playlists, WebSocket and the
> installer logic). It has not yet been run against physical LEDs, so treat the first hardware run as a test.

## Install

On the Pi (Raspberry Pi OS Trixie, 64-bit recommended):

```bash
git clone <your repo url> wled-pi        # or copy and unzip the release
cd wled-pi
sudo ./install.sh --gpio 12 --count 60 --port 8080
```

The installer installs `python3-numpy` (plus `rpi_ws281x` on a Pi 4), copies the code to `/opt/wled-pi`, downloads
the WLED web UI, creates `/etc/wled-pi/config.json`, offers to add the needed line(s) to
`/boot/firmware/config.txt`, and enables the `wled-pi` systemd service. Reboot once if it changed the boot config.

Then open `http://<pi-name>.local:8080/`. Settings are at `/settings` (also via WLED's Config button).

Re-running `install.sh` upgrades the code and keeps settings and presets. Other options: `--yes`
(non-interactive), `--no-boot-config`, `--no-mdns`, and `--ui-file WLED-0.15.3.tar.gz` for offline installs
(download the tarball from WLED's GitHub releases on another computer).

## Wiring and pins

Both SPI buses are left free by default: SPI0 uses GPIO 7–11 and SPI1 uses GPIO 16–21, and the default data pin
is **GPIO 12** (physical pin 32).

**Raspberry Pi 5** uses the kernel's PIO-based `ws2812-pio` driver (no extra software). Any GPIO 0–27 works; GPIO 12
and 13 are good choices. Each output needs one overlay line, which the installer or settings page writes for you:

```
dtoverlay=ws2812-pio,gpio=12,num_leds=60,brightness=0
```

Up to 4 outputs. The LED count is fixed by the overlay, so changing it in settings means re-applying the boot
config and rebooting (the settings page shows a banner when this is needed). `brightness=0` puts the kernel driver
into pass-through mode so WLED-Pi's gamma and brightness are used; this needs a kernel from November 2025 or later
(any up-to-date Trixie install). On an older kernel, untick "PIO pass-through" for the output instead.

**Raspberry Pi 4** uses `rpi_ws281x` with the PWM peripheral: GPIO 12 or 13 (SPI-safe), or 18/19. PWM shares hardware
with the 3.5 mm analog audio, so `dtparam=audio=off` is added to the boot config (HDMI audio keeps working).
GPIO 21 (PCM) also works without touching audio, but it is an SPI1 pin. One output is recommended on a Pi 4; a second
output on the other PWM channel (13/19) is supported in settings but untested.

**Electrical:** the Pi's GPIO is 3.3 V; most WS2812 strips work with that over a short wire, but a 74AHCT125 (or
similar) level shifter makes it reliable. Power the LEDs from their own supply, connect its ground to the Pi's ground,
and never power more than a few LEDs from the Pi's 5 V pin. The power limiter in settings caps brightness to your
supply's rating.

## Using it alongside other software

- Runs on its own TCP port (default 8080; change with `--port` or in settings). Use port 80 only if nothing else
  needs it; the WLED mobile apps and Home Assistant generally expect port 80.
- Leaves SPI, I²C and UART pins alone, and only touches `config.txt` inside a marked block
  (`# >>> wled-pi (managed) >>>`), with a backup the first time.
- Runs as root (needed for the LED hardware) with a hardened systemd unit: read-only system, no home access,
  writes only to `/etc/wled-pi`, `/var/lib/wled-pi` and the boot config.
- Uses one CPU thread for rendering; typical effects take well under a millisecond per frame for 1000 LEDs.
  Idles at low CPU while the lights are off.

## Presets

`presets.json` uses WLED's exact format. To import from an ESP-based WLED: on the old device open
Config → Security & Updates → Backup presets; on WLED-Pi open Settings → Presets → Restore presets.
`/presets.json` can also be downloaded directly. Presets referencing effects WLED-Pi does not implement fall back
to Solid.

Custom palettes made with WLED's palette editor are stored as `palette0.json` … `palette9.json` in
`/var/lib/wled-pi`.

## Effects

Implemented (WLED IDs): 0–47, 49–52, 54–113, 115–117, plus Perlin Move (147), Flow Stripe (179) and Wavesins (184).
Most follow WLED's algorithms closely. Fairy (49), Fairytwinkle (51), Halloween Eyes (82), Dancing Shadows (112) and
TV Simulator (116) are close approximations rather than ports. Effects that are 2D-only or need audio are not listed in the UI.

## Files

| Path | Contents |
|---|---|
| `/etc/wled-pi/config.json` | service settings (see `config.example.json`) |
| `/var/lib/wled-pi/` | `presets.json`, custom palettes, last state |
| `/opt/wled-pi/` | code, Python environment, WLED web UI (`www/`) and palette data (`assets/`) |

Logs: `journalctl -u wled-pi -f`. Restart: `sudo systemctl restart wled-pi`.

## Uninstall

```bash
sudo /opt/wled-pi/uninstall.sh          # keeps settings and presets
sudo /opt/wled-pi/uninstall.sh --purge  # removes everything
```

The boot-config lines are removed too (reboot afterwards).

## Development

Runs on any Linux machine with Python 3.9+ and numpy, without LEDs:

```bash
python3 tools/fetch_wled_assets.py --www ./www --assets ./assets/palettes.json
python3 -m wledpi --config ./dev-config.json --virtual --port 8080 \
    --data-dir ./data --www-dir ./www --assets ./assets/palettes.json
```

Tests: `python3 tests/fx_stress.py` and `python3 tests/api_smoke.py`.

Layout: `wledpi/effects*.py` (effects), `segment.py`, `palettes.py`, `strip.py` (render loop), `drivers.py`
(Pi 4/Pi 5 output), `state.py` (WLED JSON semantics, presets, playlists), `server.py` (HTTP/WebSocket),
`httpapi.py` (`/win`), `bootcfg.py` (`config.txt` block).

## License and credits

EUPL-1.2, matching WLED. Effect algorithms are re-implementations of WLED's (by Aircoookie, Blaž Kristan,
Andrew Tuline and the WLED contributors), several of which build on FastLED examples by Mark Kriegsman.
WLED's web interface and palette data are not part of this repository; they are downloaded from the WLED project
when you install. WLED-Pi is not affiliated with the WLED project.

The full licence text is in [LICENSE](LICENSE). Dependencies are installed separately and keep their own licences:
`rpi_ws281x` (BSD-2-Clause, from PyPI on Raspberry Pi 4) and `numpy` (BSD-3-Clause, from Raspberry Pi OS packages).
