"""WLED-compatible effects.

Effect IDs and names match WLED 0.15 so presets created on WLED refer to the
same effect here. Each effect is a function f(seg, now_ms) that renders into
seg.buf (virtual pixels, float RGBW 0-255). Returning an int > FRAMETIME
requests a custom delay before the next call (like WLED's legacy effects).

The algorithms follow WLED's FX.cpp closely (which in turn credits FastLED
examples, Andrew Tuline, Mark Kriegsman and others) but are re-implemented
in numpy; output is visually equivalent, not bit-identical.
"""
import math
import random

import numpy as np

from . import fastled as F
from .segment import FRAMETIME, SOLID_WRAP, color_blend as cb
from .palettes import tables_from_16, _hex16

EFFECTS = {}          # id -> (name, metadata, function)
MODE_COUNT = 187      # same ID space as WLED 0.15

BLACK = np.zeros(4, dtype=np.float32)
WHITE = np.array([255, 255, 255, 0], dtype=np.float32)
ULTRAWHITE = np.array([255, 255, 255, 255], dtype=np.float32)


def fx(fid, name, meta=""):
    def deco(f):
        EFFECTS[fid] = (name, meta, f)
        return f
    return deco


def has(c):
    return any(c)


def effect_names():
    return [EFFECTS[i][0] if i in EFFECTS else "RSVD" for i in range(MODE_COUNT)]


def effect_data():
    return [EFFECTS[i][1] if i in EFFECTS else "" for i in range(MODE_COUNT)]


def effect_defaults(fid):
    """Parse the defaults section (5th) of the metadata string, e.g. 'sx=24,pal=50'."""
    meta = EFFECTS.get(fid, ("", "", None))[1]
    parts = meta.split(";")
    out = {}
    if len(parts) >= 5:
        for kv in parts[4].split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    out[k.strip()] = int(v)
                except ValueError:
                    pass
    return out


# ============================================================================ basics

@fx(0, "Solid")
def solid(s, now):
    s.fill(s.col(0))


def _blink(s, now, c1, c2, strobe, do_pal):
    cycle = (255 - s.sx) * 20
    on_t = FRAMETIME
    if not strobe:
        on_t += (cycle * s.ix) >> 8
    cycle += FRAMETIME * 2
    it = now // cycle
    on = it != s.step or (now % cycle) <= on_t
    s.step = it
    if on and do_pal:
        s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 0)
    else:
        s.fill(c1 if on else c2)


@fx(1, "Blink", "@!,Duty cycle;!,!;!;01")
def blink(s, now):
    _blink(s, now, s.col(0), s.col(1), False, True)


@fx(23, "Strobe", "@!;!,!;!;01")
def strobe(s, now):
    _blink(s, now, s.col(0), s.col(1), True, True)


@fx(24, "Strobe Rainbow", "@!;,!;!;01")
def strobe_rainbow(s, now):
    _blink(s, now, s.wheel(s.call & 255), s.col(1), True, False)


@fx(26, "Blink Rainbow", "@Frequency,Blink duration;!,!;!;01")
def blink_rainbow(s, now):
    _blink(s, now, s.wheel(s.call & 255), s.col(1), False, False)


@fx(2, "Breathe", "@!;!,!;!;01")
def breathe(s, now):
    counter = (now * ((s.sx >> 3) + 10)) & 0xFFFF
    counter = (counter >> 2) + (counter >> 4)
    var = 0
    if counter < 16384:
        if counter > 8192:
            counter = 8192 - (counter - 8192)
        var = F.sin16(counter) // 103
    s.buf[:] = cb(s.col(1), s.cfp(s.idx, True, SOLID_WRAP, 0), 30 + var)


@fx(12, "Fade", "@!;!,!;!;01")
def fade(s, now):
    lum = F.triwave16((now * ((s.sx >> 3) + 10)) & 0xFFFF) >> 8
    s.buf[:] = cb(s.col(1), s.cfp(s.idx, True, SOLID_WRAP, 0), lum)


def _wipe(s, now, rev, rnd):
    L = s.L
    cycle = 750 + (255 - s.sx) * 150
    prog = ((now % cycle) * 65535) // cycle
    back = prog > 32767
    if back:
        prog -= 32767
        if s.step == 0:
            s.step = 1
    elif s.step == 2:
        s.step = 3
    if rnd:
        if s.call == 0:
            s.aux0 = F.random8()
            s.step = 3
        if s.step == 1:
            s.aux1 = F.random_wheel_index(s.aux0)
            s.step = 2
        if s.step == 3:
            s.aux0 = F.random_wheel_index(s.aux1)
            s.step = 0
    led = (prog * L) >> 15
    rem = min(255, (((prog * L) * 2) & 0xFFFF) // (s.ix + 1))
    i = s.idx
    index = (L - 1 - i) if (rev and back) else i
    col1 = np.broadcast_to(s.wheel(s.aux1) if rnd else s.col(1), (L, 4))
    col0 = np.broadcast_to(s.wheel(s.aux0), (L, 4)) if rnd else s.cfp(index, True, SOLID_WRAP, 0)
    before = (i < led)[:, None]
    out = np.where(before, col1 if back else col0, col0 if back else col1).astype(np.float32)
    if 0 <= led < L:
        a = col0[led] if back else col1[led]
        b = col1[led] if back else col0[led]
        out[led] = cb(a, b, rem)
    s.buf[index] = out


@fx(3, "Wipe", "@!,!;!,!;!")
def wipe(s, now):
    _wipe(s, now, False, False)


@fx(4, "Wipe Random", "@!;;!")
def wipe_random(s, now):
    _wipe(s, now, False, True)


@fx(6, "Sweep", "@!,!;!,!;!")
def sweep(s, now):
    _wipe(s, now, True, False)


@fx(36, "Sweep Random", "@!;;!")
def sweep_random(s, now):
    _wipe(s, now, True, True)


@fx(5, "Random Colors", "@!,Fade time;;!;01")
def random_colors(s, now):
    cycle = 200 + (255 - s.sx) * 50
    it = now // cycle
    fadedur = (cycle * s.ix) >> 8
    f = 255 if not fadedur else min(255, (now % cycle) * 255 // fadedur)
    if s.call == 0:
        s.aux0 = F.random8()
        s.step = 2
    if it != s.step:
        s.aux1 = s.aux0
        s.aux0 = F.random_wheel_index(s.aux0)
        s.step = it
    s.fill(cb(s.wheel(s.aux1), s.wheel(s.aux0), f))


def _dynamic(s, now, smooth):
    d = s.data
    if s.call == 0 or len(d.get("c", ())) != s.L:
        d["c"] = np.random.randint(0, 256, s.L)
    cycle = 50 + (255 - s.sx) * 15
    it = now // cycle
    if it != s.step and s.sx != 0:
        mask = np.random.randint(0, 256, s.L) <= s.ix
        d["c"] = np.where(mask, np.random.randint(0, 256, s.L), d["c"])
        s.step = it
    cols = s.wheel(d["c"])
    s.buf[:] = cb(s.buf, cols, 16) if smooth else cols


@fx(7, "Dynamic", "@!,!,,,,Smooth;;!")
def dynamic(s, now):
    _dynamic(s, now, s.o1)


@fx(117, "Dynamic Smooth", "@!,!;;!")
def dynamic_smooth(s, now):
    _dynamic(s, now, True)


@fx(8, "Colorloop", "@!,Saturation;;!;01")
def colorloop(s, now):
    counter = ((now * ((s.sx >> 2) + 2)) & 0xFFFF) >> 8
    c = s.wheel(counter)
    if s.ix < 128:
        c = cb(c, WHITE, 128 - s.ix)
    s.fill(c)


@fx(9, "Rainbow", "@!,Size;;!")
def rainbow(s, now):
    counter = ((now * ((s.sx >> 2) + 2)) & 0xFFFF) >> 8
    idx = (s.idx * (16 << (s.ix // 29)) // max(1, s.L)) + counter
    s.buf[:] = s.wheel(idx & 255)


def _scan(s, now, dual):
    L = s.L
    cycle = 750 + (255 - s.sx) * 150
    prog = ((now % cycle) * 65535) // cycle
    size = 1 + ((s.ix * L) >> 9)
    led = (prog * max(0, (L * 2) - size * 2)) >> 16
    if not s.o2:
        s.fill(s.col(1))
    off = abs(led - (L - size))
    j = np.arange(off, min(off + size, L))
    if len(j) == 0:
        return
    if dual:
        i2 = L - 1 - j
        s.buf[i2] = s.cfp(i2, True, SOLID_WRAP, 2 if has(s.colors[2]) else 0)
    s.buf[j] = s.cfp(j, True, SOLID_WRAP, 0)


@fx(10, "Scan", "@!,# of dots,,,,,Overlay;!,!,!;!")
def scan(s, now):
    _scan(s, now, False)


@fx(11, "Scan Dual", "@!,# of dots,,,,,Overlay;!,!,!;!")
def scan_dual(s, now):
    _scan(s, now, True)


def _theater(s, now, c1, c2, do_pal):
    gap = 2 + ((255 - s.ix) >> 5)
    it = now // (50 + (255 - s.sx) * 2)
    if it != s.step:
        s.aux0 = (s.aux0 + 1) % gap
        s.step = it
    on = ((s.idx % gap) == s.aux0)[:, None]
    fg = s.cfp(s.idx, True, SOLID_WRAP, 0) if do_pal else np.broadcast_to(c1, (s.L, 4))
    s.buf[:] = np.where(on, fg, c2)


@fx(13, "Theater", "@!,Gap size;!,!;!")
def theater(s, now):
    _theater(s, now, s.col(0), s.col(1), True)


@fx(14, "Theater Rainbow", "@!,Gap size;,!;!")
def theater_rainbow(s, now):
    _theater(s, now, s.wheel(s.step & 255), s.col(1), False)


def _sin_gap(a):
    a = np.asarray(a)
    return np.where(a & 0x100, 0, F.sin8((a + 192) & 255))


def _running(s, now, saw, dual=False):
    x_scale = s.ix >> 2
    counter = (now * s.sx) >> 9
    i = s.idx
    a = (i * x_scale - counter) & 0xFFFF
    if saw:
        a = a & 0xFF
        a = np.where(a < 16, 192 + a * 8, 64 + (a - 16) * 128 // 239)
        a = 255 - a
    sv = _sin_gap(a) if dual else F.sin8(a)
    ca = cb(s.col(1), s.cfp(i, True, SOLID_WRAP, 0), sv)
    if dual:
        b = ((s.L - 1 - i) * x_scale - counter) & 0xFFFF
        cbb = cb(s.col(1), s.cfp(i, True, SOLID_WRAP, 2), _sin_gap(b))
        ca = cb(ca, cbb, 127)
    s.buf[:] = ca


@fx(15, "Running", "@!,Wave width;!,!;!")
def running(s, now):
    _running(s, now, False)


@fx(16, "Saw", "@!,Width;!,!;!")
def saw(s, now):
    _running(s, now, True)


@fx(52, "Running Dual", "@!,Wave width;L,!,R;!")
def running_dual(s, now):
    _running(s, now, False, True)


@fx(17, "Twinkle", "@!,!;!,!;!;;m12=0")
def twinkle(s, now):
    s.fade_out(224)
    it = now // (20 + (255 - s.sx) * 5)
    if it != s.step:
        maxon = 1 + (s.ix * (s.L - 1)) // 255
        if s.aux0 >= maxon:
            s.aux0 = 0
            s.aux1 = F.random16()
        s.aux0 += 1
        s.step = it
    prng = s.aux1
    js = []
    for _ in range(s.aux0):
        prng = (prng * 2053 + 13849) & 0xFFFF
        js.append((s.L * prng) >> 16)
    if js:
        j = np.array(js)
        s.buf[j] = s.cfp(j, True, SOLID_WRAP, 0)


def _dissolve(s, now, color_fn):
    if s.call == 0:
        s.fill(s.col(1))
    c1 = s.col(1)
    for _ in range(s.L // 15 + 1):
        if F.random8() <= s.ix:
            for _t in range(10):
                i = F.random16(s.L)
                same = np.array_equal(s.buf[i], c1)
                if s.aux0:
                    if same:
                        s.buf[i] = color_fn(i)
                        break
                elif not same:
                    s.buf[i] = c1
                    break
    if s.step > (255 - s.sx) + 15:
        s.aux0 = 0 if s.aux0 else 1
        s.step = 0
    else:
        s.step += 1


@fx(18, "Dissolve", "@Repeat speed,Dissolve speed,,,,Random;!,!;!")
def dissolve(s, now):
    if s.o1:
        _dissolve(s, now, lambda i: s.wheel(F.random8()))
    else:
        _dissolve(s, now, lambda i: s.cfp(i, True, SOLID_WRAP, 0))


@fx(19, "Dissolve Rnd", "@Repeat speed,Dissolve speed;,!;!")
def dissolve_rnd(s, now):
    _dissolve(s, now, lambda i: s.wheel(F.random8()))


@fx(20, "Sparkle", "@!,,,,,,Overlay;!,!;!;;m12=0")
def sparkle(s, now):
    if not s.o2:
        s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 1)
    it = now // (10 + (255 - s.sx) * 2)
    if it != s.step:
        s.aux0 = F.random16(s.L)
        s.step = it
    s.setp(s.aux0, s.col(0))


def _flash_sparkle(s, now, many):
    if not s.o2:
        s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 0)
    if now - s.aux0 > s.step:
        if F.random8((255 - s.ix) >> 4) == 0:
            n = max(1, s.L // 3) if many else 1
            for _ in range(n):
                s.setp(F.random16(s.L), s.col(1))
        s.step = now
        s.aux0 = 255 - s.sx


@fx(21, "Sparkle Dark", "@!,!,,,,,Overlay;Bg,Fx;!;;m12=0")
def sparkle_dark(s, now):
    _flash_sparkle(s, now, False)


@fx(22, "Sparkle+", "@!,!,,,,,Overlay;Bg,Fx;!;;m12=0")
def sparkle_plus(s, now):
    _flash_sparkle(s, now, True)


@fx(25, "Strobe Mega", "@!,!;!,!;!;01")
def strobe_mega(s, now):
    s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 1)
    delay = 50 + 20 * (255 - s.sx)
    count = 2 * ((s.ix // 10) + 1)
    if s.aux1 < count:
        if (s.aux1 & 1) == 0:
            s.fill(s.col(0))
            delay = 15
        else:
            delay = 50
    if now - delay > s.step:
        s.aux1 += 1
        if s.aux1 > count:
            s.aux1 = 0
        s.step = now


@fx(27, "Android", "@!,Width;!,!;!;;m12=1")
def android(s, now):
    L = s.L
    s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 1)
    if s.aux1 > (s.ix / 255.0) * L:
        s.aux0 = 1
    elif s.aux1 < 2:
        s.aux0 = 0
    a = s.step
    if s.aux0 == 0:
        if s.call % 3 == 1:
            a += 1
        else:
            s.aux1 += 1
    else:
        a += 1
        if s.call % 3 != 1:
            s.aux1 -= 1
    if a >= L:
        a = 0
    c = s.col(0)
    if a + s.aux1 < L:
        s.set_range(a, a + s.aux1, c)
    else:
        s.set_range(a, L, c)
        s.set_range(0, s.aux1 - (L - a), c)
    s.step = a
    return 3 + ((8 * (255 - s.sx)) // max(1, L))


def _chase(s, now, c1, c2, c3, do_pal, rnd=False):
    L = s.L
    counter = (now * ((s.sx >> 2) + 1)) & 0xFFFF
    a = (counter * L) >> 16
    if rnd:
        if a < s.step:
            s.aux1 = s.aux0
            s.aux0 = F.random_wheel_index(s.aux0)
        c1 = s.wheel(s.aux0)
    s.step = a
    size = 1 + ((s.ix * L) >> 10)
    b = a + size
    if b > L:
        b -= L
    c = b + size
    if c > L:
        c -= L
    if do_pal:
        s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 1)
    else:
        s.fill(c1)
    if rnd:
        s.set_range(a, L, s.wheel(s.aux1))

    def span(x, y, col):
        if x < y:
            s.set_range(x, y, col)
        else:
            s.set_range(x, L, col)
            s.set_range(0, y, col)
    span(a, b, c2)
    span(b, c, c3)


@fx(28, "Chase", "@!,Width;!,!,!;!")
def chase(s, now):
    _chase(s, now, s.col(1), s.col(2) if has(s.colors[2]) else s.col(0), s.col(0), True)


@fx(29, "Chase Random", "@!,Width;!,,!;!")
def chase_random(s, now):
    _chase(s, now, s.col(1), s.col(2) if has(s.colors[2]) else s.col(0), s.col(0), False, True)


@fx(30, "Chase Rainbow", "@!,Width;!,!;!")
def chase_rainbow(s, now):
    sep = max(1, 256 // max(1, s.L))
    color = s.wheel(((s.step * sep) + (s.call & 255)) & 255)
    _chase(s, now, color, s.col(0), s.col(1), False)


@fx(33, "Rainbow Runner", "@!,Size;Bg;!")
def rainbow_runner(s, now):
    L = max(1, s.L)
    n = s.step
    m = (s.step + 1) % L
    c2 = s.wheel(((n * 256 // L) + (s.call & 255)) & 255)
    c3 = s.wheel(((m * 256 // L) + (s.call & 255)) & 255)
    _chase(s, now, s.col(0), c2, c3, False)


@fx(31, "Chase Flash", "@!;Bg,Fx;!")
def chase_flash(s, now):
    L = s.L
    flash_step = s.call % 9
    s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 0)
    delay = 10 + ((30 * (255 - s.sx)) // L)
    if flash_step < 8:
        if flash_step % 2 == 0:
            s.setp(s.step, s.col(1))
            s.setp((s.step + 1) % L, s.col(1))
            delay = 20
        else:
            delay = 30
    else:
        s.step = (s.step + 1) % L
    return delay


@fx(32, "Chase Flash Rnd", "@!;!,!;!")
def chase_flash_rnd(s, now):
    L = s.L
    flash_step = s.call % 9
    s.set_range(0, s.aux1, s.wheel(s.aux0))
    delay = 1 + ((10 * (255 - s.sx)) // L)
    n = s.aux1
    m = (s.aux1 + 1) % L
    if flash_step < 8:
        if flash_step % 2 == 0:
            s.setp(n, s.col(0))
            s.setp(m, s.col(0))
            delay = 30
        else:
            s.setp(n, s.wheel(s.aux0))
            s.setp(m, s.col(1))
    else:
        s.aux1 = (s.aux1 + 1) % L
        if s.aux1 == 0:
            s.aux0 = F.random_wheel_index(s.aux0)
    return delay


@fx(34, "Colorful", "@!,Saturation;1,2,3;!")
def colorful(s, now):
    num = 4
    cols = [np.array([(c >> 16) & 255, (c >> 8) & 255, c & 255, 0], np.float32)
            for c in (0xFF0000, 0xEEBB00, 0x00EE00, 0x0077CC)]
    if s.ix > 160 or s.pal:
        if not s.pal:
            num = 3
            cols = [s.col(0), s.col(1), s.col(2)]
        else:
            fac = 80
            if s.pal == 52:
                num, fac = 5, 61
            cols = [s.cfp(i * fac, False, True, 255) for i in range(num)]
    elif s.ix < 80:
        cols = [np.array([(c >> 16) & 255, (c >> 8) & 255, c & 255, 0], np.float32)
                for c in (0xFF8040, 0xE5D241, 0x77FF77, 0x77F0F0)]
    it = now // (50 + 8 * (255 - s.sx))
    if it != s.step:
        if s.sx > 0:
            s.aux0 += 1
        if s.aux0 >= num:
            s.aux0 = 0
        s.step = it
    pattern = np.array(cols + cols, dtype=np.float32)
    s.buf[:] = pattern[(s.idx % num) + s.aux0]


@fx(35, "Traffic Light", "@!,US style;,!;!")
def traffic_light(s, now):
    s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 1)
    red = np.array([255, 0, 0, 0], np.float32)
    amber = np.array([238, 204, 0, 0], np.float32)
    green = np.array([0, 255, 0, 0], np.float32)
    k = 255 - s.sx
    state = s.aux0
    mdelay = 150 + (100 * k if state in (0, 2) else 20 * k)
    for i in range(0, s.L - 2, 3):
        if state == 0:
            s.setp(i, red)
        elif state == 1:
            s.setp(i, red)
            s.setp(i + 1, amber)
        elif state == 2:
            s.setp(i + 2, green)
        else:
            s.setp(i + 1, amber)
    if now - s.step > mdelay:
        s.aux0 += 1
        if s.aux0 == 1 and s.ix > 140:
            s.aux0 = 2
        if s.aux0 > 3:
            s.aux0 = 0
        s.step = now


@fx(37, "Chase 2", "@!,Width;!,!;!")
def chase2(s, now):
    width = 1 + (s.ix >> 4)
    it = now // (50 + (255 - s.sx))
    pos = s.idx % (width << 1)
    on = ((pos < s.aux0 - width) | ((pos >= s.aux0) & (pos < s.aux0 + width)))[:, None]
    s.buf[:] = np.where(on, s.cfp(s.idx, True, SOLID_WRAP, 0), s.col(1))
    if it != s.step:
        s.aux0 = (s.aux0 + 1) % (width << 1)
        s.step = it


@fx(54, "Chase 3", "@!,Size;1,2,3;!")
def chase3(s, now):
    it = now // (50 + ((255 - s.sx) << 1))
    width = 1 + (s.ix >> 4)
    index = (it % (width * 3) + s.idx) % (width * 3)
    pal = s.cfp(s.idx, True, SOLID_WRAP, 1)
    out = np.where((index > (width << 1) - 1)[:, None], pal,
                   np.where((index > width - 1)[:, None], s.col(0), s.col(2)))
    s.buf[s.L - 1 - s.idx] = out


# ============================================================================ aurora / streams

@fx(38, "Aurora", "@!,!;1,2,3;!;;sx=24,pal=50")
def aurora(s, now):
    d = s.data
    L = s.L

    def new_wave(k):
        w = d["w"]
        w["ttl"][k] = random.randint(500, 1500)
        c = s.cfp(F.random8(), False, False, random.randint(0, 2))
        w["col"][k] = c[:3]
        w["alpha"][k] = random.randint(60, 100) / 100.0
        w["age"][k] = 0
        w["width"][k] = max(1, random.randint(L // 20, max(L // 20, L // 6)))
        w["center"][k] = random.randint(0, 100) / 100.0 * L
        w["left"][k] = random.randint(0, 1) == 0
        w["spd"][k] = random.randint(10, 30) / 100.0 * 6 / 255

    if s.aux0 != s.ix or s.call == 0:
        n = 2 + (s.ix * 18) // 255
        s.aux0 = s.ix
        d["w"] = {"ttl": np.zeros(n), "col": np.zeros((n, 3)), "alpha": np.zeros(n), "age": np.zeros(n),
                  "width": np.ones(n), "center": np.zeros(n), "left": np.zeros(n, bool), "spd": np.zeros(n)}
        for k in range(n):
            new_wave(k)
    w = d["w"]
    w["center"] += np.where(w["left"], -1, 1) * w["spd"] * s.sx
    w["age"] += 1
    dead = (w["age"] > w["ttl"]) | (w["left"] & (w["center"] + w["width"] < 0)) | \
           (~w["left"] & (w["center"] - w["width"] > L))
    for k in np.nonzero(dead)[0]:
        new_wave(k)
    backlight = 1 + sum(1 for c in s.colors if has(c))
    i = s.idx[:, None].astype(np.float64)
    off = np.abs(i - w["center"][None, :])
    inside = off <= w["width"][None, :]
    halfttl = w["ttl"] / 2
    agef = np.where(w["age"] / w["ttl"] < 0.5, w["age"] / halfttl, (w["ttl"] - w["age"]) / halfttl)
    factor = np.where(inside, (1 - off / w["width"][None, :]) * agef[None, :] * w["alpha"][None, :], 0)
    rgb = backlight + factor @ w["col"]
    s.buf[:, :3] = np.clip(rgb, 0, 255)
    s.buf[:, 3] = 0


@fx(39, "Stream", "@!,Zone size;;!")
def stream(s, now):
    it = now // (25 + 3 * (255 - s.sx))
    if s.call == 0:
        s.aux0 = F.random16()
    zone = ((255 - s.ix) >> 4) + 1
    prng = s.aux0
    z = it % zone
    nzone = (z == 0 and it != s.aux1)
    vals = np.zeros(s.L, dtype=np.int64)
    for i in range(s.L - 1, 0, -1):
        if nzone or z >= zone:
            last = prng >> 8
            diff = 0
            while abs(diff) < 42:
                prng = (prng * 2053 + 13849) & 0xFFFF
                diff = (prng >> 8) - last
            if nzone:
                s.aux0 = prng
                nzone = False
            z = 0
        vals[i] = prng >> 8
        z += 1
    s.buf[1:] = s.wheel(vals[1:]) if s.L > 1 else s.buf[1:]
    s.aux1 = it


@fx(61, "Stream 2", "@!;;")
def stream2(s, now):
    if s.call == 0:
        s.data["c"] = [F.random8(), F.random8(), F.random8()]
        s.aux0 = F.random16()
    it = now // (25 + 3 * (255 - s.sx))
    color = list(s.data["c"])
    rng = F.Rand16(s.aux0)
    for i in range(s.L - 1, 0, -1):
        color = [color[k] if rng.r8(6) != 0 else rng.r8() for k in range(3)]
        s.buf[i] = (color[0], color[1], color[2], 0)
        if i == s.L - 1 and s.aux1 != (it & 0xFFFF):
            s.data["c"] = color
            s.aux0 = rng.s
    s.aux1 = it & 0xFFFF


# ============================================================================ scanners / comets

def _larson(s, now, dual):
    L = s.L
    speed = FRAMETIME * (96 + ((2 - 96) * s.sx) // 255)
    pixels = L // speed if speed else L
    s.fade_out(255 - s.ix)
    if s.step > now:
        return
    index = s.aux1 + pixels
    if pixels == 0:
        frames = speed // L
        if s.step < frames:
            s.step += 1
            return
        s.step = 0
        index += 1
    if index > L:
        s.aux0 = 0 if s.aux0 else 1
        s.aux1 = 0
        s.step = now + s.c1 * 25 if (s.aux0 or s.o2) else 0
    else:
        for i in range(s.aux1, index):
            j = i if s.aux0 else L - 1 - i
            c = s.cfp(j, True, SOLID_WRAP, 0)
            s.setp(j, c)
            if dual:
                s.setp(L - 1 - j, s.col(2) if has(s.colors[2]) else c)
        s.aux1 = index


@fx(40, "Scanner", "@!,Trail,Delay,,,Dual,Bi-delay;!,!,!;!;;m12=0,c1=0")
def scanner(s, now):
    _larson(s, now, s.o1)


@fx(60, "Scanner Dual", "@!,Trail,Delay,,,,Bi-delay;!,!,!;!;;m12=0,c1=0")
def scanner_dual(s, now):
    _larson(s, now, True)


@fx(41, "Lighthouse", "@!,Fade rate;!,!;!")
def lighthouse(s, now):
    L = s.L
    counter = (now * ((s.sx >> 2) + 1)) & 0xFFFF
    index = (counter * L) >> 16
    if s.call == 0:
        s.aux0 = index
    s.fade_out(s.ix)
    s.setp(index, s.cfp(index, True, SOLID_WRAP, 0))
    if index > s.aux0:
        j = np.arange(s.aux0, index)
        s.buf[j] = s.cfp(j, True, SOLID_WRAP, 0)
    elif index < s.aux0 and index < 10:
        j = np.arange(0, index)
        if len(j):
            s.buf[j] = s.cfp(j, True, SOLID_WRAP, 0)
    s.aux0 = index


@fx(59, "Multi Comet", "@!,Fade;!,!,!;!")
def multi_comet(s, now):
    it = now // (10 + (255 - s.sx))
    if s.step == it:
        return
    d = s.data
    if "c" not in d:
        d["c"] = [s.L] * 8
    s.fade_out(s.ix)
    for k in range(8):
        if d["c"][k] < s.L:
            i = d["c"][k]
            if has(s.colors[2]):
                s.setp(i, s.cfp(i, True, SOLID_WRAP, 0) if k % 2 else s.col(2))
            else:
                s.setp(i, s.cfp(i, True, SOLID_WRAP, 0))
            d["c"][k] += 1
        elif F.random16(s.L) == 0:
            d["c"][k] = 0
    s.step = it


# ============================================================================ fireworks / rain

def _fireworks(s, now):
    L = s.L
    if s.call == 0:
        s.aux0 = 0xFFFF
        s.aux1 = 0xFFFF
    s.fade_out(128)
    v1, v2 = s.aux0 < L, s.aux1 < L
    sv1 = s.get(s.aux0) if v1 else None
    sv2 = s.get(s.aux1) if v2 else None
    if not s.step:
        s.blur(16)
    if v1:
        s.setp(s.aux0, sv1)
    if v2:
        s.setp(s.aux1, sv2)
    for _ in range(max(1, L // 20)):
        if F.random8(129 - (s.ix >> 1)) == 0:
            index = F.random16(L)
            s.setp(index, s.cfp(F.random8(), False, False, 0))
            s.aux1 = s.aux0
            s.aux0 = index


@fx(42, "Fireworks", "@,Frequency;!,!;!;12;ix=192,pal=11")
def fireworks(s, now):
    _fireworks(s, now)


@fx(43, "Rain", "@!,Spawning rate;!,!;!;12;ix=128,pal=0")
def rain(s, now):
    L = s.L
    s.step += FRAMETIME
    if s.call and s.step > 5 + (50 * (255 - s.sx)) // L:
        s.step = 1
        s.buf[:] = np.roll(s.buf, -1, axis=0)
        s.aux0 = (s.aux0 + 1) & 0xFFFF
        s.aux1 = (s.aux1 + 1) & 0xFFFF
        if s.aux0 == 0:
            s.aux0 = 0xFFFF
        if s.aux1 == 0:
            s.aux0 = 0xFFFF
        if s.aux0 >= L:
            s.aux0 = 0
        if s.aux1 >= L:
            s.aux1 = 0
    _fireworks(s, now)


@fx(44, "Tetrix", "@!,Width,,,,One color;!,!;!;;sx=0,ix=0,pal=11,m12=1")
def tetrix(s, now):
    L = s.L
    d = s.data
    if s.call == 0:
        d.update(stack=0, step=now + 2000, col=0, pos=0.0, speed=0.0, brick=0)
    if d["step"] == 0:
        sp = s.sx if s.sx else F.random8(1, 255)
        sp = 5000 + (sp - 1) * (250 - 5000) // 254
        d["speed"] = L * FRAMETIME / sp
        d["pos"] = float(L)
        if not s.o1:
            d["col"] = F.random8(0, 15) << 4
        d["step"] = 1
        d["brick"] = ((s.ix >> 5) + 1 if s.ix else F.random8(1, 5)) * (1 + (L >> 6))
    if d["step"] == 1 and (F.random8() >> 6):
        d["step"] = 2
    if d["step"] == 2:
        if d["pos"] > d["stack"]:
            d["pos"] -= d["speed"]
            if int(d["pos"]) < int(d["stack"]):
                d["pos"] = d["stack"]
            p = int(d["pos"])
            col = s.cfp(d["col"], False, False, 0)
            for i in range(max(0, p), L):
                s.buf[i] = col if i < p + d["brick"] else s.col(1)
        else:
            d["step"] = 0
            d["stack"] += d["brick"]
            if d["stack"] >= L:
                d["step"] = now + 2000
    if d["step"] > 2:
        d["brick"] = 0
        if d["step"] > now:
            s.buf[:] = cb(s.buf, s.col(1), 25)
        else:
            d["stack"] = 0
            d["step"] = 0
            if s.o1:
                d["col"] = (d["col"] + 8) & 255


@fx(45, "Fire Flicker", "@!,!;!;!;01")
def fire_flicker(s, now):
    it = now // (40 + (255 - s.sx))
    if s.step == it:
        return
    c = s.col(0)
    lum = int(max(c)) if s.pal == 0 else 255
    lum //= (((256 - s.ix) // 16) + 1)
    flick = np.random.randint(0, max(1, lum), s.L) if lum > 0 else np.zeros(s.L, dtype=np.int64)
    if s.pal == 0:
        s.buf[:] = np.maximum(c[None, :] - flick[:, None], 0)
    else:
        s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 0, 255 - flick)
    s.step = it


def _gradient(s, now, loading):
    L = s.L
    counter = (now * ((s.sx >> 2) + 1)) & 0xFFFF
    pp = (counter * L) >> 16
    if s.call == 0:
        pp = 0
    brd = max(1.0, float(s.ix if loading else s.ix / 2))
    i = s.idx
    p1, p2 = pp - L, pp + L
    if loading:
        val = np.abs(np.where(i > pp, p2, pp) - i).astype(np.float64)
    else:
        val = np.minimum(np.abs(pp - i), np.minimum(np.abs(p1 - i), np.abs(p2 - i))).astype(np.float64)
    val = np.where(brd > val, val / brd * 255, 255)
    s.buf[:] = cb(s.col(0), s.cfp(i, True, SOLID_WRAP, 1), val)


@fx(46, "Gradient", "@!,Spread;!,!;!;;ix=16")
def gradient(s, now):
    _gradient(s, now, False)


@fx(47, "Loading", "@!,Fade;!,!;!;;ix=16")
def loading(s, now):
    _gradient(s, now, True)


# ============================================================================ fairy lights

@fx(49, "Fairy", "@!,# of flashers;!,!;!")
def fairy(s, now):
    d = s.data
    L = s.L
    if d.get("L") != L:
        rng = F.Rand16(5100 + s.id)
        d["L"] = L
        d["base"] = np.array([rng.r16() >> 8 for _ in range(L)])
        d["phase"] = np.random.randint(0, 65536, L)
        d["period"] = np.random.randint(1500, 4000, L)
    s.buf[:] = s.cfp(d["base"], False, False, 0)
    nflash = (s.ix * L) // 255
    if nflash:
        rate = 1 + s.sx / 64.0
        t = (now * rate + d["phase"][:nflash]) % d["period"][:nflash] / d["period"][:nflash]
        bri = np.clip(np.abs(np.sin(t * math.pi)) * 1.6, 0, 1)
        sel = np.unique((np.arange(nflash) * L) // max(1, nflash))
        bri = bri[:len(sel)]
        s.buf[sel] = cb(s.col(1), s.buf[sel], bri * 255)


@fx(51, "Fairytwinkle", "@!,!;!,!;!;;m12=0")
def fairytwinkle(s, now):
    d = s.data
    L = s.L
    if d.get("L") != L:
        rng = F.Rand16(5100 + s.id)
        d["L"] = L
        d["base"] = np.array([rng.r16() >> 8 for _ in range(L)])
        d["phase"] = np.random.randint(0, 1 << 20, L)
        d["period"] = np.random.randint(2000, 6000, L)
        d["active"] = np.random.randint(0, 256, L)
    rate = 0.25 + s.sx / 96.0
    t = ((now * rate + d["phase"]) % d["period"]) / d["period"]
    bri = np.clip(np.sin(t * math.pi) * 1.4, 0, 1) * 255
    bri = np.where(d["active"] <= s.ix, bri, 0)
    s.buf[:] = cb(s.col(1), s.cfp(d["base"], False, False, 0), bri)


# ============================================================================ dots & wipes

@fx(50, "Two Dots", "@!,Dot size,,,,,Overlay;1,2,Bg;!")
def two_dots(s, now):
    L = s.L
    delay = 1 + (FRAMETIME << 3) // L
    div = (delay << 4) + ((delay - (delay << 4)) * s.sx) // 255
    offset = (now // max(1, div)) % L
    width = max(1, (L * (s.ix + 1)) >> 9)
    if not s.o2:
        s.fill(s.col(2))
    c1 = s.col(0)
    c2 = c1 if s.colors[1] == s.colors[2] else s.col(1)
    i = np.arange(width)
    s.buf[(offset + i) % L] = c1
    s.buf[(offset + i + (L >> 1)) % L] = c2


@fx(55, "Tri Wipe", "@!;1,2,3;!")
def tri_wipe(s, now):
    L = s.L
    cycle = 1000 + (255 - s.sx) * 200
    prog = ((now % cycle) * 65535) // cycle
    led = (prog * L * 3) >> 16
    s.buf[:] = s.cfp(s.idx, True, SOLID_WRAP, 2)
    i = s.idx
    if led < L:
        s.buf[:] = np.where((i > led)[:, None], s.col(0), s.col(1))
    elif led < L * 2:
        s.set_range(led - L + 1, L, s.col(1))
    else:
        s.set_range(0, led - L * 2 + 1, s.col(0))


@fx(56, "Tri Fade", "@!;1,2,3;!")
def tri_fade(s, now):
    counter = (now * ((s.sx >> 3) + 1)) & 0xFFFF
    prog = (counter * 768) >> 16
    stp = prog & 255
    if prog < 256:
        s.fill(cb(s.col(0), s.col(1), stp))
    elif prog < 512:
        s.buf[:] = cb(s.col(1), s.cfp(s.idx, True, SOLID_WRAP, 2), stp)
    else:
        s.buf[:] = cb(s.cfp(s.idx, True, SOLID_WRAP, 2), s.col(0), stp)


@fx(57, "Lightning", "@!,!,,,,,Overlay;!,!;!")
def lightning(s, now):
    L = s.L
    start = F.random16(L)
    ln = 1 + F.random16(L - start)
    bri = 255 // F.random8(1, 3)
    if s.aux1 == 0:
        s.aux1 = F.random8(4, 4 + s.ix // 20) * 2
        bri = 52
        s.aux0 = 200
    if not s.o2:
        s.fill(s.col(1))
    if s.aux1 > 3 and not (s.aux1 & 1):
        j = np.arange(start, start + ln)
        s.buf[j] = s.cfp(j, True, SOLID_WRAP, 0, bri)
        s.aux1 -= 1
        s.step = now
    elif now - s.step > s.aux0:
        s.aux1 -= 1
        if s.aux1 < 2:
            s.aux1 = 0
        s.aux0 = 50 + F.random8(100)
        if s.aux1 == 2:
            s.aux0 = F.random8(255 - s.sx) * 100
        s.step = now


@fx(58, "ICU", "@!,!,,,,,Overlay;!,!;!")
def icu(s, now):
    L = s.L
    dest = s.step & 0xFFFF
    space = (s.ix >> 3) + 2
    if not s.o2:
        s.fill(s.col(1))
    span = max(1, L - L // space)
    col = s.cfp((dest * 255) // span, False, False, 0)
    s.setp(dest, col)
    s.setp(dest + L // space, col)
    if s.aux0 == dest:
        if F.random8(6) == 0:
            s.setp(dest, s.col(1))
            s.setp(dest + L // space, s.col(1))
            return 200
        s.aux0 = F.random16(span)
        return 1000 + F.random16(2000)
    if s.aux0 > s.step:
        s.step += 1
        dest += 1
    elif s.aux0 < s.step:
        s.step -= 1
        dest -= 1
    s.setp(dest, col)
    s.setp(dest + L // space, col)
    return 5 + (50 * (255 - s.sx)) // L
