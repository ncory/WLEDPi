"""Palettes, using WLED's palette IDs.

IDs 0-12 are built in (FastLED palettes + colour based ones). IDs 13+ are the
cpt-city gradient palettes from WLED; their data is extracted from the WLED
source by tools/fetch_wled_assets.py at install time (assets/palettes.json).
Custom palettes (palette0.json ... palette9.json in the data dir) map to IDs
255, 254, ... exactly like WLED.
"""
import json
import logging
import os
import random

import numpy as np

from . import fastled as F

log = logging.getLogger("wledpi.palettes")

NAMES = [
    "Default", "* Random Cycle", "* Color 1", "* Colors 1&2", "* Color Gradient", "* Colors Only", "Party", "Cloud",
    "Lava", "Ocean", "Forest", "Rainbow", "Rainbow Bands", "Sunset", "Rivendell", "Breeze", "Red & Blue",
    "Yellowout", "Analogous", "Splash", "Pastel", "Sunset 2", "Beach", "Vintage", "Departure", "Landscape",
    "Beech", "Sherbet", "Hult", "Hult 64", "Drywet", "Jul", "Grintage", "Rewhi", "Tertiary", "Fire", "Icefire",
    "Cyane", "Light Pink", "Autumn", "Magenta", "Magred", "Yelmag", "Yelblu", "Orange & Teal", "Tiamat",
    "April Night", "Orangery", "C9", "Sakura", "Aurora", "Atlantica", "C9 2", "C9 New", "Temperature",
    "Aurora 2", "Retro Clown", "Candy", "Toxy Reaf", "Fairy Reaf", "Semi Blue", "Pink Candy", "Red Reaf",
    "Aqua Flash", "Yelblu Hot", "Lite Light", "Red Flash", "Blink Red", "Red Shift", "Red Tide", "Candy2",
]


def _hex16(vals):
    return np.array([[(v >> 16) & 255, (v >> 8) & 255, v & 255] for v in vals], dtype=np.float32)


PARTY = _hex16([0x5500AB, 0x84007C, 0xB5004B, 0xE5001B, 0xE81700, 0xB84700, 0xAB7700, 0xABAB00,
                0xAB5500, 0xDD2200, 0xF2000E, 0xC2003E, 0x8F0071, 0x5F00A1, 0x2F00D0, 0x0007F9])
CLOUD = _hex16([0x0000FF, 0x00008B, 0x00008B, 0x00008B, 0x00008B, 0x00008B, 0x00008B, 0x00008B,
                0x0000FF, 0x00008B, 0x87CEEB, 0x87CEEB, 0xADD8E6, 0xFFFFFF, 0xADD8E6, 0x87CEEB])
LAVA = _hex16([0x000000, 0x800000, 0x000000, 0x800000, 0x8B0000, 0x8B0000, 0x800000, 0x8B0000,
               0x8B0000, 0x8B0000, 0xFF0000, 0xFFA500, 0xFFFFFF, 0xFFA500, 0xFF0000, 0x8B0000])
OCEAN = _hex16([0x191970, 0x00008B, 0x191970, 0x000080, 0x00008B, 0x0000CD, 0x2E8B57, 0x008080,
                0x5F9EA0, 0x0000FF, 0x008B8B, 0x6495ED, 0x7FFFD4, 0x2E8B57, 0x00FFFF, 0x87CEFA])
FOREST = _hex16([0x006400, 0x006400, 0x556B2F, 0x006400, 0x008000, 0x228B22, 0x6B8E23, 0x008000,
                 0x2E8B57, 0x66CDAA, 0x32CD32, 0x9ACD32, 0x90EE90, 0x7CFC00, 0x66CDAA, 0x228B22])
RAINBOW = _hex16([0xFF0000, 0xD52A00, 0xAB5500, 0xAB7F00, 0xABAB00, 0x56D500, 0x00FF00, 0x00D52A,
                  0x00AB55, 0x0056AA, 0x0000FF, 0x2A00D5, 0x5500AB, 0x7F0081, 0xAB0055, 0xD5002B])
RAINBOW_STRIPE = _hex16([0xFF0000, 0, 0xAB5500, 0, 0xABAB00, 0, 0x00FF00, 0,
                         0x00AB55, 0, 0x0000FF, 0, 0x5500AB, 0, 0xAB0055, 0])
HEAT = _hex16([0x000000, 0x330000, 0x660000, 0x990000, 0xCC0000, 0xFF0000, 0xFF3300, 0xFF6600,
               0xFF9900, 0xFFCC00, 0xFFFF00, 0xFFFF33, 0xFFFF66, 0xFFFF99, 0xFFFFCC, 0xFFFFFF])

BUILTIN16 = {0: PARTY, 6: PARTY, 7: CLOUD, 8: LAVA, 9: OCEAN, 10: FOREST, 11: RAINBOW, 12: RAINBOW_STRIPE}

# Approximations used ONLY if the WLED palette data could not be imported at install time.
FALLBACK_GRADIENTS = {
    13: [0, 120, 0, 0, 22, 179, 22, 0, 51, 255, 104, 0, 85, 167, 22, 18, 135, 100, 0, 103, 198, 16, 0, 130, 255, 0, 0, 160],
    35: [0, 0, 0, 0, 60, 128, 0, 0, 120, 255, 30, 0, 180, 255, 160, 0, 230, 255, 255, 40, 255, 255, 255, 255],
    36: [0, 0, 0, 0, 60, 0, 0, 128, 120, 0, 60, 255, 180, 0, 200, 255, 230, 160, 255, 255, 255, 255, 255, 255],
    54: [0, 0, 0, 255, 64, 0, 160, 255, 128, 0, 255, 0, 192, 255, 200, 0, 255, 255, 0, 0],
}


def fill_gradient(entries, i0, c0, i1, c1):
    if i1 < i0:
        i0, i1, c0, c1 = i1, i0, c1, c0
    n = max(i1 - i0, 1)
    c0 = np.asarray(c0, dtype=np.float32)
    c1 = np.asarray(c1, dtype=np.float32)
    for i in range(i0, i1 + 1):
        entries[i] = np.floor(c0 + (c1 - c0) * (i - i0) / n)


def gradient_to_16(stops):
    """Convert a gradient [idx,r,g,b, idx,r,g,b, ...] to 16 entries like FastLED does."""
    q = [stops[i:i + 4] for i in range(0, len(stops) - 3, 4)]
    if not q:
        return RAINBOW.copy()
    if q[-1][0] != 255:
        q.append([255] + list(q[-1][1:]))
    entries = np.zeros((16, 3), dtype=np.float32)
    count = len(q)
    last = -1
    istart = 0
    cstart = q[0][1:]
    for st in q[1:]:
        iend = st[0]
        i0, i1 = istart // 16, iend // 16
        if count < 16:
            if i0 <= last < 15:
                i0 = last + 1
                if i1 < i0:
                    i1 = i0
            last = i1
        fill_gradient(entries, min(i0, 15), cstart, min(i1, 15), st[1:])
        istart = iend
        cstart = st[1:]
        if istart >= 255:
            break
    return entries


def pal16_from_colors(cols):
    """CRGBPalette16(c1[,c2[,c3[,c4]]]) gradient constructors."""
    e = np.zeros((16, 3), dtype=np.float32)
    n = len(cols)
    if n == 1:
        e[:] = cols[0]
    elif n == 2:
        fill_gradient(e, 0, cols[0], 15, cols[1])
    elif n == 3:
        fill_gradient(e, 0, cols[0], 8, cols[1])
        fill_gradient(e, 8, cols[1], 15, cols[2])
    else:
        fill_gradient(e, 0, cols[0], 5, cols[1])
        fill_gradient(e, 5, cols[1], 10, cols[2])
        fill_gradient(e, 10, cols[2], 15, cols[3])
    return e


_IDX = np.arange(256)
_HI = _IDX >> 4
_LO = _IDX & 15


def tables_from_16(p16):
    """256-entry lookup tables (LINEARBLEND with wrap, and NOBLEND) for a 16 entry palette."""
    p16 = np.asarray(p16, dtype=np.float32)
    e1 = p16[_HI]
    e2 = p16[(_HI + 1) & 15]
    f2 = (_LO << 4)[:, None].astype(np.float32)
    f1 = 255 - f2
    mixed = np.floor(e1 * (f1 + 1) / 256) + np.floor(e2 * (f2 + 1) / 256)
    blend = np.where((_LO == 0)[:, None], e1, mixed)
    return blend.astype(np.float32), e1.astype(np.float32)


class PaletteBank:
    """Holds all palette data and the global random palette."""

    def __init__(self):
        self.gradients = {}      # id -> stops list
        self.custom = []         # list of stops lists (palette0.json -> 255 ...)
        self.names = list(NAMES)
        self._cache = {}
        # random palette (ID 1)
        self.rand_cur = pal16_from_colors([F.hsv_rainbow(random.randrange(256), 255, 200)[0] for _ in range(4)])
        self.rand_target = self.rand_cur.copy()
        self.rand_last_change = -1e9
        self.rand_version = 0

    def load_assets(self, path):
        try:
            with open(path) as f:
                data = json.load(f)
            g = data.get("gradients", {})
            self.gradients = {int(k): list(v) for k, v in g.items()}
            names = data.get("names")
            need = max([len(NAMES)] + [k + 1 for k in self.gradients])
            if isinstance(names, list) and len(names) >= need and all(isinstance(n, str) for n in names):
                self.names = names
            log.info("Loaded %d gradient palettes from %s", len(self.gradients), path)
        except FileNotFoundError:
            log.warning("Palette assets %s not found; gradient palettes use approximations", path)
        except Exception as e:  # noqa
            log.warning("Could not load palette assets %s: %s", path, e)
        self._cache.clear()

    def load_custom(self, data_dir):
        self.custom = []
        for i in range(10):
            p = os.path.join(data_dir, "palette%d.json" % i)
            if not os.path.exists(p):
                break
            try:
                with open(p) as f:
                    raw = json.load(f).get("palette", [])
                stops = []
                j = 0
                while j < len(raw):
                    if j + 1 < len(raw) and isinstance(raw[j + 1], str):
                        h = raw[j + 1].lstrip("#")
                        stops += [int(raw[j]), int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
                        j += 2
                    else:
                        stops += [int(v) for v in raw[j:j + 4]]
                        j += 4
                self.custom.append(stops)
            except Exception as e:  # noqa
                log.warning("Bad custom palette %s: %s", p, e)
                break
        self._cache.clear()

    @property
    def count(self):
        return len(self.names)

    def tick_random(self, now_ms, interval_s):
        if interval_s > 0 and now_ms - self.rand_last_change > interval_s * 1000:
            self.rand_last_change = now_ms
            cols = [F.hsv_rainbow(random.randrange(256), 255, random.randrange(128, 256))[0] for _ in range(4)]
            self.rand_target = pal16_from_colors(cols)
        diff = self.rand_target - self.rand_cur
        if np.any(diff):
            self.rand_cur = self.rand_cur + np.clip(diff, -6, 6)
            self.rand_version += 1

    def stops_for(self, pid):
        if pid >= 246:
            i = 255 - pid
            return self.custom[i] if i < len(self.custom) else None
        if pid in self.gradients:
            return self.gradients[pid]
        return FALLBACK_GRADIENTS.get(pid)

    def pal16(self, pid, colors):
        """16 entry palette for palette id; colors = list of 3 RGBW colour lists of the segment."""
        if pid >= 246 and (255 - pid) >= len(self.custom):
            pid = 0
        if pid in BUILTIN16:
            return BUILTIN16[pid]
        if pid == 1:
            return self.rand_cur
        if pid in (2, 3, 4, 5):
            c = [np.asarray(x[:3], dtype=np.float32) for x in colors]
            if pid == 2:
                return pal16_from_colors([c[0]])
            if pid == 3:
                return pal16_from_colors([c[0], c[0], c[1], c[1]])
            if pid == 4:
                return pal16_from_colors([c[2], c[1], c[0]])
            if any(colors[2][:3]):
                seq = [c[0]] * 5 + [c[1]] * 5 + [c[2]] * 5 + [c[0]]
            else:
                seq = [c[0]] * 8 + [c[1]] * 8
            return np.array(seq, dtype=np.float32)
        key = ("g", pid)
        if key not in self._cache:
            st = self.stops_for(pid)
            self._cache[key] = gradient_to_16(st) if st else RAINBOW
        return self._cache[key]

    def tables(self, pid, colors):
        if pid in (1, 2, 3, 4, 5):
            return tables_from_16(self.pal16(pid, colors))
        key = ("t", pid)
        t = self._cache.get(key)
        if t is None:
            t = tables_from_16(self.pal16(pid, colors))
            self._cache[key] = t
        return t

    # ---- /json/palx preview data, same format as WLED ----
    def preview(self, pid):
        if pid == 1:
            return ["r", "r", "r", "r"]
        if pid == 2:
            return ["c1"]
        if pid == 3:
            return ["c1", "c1", "c2", "c2"]
        if pid == 4:
            return ["c3", "c2", "c1"]
        if pid == 5:
            return ["c1"] * 5 + ["c2"] * 5 + ["c3"] * 5 + ["c1"]
        if pid in BUILTIN16:
            p = BUILTIN16[pid]
            return [[int(i * 255 / 15), int(p[i][0]), int(p[i][1]), int(p[i][2])] for i in range(16)]
        st = self.stops_for(pid)
        if not st:
            p = RAINBOW
            return [[int(i * 255 / 15), int(p[i][0]), int(p[i][1]), int(p[i][2])] for i in range(16)]
        return [list(st[i:i + 4]) for i in range(0, len(st) - 3, 4)]
