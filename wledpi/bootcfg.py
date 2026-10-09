"""Keeps a small managed block in /boot/firmware/config.txt.

Pi 5:  dtoverlay=ws2812-pio,gpio=N,num_leds=M[,rgbw],brightness=0   (one per output, max 4)
Pi 4:  dtparam=audio=off   (needed for PWM pins 12/13/18/19; HDMI audio keeps working under KMS)

Nothing outside the markers is touched; a backup is written the first time.
"""
import os
import shutil

from .drivers import resolve_driver, pi_generation

BEGIN = "# >>> wled-pi (managed) >>>"
END = "# <<< wled-pi <<<"
CANDIDATES = ("/boot/firmware/config.txt", "/boot/config.txt")


def config_path():
    for p in CANDIDATES:
        if os.path.exists(p):
            return p
    return CANDIDATES[0]


def desired_lines(cfg, model):
    lines = []
    audio_off = False
    for o in cfg["outputs"]:
        drv = resolve_driver(o.get("driver", "auto"), model)
        if drv == "pio":
            ln = "dtoverlay=ws2812-pio,gpio=%d,num_leds=%d" % (o["gpio"], o["count"])
            if o.get("rgbw"):
                ln += ",rgbw"
            if o.get("pio_passthrough", True):
                ln += ",brightness=0"
            lines.append(ln)
        elif drv == "ws281x" and o["gpio"] in (12, 13, 18, 19):
            audio_off = True
    if audio_off:
        lines.insert(0, "dtparam=audio=off")
    return lines


def read_block(path=None):
    path = path or config_path()
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    if BEGIN not in text:
        return []
    body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    return [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("[")]


def write_block(lines, path=None):
    path = path or config_path()
    with open(path) as f:
        text = f.read()
    if BEGIN in text:
        pre, rest = text.split(BEGIN, 1)
        post = rest.split(END, 1)[1] if END in rest else ""
        pre = pre.rstrip("\n")
        if pre.endswith("[all]"):
            pre = pre[:-5].rstrip("\n")
        text = pre + "\n" + post.lstrip("\n")
    text = text.rstrip("\n") + "\n"
    if lines:
        text += "\n[all]\n" + BEGIN + "\n" + "\n".join(lines) + "\n" + END + "\n"
    bak = path + ".wled-pi.bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    tmp = path + ".wled-pi.tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def status(cfg, model):
    path = config_path()
    cur = read_block(path)
    want = desired_lines(cfg, model)
    return {"path": path, "exists": cur is not None, "current": cur or [], "desired": want,
            "in_sync": cur is not None and cur == want, "pi": pi_generation(model)}
