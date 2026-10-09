"""Service configuration (/etc/wled-pi/config.json)."""
import copy
import json
import logging
import os

log = logging.getLogger("wledpi.config")

DEFAULT_OUTPUT = {
    "driver": "auto",        # auto | ws281x | pio | virtual
    "gpio": 12,              # 12 keeps both SPI0 (GPIO7-11) and SPI1 (GPIO16-21) free
    "count": 60,
    "start": 0,              # first LED index of this output on the combined strip
    "order": "GRB",
    "rgbw": False,
    "reverse": False,
    "dma": 10,               # ws281x only
    "invert": False,         # ws281x only (for inverting level shifters)
    "device": "",            # pio only, e.g. /dev/leds0 (auto-detected when empty)
    "pio_passthrough": True, # pio only: brightness byte 0 = no kernel gamma/scaling
}

DEFAULTS = {
    "name": "WLED-Pi",
    "http": {"port": 8080, "bind": "0.0.0.0"},
    "data_dir": "/var/lib/wled-pi",
    "www_dir": "/opt/wled-pi/www",
    "assets": "/opt/wled-pi/assets/palettes.json",
    "fps": 42,
    "outputs": [copy.deepcopy(DEFAULT_OUTPUT)],
    "gamma": {"color": True, "value": 2.8},
    "abl": {"enabled": False, "max_ma": 5000, "ma_per_led": 55},
    "defaults": {"on": True, "bri": 128, "transition": 7, "boot_preset": 0, "restore_state": True},
    "max_segments": 32,
    "random_palette_interval": 5,
    "off_on_exit": True,
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _int(v, lo, hi, d):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return d


def normalize(cfg):
    c = _merge(DEFAULTS, cfg)
    c["name"] = str(c.get("name") or "WLED-Pi")[:32]
    c["http"]["port"] = _int(c["http"].get("port"), 1, 65535, 8080)
    c["http"]["bind"] = str(c["http"].get("bind") or "0.0.0.0")
    c["fps"] = _int(c.get("fps"), 1, 250, 42)
    outs = []
    for o in c.get("outputs") or []:
        oo = _merge(DEFAULT_OUTPUT, o)
        oo["gpio"] = _int(oo["gpio"], 0, 53, 12)
        oo["count"] = _int(oo["count"], 1, 10000, 60)
        oo["start"] = _int(oo["start"], 0, 100000, 0)
        oo["dma"] = _int(oo["dma"], 0, 14, 10)
        oo["order"] = str(oo.get("order", "GRB")).upper()
        if sorted(oo["order"]) != ["B", "G", "R"]:
            oo["order"] = "GRB"
        if oo["driver"] not in ("auto", "ws281x", "pio", "virtual"):
            oo["driver"] = "auto"
        for b in ("rgbw", "reverse", "invert", "pio_passthrough"):
            oo[b] = bool(oo[b])
        outs.append(oo)
    if not outs:
        outs = [copy.deepcopy(DEFAULT_OUTPUT)]
    c["outputs"] = outs[:4]
    c["gamma"]["value"] = max(1.0, min(3.5, float(c["gamma"].get("value", 2.8) or 2.8)))
    c["gamma"]["color"] = bool(c["gamma"].get("color", True))
    c["abl"]["enabled"] = bool(c["abl"].get("enabled"))
    c["abl"]["max_ma"] = _int(c["abl"].get("max_ma"), 250, 100000, 5000)
    c["abl"]["ma_per_led"] = _int(c["abl"].get("ma_per_led"), 1, 255, 55)
    dfl = c["defaults"]
    dfl["bri"] = _int(dfl.get("bri"), 1, 255, 128)
    dfl["transition"] = _int(dfl.get("transition"), 0, 65535, 7)
    dfl["boot_preset"] = _int(dfl.get("boot_preset"), 0, 250, 0)
    dfl["on"] = bool(dfl.get("on", True))
    dfl["restore_state"] = bool(dfl.get("restore_state", True))
    c["max_segments"] = _int(c.get("max_segments"), 1, 64, 32)
    c["random_palette_interval"] = _int(c.get("random_palette_interval"), 1, 255, 5)
    return c


def load(path):
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        log.warning("Config %s not found, using defaults", path)
        raw = {}
    except ValueError as e:
        log.error("Config %s is not valid JSON (%s), using defaults", path, e)
        raw = {}
    return normalize(raw)


def atomic_write_json(path, data, indent=None):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".%s.tmp" % os.path.basename(path))
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent, separators=None if indent else (",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save(path, cfg):
    atomic_write_json(path, cfg, indent=2)
