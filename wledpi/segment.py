"""Segments: a slice of the strip running one effect, with WLED's options."""
import numpy as np

from . import fastled as F

DEFAULT_COLORS = [[255, 160, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
FRAMETIME = 24          # WLED's nominal frame time in ms
SOLID_WRAP = False      # PALETTE_SOLID_WRAP with default palette blending mode


def color_blend(a, b, amt):
    """Blend colour(s) a -> b by amt/255. amt may be scalar or per-pixel array."""
    amt = np.asarray(amt, dtype=np.float32)
    if amt.ndim == 1:
        amt = amt[:, None]
    return a + (np.asarray(b, dtype=np.float32) - a) * (amt / 255.0)


# WLED 16 transition styles ("bs") on a 1D segment: which part of the segment already shows the new state.
# 2D-only styles have no 1D clipping in WLED, so they switch at once.
_STYLE_RIGHT = {2, 10, 13, 16, 20, 23}       # swipe/push right (and the 2D diagonals that sweep left->right)
_STYLE_LEFT = {3, 11, 12, 17, 21, 22}        # swipe/push left
_STYLE_OUT_IN, _STYLE_IN_OUT, _STYLE_OPEN_H, _STYLE_FAIRY = 4, 5, 8, 1
_STYLE_PUSH_RIGHT, _STYLE_PUSH_LEFT = 16, 17


def _hash_int(k):
    """WLED hashInt() on an int array (uint32 arithmetic)."""
    s = k.astype(np.uint64)
    s = (((s >> 16) ^ s) * 0x45d9f3b) & 0xFFFFFFFF
    s = (((s >> 16) ^ s) * 0x45d9f3b) & 0xFFFFFFFF
    return (s >> 16) ^ s


def style_mask(style, n, prog):
    """Boolean mask (length n) of virtual pixels that show the new state at progress 0..1, as WLED 16's
    Segment::isPixelClipped() + setClippingRect() in blendSegment()."""
    if style == 0 or n <= 1 or prog >= 1:
        return np.ones(n, dtype=bool)
    p16 = int(prog * 0xFFFF)
    k = np.arange(n)
    dw = p16 * n // 0xFFFF + 1
    if style in _STYLE_RIGHT:
        return k < dw
    if style in _STYLE_LEFT:
        return k >= n - dw
    if style == _STYLE_OUT_IN:
        dw = (0xFFFF - p16) * n // 0xFFFF + 1
        return (k < (n - dw) // 2) | (k >= (n + dw) // 2)
    if style in (_STYLE_IN_OUT, _STYLE_OPEN_H):
        return (k >= (n - dw) // 2) & (k < (n + dw) // 2)
    if style == _STYLE_FAIRY:
        pos = (_hash_int(k) % n) * 0xFFFF // n
        return p16 > pos
    return np.ones(n, dtype=bool)


def push_index(style, n, prog):
    """Source index for "push" styles (the canvas slides in), else None."""
    if style not in (_STYLE_PUSH_RIGHT, _STYLE_PUSH_LEFT) or n <= 1 or prog >= 1:
        return None
    off = (0xFFFF - int(prog * 0xFFFF)) * n // 0xFFFF
    k = np.arange(n)
    return (k + off) % n if style == _STYLE_PUSH_RIGHT else (k - off + n) % n


class Segment:
    def __init__(self, strip, sid, start=0, stop=0):
        self.strip = strip
        self.id = sid
        self.start = start
        self.stop = stop
        self.grp = 1
        self.spc = 0
        self.of = 0
        self.on = True
        self.frz = False
        self.bri = 255
        self.cct = 127
        self.set = 0
        self.name = ""
        self.colors = [list(c) for c in DEFAULT_COLORS]
        self.fx = 0
        self.sx = 128
        self.ix = 128
        self.pal = 0
        self.c1 = 128
        self.c2 = 128
        self.c3 = 16
        self.sel = sid == 0
        self.rev = False
        self.mi = False
        self.o1 = False
        self.o2 = False
        self.o3 = False
        self.si = 0
        self.m12 = 0
        self.bm = 0             # WLED 16 blend mode with the layers below (0 = top)
        self._defpal_fx = None
        self._defpal = 6
        # runtime
        self.L = 0
        self.buf = np.zeros((0, 4), dtype=np.float32)
        self.map = None
        self.valid = None
        self.next_run = 0
        # transitions
        self.op_from = 0.0
        self.op_to = 255.0
        self.op_t0 = 0
        self.op_dur = 0
        self.xf_buf = None
        self.xf_t0 = 0
        self.xf_dur = 0
        self._last_out = None
        self.reset_fx()
        self.update_geometry()

    # ------------------------------------------------------------------ geometry
    @property
    def length(self):
        return max(0, self.stop - self.start)

    def is_valid(self):
        return self.stop > self.start

    def update_geometry(self):
        n = self.length
        gl = max(1, self.grp + self.spc)
        vfull = (n + gl - 1) // gl if n else 0
        vlen = (vfull + 1) // 2 if self.mi else vfull
        vlen = max(1, vlen) if n else 0
        if n:
            k = np.arange(n)
            kk = (k - self.of) % n
            g = kk // gl
            valid = (kk % gl) < max(1, self.grp)
            if self.mi:
                g = np.where(g >= vlen, vfull - 1 - g, g)
            if self.rev:
                g = vlen - 1 - g
            g = np.clip(g, 0, vlen - 1)
            self.map, self.valid = g, valid
        else:
            self.map, self.valid = np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool)
        if vlen != self.L:
            self.L = vlen
            self.buf = np.zeros((vlen, 4), dtype=np.float32)
            self.xf_buf = None
            self.reset_fx()
        self.idx = np.arange(self.L)

    def reset_fx(self):
        self.call = 0
        self.step = 0
        self.aux0 = 0
        self.aux1 = 0
        self.data = {}
        self.next_run = 0

    # ------------------------------------------------------------------ colours / palette
    def col(self, n):
        return np.array(self.colors[n], dtype=np.float32)

    def colint(self, n):
        c = self.colors[n]
        return (c[3] << 24) | (c[0] << 16) | (c[1] << 8) | c[2]

    def _tables(self):
        pid = self.pal
        if pid == 0:            # WLED 16: "Default" = the effect's default palette ("pal=" in its metadata), else Party
            if self._defpal_fx != self.fx:
                from .effects import effect_defaults
                p = effect_defaults(self.fx).get("pal", 0)
                self._defpal, self._defpal_fx = (p if p > 0 else 6), self.fx
            pid = self._defpal
        return self.strip.palettes.tables(pid, self.colors)

    def cfp(self, i, mapping=False, wrap=True, mcol=0, pbri=255, blend=True):
        """color_from_palette. i: int or int array. Returns (4,) or (N,4) float32."""
        scalar = np.ndim(i) == 0
        if self.pal == 0 and mcol < 3:
            c = self.col(mcol)
            if np.ndim(pbri) == 0:
                out = c * (pbri / 255.0) if pbri != 255 else c
                return out if scalar else np.tile(out, (len(np.atleast_1d(i)), 1))
            pb = np.asarray(pbri, dtype=np.float32)[:, None]
            return np.tile(c, (len(pb), 1)) * (pb / 255.0)
        idx = np.asarray(i, dtype=np.int64)
        if mapping and self.L > 1:
            idx = (idx * 255) // (self.L - 1)
        idx = idx & 255
        if not wrap:
            idx = (idx * 241) >> 8
        tb, tn = self._tables()
        rgb = (tb if blend else tn)[idx]
        if np.ndim(pbri) == 0:
            if pbri != 255:
                rgb = np.floor(rgb * ((pbri + 1) / 256.0))
        else:
            rgb = np.floor(rgb * ((np.asarray(pbri, dtype=np.float32)[..., None] + 1) / 256.0))
        w = self.colors[mcol][3] if mcol < 3 else 0
        if scalar:
            return np.array([rgb[0], rgb[1], rgb[2], w], dtype=np.float32)
        out = np.empty((len(idx), 4), dtype=np.float32)
        out[:, :3] = rgb
        out[:, 3] = w
        return out

    def wheel(self, pos):
        if self.pal:
            return self.cfp(pos, False, True, 0)
        scalar = np.ndim(pos) == 0
        p = 255 - (np.atleast_1d(np.asarray(pos, dtype=np.int64)) & 255)
        r = np.select([p < 85, p < 170], [255 - p * 3, 0], (p - 170) * 3)
        g = np.select([p < 85, p < 170], [0, (p - 85) * 3], 255 - (p - 170) * 3)
        b = np.select([p < 85, p < 170], [p * 3, 255 - (p - 85) * 3], 0)
        out = np.stack([r, g, b, np.full_like(r, self.colors[0][3])], axis=1).astype(np.float32)
        return out[0] if scalar else out

    # ------------------------------------------------------------------ pixel helpers
    def fill(self, c):
        self.buf[:] = c

    def setp(self, i, c):
        if 0 <= i < self.L:
            self.buf[int(i)] = c

    def get(self, i):
        if 0 <= i < self.L:
            return self.buf[int(i)].copy()
        return np.zeros(4, dtype=np.float32)

    def set_range(self, a, b, c):
        a = max(0, int(a))
        b = min(self.L, int(b))
        if b > a:
            self.buf[a:b] = c

    def blend_px(self, i, c, amt):
        if 0 <= i < self.L:
            self.buf[i] = color_blend(self.buf[i], c, amt)

    def fade_out(self, rate):
        rate = (255 - rate) >> 1
        mapped = rate + 1.1
        tgt = self.col(1)
        d = tgt - self.buf
        delta = np.trunc(d / mapped) + np.sign(d)
        self.buf += delta
        np.clip(self.buf, 0, 255, out=self.buf)

    def fade_black(self, n):
        self.buf[:] = np.floor(self.buf * ((255 - n) / 256.0))

    def blur(self, amount):
        if self.L < 2 or amount <= 0:
            return
        keep = (255 - amount) / 255.0
        seep = (amount / 2) / 255.0
        b = self.buf
        part = b * seep
        out = b * keep
        out[1:] += part[:-1]
        out[:-1] += part[1:]
        # edges keep what would have seeped outwards
        out[0] += part[0]
        out[-1] += part[-1]
        self.buf[:] = np.clip(out, 0, 255)

    # ------------------------------------------------------------------ transitions
    def start_xfade(self, dur):
        if dur <= 0 or self.L == 0:
            self.xf_buf = None
            return
        self.xf_buf = (self._last_out if self._last_out is not None and len(self._last_out) == self.L
                       else self.buf).copy()
        self.xf_t0 = self.strip.now
        self.xf_dur = dur

    def set_opacity_target(self, target, dur):
        cur = self.opacity()
        self.op_from = cur
        self.op_to = float(target)
        self.op_t0 = self.strip.now
        self.op_dur = dur

    def opacity(self):
        if self.op_dur <= 0:
            return self.op_to
        p = (self.strip.now - self.op_t0) / self.op_dur
        if p >= 1:
            self.op_dur = 0
            return self.op_to
        return self.op_from + (self.op_to - self.op_from) * p

    def transitioning(self):
        return self.op_dur > 0 or self.xf_buf is not None

    def output(self):
        """(virtual buffer, opacity) including the running transition. With WLED 16's fade style the old
        and new state are blended; with the other styles each pixel shows either the old or the new state
        (and the matching opacity), so opacity may come back as a per-pixel array."""
        style = self.strip.blend_style
        out = self.buf
        if self.xf_buf is not None:
            p = (self.strip.now - self.xf_t0) / max(1, self.xf_dur)
            if p >= 1 or len(self.xf_buf) != self.L:
                self.xf_buf = None
            elif style == 0:
                out = self.xf_buf + (self.buf - self.xf_buf) * p
            else:
                idx = push_index(style, self.L, p)
                new, old = (self.buf, self.xf_buf) if idx is None else (self.buf[idx], self.xf_buf[idx])
                out = np.where(style_mask(style, self.L, p)[:, None], new, old)
        self._last_out = out
        if style == 0 or self.op_dur <= 0:
            return out, self.opacity()
        p = (self.strip.now - self.op_t0) / self.op_dur
        if p >= 1:
            self.op_dur = 0
            return out, self.op_to
        return out, np.where(style_mask(style, self.L, p), self.op_to, self.op_from)

    # ------------------------------------------------------------------ misc
    def light_caps(self):
        return 3 if self.strip.has_white else 1
