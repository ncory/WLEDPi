"""LED output drivers.

* ``ws281x``  - Raspberry Pi 4 (and older): rpi_ws281x library, PWM (GPIO 12/13/18/19) or PCM (GPIO 21).
* ``pio``     - Raspberry Pi 5: the kernel's ws2812-pio-rp1 driver (dtoverlay=ws2812-pio) via /dev/ledsN.
* ``virtual`` - no hardware, for testing (the web UI's Peek still works).

Every driver receives a uint8 array of shape (n, 4) with R, G, B, W columns.
"""
import logging
import os

import numpy as np

log = logging.getLogger("wledpi.drivers")

SPI0_PINS = {7, 8, 9, 10, 11}
SPI1_PINS = {16, 17, 18, 19, 20, 21}
WS281X_PINS = {12: ("PWM0", 0), 18: ("PWM0", 0), 13: ("PWM1", 1), 19: ("PWM1", 1), 21: ("PCM", 0), 10: ("SPI", 0)}


def pi_model():
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().decode(errors="replace").strip("\x00").strip()
    except OSError:
        return ""


def pi_generation(model=None):
    m = model if model is not None else pi_model()
    if "Raspberry Pi 5" in m or "Compute Module 5" in m or "Raspberry Pi 500" in m:
        return 5
    if "Raspberry Pi" in m or "Compute Module" in m:
        return 4
    return 0


def resolve_driver(name, model=None):
    if name and name != "auto":
        return name
    gen = pi_generation(model)
    return {5: "pio", 4: "ws281x"}.get(gen, "virtual")


def pio_nodes():
    """Find ws2812-pio overlay instances in the live device tree."""
    out = []
    base = "/proc/device-tree"
    if not os.path.isdir(base):
        return out
    for root, dirs, files in os.walk(base):
        if "compatible" not in files:
            continue
        try:
            with open(os.path.join(root, "compatible"), "rb") as f:
                if b"ws2812-pio-rp1" not in f.read():
                    continue
            status = "okay"
            if "status" in files:
                with open(os.path.join(root, "status"), "rb") as f:
                    status = f.read().strip(b"\x00").decode()
            if status not in ("okay", "ok"):
                continue
            n = 0
            if "rpi,num-leds" in files:
                with open(os.path.join(root, "rpi,num-leds"), "rb") as f:
                    n = int.from_bytes(f.read()[:4], "big")
            gpio = -1
            if "leds-gpios" in files:
                with open(os.path.join(root, "leds-gpios"), "rb") as f:
                    g = f.read()
                if len(g) >= 8:
                    gpio = int.from_bytes(g[4:8], "big")
            out.append({"gpio": gpio, "num_leds": n, "rgbw": "rpi,rgbw" in files, "node": root})
        except OSError:
            continue
    return out


def pin_warnings(gpio):
    w = []
    if gpio in SPI0_PINS:
        w.append("GPIO%d is an SPI0 pin" % gpio)
    elif gpio in SPI1_PINS:
        w.append("GPIO%d is an SPI1 (auxiliary SPI) pin" % gpio)
    return w


def _fold_white(px):
    """RGB-only strips: add W into RGB like WLED's preview does."""
    rgb = px[:, :3].astype(np.uint16) + px[:, 3:4]
    return np.minimum(rgb, 255).astype(np.uint8)


class Output:
    kind = "base"

    def __init__(self, ocfg):
        self.cfg = ocfg
        self.count = int(ocfg.get("count", 0))
        self.rgbw = bool(ocfg.get("rgbw", False))
        self.order = str(ocfg.get("order", "GRB")).upper()
        if sorted(self.order) != ["B", "G", "R"]:
            self.order = "GRB"
        self.error = None

    def describe(self):
        return self.kind

    def show(self, px):
        pass

    def close(self, blank=True):
        pass


class VirtualOutput(Output):
    kind = "virtual"

    def describe(self):
        return "virtual (no hardware)"


class Ws281xOutput(Output):
    kind = "ws281x"

    def __init__(self, ocfg):
        super().__init__(ocfg)
        import rpi_ws281x as R  # noqa  (pip package rpi_ws281x)
        try:
            import _rpi_ws281x as ws
        except ImportError:
            ws = R
        self._ws = ws
        self.gpio = int(ocfg.get("gpio", 12))
        if self.gpio not in WS281X_PINS:
            raise ValueError("GPIO%d cannot drive WS281x on a Pi 4 (use 12, 13, 18, 19 or 21)" % self.gpio)
        self.mode, channel = WS281X_PINS[self.gpio]
        name = ("SK6812_STRIP_" + self.order + "W") if self.rgbw else ("WS2811_STRIP_" + self.order)
        stype = getattr(R, name, None)
        if stype is None:
            stype = getattr(ws, name)
        self.strip = R.PixelStrip(self.count, self.gpio, int(ocfg.get("freq", 800000)), int(ocfg.get("dma", 10)),
                                  bool(ocfg.get("invert", False)), 255, channel, stype)
        self.strip.begin()
        self._chan = getattr(self.strip, "_channel", None)
        self._set = getattr(ws, "ws2811_led_set", None)

    def describe(self):
        return "rpi_ws281x %s GPIO%d" % (self.mode, self.gpio)

    def show(self, px):
        if self.rgbw:
            p = px.astype(np.uint32)
            vals = (p[:, 3] << 24) | (p[:, 0] << 16) | (p[:, 1] << 8) | p[:, 2]
        else:
            p = _fold_white(px).astype(np.uint32)
            vals = (p[:, 0] << 16) | (p[:, 1] << 8) | p[:, 2]
        vals = vals.tolist()
        if self._chan is not None and self._set is not None:
            st, ch = self._set, self._chan
            for i, c in enumerate(vals):
                st(ch, i, c)
        else:
            for i, c in enumerate(vals):
                self.strip.setPixelColor(i, c)
        self.strip.show()

    def close(self, blank=True):
        if blank:
            try:
                self.show(np.zeros((self.count, 4), np.uint8))
            except Exception:  # noqa
                pass
        try:
            self.strip._cleanup()
        except Exception:  # noqa
            pass


class PioOutput(Output):
    """Pi 5 kernel driver. The device takes 4 bytes per LED (R,G,B,W in a little endian
    word) and clocks them out as G,R,B(,W); we permute for other colour orders.
    Writing a single byte at offset 0 sets brightness; 0 = pass-through (no gamma)."""
    kind = "pio"

    def __init__(self, ocfg, index=0):
        super().__init__(ocfg)
        self.gpio = int(ocfg.get("gpio", 12))
        nodes = pio_nodes()
        dev = ocfg.get("device") or ""
        self.dev_leds = self.count
        match = [k for k, n in enumerate(nodes) if n["gpio"] == self.gpio]
        if not dev:
            dev = "/dev/leds%d" % (match[0] if match else index)
        if match:
            n = nodes[match[0]]
            self.dev_leds = n["num_leds"] or self.count
            if n["num_leds"] and n["num_leds"] != self.count:
                log.warning("dtoverlay for GPIO%d has num_leds=%d but config says %d; update config.txt",
                            self.gpio, n["num_leds"], self.count)
            if n["rgbw"] != self.rgbw:
                log.warning("dtoverlay rgbw setting for GPIO%d does not match config", self.gpio)
        elif nodes:
            log.warning("No ws2812-pio overlay found for GPIO%d (found GPIOs %s)", self.gpio,
                        [n["gpio"] for n in nodes])
        if not os.path.exists(dev):
            raise FileNotFoundError("%s not found - add 'dtoverlay=ws2812-pio,gpio=%d,num_leds=%d%s,brightness=0' "
                                    "to /boot/firmware/config.txt and reboot (Settings page can do this)"
                                    % (dev, self.gpio, self.count, ",rgbw" if self.rgbw else ""))
        self.dev = dev
        self.fd = os.open(dev, os.O_WRONLY)
        self.passthrough = bool(ocfg.get("pio_passthrough", True))
        os.pwrite(self.fd, bytes([0 if self.passthrough else 255]), 0)
        ch = {"R": 0, "G": 1, "B": 2}
        self.wire = [ch[c] for c in self.order]
        self.buf = np.zeros((max(self.dev_leds, self.count), 4), np.uint8)

    def describe(self):
        return "ws2812-pio %s GPIO%d" % (self.dev, self.gpio)

    def show(self, px):
        n = min(len(px), self.dev_leds)
        p = px[:n] if self.rgbw else np.concatenate([_fold_white(px[:n]), np.zeros((n, 1), np.uint8)], axis=1)
        b = self.buf
        b[:n, 1] = p[:, self.wire[0]]
        b[:n, 0] = p[:, self.wire[1]]
        b[:n, 2] = p[:, self.wire[2]]
        b[:n, 3] = p[:, 3]
        b[n:] = 0
        os.pwrite(self.fd, b[:self.dev_leds].tobytes(), 0)

    def close(self, blank=True):
        try:
            if blank:
                self.buf[:] = 0
                os.pwrite(self.fd, self.buf[:self.dev_leds].tobytes(), 0)
            os.close(self.fd)
        except OSError:
            pass


def make_output(ocfg, index=0, model=None):
    name = resolve_driver(ocfg.get("driver", "auto"), model)
    try:
        if name == "ws281x":
            return Ws281xOutput(ocfg)
        if name == "pio":
            return PioOutput(ocfg, index)
        return VirtualOutput(ocfg)
    except Exception as e:  # noqa
        log.error("Output %d (%s, GPIO%s) failed to start: %s", index, name, ocfg.get("gpio"), e)
        o = VirtualOutput(ocfg)
        o.error = "%s: %s" % (name, e)
        return o
