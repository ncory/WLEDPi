"""FastLED-style integer math helpers, vectorised with numpy where useful.

These mimic the behaviour of the FastLED functions WLED's effects are built on
(sin8, beatsin8, triwave8, inoise8, CHSV rainbow conversion, ...). They do not
need to be bit-exact, just behave the same way visually.
"""
import math
import random

import numpy as np

_I = np.arange(256)
SIN8_T = np.clip(np.round(128 + 127.5 * np.sin(_I * 2 * math.pi / 256)), 0, 255).astype(np.int32)


def _arr(x):
    return np.asarray(x, dtype=np.int64)


def sin8(x):
    if isinstance(x, (int, np.integer)):
        return int(SIN8_T[int(x) & 255])
    return SIN8_T[_arr(x) & 255]


def cos8(x):
    if isinstance(x, (int, np.integer)):
        return int(SIN8_T[(int(x) + 64) & 255])
    return SIN8_T[(_arr(x) + 64) & 255]


def sin16(x):
    if isinstance(x, (int, np.integer)):
        return int(32767 * math.sin((int(x) & 0xFFFF) * 2 * math.pi / 65536))
    return (32767 * np.sin((_arr(x) & 0xFFFF) * (2 * math.pi / 65536))).astype(np.int64)


def scale8(i, s):
    return (i * (s + 1)) >> 8 if isinstance(i, (int, np.integer)) else (_arr(i) * (_arr(s) + 1)) >> 8


def scale16(i, s):
    return (i * (s + 1)) >> 16 if isinstance(i, (int, np.integer)) else (_arr(i) * (_arr(s) + 1)) >> 16


def qadd8(a, b):
    return min(255, a + b) if isinstance(a, (int, np.integer)) else np.minimum(255, _arr(a) + b)


def qsub8(a, b):
    return max(0, a - b) if isinstance(a, (int, np.integer)) else np.maximum(0, _arr(a) - b)


def triwave8(x):
    x = _arr(x) & 255
    x = np.where(x & 0x80, 255 - x, x)
    r = (x << 1) & 255
    return int(r) if r.ndim == 0 else r


def triwave16(x):
    x = _arr(x) & 0xFFFF
    x = np.where(x & 0x8000, 65535 - x, x)
    r = (x << 1) & 0xFFFF
    return int(r) if r.ndim == 0 else r


def ease8InOutQuad(i):
    i = _arr(i) & 255
    j = np.where(i & 0x80, 255 - i, i)
    jj2 = ((j * (j + 1)) >> 8) << 1
    r = np.where(i & 0x80, 255 - jj2, jj2) & 255
    return r


def ease8InOutCubic(i):
    i = _arr(i) & 255
    f = i / 255.0
    r = np.clip(np.round((3 * f * f - 2 * f * f * f) * 255), 0, 255).astype(np.int64)
    return r


def quadwave8(x):
    r = ease8InOutQuad(triwave8(x))
    return int(r) if np.ndim(r) == 0 else r


def cubicwave8(x):
    r = ease8InOutCubic(triwave8(x))
    return int(r) if np.ndim(r) == 0 else r


# ---- beat generators (time based) -------------------------------------------------

def beat88(bpm88, t):
    return ((int(t) * int(bpm88) * 280) >> 16) & 0xFFFF


def beat16(bpm, t):
    if bpm < 256:
        bpm <<= 8
    return beat88(bpm, t)


def beat8(bpm, t):
    return beat16(bpm, t) >> 8


def beatsin88(bpm88, lo, hi, t, phase=0):
    b = (beat88(bpm88, t) + phase) & 0xFFFF
    s = sin16(b) + 32768
    return lo + ((s * (hi - lo)) >> 16)


def beatsin16(bpm, lo, hi, t, phase=0):
    b = (beat16(bpm, t) + phase) & 0xFFFF
    s = sin16(b) + 32768
    return lo + scale16(s, hi - lo)


def beatsin8(bpm, lo, hi, t, phase=0):
    lo &= 255
    hi &= 255
    b = (beat8(bpm, t) + phase) & 255
    s = sin8(b)
    return (lo + scale8(s, (hi - lo) & 255)) & 255


# ---- random -------------------------------------------------------------------------

def random8(a=None, b=None):
    if a is None:
        return random.randrange(256)
    if b is None:
        return random.randrange(a) if a > 0 else 0
    return a + random.randrange(b - a) if b > a else a


def random16(a=None):
    if a is None:
        return random.randrange(65536)
    return random.randrange(a) if a > 0 else 0


class Rand16:
    """FastLED's deterministic 16 bit PRNG (used where effects reseed it)."""

    def __init__(self, seed=1337):
        self.s = seed & 0xFFFF

    def r16(self, lim=None):
        self.s = (self.s * 2053 + 13849) & 0xFFFF
        return self.s if lim is None else (self.s * lim) >> 16

    def r8(self, a=None, b=None):
        self.s = (self.s * 2053 + 13849) & 0xFFFF
        v = ((self.s & 0xFF) + (self.s >> 8)) & 0xFF
        if a is None:
            return v
        if b is None:
            return (v * a) >> 8
        return a + ((v * (b - a)) >> 8)


def random_wheel_index(pos):
    d = 0
    r = 0
    while d < 42:
        r = random.randrange(256)
        x = abs(pos - r)
        d = min(x, 255 - x)
    return r


# ---- colour -------------------------------------------------------------------------

def hsv_rainbow(h, s, v):
    """FastLED hsv2rgb_rainbow, vectorised. h,s,v: scalars or arrays (0-255). Returns (N,3) float."""
    h = np.atleast_1d(_arr(h) & 255)
    s = np.broadcast_to(np.atleast_1d(_arr(s)), h.shape)
    v = np.broadcast_to(np.atleast_1d(_arr(v)), h.shape)
    offset8 = (h & 0x1F) << 3
    third = (offset8 * 86) >> 8
    twothirds = (offset8 * 171) >> 8
    seg = h >> 5
    r = np.zeros_like(h)
    g = np.zeros_like(h)
    b = np.zeros_like(h)
    conds = [seg == k for k in range(8)]
    r = np.select(conds, [255 - third, 171, 171 - twothirds, 0, 0, third, 85 + third, 170 + third])
    g = np.select(conds, [third, 85 + third, 170 + third, 255 - third, 171 - twothirds, 0, 0, 0])
    b = np.select(conds, [0, 0, 0, third, 85 + twothirds, 255 - third, 171 - third, 85 - third])
    rgb = np.stack([r, g, b], axis=1).astype(np.float32)
    # desaturate
    desat = 255 - s
    desat = (desat * desat) >> 8
    satscale = (255 - desat)[:, None]
    rgb = np.where((s == 255)[:, None], rgb, np.floor(rgb * (satscale + 1) / 256) + desat[:, None])
    vv = ((v * v) >> 8) + (v > 0)
    rgb = np.where((v == 255)[:, None], rgb, np.floor(rgb * (vv[:, None] + 1) / 256))
    return np.clip(rgb, 0, 255)


# ---- noise (Perlin) ------------------------------------------------------------------

_rng = np.random.RandomState(0x5EED)
_P = _rng.permutation(256)
PERM = np.concatenate([_P, _P]).astype(np.int64)


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _grad(h, x, y, z):
    h = h & 15
    u = np.where(h < 8, x, y)
    v = np.where(h < 4, y, np.where((h == 12) | (h == 14), x, z))
    return np.where(h & 1, -u, u) + np.where(h & 2, -v, v)


def pnoise3(x, y, z):
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    x, y, z = np.broadcast_arrays(x, y, z)
    xi = np.floor(x).astype(np.int64) & 255
    yi = np.floor(y).astype(np.int64) & 255
    zi = np.floor(z).astype(np.int64) & 255
    xf = x - np.floor(x)
    yf = y - np.floor(y)
    zf = z - np.floor(z)
    u, v, w = _fade(xf), _fade(yf), _fade(zf)
    P = PERM
    A = P[xi] + yi
    AA = P[A & 511] + zi
    AB = P[(A + 1) & 511] + zi
    B = P[(xi + 1) & 511] + yi
    BA = P[B & 511] + zi
    BB = P[(B + 1) & 511] + zi

    def lerp(t, a, b):
        return a + t * (b - a)

    r = lerp(w,
             lerp(v, lerp(u, _grad(P[AA & 511], xf, yf, zf), _grad(P[BA & 511], xf - 1, yf, zf)),
                  lerp(u, _grad(P[AB & 511], xf, yf - 1, zf), _grad(P[BB & 511], xf - 1, yf - 1, zf))),
             lerp(v, lerp(u, _grad(P[(AA + 1) & 511], xf, yf, zf - 1), _grad(P[(BA + 1) & 511], xf - 1, yf, zf - 1)),
                  lerp(u, _grad(P[(AB + 1) & 511], xf, yf - 1, zf - 1), _grad(P[(BB + 1) & 511], xf - 1, yf - 1, zf - 1))))
    return r


def inoise8(x, y=0, z=0):
    """x,y,z in 8.8 fixed point like FastLED. Returns 0-255 (int array)."""
    n = pnoise3(np.asarray(x) / 256.0, np.asarray(y) / 256.0 + 0.37, np.asarray(z) / 256.0 + 0.71)
    return np.clip(128 + n * 170, 0, 255).astype(np.int64)


def inoise16(x, y=0, z=0):
    """x,y,z in 16.16 fixed point. Returns 0-65535."""
    n = pnoise3(np.asarray(x) / 65536.0, np.asarray(y) / 65536.0 + 0.37, np.asarray(z) / 65536.0 + 0.71)
    return np.clip(32768 + n * 43000, 0, 65535).astype(np.int64)
