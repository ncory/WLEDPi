"""More WLED effects (IDs 62 and up). Registered into effects.EFFECTS."""
import math
import random

import numpy as np

from . import fastled as F
from .effects import fx, has, BLACK, WHITE, ULTRAWHITE
from .palettes import tables_from_16, _hex16
from .segment import FRAMETIME, SOLID_WRAP, color_blend as cb


@fx(62, "Oscillate", "@!,!;!,!,!;!")
def oscillate(s, now):
    L = s.L
    d = s.data
    if s.call == 0 or "o" not in d:
        d["o"] = [[L // 4, 1, 1], [L // 4 * 3, 1, 2], [L // 4 * 2, -1, 1]]   # pos, dir, speed
    it = now // (20 + 2 * (255 - s.sx))
    size = L // (3 + s.ix // 8)
    for o in d["o"]:
        if it != s.step:
            o[0] += o[1] * o[2]
        if o[1] == -1 and o[0] <= 0:
            o[0], o[1] = 0, 1
            o[2] = F.random8(2, 4) if s.sx > 100 else F.random8(1, 3)
        if o[1] == 1 and o[0] >= L - 1:
            o[0], o[1] = L - 1, -1
            o[2] = F.random8(2, 4) if s.sx > 100 else F.random8(1, 3)
    out = np.zeros((L, 4), np.float32)
    lit = np.zeros(L, bool)
    for j, o in enumerate(d["o"]):
        m = (s.idx >= o[0] - size) & (s.idx <= o[0] + size)
        c = s.col(j)
        out[m & ~lit] = c
        out[m & lit] = cb(out[m & lit], c, 128)
        lit |= m
    s.buf[:] = out
    s.step = it


@fx(63, "Pride 2015", "@!;;")
def pride2015(s, now):
    duration = 10 + s.sx
    sat8 = F.beatsin88(87, 220, 250, now)
    brightdepth = F.beatsin88(341, 96, 224, now)
    bthetainc = F.beatsin88(203, 25 * 256, 40 * 256, now)
    msmult = F.beatsin88(147, 23, 60, now)
    hueinc = F.beatsin88(113, 1, 3000, now)
    pseudotime = (s.step + duration * msmult) & 0xFFFF
    hue16_0 = s.aux0
    s.aux0 = (s.aux0 + duration * F.beatsin88(400, 5, 9, now)) & 0xFFFF
    k = s.idx + 1
    hue8 = ((hue16_0 + k * hueinc) & 0xFFFF) >> 8
    btheta = (pseudotime + k * bthetainc) & 0xFFFF
    b16 = F.sin16(btheta) + 32768
    bri16 = (b16 * b16) >> 16
    bri8 = ((bri16 * brightdepth) >> 16) + (255 - brightdepth)
    rgb = F.hsv_rainbow(hue8, sat8, bri8)
    s.buf[:, :3] = cb(s.buf[:, :3], rgb, 64)
    s.buf[:, 3] = 0
    s.step = pseudotime


@fx(64, "Juggle", "@!,Trail;;!;;sx=64,ix=128")
def juggle(s, now):
    s.fade_black(192 - (3 * s.ix // 4))
    dothue = 0
    for i in range(8):
        index = F.beatsin88((16 + s.sx) * (i + 7), 0, s.L - 1, now)
        if s.pal == 0:
            c = F.hsv_rainbow(dothue, 220, 255)[0]
        else:
            c = s.cfp(dothue, False, True, 255)[:3]
        s.buf[index, :3] = np.maximum(s.buf[index, :3], c)
        dothue += 32


def _palette_bg(s, now):
    counter = 0
    if s.sx:
        counter = ((now * ((s.sx >> 3) + 1)) & 0xFFFF) >> 8
    idx = ((s.idx * 255) // max(1, s.L) - counter) & 255
    s.buf[:] = s.cfp(idx, False, True, 255)


@fx(65, "Palette", "@Cycle speed;;!;12;c3=0,o2=0")
def palette(s, now):
    _palette_bg(s, now)


@fx(66, "Fire 2012", "@Cooling,Spark rate,,2D Blur,Boost;;!;1;pal=35,sx=64,ix=160,m12=1,c2=128")
def fire2012(s, now):
    L = s.L
    d = s.data
    if d.get("h") is None or len(d["h"]) != L:
        d["h"] = np.zeros(L, dtype=np.int64)
    heat = d["h"]
    it = now >> 5
    ign = max(3, L // 10)
    if it != s.step:
        cool = np.random.randint(0, max(1, (((20 + s.sx // 3) * 16) // L) + 2), L)
    else:
        cool = np.random.randint(0, 4, L)
    mint = np.where(s.idx < ign, (ign - s.idx) // 4 + 16, 0)
    heat[:] = np.maximum(np.maximum(heat - cool, 0), mint)
    if it != s.step:
        if L > 2:
            heat[2:] = (heat[1:-1] + (heat[:-2] << 1)) // 3
        if F.random8() <= s.ix:
            y = min(F.random8(ign), L - 1)
            boost = (17 + s.c3) * (ign - y // 2) // ign
            heat[y] = min(255, heat[y] + F.random8(96 + 2 * boost, 207 + boost))
        s.step = it
    s.buf[:] = s.cfp(np.minimum(heat, 240), False, True, 255, blend=False)


@fx(67, "Colorwaves", "@!,Hue;!;!")
def colorwaves(s, now):
    duration = 10 + s.sx
    brightdepth = F.beatsin88(341, 96, 224, now)
    bthetainc = F.beatsin88(203, 25 * 256, 40 * 256, now)
    msmult = F.beatsin88(147, 23, 60, now)
    hueinc = F.beatsin88(113, 60, 300, now) * s.ix * 10 // 255
    pseudotime = (s.step + duration * msmult) & 0xFFFF
    hue0 = s.aux0
    s.aux0 = (s.aux0 + duration * F.beatsin88(400, 5, 9, now)) & 0xFFFF
    k = s.idx + 1
    hue16 = (hue0 + k * hueinc) & 0xFFFF
    h128 = hue16 >> 7
    hue8 = np.where(h128 & 0x100, 255 - ((h128 >> 1) & 255), (h128 >> 1) & 255)
    btheta = (pseudotime + k * bthetainc) & 0xFFFF
    b16 = F.sin16(btheta) + 32768
    bri16 = (b16 * b16) >> 16
    bri8 = ((bri16 * brightdepth) >> 16) + (255 - brightdepth)
    s.buf[:] = cb(s.buf, s.cfp(hue8, False, SOLID_WRAP, 0, bri8), 128)
    s.step = pseudotime


@fx(68, "Bpm", "@!;!;!;;sx=64")
def bpm(s, now):
    stp = (now // 20) & 255
    beat = F.beatsin8(s.sx, 64, 255, now)
    s.buf[:] = s.cfp((stp + s.idx * 2) & 255, False, SOLID_WRAP, 0, (beat - stp + s.idx * 10) & 255)


@fx(69, "Fill Noise", "@!;!;!;;pal=9")
def fill_noise(s, now):
    if s.call == 0:
        s.step = F.random16(12345)
    L = s.L
    idx = F.inoise8(s.idx * L, s.step + s.idx * L)
    s.buf[:] = s.cfp(idx, False, SOLID_WRAP, 0)
    s.step += F.beatsin8(s.sx, 1, 6, now)


@fx(70, "Noise 1", "@!;!;!;;pal=20")
def noise1(s, now):
    s.step += 1 + s.sx // 16
    shift_x = F.beatsin8(11, 0, 255, now)
    shift_y = s.step // 42
    rx = (s.idx + shift_x) * 320
    ry = (s.idx + shift_y) * 320
    n = F.inoise16(rx, ry, s.step) >> 8
    s.buf[:] = s.cfp(F.sin8(n * 3), False, SOLID_WRAP, 0)


@fx(71, "Noise 2", "@!;!;!;;pal=43")
def noise2(s, now):
    s.step += 1 + (s.sx >> 1)
    rx = (s.idx + (s.step >> 6)) * 1000
    n = F.inoise16(rx, 0, 4223) >> 8
    s.buf[:] = s.cfp(F.sin8(n * 3), False, SOLID_WRAP, 0, n)


@fx(72, "Noise 3", "@!;!;!;;pal=35")
def noise3(s, now):
    s.step += 1 + s.sx
    rx = (s.idx + 4223) * 800
    ry = (s.idx + 1234) * 800
    n = F.inoise16(rx, ry, s.step * 8) >> 8
    s.buf[:] = s.cfp(F.sin8(n * 3), False, SOLID_WRAP, 0, n)


@fx(73, "Noise 4", "@!;!;!;;pal=26")
def noise4(s, now):
    stp = (now * s.sx) >> 7
    idx = F.inoise16(s.idx << 12, stp)
    s.buf[:] = s.cfp(idx & 255, False, SOLID_WRAP, 0)


@fx(74, "Colortwinkles", "@Fade speed,Spawn speed;;!;;m12=0")
def colortwinkles(s, now):
    d = s.data
    L = s.L
    if d.get("up") is None or len(d["up"]) != L:
        d["up"] = np.zeros(L, bool)
    up = d["up"]
    gb = s.strip.bri_now()
    fu = 8 + (s.sx >> 2) if gb > 28 else 68 - gb
    fdn = 8 + (s.sx >> 3) if gb > 28 else 68 - gb
    c = s.buf[:, :3]
    inc = np.floor(c * (fu + 1) / 256) + (c > 0)
    cu = np.minimum(c + inc, 255)
    stuck = np.all(cu == c, axis=1) & np.any(c > 0, axis=1)
    cu[stuck] = np.minimum(cu[stuck] * 2, 255)
    reached = np.any(cu >= 255, axis=1)
    cdn = np.floor(c * (256 - fdn) / 256)
    s.buf[:, :3] = np.where(up[:, None], cu, cdn)
    s.buf[:, 3] = 0
    up &= ~reached
    tb, tn = s._tables()
    for _ in range(L // 50 + 1):
        if F.random8() <= s.ix:
            for _t in range(5):
                i = F.random16(L)
                if not np.any(s.buf[i]):
                    s.buf[i, :3] = np.floor(tn[F.random8()] * 65 / 256)
                    up[i] = True
                    break


@fx(75, "Lake", "@!;Fx;!")
def lake(s, now):
    sp = s.sx // 10
    w1 = F.beatsin8(sp + 2, -64, 64, now)
    w2 = F.beatsin8(sp + 1, -64, 64, now)
    w3 = F.beatsin8(sp + 2, 0, 80, now)
    index = F.cos8((s.idx * 15 + w1) & 255) // 2 + F.cubicwave8((s.idx * 23 + w2) & 255) // 2
    lum = np.where(index > w3, index - w3, 0)
    s.buf[:] = s.cfp(index, False, False, 0, lum)


@fx(76, "Meteor", "@!,Trail,,,,Gradient,,Smooth;;!;1")
def meteor(s, now):
    if s.o3:
        return meteor_smooth(s, now)
    L = s.L
    d = s.data
    if d.get("t") is None or len(d["t"]) != L:
        d["t"] = np.zeros(L, dtype=np.int64)
    tr = d["t"]
    size = 1 + L // 20
    counter = (now * ((s.sx >> 2) + 8)) & 0xFFFF
    start = (counter * L) >> 16
    mx = 255 if (s.o1 and s.pal != 5) else 240
    m = np.random.randint(0, 256, L) <= 255 - s.ix
    decay = 128 + np.random.randint(0, 127, L)
    tr[m] = (tr[m] * (decay[m] + 1)) >> 8
    if s.o1:
        cols = s.cfp(s.idx, True, False, 0, tr)
    else:
        cols = s.cfp(tr, False, True, 255)
    s.buf[m] = cols[m]
    j = (start + np.arange(size)) % L
    tr[j] = mx
    s.buf[j] = s.cfp(j, True, False, 0, 255) if s.o1 else s.cfp(tr[j], False, True, 255)


@fx(77, "Meteor Smooth", "@!,Trail,,,,Gradient;;!;1")
def meteor_smooth(s, now):
    L = s.L
    d = s.data
    if d.get("t") is None or len(d["t"]) != L:
        d["t"] = np.zeros(L, dtype=np.int64)
    tr = d["t"]
    size = 1 + L // 20
    start = ((s.step >> 6 & 0xFF) * (L - 1)) // 255
    m = (tr != 0) & (np.random.randint(0, 256, L) <= 255 - s.ix)
    tr[m] = tr[m] + 3 - np.random.randint(0, 12, int(m.sum()))
    tr[tr > 245] = 0
    tr[tr > 240] = 240
    s.buf[m] = s.cfp(tr[m], False, True, 255)
    j = (start + np.arange(size)) % L
    s.buf[j] = cb(s.buf[j], s.cfp(240, False, True, 255), 48)
    tr[j] = 240
    s.step += s.sx + 1


@fx(78, "Railway", "@!,Smoothness;1,2;!;;pal=3")
def railway(s, now):
    dur = (256 - s.sx) * 40
    ramp = (dur * s.ix) >> 8
    if s.step > dur:
        s.step = 0
        s.aux0 = 0 if s.aux0 else 1
    pos = 255
    if ramp:
        p0 = (s.step * 255) // ramp
        if p0 < 255:
            pos = p0
    if s.aux0:
        pos = 255 - pos
    a = s.cfp(255 - pos, False, False, 255)
    b = s.cfp(pos, False, False, 255)
    s.buf[0::2] = a
    s.buf[1::2] = b
    s.step += FRAMETIME


def _ripple_base(s, now):
    L = s.L
    maxr = min(1 + (L >> 2), 100)
    d = s.data
    if "r" not in d or len(d["r"]) != maxr:
        d["r"] = [[0, 0, 0] for _ in range(maxr)]   # state, color, pos
    idxs, cols, mags = [], [], []
    decay = (s.sx >> 4) + 1
    for r in d["r"]:
        st = r[0]
        if st:
            prop = ((st // decay - 1) * (s.sx + 1))
            propI = prop >> 8
            propF = prop & 0xFF
            amp = F.triwave8((st - 1) * 8) if st < 17 else 255 - (st - 17) * 253 // 238
            left = r[2] - propI - 1
            for k in range(4):
                v = left + k
                mag = (F.cubicwave8((propF >> 2) + k * 64) * (amp + 1)) >> 8
                w = left + propI * 2 + 3 - k
                idxs += [v, w]
                cols += [r[1], r[1]]
                mags += [mag, mag]
            st += decay
            r[0] = 0 if st > 254 else st
        elif F.random16(5100 + 10000) <= s.ix:
            r[0] = 1
            r[2] = F.random16(L)
            r[1] = F.random8()
    if idxs:
        idx = np.array(idxs)
        ok = (idx >= 0) & (idx < L)
        if ok.any():
            idx = idx[ok]
            col = s.cfp(np.array(cols)[ok], False, False, 255)
            mag = np.array(mags)[ok]
            s.buf[idx] = cb(s.buf[idx], col, mag)


@fx(79, "Ripple", "@!,Wave #,,,,,Overlay;,!;!;12")
def ripple(s, now):
    if not s.o2:
        s.fill(s.col(1))
    else:
        s.fade_out(250)
    _ripple_base(s, now)


@fx(99, "Ripple Rainbow", "@!,Wave #;;!;12")
def ripple_rainbow(s, now):
    if s.call == 0:
        s.aux0 = F.random8()
        s.aux1 = F.random8()
    if s.aux0 == s.aux1:
        s.aux1 = F.random8()
    elif s.aux1 > s.aux0:
        s.aux0 += 1
    else:
        s.aux0 -= 1
    s.fill(cb(s.wheel(s.aux0), BLACK, 235))
    _ripple_base(s, now)


def _twinklefox(s, now, cat):
    d = s.data
    L = s.L
    if d.get("L") != L:
        prng = 11337
        offs, mult, salt = [], [], []
        for _ in range(L):
            prng = (prng * 2053 + 1384) & 0xFFFF
            offs.append(prng)
            prng = (prng * 2053 + 1384) & 0xFFFF
            mult.append(((((prng & 0xFF) >> 4) + (prng & 0x0F)) & 0x0F) + 0x08)
            salt.append(prng >> 8)
        d.update(L=L, offs=np.array(offs, dtype=np.int64), mult=np.array(mult, dtype=np.int64),
                 salt=np.array(salt, dtype=np.int64))
    div = 3 + ((255 - s.sx) >> 3) if s.sx > 100 else 22 + ((100 - s.sx) >> 1)
    bg = s.col(1)[:3]
    bgl = bg.sum() / 3
    bg = np.floor(bg * ((16 if bgl > 64 else 64 if bgl > 16 else 86) / 256.0))
    bgb = bg.sum() / 3
    clock = ((now * d["mult"]) >> 3) + d["offs"]
    ticks = (clock // div) & 0xFFFF
    fast = ticks & 0xFF
    slow = ((ticks >> 8) + d["salt"]) & 0xFFFF
    slow = (slow + F.sin8(slow)) & 0xFFFF
    slow = (slow * 2053 + 1384) & 0xFFFF
    slow8 = ((slow & 0xFF) + (slow >> 8)) & 0xFF
    density = (s.ix >> 5) + 1
    lit = ((slow8 & 0x0E) // 2) < density
    if cat:
        bright = 255 - fast
    else:
        bright = np.where(fast < 86, fast * 3, 255 - ((fast - 86) + (fast - 86) // 2))
    bright = np.where(lit, bright, 0)
    hue = (slow8 - d["salt"]) & 255
    c = s.cfp(hue, False, True, 255, bright, blend=False)[:, :3]
    if not s.o1:
        cool = np.where(fast >= 128, (fast - 128) >> 4, 0)
        c[:, 1] = np.maximum(c[:, 1] - cool, 0)
        c[:, 2] = np.maximum(c[:, 2] - cool * 2, 0)
    c[bright <= 0] = 0
    delta = c.sum(axis=1) / 3 - bgb
    bg_black = not np.any(bg)
    out = np.where(((delta >= 32) | bg_black)[:, None], c,
                   np.where((delta > 0)[:, None], cb(bg, c, np.clip(delta * 8, 0, 255)), bg))
    s.buf[:, :3] = out
    s.buf[:, 3] = 0


@fx(80, "Twinklefox", "@!,Twinkle rate,,,,Cool;!,!;!")
def twinklefox(s, now):
    _twinklefox(s, now, False)


@fx(81, "Twinklecat", "@!,Twinkle rate,,,,Cool;!,!;!")
def twinklecat(s, now):
    _twinklefox(s, now, True)


@fx(82, "Halloween Eyes", "@Duration,Eye fade time,,,,,Overlay;!,!;!;12")
def halloween_eyes(s, now):
    L = s.L
    d = s.data
    if not s.o2:
        s.fill(s.col(1))
    if L < 4:
        return
    st = d.get("state", 0)
    if st == 0:
        w = max(1, L // 40)
        gap = max(2, min(L - 2 * w, L // 12 + 1))
        d.update(w=w, gap=gap, pos=F.random16(max(1, L - 2 * w - gap)),
                 col=s.cfp(F.random8(), False, False, 0), t0=now,
                 dur=800 + s.sx * 25 + F.random16(1500), fade=150 + s.ix * 4,
                 blink=now + 400 + F.random16(2500), state=1)
    elif st == 1 and now - d["t0"] > d["dur"]:
        d.update(state=2, t0=now)
    elif st == 2 and now - d["t0"] > d["fade"]:
        d.update(state=3, t0=now, pause=400 + F.random16(max(1, 4000 - s.sx * 12)))
    elif st == 3 and now - d["t0"] > d["pause"]:
        d["state"] = 0
    st = d["state"]
    if st in (1, 2):
        bri = 255 if st == 1 else max(0, 255 - (now - d["t0"]) * 255 // d["fade"])
        blinking = st == 1 and d["blink"] <= now < d["blink"] + 160
        if now >= d["blink"] + 160:
            d["blink"] = now + 800 + F.random16(3000)
        if not blinking:
            c = cb(s.col(1), d["col"], bri)
            p, w, g = d["pos"], d["w"], d["gap"]
            s.set_range(p, p + w, c)
            s.set_range(p + w + g, p + 2 * w + g, c)


@fx(83, "Solid Pattern", "@Fg size,Bg size;Fg,!;!;;pal=0")
def solid_pattern(s, now):
    lit, unlit = 1 + s.sx, 1 + s.ix
    on = (s.idx % (lit + unlit)) < lit
    s.buf[:] = np.where(on[:, None], s.cfp(s.idx, True, SOLID_WRAP, 0), s.col(1))


@fx(84, "Solid Pattern Tri", "@,Size;1,2,3;;;pal=0")
def solid_pattern_tri(s, now):
    size = (s.ix >> 5) + 1
    k = (s.idx // size) % 3
    s.buf[:] = np.array([s.col(0), s.col(1), s.col(2)], np.float32)[k]


def _spots(s, now, threshold):
    L = s.L
    if not s.o2:
        s.fill(s.col(1))
    zones = 1 + ((s.ix * (L >> 2)) >> 8)
    zlen = L // zones
    if zlen <= 0:
        return
    offset = (L - zones * zlen) >> 1
    i = np.arange(zlen)
    wave = F.triwave16((i * 0xFFFF) // zlen)
    m = wave > threshold
    if not np.any(m):
        return
    sv = ((wave[m] - threshold) * 255) // max(1, 0xFFFF - threshold)
    idx = (offset + np.arange(zones)[:, None] * zlen + i[m][None, :]).ravel()
    svv = np.tile(sv, zones)
    ok = idx < L
    idx, svv = idx[ok], svv[ok]
    s.buf[idx] = cb(s.cfp(idx, True, SOLID_WRAP, 0), s.col(1), 255 - svv)


@fx(85, "Spots", "@!,Width,,,,,Overlay;!,!;!")
def spots(s, now):
    _spots(s, now, (255 - s.sx) << 8)


@fx(86, "Spots Fade", "@!,Width,,,,,Overlay;!,!;!")
def spots_fade(s, now):
    t = F.triwave16((now * ((s.sx >> 2) + 8)) & 0xFFFF)
    _spots(s, now, (t >> 1) + (t >> 2))


def _glitter(s, intensity, col):
    if intensity > F.random8():
        s.setp(F.random16(s.L), col)


@fx(87, "Glitter", "@!,!,,,,,Overlay;,,Glitter color;!;;pal=11,m12=0")
def glitter(s, now):
    if not s.o2:
        _palette_bg(s, now)
    _glitter(s, s.ix, s.col(2) if has(s.colors[2]) else ULTRAWHITE)


@fx(103, "Solid Glitter", "@,!;Bg,,Glitter color;;;m12=0")
def solid_glitter(s, now):
    s.fill(s.col(0))
    _glitter(s, s.ix, s.col(2) if has(s.colors[2]) else ULTRAWHITE)


def _candle(s, now, multi):
    L = s.L
    d = s.data
    rnd = s.ix >> 1
    sf = 1 if s.sx > 252 else 2 if s.sx > 99 else 3 if s.sx > 49 else 4
    n = L if multi else 1
    if d.get("n") != n:
        d.update(n=n, s=np.full(n, 128), t=130 + np.random.randint(0, 4, n), f=np.ones(n, dtype=np.int64))
    sv, tg, fs = d["s"], d["t"], d["f"]
    upm = tg > sv
    sv[:] = np.where(upm, np.minimum(sv + fs, 255), np.maximum(sv - fs, 0))
    newt = np.where(upm, sv >= tg, sv <= tg)
    k = int(newt.sum())
    if k:
        r = max(1, rnd)
        nt = np.random.randint(0, r, k) + np.random.randint(0, r, k)
        low = nt < (rnd >> 1)
        nt[low] = (rnd >> 1) + np.random.randint(0, r, int(low.sum()))
        nt = nt + (255 - s.ix)
        tg[newt] = np.clip(nt, 0, 255)
        fs[newt] = np.maximum(1, np.abs(tg[newt] - sv[newt]) >> sf)
    pal = s.cfp(s.idx, True, SOLID_WRAP, 0)
    s.buf[:] = cb(s.col(1), pal, sv if multi else int(sv[0]))


@fx(88, "Candle", "@!,!;!,!;!;01;sx=96,ix=224,pal=0")
def candle(s, now):
    _candle(s, now, False)


@fx(102, "Candle Multi", "@!,!;!,!;!;;sx=96,ix=224,pal=0")
def candle_multi(s, now):
    _candle(s, now, True)


@fx(89, "Fireworks Starburst", "@Chance,Fragments,,,,,Overlay;,!;!;;pal=11,m12=0")
def starburst(s, now):
    L = s.L
    d = s.data
    n = 1 + (L >> 3)
    if d.get("n") != n:
        d.update(n=n, stars=[{"birth": 0} for _ in range(n)])
    for st in d["stars"]:
        if st["birth"] == 0 and F.random8(144 - (s.sx >> 1)) == 0:
            pos = F.random16(max(1, L - 1))
            mult = F.random8() / 255.0
            num = F.random8(3, 6 + (s.ix >> 5))
            st.update(color=s.wheel(F.random8()), pos=float(pos), vel=375.0 * F.random8() / 255.0 * mult,
                      birth=now, last=now, frag=[float(pos) if i < num else -1.0 for i in range(12)])
    if not s.o2:
        s.fill(s.col(1))
    for st in d["stars"]:
        if st["birth"] == 0:
            continue
        dt = (now - st["last"]) / 1000.0
        for i in range(12):
            if st["frag"][i] > 0:
                st["frag"][i] += st["vel"] * dt * (i >> 1) / 3.0
        st["last"] = now
        st["vel"] -= 3 * st["vel"] * dt
        c = st["color"]
        age = now - st["birth"]
        f = 0.0
        if age < 250:
            c = cb(WHITE, c, 254.5 * age / 250)
        elif age > 1750:
            f = 1.0
            st["birth"] = 0
            c = s.col(1)
        else:
            f = (age - 250) / 1500.0
            c = cb(c, s.col(1), 254.5 * f)
        size = (1.0 - f) * 2.0
        for idx in range(24):
            fr = st["frag"][idx >> 1]
            if fr > 0:
                loc = fr - (fr - st["pos"]) * 2 if idx & 1 else fr
                a, b = int(loc - size), int(loc + size)
                a = max(a, 0)
                if a == b:
                    b += 1
                s.set_range(a, min(b, L), c)


@fx(90, "Fireworks 1D", "@Gravity,Firing side;!,!;!;12;pal=11,ix=128")
def fireworks_1d(s, now):
    L = s.L
    d = s.data
    if s.call == 0:
        s.aux0 = 0
        s.fill(BLACK)
        d.update(fl={}, sp=[])
    s.fade_out(252)
    g = (-0.0004 - s.sx / 800000.0) * L
    fl = d["fl"]
    if s.aux0 < 2:
        if s.aux0 == 0:
            peak = ((75 + F.random8(180)) * (L - 1)) >> 8
            fl.update(pos=0.0, side=s.ix > F.random8(), vel=math.sqrt(max(0.0, -2.0 * g * peak)), col=255)
            s.aux0 = 1
        if fl["vel"] > 12 * g:
            p = int(fl["pos"])
            c = fl["col"]
            s.setp(L - p - 1 if fl["side"] else p, (c, c, c, 0))
            fl["pos"] = min(max(fl["pos"] + fl["vel"], 0), L - 1)
            fl["vel"] += g
            fl["col"] = max(0, c - 2)
        else:
            s.aux0 = 2
    elif s.aux0 < 4:
        nsp = int(min(max(fl["pos"] + F.random8(4), 4), 2 + (L >> 1)))
        if s.aux0 == 2:
            sp = []
            for _ in range(1, nsp):
                v = (F.random16(20001) / 10000.0 - 0.9) * (0.5 if L < 32 else 1)
                v *= fl["pos"] / L
                v *= -g * 50
                sp.append({"pos": fl["pos"], "vel": v, "col": 345, "ci": F.random8()})
            d["sp"] = sp
            d["dg"] = g / 2
            s.aux0 = 3
        sp = d["sp"]
        if sp and sp[0]["col"] > 4:
            base = None if s.pal else [float(x) for x in s.col(0)]
            side = fl.get("side")
            for k in sp:
                k["pos"] += k["vel"]
                k["vel"] += d["dg"]
                if k["col"] > 3:
                    k["col"] -= 4
                if 0 < k["pos"] < L:
                    prog = k["col"]
                    spc = base if base is not None else [float(x) for x in s.wheel(k["ci"])]
                    if prog > 300:
                        a = min(255, (prog - 300) * 5) / 255.0
                        c = [v + (255 - v) * a for v in spc[:3]] + [spc[3] * (1 - a)]
                    elif prog > 45:
                        a = (prog - 45) / 255.0
                        cool = (300 - prog) >> 5
                        c = [spc[0] * a, max(0.0, spc[1] * a - cool), max(0.0, spc[2] * a - cool * 2), spc[3] * a]
                    else:
                        c = (0, 0, 0, 0)
                    p = int(k["pos"])
                    s.buf[L - p - 1 if side else p] = c
            s.blur(16)
            d["dg"] *= 0.8
        else:
            s.aux0 = 6 + F.random8(10)
    else:
        s.aux0 -= 1
        if s.aux0 < 4:
            s.aux0 = 0


@fx(91, "Bouncing Balls", "@Gravity,# of balls,,,,,Overlay;!,!,!;!;1;m12=1")
def bouncing_balls(s, now):
    L = s.L
    d = s.data
    if not s.o2:
        s.fill(BLACK if has(s.colors[2]) else s.col(1))
    n = (s.ix * 15) // 255 + 1
    if "b" not in d:
        d["b"] = [{"t": now, "v": 0.0, "h": 0.0} for _ in range(16)]
    grav = -9.81
    for i in range(n):
        b = d["b"][i]
        tsec = (now - b["t"]) / ((255 - s.sx) // 64 + 1) / 1000.0
        b["h"] = (0.5 * grav * tsec + b["v"]) * tsec
        if b["h"] <= 0:
            b["h"] = 0.0
            b["v"] *= 0.9 - i / (n * n)
            b["t"] = now
            if b["v"] < 0.015:
                b["v"] = math.sqrt(-2.0 * grav) * F.random8(5, 11) / 10.0
        elif b["h"] > 1.0:
            continue
        if s.pal:
            c = s.wheel(i * (256 // max(n, 8)))
        elif has(s.colors[2]):
            c = s.col(i % 3)
        else:
            c = s.col(0)
        s.setp(int(round(b["h"] * (L - 1))), c)


def _sinelon(s, now, dual, rainbow=False):
    L = s.L
    s.fade_out(s.ix)
    pos = F.beatsin16(s.sx // 10, 0, L - 1, now)
    if s.call == 0:
        s.aux0 = pos
    c1 = s.cfp(pos, True, False, 0)
    c2 = s.col(2)
    if rainbow:
        c1 = s.wheel((pos & 0x07) * 32)
    s.setp(pos, c1)
    if dual:
        if not has(s.colors[2]):
            c2 = s.cfp(pos, True, False, 0)
        if rainbow:
            c2 = c1
        s.setp(L - 1 - pos, c2)
    if s.aux0 != pos:
        rng = range(s.aux0, pos) if s.aux0 < pos else range(s.aux0, pos, -1)
        for i in rng:
            s.setp(i, c1)
            if dual:
                s.setp(L - 1 - i, c2)
        s.aux0 = pos


@fx(92, "Sinelon", "@!,Trail;!,!,!;!")
def sinelon(s, now):
    _sinelon(s, now, False)


@fx(93, "Sinelon Dual", "@!,Trail;!,!,!;!")
def sinelon_dual(s, now):
    _sinelon(s, now, True)


@fx(94, "Sinelon Rainbow", "@!,Trail;,,!;!")
def sinelon_rainbow(s, now):
    _sinelon(s, now, s.o1, True)


@fx(95, "Popcorn", "@!,!,,,,,Overlay;!,!,!;!;;m12=1")
def popcorn(s, now):
    L = s.L
    d = s.data
    g = (-0.0001 - s.sx / 200000.0) * L
    if not s.o2:
        s.fill(s.col(1))
    n = max(1, s.ix * 21 // 255)
    if "k" not in d:
        d["k"] = [{"pos": -1.0, "vel": 0.0, "ci": 0} for _ in range(21)]
    for k in d["k"][:n]:
        if k["pos"] >= 0:
            k["pos"] += k["vel"]
            k["vel"] += g
        elif F.random8() < 2:
            k["pos"] = 0.01
            peak = ((128 + F.random8(128)) * (L - 1)) >> 8
            k["vel"] = math.sqrt(max(0.0, -2.0 * g * peak))
            if s.pal:
                k["ci"] = F.random8()
            else:
                c = F.random8(0, 3)
                if not has(s.colors[2]) or not has(s.colors[c]):
                    c = 0
                k["ci"] = c
        if k["pos"] >= 0:
            col = s.col(k["ci"]) if (not s.pal and k["ci"] < 3) else s.wheel(k["ci"])
            s.setp(int(k["pos"]), col)


@fx(96, "Drip", "@Gravity,# of drips,,,,,Overlay;!,!;!;;m12=1")
def drip(s, now):
    L = s.L
    d = s.data
    if not s.o2:
        s.fill(s.col(1))
    n = 1 + (s.ix >> 6)
    g = (-0.0005 - s.sx / 50000.0) * max(1, L - 1)
    src = 12
    if "d" not in d:
        d["d"] = [{"pos": 0.0, "vel": 0.0, "col": 0, "st": 0} for _ in range(4)]
    c0 = s.col(0)
    for dr in d["d"][:n]:
        if dr["st"] == 0:
            dr.update(pos=float(L - 1), vel=0.0, col=src, st=1)
        s.setp(L - 1, cb(BLACK, c0, src))
        if dr["st"] == 1:
            dr["col"] = min(dr["col"], 255)
            s.setp(int(dr["pos"]), cb(BLACK, c0, dr["col"]))
            dr["col"] += 1 + (s.sx * 5) // 255
            if F.random8() < dr["col"] / 10:
                dr["st"] = 2
                dr["col"] = 255
        if dr["st"] > 1:
            if dr["pos"] > 0:
                dr["pos"] = max(0.0, dr["pos"] + dr["vel"])
                dr["vel"] += g
                for i in range(1, 7 - dr["st"]):
                    p = min(max(int(dr["pos"]) + i, 0), L - 1)
                    s.setp(p, cb(BLACK, c0, dr["col"] // i))
                if dr["st"] > 2:
                    s.setp(0, cb(c0, BLACK, dr["col"]))
            elif dr["st"] > 2:
                dr["st"] = 0
                dr["col"] = src
            else:
                dr["vel"] = -dr["vel"] / 4
                dr["pos"] += dr["vel"]
                dr["col"] = src * 2
                dr["st"] = 5


@fx(97, "Plasma", "@Phase,!;!;!")
def plasma(s, now):
    if s.call == 0:
        s.aux0 = F.random8(0, 2)
    this_ph = F.beatsin8(6 + s.aux0, -64, 64, now)
    that_ph = F.beatsin8(7 + s.aux0, -64, 64, now)
    i = s.idx
    k = s.sx >> 5
    ci = F.cubicwave8((i * (2 + 3 * k) + this_ph) & 255) // 2 + F.cos8((i * (1 + 2 * k) + that_ph) & 255) // 2
    br = np.maximum(ci - F.beatsin8(7, 0, 128 - (s.ix >> 1), now), 0)
    s.buf[:] = s.cfp(ci, False, SOLID_WRAP, 0, br)


@fx(98, "Percent", "@,% of fill,,,,One color;!,!;!")
def percent(s, now):
    L = s.L
    pct = min(s.ix, 200)
    active = round(L * pct / 100.0) if pct < 100 else round(L * (200 - pct) / 100.0)
    size = 255 if s.sx == 255 else 1 + ((s.sx * L) >> 11)
    i = s.idx
    if pct <= 100:
        fg = np.broadcast_to(s.cfp(pct * 255 // 100, False, False, 0), (L, 4)) if s.o1 \
            else s.cfp(i, True, SOLID_WRAP, 0)
        s.buf[:] = np.where((i < s.aux1)[:, None], fg, s.col(1))
    else:
        fg = np.broadcast_to(s.cfp((200 - pct) * 255 // 100, False, False, 0), (L, 4)) if s.o1 \
            else s.cfp(i, True, SOLID_WRAP, 0)
        s.buf[:] = np.where((i < (L - s.aux1))[:, None], s.col(1), fg)
    if active > s.aux1:
        s.aux1 = min(active, s.aux1 + size)
    elif active < s.aux1:
        s.aux1 = max(active, s.aux1 - size if s.aux1 > size else 0)


@fx(100, "Heartbeat", "@!,!;!,!;!;01;m12=1")
def heartbeat(s, now):
    bpm_ = 40 + (s.sx >> 3)
    ms_per = 60000 // bpm_
    second = ms_per // 3
    timer = now - s.step
    s.aux1 = s.aux1 * 2042 // (2048 + s.ix)
    if timer > second and not s.aux0:
        s.aux1 = 0xFFFF
        s.aux0 = 1
    if timer > ms_per:
        s.aux1 = 0xFFFF
        s.aux0 = 0
        s.step = now
    s.buf[:] = cb(s.cfp(s.idx, True, SOLID_WRAP, 0), s.col(1), 255 - (s.aux1 >> 8))


PAC1 = tables_from_16(_hex16([0x000507, 0x000409, 0x00030B, 0x00030D, 0x000210, 0x000212, 0x000114, 0x000117,
                              0x000019, 0x00001C, 0x000026, 0x000031, 0x00003B, 0x000046, 0x14554B, 0x28AA50]))[0]
PAC2 = tables_from_16(_hex16([0x000507, 0x000409, 0x00030B, 0x00030D, 0x000210, 0x000212, 0x000114, 0x000117,
                              0x000019, 0x00001C, 0x000026, 0x000031, 0x00003B, 0x000046, 0x0C5F52, 0x19BE5F]))[0]
PAC3 = tables_from_16(_hex16([0x000208, 0x00030E, 0x000514, 0x00061A, 0x000820, 0x000927, 0x000B2D, 0x000C33,
                              0x000E39, 0x001040, 0x001450, 0x001860, 0x001C70, 0x002080, 0x1040BF, 0x2060FF]))[0]


def _pac_layer(L, tbl, cistart, wavescale, bri, ioff):
    k = np.arange(1, L + 1)
    wa = (ioff + 250 * k) & 0xFFFF
    s16 = F.sin16(wa) + 32768
    wsh = wavescale // 2 + 20
    cs = ((s16 * (wsh + 1)) >> 16) + wsh
    ci = (cistart + np.cumsum(cs)) & 0xFFFF
    si8 = ((F.sin16(ci) + 32768) * 241) >> 16
    return np.floor(tbl[si8 & 255] * ((bri + 1) / 256.0))


@fx(101, "Pacifica", "@!,Angle;;!;;pal=51")
def pacifica(s, now):
    L = s.L
    d = s.data
    if s.call == 0:
        d.update(c=[0, 0, 0, 0], t=0)
    dms = (FRAMETIME >> 2) + ((FRAMETIME * s.sx) >> 7)
    d["t"] += dms
    t = d["t"]
    sf1 = F.beatsin16(3, 179, 269, t)
    sf2 = F.beatsin16(4, 179, 269, t)
    d1 = (dms * sf1) // 256
    d2 = (dms * sf2) // 256
    d21 = (d1 + d2) // 2
    c = d["c"]
    c[0] = (c[0] + d1 * F.beatsin88(1011, 10, 13, t)) & 0xFFFF
    c[1] = (c[1] - d21 * F.beatsin88(777, 8, 11, t)) & 0xFFFF
    c[2] = (c[2] - d1 * F.beatsin88(501, 5, 7, t)) & 0xFFFF
    c[3] = (c[3] - d2 * F.beatsin88(257, 4, 6, t)) & 0xFFFF
    acc = np.tile(np.array([2, 6, 10], np.float32), (L, 1))
    acc += _pac_layer(L, PAC1, c[0], F.beatsin16(3, 11 * 256, 14 * 256, t), F.beatsin8(10, 70, 130, t),
                      (-F.beat16(301, t)) & 0xFFFF)
    acc += _pac_layer(L, PAC2, c[1], F.beatsin16(4, 6 * 256, 9 * 256, t), F.beatsin8(17, 40, 80, t),
                      F.beat16(401, t))
    acc += _pac_layer(L, PAC3, c[2], 6 * 256, F.beatsin8(9, 10, 38, t), (-F.beat16(503, t)) & 0xFFFF)
    acc += _pac_layer(L, PAC3, c[3], 5 * 256, F.beatsin8(8, 10, 28, t), F.beat16(601, t))
    acc = np.minimum(acc, 255)
    base = F.beatsin8(9, 55, 65, t)
    wave = F.beat8(7, t)
    thr = ((F.sin8((wave + 7 * s.idx) & 255) * 21) >> 8) + base
    light = acc.sum(axis=1) / 3
    over = np.maximum(light - thr, 0)
    o2 = np.minimum(over * 2, 255)
    add = np.stack([over, o2, np.minimum(o2 * 2, 255)], axis=1) * (light > thr)[:, None]
    acc = np.minimum(acc + add, 255)
    acc[:, 2] = np.floor(acc[:, 2] * 146 / 256)
    acc[:, 1] = np.floor(acc[:, 1] * 201 / 256)
    acc = np.maximum(acc, np.array([2, 5, 7], np.float32))
    s.buf[:, :3] = acc
    s.buf[:, 3] = 0


@fx(104, "Sunrise", "@Time [min],Width;;!;;sx=60,pal=35")
def sunrise(s, now):
    L = s.L
    if s.call == 0 or s.sx != s.aux0:
        s.step = now
        s.aux0 = s.sx
    s.fill(BLACK)
    stage = 0xFFFF
    s10 = (now - s.step) // 100
    if s.sx > 120:
        counter = ((now >> 1) * (((s.sx - 120) >> 1) + 1)) & 0xFFFF
        stage = F.triwave16(counter)
    elif s.sx:
        mins = s.sx - 60 if s.sx > 60 else s.sx
        target = mins * 600
        s10 = min(s10, target)
        stage = s10 * 0xFFFF // max(1, target)
        if s.sx > 60:
            stage = 0xFFFF - stage
    i = np.arange(L // 2 + 1)
    wave = F.triwave16((i * stage) // L)
    wave = (wave >> 8) + ((wave * s.ix) >> 15)
    c = s.cfp(np.minimum(wave, 240), False, True, 255)
    ok = i < L
    s.buf[i[ok]] = c[ok]
    s.buf[L - 1 - i[ok]] = c[ok]


def _phased(s, now, noise):
    d = s.data
    d["ph"] = d.get("ph", 0.0) + s.sx / 32.0
    cutoff = 255 - s.ix
    i = s.idx
    if noise:
        modv = F.inoise8(i * 20) // 16
        modv = np.where(modv == 0, 1, modv)
    else:
        modv = 5
    val = ((i + 1) * 16 + (d["ph"] * ((i % modv) + 1) / 2).astype(np.int64)) & 255
    b = F.cubicwave8(val)
    b = np.where(b > cutoff, b - cutoff, 0)
    step = 256 // max(1, s.L) + (1 if s.L > 256 else 0)
    index = ((now // 64) + i * step) & 255
    s.buf[:] = cb(s.col(1), s.cfp(index, False, False, 0), b)


@fx(105, "Phased", "@!,!;!,!;!")
def phased(s, now):
    _phased(s, now, False)


@fx(109, "Phased Noise", "@!,!;!,!;!")
def phased_noise(s, now):
    _phased(s, now, True)


@fx(106, "Twinkleup", "@!,Intensity;!,!;!;;m12=0")
def twinkleup(s, now):
    d = s.data
    L = s.L
    if d.get("L") != L:
        rng = F.Rand16(535)
        r = np.array([[rng.r8(), rng.r8(), rng.r8()] for _ in range(L)], dtype=np.int64).reshape(L, 3)
        d.update(L=L, r=r)
    r = d["r"]
    pix = F.sin8((r[:, 0] + 16 * now // (256 - s.sx)) & 255)
    pix = np.where(r[:, 1] > s.ix, 0, pix)
    s.buf[:] = cb(s.col(1), s.cfp((r[:, 2] + now // 100) & 255, False, SOLID_WRAP, 0), pix)


@fx(107, "Noise Pal", "@!,Scale;;!")
def noise_pal(s, now):
    d = s.data
    scale = 15 + (s.ix >> 2)
    if s.call == 0:
        d["cur"] = tables_from_16(np.zeros((16, 3), np.float32))[0]
        d["tgt"] = d["cur"].copy()
        s.step = -100000
    if now - s.step > 4000 + s.sx * 10:
        s.step = now
        base = F.random8()
        from .palettes import pal16_from_colors
        cols = [F.hsv_rainbow(base + F.random8(64), 255, F.random8(128, 255))[0],
                F.hsv_rainbow(base + 128, 255, F.random8(128, 255))[0],
                F.hsv_rainbow(base + F.random8(92), 192, F.random8(128, 255))[0],
                F.hsv_rainbow(base + F.random8(92), 255, F.random8(128, 255))[0]]
        d["tgt"] = tables_from_16(pal16_from_colors(cols))[0]
    d["cur"] = d["cur"] + np.clip(d["tgt"] - d["cur"], -3, 3)
    idx = F.inoise8(s.idx * scale, s.aux0 + s.idx * scale)
    if s.pal > 0:
        s.buf[:] = s.cfp(idx, False, True, 255)
    else:
        s.buf[:, :3] = d["cur"][idx]
        s.buf[:, 3] = 0
    s.aux0 = (s.aux0 + F.beatsin8(10, 1, 4, now)) & 0xFFFF


@fx(108, "Sine", "@!,Scale;;!")
def sine(s, now):
    ci = now // 32
    s.step += s.sx // 16
    freq = s.ix // 4
    pix = F.cubicwave8((s.idx * freq + s.step) & 255)
    s.buf[:] = cb(s.col(1), s.cfp((s.idx * ci // 255) & 255, False, SOLID_WRAP, 0), pix)


@fx(110, "Flow", "@!,Zones;;!;;m12=1")
def flow(s, now):
    L = s.L
    counter = 0
    if s.sx:
        counter = ((now * ((s.sx >> 2) + 1)) & 0xFFFF) >> 8
    zones = (s.ix * (L // 6)) >> 8
    if zones & 1:
        zones += 1
    zones = max(2, zones)
    zl = L // zones
    s.fill(s.cfp((-counter) & 255, False, True, 255))
    if zl <= 0:
        return
    off = (L - zones * zl) >> 1
    i = np.arange(zl)
    ci = ((i * 255 // zl) - counter) & 255
    cols = s.cfp(ci, False, True, 255)
    for z in range(zones):
        led = i if z & 1 else (zl - 1) - i
        if s.rev:
            led = (zl - 1) - led
        s.buf[off + z * zl + led] = cols


@fx(111, "Chunchun", "@!,Gap size;!,!;!")
def chunchun(s, now):
    L = s.L
    s.fade_out(254)
    counter = (now * (6 + (s.sx >> 4))) & 0xFFFF
    n = 2 + (L >> 3)
    span = (s.ix << 8) // n
    k = np.arange(n)
    cnt = (counter - (k + 1) * span) & 0xFFFF
    meg = (F.sin16(cnt) + 0x8000) & 0xFFFF
    bird = np.clip((meg * L) >> 16, 0, L - 1)
    s.buf[bird] = s.cfp((k * 255) // n, False, False, 0)


@fx(112, "Dancing Shadows", "@!,# of shadows;!;!")
def dancing_shadows(s, now):
    L = s.L
    d = s.data
    n = max(1, (s.ix * 10) // 255 + 1)
    if d.get("n") != n:
        d["n"] = n
        d["sp"] = [None] * n
    s.fill(BLACK)
    for k in range(n):
        sp = d["sp"][k]
        if sp is None or sp["pos"] < -sp["w"] - 2 or sp["pos"] > L + sp["w"] + 2:
            w = 1 + F.random8(max(2, L // 6))
            v = (F.random8(1, 10) / 10.0) * (s.sx / 128.0 + 0.1) * (1 if F.random8(2) else -1)
            sp = {"w": w, "v": v, "pos": -w if v > 0 else L + w, "col": s.cfp(F.random8(), False, False, 255)}
            d["sp"][k] = sp
        sp["pos"] += sp["v"]
        a, b = int(sp["pos"] - sp["w"]), int(sp["pos"] + sp["w"])
        for p in range(max(0, a), min(L, b + 1)):
            f = 1 - abs(p - sp["pos"]) / (sp["w"] + 1)
            s.buf[p] = np.minimum(s.buf[p] + sp["col"] * f, 255)


def _tristate_square8(x, pw, ad):
    a = 127
    x &= 255
    if x > 127:
        a = -127
        x -= 127
    if x < ad:
        return x * a // ad
    if x < pw - ad:
        return a
    if x < pw:
        return (pw - x) * a // ad
    return 0


@fx(113, "Washing Machine", "@!,!;;!")
def washing_machine(s, now):
    sp = _tristate_square8(now >> 7, 90, 15)
    s.step += (sp * 2048) // (512 - s.sx)
    col = F.sin8((((s.ix // 25 + 1) * 255 * s.idx // max(1, s.L)) + (s.step >> 7)) & 255)
    s.buf[:] = s.cfp(col, False, SOLID_WRAP, 3)


@fx(115, "Blends", "@Shift speed,Blend speed;;!")
def blends(s, now):
    d = s.data
    plen = min(s.L, 255)
    if d.get("p") is None or len(d["p"]) != plen + 1:
        d["p"] = np.zeros((plen + 1, 4), np.float32)
    bs = 10 + (s.ix * 118) // 255
    shift = ((now * ((s.sx >> 3) + 1)) >> 8) & 255
    k = np.arange(plen)
    tgt = s.cfp((shift + 3 * k + F.quadwave8(((k + 1) * 16) & 255)) & 255, False, SOLID_WRAP, 255)
    d["p"][:plen] = cb(d["p"][:plen], tgt, bs)
    src = np.arange(s.L) % (plen + 1)
    s.buf[:] = d["p"][src]


@fx(116, "TV Simulator", "@!,!;;!;01")
def tv_simulator(s, now):
    d = s.data
    if s.call == 0:
        d.update(cur=np.array([30, 30, 60], np.float32), tgt=np.array([30, 30, 60], np.float32),
                 t0=now, dur=500, start=np.array([30, 30, 60], np.float32))
    if now - d["t0"] >= d["dur"]:
        d["start"] = d["cur"].copy()
        if F.random8() < 40:      # scene cut
            h = F.random8()
            sat = F.random8(0, max(1, 80 + s.ix // 2))
            v = F.random8(60, 255)
            d["dur"] = 50 + F.random16(300)
        else:                     # small change within a scene
            h = (F.random8(0, 24) - 12) & 255
            sat = F.random8(0, 60)
            v = F.random8(40, 220)
            d["dur"] = 300 + F.random16(1500)
        d["dur"] = max(30, d["dur"] * (300 - s.sx) // 200)
        d["tgt"] = F.hsv_rainbow(h, sat, v)[0]
        d["t0"] = now
    p = min(1.0, (now - d["t0"]) / max(1, d["dur"]))
    d["cur"] = d["start"] + (d["tgt"] - d["start"]) * p
    flicker = 1.0 - np.random.random(s.L)[:, None] * 0.08
    s.buf[:, :3] = np.clip(d["cur"][None, :] * flicker, 0, 255)
    s.buf[:, 3] = 0


# ---- later 1D effects that need no audio --------------------------------------------

@fx(147, "Perlin Move", "@!,# of pixels,Fade rate;!,!;!;1;m12=0")
def perlin_move(s, now):
    L = s.L
    s.fade_out(255 - s.c1)
    t = now * 128 // (260 - s.sx)
    for i in range(s.ix // 16 + 1):
        loc = int(F.inoise16(t + i * 15000, t))
        p = (loc - 50 * 256) * (L - 1) // (142 * 256)
        p = min(max(p, 0), L - 1)
        s.setp(p, s.cfp(p % 255, False, SOLID_WRAP, 0))


@fx(179, "Flow Stripe", "@Hue speed,Effect speed;;;1")
def flow_stripe(s, now):
    L = s.L
    hl = max(1, L * 10 // 13)
    hue = (now // (s.sx + 1)) & 255
    t = now // (s.ix // 8 + 1)
    c = (np.abs(s.idx - hl) // hl) * 127
    c = F.sin8(c)
    c = F.sin8((c // 2 + t) & 255)
    b = F.sin8((c + t // 8) & 255)
    s.buf[:, :3] = F.hsv_rainbow((b + hue) & 255, 255, 255)
    s.buf[:, 3] = 0


@fx(184, "Wavesins", "@!,Brightness variation,Starting color,Range of colors,Color variation;!;!;1")
def wavesins(s, now):
    i = s.idx
    bri = F.sin8((now // 4 + i * s.ix) & 255)
    b = (F.beat8(s.sx, now) + i * (s.c3 << 3)) & 255
    lo = s.c1
    rng = (s.c2) & 255
    idx = (lo + ((F.sin8(b) * (rng + 1)) >> 8)) & 255
    s.buf[:] = s.cfp(idx, False, SOLID_WRAP, 0, bri)
