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
- **Tracks WLED 16.0.1** (moved from 0.15.3 on 2026-11-09). Metadata strings, defaults and algorithms of the
  implemented effects follow 16.0.1's FX.cpp; when WLED changes, diff its FX.cpp/json.cpp/data/ against the
  version above rather than assuming (several 0.15-era ports turned out to be from older WLED code).
- `wledpi/effects.py`, `effects_more.py`: effects registered with `@fx(id, name, metadata)`; IDs match WLED 16
  (`MODE_COUNT = 220`; 77 is Copy Segment, Meteor Smooth is Meteor's "Smooth" option). Unimplemented IDs report
  as "RSVD" so the UI hides them. Effects render into `seg.buf` (virtual pixels, float RGBW); `Segment.setp()`
  sets one pixel (not `set` — that's WLED's "set" field). Return an int > 24 to request a longer delay.
  Where WLED 16 uses `gamma8inv()`/`gamma32inv()` to keep a pre-16 look, use `s.strip.gamma_inv()`.
- `segment.py` geometry (grp/spc/of/mi/rev → `map`/`valid`), palette lookup `cfp()` (palette 0 = the effect's
  `pal=` default, else Party), transitions: `output()` returns (pixels, opacity) and applies WLED 16 transition
  styles (`bs`: fade, fairy dust, swipe, push, inside-out/outside-in; 2D-only styles switch at once).
- `strip.py` composites segments like WLED 16 `blendSegment()`: blend mode `bm` (17 modes) with the layers below,
  opacity gamma-inverted, spacing pixels show lower layers. **Gamma (default 2.2) is applied to the whole frame on
  output** — WLED 16's model; WLED 0.15 instead put gamma into palette data and colour inputs.
- `palettes.py`: IDs 0–12 built in (Party/Rainbow/Rainbow Bands inverse-gamma 2.2 like WLED 16's `_gc22`),
  13–71 from `assets/palettes.json` (extracted from WLED source at install; gamma-free in WLED 16). Custom
  palettes `palette0..128.json` → IDs 200, 199, …, a missing file leaves a grey placeholder (IDs 201–255 are
  WLED's usermod palettes; unknown IDs fall back to 0).
- `state.py` `Controller`: WLED JSON semantics (apply_state order, segments, presets, playlists, nightlight,
  state.json persistence) and state/info serialisation. `httpapi.py`: legacy `/win&…`. `presets.py`: storage.
- `server.py`: routes (`/json*`, `/ws`, `/presets.json`, `/upload`, `/edit`, `/win`, `/settings`, `/api/*`),
  static files from `www_dir`, gzip, ETags. `web/settings.html` is our own settings page (replaces WLED's).
- `drivers.py`: Pi 4 `rpi_ws281x` PWM GPIO 12/13/18/19 or PCM 21; Pi 5 kernel `ws2812-pio-rp1` via `/dev/ledsN`
  (4 bytes/LED R,G,B,W little-endian word, wire order G,R,B[,W]; single byte at offset 0 = brightness,
  0 = pass-through on kernels after Oct 2025); `virtual` for testing.
- `bootcfg.py`: managed block in `/boot/firmware/config.txt` (`# >>> wled-pi (managed) >>>`): Pi 5 overlay lines,
  Pi 4 `dtparam=audio=off`.
- `tools/fetch_wled_assets.py`: downloads WLED v16.0.1 source, installs `wled00/data/` as the UI (patches
  `location.hostname` → `location.host` for the WebSocket URL so non-80 ports work; in 16.0.1 that is index.js,
  common.js and cpal/cpal.htm — the liveview pages use `location.origin`) and extracts palettes (palettes.cpp,
  names from FX_fcn.cpp). WLED code is never committed to this repo. Older tags are not supported (palette gamma).
- WLED UI behaviours the backend has to honour (all found by running the real UI; covered in `api_smoke.py`):
  presets are applied as `{"pd": id, …preset content}` (sets `ps`); a playlist ends on `{"playlist":{}}`
  (the UI's Stop) or when an effect is set by hand (`fx` in `seg`, not from a preset or nested `win`);
  `{"rmcpal": n}` deletes `palette<n>.json` (`true` = the last one, 0.15 UI); `bootps` in a `psave` sets
  `defaults.boot_preset` and is not stored in the preset; new segments are selected (orange when made by hand,
  black from a preset); every segment reports `lc` (without it the v16 UI hides the colour controls); `bm`, `bs`,
  `rSeg`, `"r"` (random colour) in `col`; the version-info lookup (`/edit?func=edit&path=/version-info.json`, and
  0.15's `/edit?edit=…`) answers `neverAsk` so the UI never offers to report a Pi to WLED's usage statistics.
  `/edit` is WLED 16's file editor (list/edit/download/delete on the data dir; only presets.json, skin.css and
  custom palettes may be replaced or deleted). Tool files (`/cpal.htm`, `/pixelforge.htm`, `/omggif.js`, …) are
  served from their `www/<tool>/` folders, as WLED's build does.

## Run and test (no hardware needed)
```bash
python3 tests/fx_stress.py     # every effect, 5 strip lengths/options; exits non-zero on failure
python3 tests/api_smoke.py     # starts the service on a free port, 68 API/WS/preset/playlist/palette checks
python3 -m wledpi --virtual --port 8080 --data-dir ./data --www-dir ./www --assets ./assets/palettes.json
python3 tools/fetch_wled_assets.py --www ./www --assets ./assets/palettes.json   # needs internet
```
Both tests pass (last run 2026-11-09). Run them after any change.
On a machine without numpy (e.g. the Mac), prefix with `uv run --no-project --python 3.13 --with numpy`.
`.claude/launch.json` has a `wledpi-virtual` preview config that does the same on port 8080.

## Upstream sources (checked 2026-11-09)
- WLED: v16.0.1 (2026-07-07) is the latest release; v0.15.5 is a tag without a release. WLED's bundled
  CHANGELOG.md is stale — read the GitHub release notes and issue #5542 (0.15 → 16 breaking changes).
- `rpi_ws281x` 5.0.0 on PyPI (unchanged since 2023).
- Raspberry Pi kernel (rpi-6.18.y) `drivers/misc/ws2812-pio-rp1.c` and `ws2812-pio` overlay: same parameters and
  pixel format as `drivers.py` assumes; brightness 0 = pass-through.

## Verified
- **Real WLED 16.0.1 UI against this backend** (2026-11-09, virtual driver, port 8080): page load, WebSocket,
  123 effects shown (RSVD hidden), 72 palette previews, colour controls, transition styles, segment blend modes,
  reset segments, presets with `bs`/`bm`, preset highlight, Peek, Info (GitHub link), new palette editor (upload,
  delete, gap placeholders hidden in the main UI), file editor, PixelForge page. (0.15.3 UI verified 2026-10-09.)
- Not supported by design: PixelForge GIF upload and the Image effect (53), 2D tools.

## Not yet verified (highest priority first)
1. **Real hardware**: Pi 5 PIO output (byte order, pass-through brightness), Pi 4 rpi_ws281x (uses
   `strip._channel` + `ws2811_led_set` fast path, falls back to `setPixelColor`), installer end to end.
2. Pi 4 second output on PWM1 (two `PixelStrip` instances) — untested, may conflict.
3. Approximated effects (not ports): Fairy 49, Fairytwinkle 51, Dancing Shadows 112, TV Simulator 116.
   Shimmer 161 and Color Clouds 218 use FastLED-style `inoise16` where WLED 16 uses its own `perlin16`.
4. Not implemented: WLED 16's 1D particle-system effects (202–211, 213, 216; needs a port of
   FXparticleSystem.cpp) — optional, they stay hidden as RSVD.

## Repo housekeeping
- LICENSE is the full EUPL-1.2 text (matches WLED) under a two-line copyright header; keep it verbatim so
  GitHub keeps detecting it.
- If committing from Windows: `git update-index --chmod=+x install.sh uninstall.sh tools/fetch_wled_assets.py`.
