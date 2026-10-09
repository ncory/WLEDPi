"""The LED engine: owns segments and outputs and runs the render loop in a thread."""
import logging
import threading
import time

import numpy as np

from . import effects
from . import effects_more  # noqa: F401  (registers effects)
from .drivers import make_output, pi_model
from .segment import Segment, FRAMETIME

log = logging.getLogger("wledpi.strip")


class Strip:
    def __init__(self, cfg, palettes):
        self.cfg = cfg
        self.palettes = palettes
        self.lock = threading.RLock()
        self.model = pi_model()
        self.t0 = time.monotonic()
        self.now = 0
        self.segments = []
        self.outputs = []
        self.total = 0
        self.has_white = False
        # master brightness / transitions
        self.on = True
        self.bri = 128
        self.transition = 7          # x100 ms (persistent)
        self._bri_from = 0.0
        self._bri_to = 0.0
        self._bri_t0 = 0
        self._bri_dur = 0
        self.nl_bri = None           # brightness override from nightlight (0-255) or None
        # stats
        self.fps = 0
        self.pwr = 0
        self.live = np.zeros((0, 3), np.uint8)
        self.frame_no = 0
        self.tick_hook = None        # called with now (ms) under lock each frame
        self._fx_errors = set()
        self._running = False
        self._thread = None
        self._wake = threading.Event()
        self._gamma_lut = None
        self.setup_outputs()
        self.reset_segments()

    # ------------------------------------------------------------------ setup
    def ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def setup_outputs(self):
        with self.lock:
            for o in self.outputs:
                try:
                    o.close(True)
                except Exception:  # noqa
                    pass
            self.outputs = []
            total = 0
            for i, oc in enumerate(self.cfg["outputs"]):
                o = make_output(oc, i, self.model)
                o.start = int(oc.get("start", 0))
                o.reverse = bool(oc.get("reverse", False))
                self.outputs.append(o)
                total = max(total, o.start + o.count)
                log.info("Output %d: %s, %d LEDs from %d%s", i, o.describe(), o.count, o.start,
                         (" - ERROR: " + o.error) if o.error else "")
            old_total = self.total
            self.total = max(1, total)
            self.has_white = any(o.rgbw for o in self.outputs)
            self.frame = np.zeros((self.total, 4), np.float32)
            g = float(self.cfg["gamma"]["value"]) if self.cfg["gamma"]["color"] else 1.0
            self._gamma_lut = np.round(((np.arange(256) / 255.0) ** g) * 255).astype(np.float32)
            # keep segments inside the strip; grow a full-length segment 0 with the strip
            for s in self.segments:
                if s.start == 0 and s.stop == old_total and len(self.segments) == 1:
                    s.stop = self.total
                s.start = min(s.start, self.total - 1)
                s.stop = min(s.stop, self.total)
                if s.stop <= s.start:
                    s.start, s.stop = 0, self.total
                s.update_geometry()

    def reset_segments(self):
        with self.lock:
            self.segments = [Segment(self, 0, 0, self.total)]

    def output_errors(self):
        return [o.error for o in self.outputs if o.error]

    # ------------------------------------------------------------------ brightness
    def set_master(self, on=None, bri=None, dur_ms=0):
        cur = self._bri_value()
        if on is not None:
            self.on = bool(on)
        if bri is not None:
            self.bri = max(0, min(255, int(bri)))
        target = float(self.bri if self.on else 0)
        self._bri_from = cur
        self._bri_to = target
        self._bri_t0 = self.now
        self._bri_dur = max(0, int(dur_ms))

    def _bri_value(self):
        if self._bri_dur <= 0:
            return self._bri_to
        p = (self.now - self._bri_t0) / self._bri_dur
        if p >= 1:
            self._bri_dur = 0
            return self._bri_to
        return self._bri_from + (self._bri_to - self._bri_from) * p

    def bri_now(self):
        """Effective master brightness 0-255 (used by some effects)."""
        b = self._bri_value()
        if self.nl_bri is not None and self.on:
            b = self.nl_bri
        return int(b)

    # ------------------------------------------------------------------ segments
    def main_seg(self, mainseg=0):
        if 0 <= mainseg < len(self.segments):
            return self.segments[mainseg]
        for s in self.segments:
            if s.sel:
                return s
        return self.segments[0]

    def purge_segments(self):
        keep = [s for s in self.segments if s.is_valid()]
        if not keep:
            keep = [self.segments[0]]
            keep[0].start, keep[0].stop = 0, self.total
            keep[0].update_geometry()
        for i, s in enumerate(keep):
            s.id = i
        self.segments = keep

    # ------------------------------------------------------------------ rendering
    def render(self):
        now = self.now
        frame = self.frame
        frame[:] = 0
        for seg in self.segments:
            if not seg.is_valid() or seg.L == 0:
                continue
            op = seg.opacity()
            if op <= 0 and not seg.transitioning():
                continue
            if not seg.frz and now >= seg.next_run:
                ent = effects.EFFECTS.get(seg.fx) or effects.EFFECTS[0]
                try:
                    r = ent[2](seg, now)
                except Exception as e:  # noqa
                    key = (seg.fx, type(e).__name__)
                    if key not in self._fx_errors:
                        self._fx_errors.add(key)
                        log.exception("Effect %d (%s) failed", seg.fx, ent[0])
                    seg.buf[:] = 0
                    r = None
                seg.call += 1
                seg.next_run = now + r if isinstance(r, (int, np.integer)) and r > FRAMETIME else 0
                np.clip(seg.buf, 0, 255, out=seg.buf)
            virt = seg.output()
            phys = virt[seg.map]
            if not seg.valid.all():
                phys = phys * seg.valid[:, None]
            if op < 255:
                phys = phys * (op / 255.0)
            a = seg.start
            b = min(seg.stop, self.total)
            frame[a:b] = phys[:b - a]
        return frame

    def _finish(self, frame):
        """Apply gamma, brightness and ABL; returns uint8 (total,4) for the outputs."""
        bri = self.bri_now()
        np.clip(frame, 0, 255, out=frame)
        # liveview: what WLED shows (brightness applied, W folded into RGB)
        lv = frame[:, :3] + frame[:, 3:4]
        self.live = np.minimum(lv * (bri / 255.0), 255).astype(np.uint8)
        if bri <= 0:
            self.pwr = 0
            return np.zeros((self.total, 4), np.uint8)
        out = self._gamma_lut[frame.astype(np.uint8)] * (bri / 255.0)
        abl = self.cfg["abl"]
        est = float(out.sum()) / 765.0 * abl["ma_per_led"] + self.total  # ~1 mA idle per LED
        if abl["enabled"] and est > abl["max_ma"]:
            scale = max(0.0, (abl["max_ma"] - self.total) / max(1.0, est - self.total))
            out *= scale
            est = abl["max_ma"]
        self.pwr = int(est)
        return np.round(out).astype(np.uint8)

    def _push(self, px):
        for o in self.outputs:
            seg = px[o.start:o.start + o.count]
            if len(seg) < o.count:
                seg = np.concatenate([seg, np.zeros((o.count - len(seg), 4), np.uint8)])
            if o.reverse:
                seg = seg[::-1]
            try:
                o.show(np.ascontiguousarray(seg))
            except Exception as e:  # noqa
                if not o.error:
                    log.error("Output %s failed: %s", o.describe(), e)
                o.error = str(e)

    def _idle(self):
        if self._bri_value() > 0 or self.nl_bri:
            return False
        return not any(s.transitioning() for s in self.segments)

    def run(self):
        self._running = True
        frames = 0
        t_fps = time.monotonic()
        last_idle_push = 0.0
        while self._running:
            t = time.monotonic()
            period = 1.0 / max(1, self.cfg["fps"])
            px = None
            with self.lock:
                self.now = self.ms()
                if self.tick_hook:
                    try:
                        self.tick_hook(self.now)
                    except Exception:  # noqa
                        log.exception("tick hook failed")
                self.palettes.tick_random(self.now, self.cfg["random_palette_interval"])
                idle = self._idle()
                if not idle or t - last_idle_push > 1.0:
                    frame = self.render()
                    px = self._finish(frame)
                    if idle:
                        last_idle_push = t
                    self.frame_no += 1
            if px is not None:
                self._push(px)
                frames += 1
            if t - t_fps >= 2.0:
                self.fps = int(round(frames / (t - t_fps)))
                frames = 0
                t_fps = t
            delay = period - (time.monotonic() - t)
            if idle:
                delay = max(delay, 0.1)
            if delay > 0:
                self._wake.wait(delay)
                self._wake.clear()

    def wake(self):
        self._wake.set()

    def start(self):
        self._thread = threading.Thread(target=self.run, name="render", daemon=True)
        self._thread.start()

    def stop(self, blank=True):
        self._running = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self.lock:
            for o in self.outputs:
                try:
                    o.close(blank)
                except Exception:  # noqa
                    pass

    # ------------------------------------------------------------------ liveview
    def live_frame(self, max_leds=1024):
        lv = self.live
        n = len(lv)
        step = 1
        while n // step > max_leds:
            step += 1
        return lv[::step], step
