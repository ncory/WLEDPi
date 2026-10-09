# WLED-Pi — project context for Claude Code

WLED (ESP32 LED firmware, https://github.com/wled/WLED) rewritten as a Python service for Raspberry Pi 4/5 on
Raspberry Pi OS Trixie (Python 3.13). Built in a claude.ai chat; this file carries that context over.

## Owner's requirements (Nate)
- Drive WS2812-type LEDs from Pi GPIO. Effects, colours, presets, segments.
- Web UI as close to WLED's as possible; REST API; same presets.json format (import WLED presets as-is).
- Runs as a systemd service, configurable TCP port, must coexist with other software and **leave SPI pins free**.
- Out of scope: Wi-Fi/AP setup, Home Assistant/MQTT, usermods, serial/UDP/E1.31 control, 2D and audio effects.

## Architecture
- Dependencies: **numpy only** (+ `rpi_ws281x` from pip on Pi 4). HTTP/WebSocket server is stdlib asyncio —
  keep it that way (no aiohttp/flask).
- Render loop runs in its own thread (`strip.py`); API handlers share `strip.lock` (RLock). Driver `show()` runs
  outside the lock.
- `wledpi/effects.py`, `effects_more.py`: effects registered with `@fx(id, name, metadata)`; IDs match WLED 0.15
  (`MODE_COUNT = 187`). Unimplemented IDs report as "RSVD" so the UI hides them. Effects render into
  `seg.buf` (virtual pixels, float RGBW); `Segment.setp()` sets one pixel (not `set` — that's WLED's "set" field).
  Return an int > 24 to request a longer delay.
- `segment.py` geometry (grp/spc/of/mi/rev → `map`/`valid`), palette lookup `cfp()`, cross-fades.
- `palettes.py`: IDs 0–12 built in, 13+ from `assets/palettes.json` (extracted from WLED source at install),
  custom palettes `palette0..9.json` → IDs 255, 254, …
- `state.py` `Controller`: WLED JSON semantics (apply_state order, segments, presets, playlists, nightlight,
  state.json persistence) and state/info serialisation. `httpapi.py`: legacy `/win&…`. `presets.py`: storage.
- `server.py`: routes (`/json*`, `/ws`, `/presets.json`, `/upload`, `/edit`, `/win`, `/settings`, `/api/*`),
  static files from `www_dir`, gzip, ETags. `web/settings.html` is our own settings page (replaces WLED's).
- `drivers.py`: Pi 4 `rpi_ws281x` PWM GPIO 12/13/18/19 or PCM 21; Pi 5 kernel `ws2812-pio-rp1` via `/dev/ledsN`
  (4 bytes/LED R,G,B,W little-endian word, wire order G,R,B[,W]; single byte at offset 0 = brightness,
  0 = pass-through on kernels after Oct 2025); `virtual` for testing.
- `bootcfg.py`: managed block in `/boot/firmware/config.txt` (`# >>> wled-pi (managed) >>>`): Pi 5 overlay lines,
  Pi 4 `dtparam=audio=off`.
- `tools/fetch_wled_assets.py`: downloads WLED v0.15.3 source, installs `wled00/data/` as the UI (patches
  `location.hostname` → `location.host` for the WebSocket URL so non-80 ports work; in 0.15.3 that is one
  place, `makeWS()` in index.js — the liveview pages use `location.origin`) and extracts palettes.
  WLED code is never committed to this repo.
- WLED UI behaviours the backend has to honour (all found by running the real UI; covered in `api_smoke.py`):
  presets are applied as `{"pd": id, …preset content}` (sets `ps`); a playlist ends on `{"playlist":{}}`
  (the UI's Stop) or when an effect is set by hand (`fx` in `seg`, not from a preset or nested `win`);
  `{"rmcpal":true}` deletes the last custom palette; `bootps` in a `psave` sets `defaults.boot_preset`
  and is not stored in the preset; new segments are selected; `GET /edit?edit=/version-info.json`
  answers `neverAsk` so the UI never offers to report a Pi to WLED's usage statistics.

## Run and test (no hardware needed)
```bash
python3 tests/fx_stress.py     # every effect, 5 strip lengths/options; exits non-zero on failure
python3 tests/api_smoke.py     # starts the service on a free port, 53 API/WS/preset/playlist checks
python3 -m wledpi --virtual --port 8080 --data-dir ./data --www-dir ./www --assets ./assets/palettes.json
python3 tools/fetch_wled_assets.py --www ./www --assets ./assets/palettes.json   # needs internet
```
Both tests pass (last run 2026-10-09). Run them after any change.
On a machine without numpy (e.g. the Mac), prefix with `uv run --no-project --python 3.13 --with numpy`.
`.claude/launch.json` has a `wledpi-virtual` preview config that does the same on port 8080.

## Verified
- **Real WLED 0.15.3 UI against this backend** (2026-10-09, virtual driver, port 8080): page load, WebSocket,
  RSVD hidden (118 effects shown), 71 palette previews, effect/palette/colour/slider/segment controls,
  presets and playlists (save, apply, edit, delete, quick-load), Peek (iframe and standalone), Info, nodes,
  settings page, palette editor upload and removal. Known cosmetic gap: the UI's "File editor" button
  leads to `/edit`, which answers 404 by design.

## Not yet verified (highest priority first)
1. **Real hardware**: Pi 5 PIO output (byte order, pass-through brightness), Pi 4 rpi_ws281x (uses
   `strip._channel` + `ws2811_led_set` fast path, falls back to `setPixelColor`), installer end to end.
2. Pi 4 second output on PWM1 (two `PixelStrip` instances) — untested, may conflict.
3. Approximated effects (not ports): Fairy 49, Fairytwinkle 51, Halloween Eyes 82, Dancing Shadows 112,
   TV Simulator 116.

## Repo housekeeping
- LICENSE is a short EUPL-1.2 notice (matches WLED); replace with the full text so GitHub detects it.
- If committing from Windows: `git update-index --chmod=+x install.sh uninstall.sh tools/fetch_wled_assets.py`.
