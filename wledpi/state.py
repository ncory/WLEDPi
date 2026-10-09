"""WLED JSON API semantics: state/info serialisation and applying state changes."""
import json
import logging
import math
import os
import platform
import random
import socket
import time

from . import __version__, VERSION_ID, WLED_VERSION
from .config import atomic_write_json, save as save_config
from .effects import EFFECTS, MODE_COUNT, effect_names, effect_data, effect_defaults
from .palettes import PaletteBank
from .presets import PresetStore, Playlist
from .segment import Segment
from .strip import Strip

log = logging.getLogger("wledpi.state")


# ---------------------------------------------------------------------------- value helpers

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def get_val(v, cur, lo=0, hi=255):
    """WLED getVal(): ints, "~" / "~-" (wrapping step), "~n" / "~-n" (relative), "r" (random)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return clamp(int(v), lo, hi)
    if isinstance(v, str):
        t = v.strip()
        if t == "r":
            return random.randint(lo, hi)
        wrap = t.startswith("w")
        if wrap:
            t = t[1:]
        if t.startswith("~"):
            rest = t[1:]
            if rest in ("", "-"):
                step, wrap = (1 if rest == "" else -1), True
            else:
                try:
                    step = int(rest)
                except ValueError:
                    return cur
            n = cur + step
            if wrap:
                return lo + (n - lo) % (hi - lo + 1)
            return clamp(n, lo, hi)
        try:
            return clamp(int(float(t)), lo, hi)
        except ValueError:
            return cur
    return cur


def get_bool(v, cur):
    if isinstance(v, str):
        if v == "t":
            return not cur
        return v.lower() in ("1", "true", "on")
    if v is None:
        return cur
    return bool(v)


def _has_fx(segv):
    """True if a "seg" value (object or array of objects) sets an effect."""
    elems = segv if isinstance(segv, list) else [segv]
    return any(isinstance(e, dict) and "fx" in e for e in elems)


def kelvin_rgb(k):
    t = max(1000, min(40000, k)) / 100.0
    r = 255 if t <= 66 else 329.698727446 * ((t - 60) ** -0.1332047592)
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * ((t - 60) ** -0.0755148492)
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return [int(clamp(round(x), 0, 255)) for x in (r, g, b)] + [0]


def parse_color(v, cur):
    """Returns new [r,g,b,w] or None to skip (WLED semantics)."""
    if isinstance(v, list):
        if not v:
            return None
        out = [0, 0, 0, 0]
        for k in range(min(4, len(v))):
            try:
                out[k] = clamp(int(v[k]), 0, 255)
            except (TypeError, ValueError):
                pass
        return out
    if isinstance(v, str):
        h = v.strip().lstrip("#")
        try:
            if len(h) == 6:
                return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 0]
            if len(h) == 8:
                return [int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16), int(h[0:2], 16)]
        except ValueError:
            return None
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v <= 0:
            return [0, 0, 0, 0] if v == 0 else None
        return kelvin_rgb(int(v))
    return None


def loads_lenient(text):
    """JSON parse that also accepts the single-quoted JSON WLED's UI sometimes sends."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        pass
    out = []
    q = None
    esc = False
    for ch in text:
        if q:
            if esc:
                esc = False
                out.append(ch)
            elif ch == "\\":
                esc = True
                out.append(ch)
            elif ch == q:
                q = None
                out.append('"')
            elif ch == '"' and q == "'":
                out.append('\\"')
            else:
                out.append(ch)
        elif ch in ("'", '"'):
            q = ch
            out.append('"')
        else:
            out.append(ch)
    return json.loads("".join(out))


# ---------------------------------------------------------------------------- system info helpers

def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is sent for UDP connect
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def default_iface():
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if len(p) > 2 and p[1] == "00000000":
                    return p[0]
    except OSError:
        pass
    return None


def mac_address():
    ifs = [default_iface()] + ["eth0", "wlan0", "end0"]
    for i in ifs:
        if not i:
            continue
        try:
            with open("/sys/class/net/%s/address" % i) as f:
                return f.read().strip().replace(":", "")
        except OSError:
            continue
    return "000000000000"


def wifi_info():
    iface = default_iface()
    try:
        with open("/proc/net/wireless") as f:
            for line in f.readlines()[2:]:
                p = line.split()
                if p and p[0].rstrip(":") == iface:
                    level = int(float(p[3].rstrip(".")))
                    sig = clamp(2 * (level + 100), 0, 100)
                    return {"bssid": "", "rssi": level, "signal": sig, "channel": 0, "ap": False}
    except (OSError, ValueError, IndexError):
        pass
    return {"bssid": "", "rssi": 0, "signal": 100, "channel": 0, "ap": False}


def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def mem_available():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


# ---------------------------------------------------------------------------- controller

class Controller:
    def __init__(self, cfg, cfg_path=None):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.data_dir = cfg["data_dir"]
        os.makedirs(self.data_dir, exist_ok=True)
        self.palettes = PaletteBank()
        self.palettes.load_assets(cfg["assets"])
        self.palettes.load_custom(self.data_dir)
        self.strip = Strip(cfg, self.palettes)
        self.strip.tick_hook = self._tick
        self.presets = PresetStore(self.data_dir)
        self.listeners = []
        self.ps = -1
        self.pl = -1
        self.playlist = None
        self.mainseg = 0
        self.lor = 0
        self.udpn = {"send": False, "recv": True, "sgrp": 1, "rgrp": 1}
        self.nl = {"on": False, "dur": 60, "mode": 1, "tbri": 0}
        self._nl_t0 = 0
        self._nl_from = 0
        self.ws_count = 0
        self.started = time.time()
        self._dirty_at = None
        self._pending_presets = []      # presets applied from the render thread (playlist)
        self.restart_requested = False
        self._apply_boot_state()

    # ------------------------------------------------------------------ boot / persistence
    def _apply_boot_state(self):
        d = self.cfg["defaults"]
        st = self.strip
        with st.lock:
            st.transition = d["transition"]
            st.set_master(on=d["on"], bri=d["bri"], dur_ms=0)
            if d["boot_preset"] and self.presets.get(d["boot_preset"]):
                self.apply_preset(d["boot_preset"], from_api=True)
                return
            if d["restore_state"]:
                try:
                    with open(os.path.join(self.data_dir, "state.json")) as f:
                        saved = json.load(f)
                    saved.pop("tt", None)
                    self.apply_state(saved, tr_override=0)
                    self.ps = saved.get("ps_restore", -1) if isinstance(saved.get("ps_restore"), int) else -1
                except FileNotFoundError:
                    pass
                except Exception as e:  # noqa
                    log.warning("Could not restore last state: %s", e)
        self._dirty_at = None

    def persist_if_needed(self, quiet_s=5.0):
        if not self.cfg["defaults"]["restore_state"] or self._dirty_at is None:
            return
        if time.monotonic() - self._dirty_at < quiet_s:
            return
        with self.strip.lock:
            st = self.preset_state(include_bri=True, seg_bounds=True, selected_only=False)
            st["ps_restore"] = self.ps
            self._dirty_at = None
        try:
            atomic_write_json(os.path.join(self.data_dir, "state.json"), st)
        except OSError as e:
            log.warning("Could not save state: %s", e)

    # ------------------------------------------------------------------ notifications
    def changed(self):
        self._dirty_at = time.monotonic()
        self.strip.wake()
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:  # noqa
                log.exception("listener failed")

    # ------------------------------------------------------------------ render-thread tick
    def _tick(self, now):
        notify = False
        if self.nl["on"]:
            notify |= self._nl_tick(now)
        pl = self.playlist
        if pl is not None and pl.due(now):
            self._playlist_next(now)
            notify = True
        if notify:
            self.changed()

    def _nl_tick(self, now):
        st = self.strip
        total = max(1, self.nl["dur"]) * 60000
        p = (now - self._nl_t0) / total
        tb = self.nl["tbri"]
        if p >= 1:
            self.nl["on"] = False
            st.nl_bri = None
            if tb == 0:
                st.set_master(on=False, bri=self._nl_from, dur_ms=0)
            else:
                st.set_master(bri=tb, dur_ms=0)
            return True
        mode = self.nl["mode"]
        if mode == 0:
            st.nl_bri = None
        elif mode == 3:   # sunrise: ramp from 0 towards target with an eased curve
            st.nl_bri = max(1, int(tb * (p ** 1.5)))
        else:
            st.nl_bri = int(self._nl_from + (tb - self._nl_from) * p)
        return False

    def _playlist_next(self, now=None):
        pl = self.playlist
        if pl is None:
            return
        now = self.strip.now if now is None else now
        nxt = pl.advance(now)
        if nxt is None:
            end = pl.end
            self.playlist = None
            self.pl = -1
            if 0 < end < 251:
                self.apply_preset(end, from_playlist=True)
            return
        pid, tr = nxt
        self.apply_preset(pid, from_playlist=True, tr=tr)

    def start_playlist(self, obj, pid, tr_default):
        pl = Playlist(obj, pid, tr_default)
        if not pl.valid():
            self.stop_playlist()        # WLED unloads first; the UI stops a playlist with {"playlist":{}}
            return False
        self.playlist = pl
        self.pl = pid if pid else 0
        if not self.strip.on:
            self.strip.set_master(on=True, dur_ms=self.strip.transition * 100)
        self._playlist_next()
        return True

    def stop_playlist(self):
        self.playlist = None
        self.pl = -1

    # ------------------------------------------------------------------ presets
    def apply_preset(self, pid, from_playlist=False, from_api=False, tr=None, depth=0):
        p = self.presets.get(pid)
        if not p or depth > 3:
            return False
        obj = {k: v for k, v in p.items() if k not in ("n", "ql")}
        if not from_playlist:
            self.stop_playlist()
        if "playlist" in obj and isinstance(obj["playlist"], dict):
            if from_playlist:
                return False           # no nested playlists
            on = obj.get("on")
            ok = self.start_playlist(obj["playlist"], pid, self.strip.transition)
            if on is not None:
                self.strip.set_master(on=get_bool(on, self.strip.on), dur_ms=self.strip.transition * 100)
            return ok
        if from_playlist and tr is not None:
            obj["tt"] = tr
            obj.pop("transition", None)
        self.apply_state(obj, preset_id=pid, depth=depth + 1)
        self.ps = pid
        return True

    def _set_boot_preset(self, obj):
        """psave with "bootps" (the UI's "Apply at boot" box) sets the boot preset; it is not stored in the preset."""
        if "bootps" not in obj:
            return
        d = self.cfg["defaults"]
        bp = get_val(obj["bootps"], d["boot_preset"], 0, 250)
        if bp == d["boot_preset"]:
            return
        d["boot_preset"] = bp
        if self.cfg_path:
            try:
                save_config(self.cfg_path, self.cfg)
            except OSError as e:
                log.warning("Could not save %s: %s", self.cfg_path, e)

    def preset_state(self, include_bri, seg_bounds, selected_only):
        st = self.strip
        out = {}
        if include_bri:
            out["on"] = st.on
            out["bri"] = st.bri
            out["transition"] = st.transition
        out["mainseg"] = self._mainseg_id()
        segs = []
        maxseg = self.cfg["max_segments"]
        for i in range(maxseg):
            if i >= len(st.segments):
                if seg_bounds and not selected_only:
                    segs.append({"stop": 0})
                    continue
                break
            s = st.segments[i]
            if selected_only and not s.sel:
                continue
            segs.append(self.seg_json(s, for_preset=True, bounds=seg_bounds))
        out["seg"] = segs
        return out

    def parse_ps(self, v):
        """ps value: int, or "a~b~" (next in range), "a~b~-" (previous), "a~b~r" (random), "~", "~-", "r"."""
        ids = self.presets.ids()
        cur = self.ps if self.ps > 0 else 0
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
        if not isinstance(v, str):
            return -1
        t = v.strip()
        lo, hi = 1, 250
        parts = t.split("~")
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            lo, hi = int(parts[0]), int(parts[1])
            t = "~" + parts[2] if parts[2] in ("", "-") else parts[2]
        cand = [i for i in ids if lo <= i <= hi]
        if not cand:
            return -1
        if t == "r":
            return random.choice(cand)
        if t in ("~", "~-"):
            if t == "~":
                nxt = [i for i in cand if i > cur]
                return nxt[0] if nxt else cand[0]
            prv = [i for i in cand if i < cur]
            return prv[-1] if prv else cand[-1]
        try:
            return int(t)
        except ValueError:
            return -1

    # ------------------------------------------------------------------ apply state
    def apply_state(self, obj, preset_id=0, depth=0, tr_override=None, keep_playlist=False):
        """Apply a WLED JSON state object. Returns True if something may have changed."""
        if not isinstance(obj, dict):
            return False
        st = self.strip
        with st.lock:
            st.now = st.ms()
            if "psave" in obj and obj.get("o"):
                pid = get_val(obj["psave"], 0, 0, 250)
                if pid > 0:
                    self._set_boot_preset(obj)
                    save = {k: v for k, v in obj.items()
                            if k not in ("psave", "o", "v", "time", "error", "ib", "sb", "sc", "bootps")}
                    self.presets.save(pid, save)
                    self.changed()
                return True

            if "transition" in obj and (self.playlist is None or not preset_id):
                st.transition = get_val(obj["transition"], st.transition, 0, 65535)
            tr = st.transition
            if "tt" in obj:
                tr = get_val(obj["tt"], tr, 0, 65535)
            if tr_override is not None:
                tr = tr_override
            tr_ms = tr * 100

            # master on / brightness
            new_on, new_bri = st.on, st.bri
            if "bri" in obj:
                new_bri = get_val(obj["bri"], st.bri, 0, 255)
            if "on" in obj:
                new_on = get_bool(obj["on"], st.on)
            if "bri" in obj and new_bri == 0:
                new_on = False
                new_bri = st.bri
            elif "bri" in obj and "on" not in obj and not st.on and new_bri > 0 and not isinstance(obj["bri"], str):
                new_on = True
            if new_on != st.on or new_bri != st.bri:
                if new_on and not st.on and new_bri == 0:
                    new_bri = 128
                st.set_master(on=new_on, bri=new_bri, dur_ms=tr_ms)

            nl = obj.get("nl")
            if isinstance(nl, dict):
                if "dur" in nl:
                    self.nl["dur"] = get_val(nl["dur"], self.nl["dur"], 1, 255)
                if "mode" in nl:
                    self.nl["mode"] = get_val(nl["mode"], self.nl["mode"], 0, 3)
                if "tbri" in nl:
                    self.nl["tbri"] = get_val(nl["tbri"], self.nl["tbri"], 0, 255)
                if "on" in nl:
                    on = get_bool(nl["on"], self.nl["on"])
                    if on and not self.nl["on"]:
                        self._nl_t0 = st.now
                        self._nl_from = st.bri
                        if not st.on:
                            st.set_master(on=True, dur_ms=tr_ms)
                    if not on:
                        st.nl_bri = None
                    self.nl["on"] = on
            if isinstance(obj.get("udpn"), dict):
                for k in ("send", "recv"):
                    if k in obj["udpn"]:
                        self.udpn[k] = get_bool(obj["udpn"][k], self.udpn[k])
            if "lor" in obj:
                self.lor = get_val(obj["lor"], self.lor, 0, 2)
            if "mainseg" in obj:
                self.mainseg = get_val(obj["mainseg"], self.mainseg, 0, len(st.segments) - 1)

            seg_changed = False
            if "seg" in obj:
                # like WLED, an effect set by hand (not by a preset or a nested "win") ends a running playlist
                if self.playlist is not None and not preset_id and not keep_playlist and _has_fx(obj["seg"]):
                    self.stop_playlist()
                seg_changed = self.apply_segments(obj["seg"], tr_ms, preset_id)

            if isinstance(obj.get("win"), str):
                from .httpapi import apply_win
                apply_win(self, obj["win"], nested=True)

            # presets / playlists
            if "playlist" in obj and isinstance(obj["playlist"], dict) and "psave" not in obj:
                self.start_playlist(obj["playlist"], preset_id, st.transition)
            if "pl" in obj and get_val(obj["pl"], 0, -1, 250) < 0:
                self.stop_playlist()
            if obj.get("np") and self.playlist is not None:
                self._playlist_next()
            pd = obj.get("pd")
            if isinstance(pd, int) and not isinstance(pd, bool) and 0 < pd < 251 and ("seg" in obj or "win" in obj):
                self.ps = pd            # "preset direct": the UI sends the preset's content along with its ID
            elif "ps" in obj and "psave" not in obj:
                pid = self.parse_ps(obj["ps"])
                if 0 < pid < 251 and depth < 4:
                    self.apply_preset(pid, from_api=not preset_id, depth=depth)
                elif pid == -1 and isinstance(obj["ps"], int):
                    self.ps = -1
            elif seg_changed and not preset_id:
                self.ps = -1

            if "psave" in obj:
                pid = get_val(obj["psave"], 0, 0, 250)
                if pid > 0:
                    self._set_boot_preset(obj)
                    p = self.preset_state(bool(obj.get("ib")), bool(obj.get("sb")), bool(obj.get("sc")))
                    p["n"] = str(obj.get("n") or ("Preset %d" % pid))
                    if obj.get("ql"):
                        p["ql"] = str(obj["ql"])[:2]
                    self.presets.save(pid, p)
                    self.ps = pid
            if "pdel" in obj:
                pid = get_val(obj["pdel"], 0, 0, 250)
                if pid > 0:
                    self.presets.delete(pid)
                    if self.ps == pid:
                        self.ps = -1
            if obj.get("rmcpal") and self.palettes.custom:       # the UI's "Remove last custom palette"
                try:
                    os.remove(os.path.join(self.data_dir, "palette%d.json" % (len(self.palettes.custom) - 1)))
                except OSError as e:
                    log.warning("Could not remove custom palette: %s", e)
                self.palettes.load_custom(self.data_dir)
            if obj.get("rb"):
                self.reinit()
        self.changed()
        return True

    def _mainseg_id(self):
        n = len(self.strip.segments)
        return self.mainseg if 0 <= self.mainseg < n else 0

    def apply_segments(self, segv, tr_ms, preset_id):
        st = self.strip
        changed = False
        if isinstance(segv, dict):
            if "id" in segv:
                changed |= self.apply_seg(segv, get_val(segv["id"], 0, 0, 255), tr_ms, preset_id)
            else:
                ids = [s.id for s in st.segments if s.sel] or [self._mainseg_id()]
                for sid in ids:
                    changed |= self.apply_seg(segv, sid, tr_ms, preset_id)
        elif isinstance(segv, list):
            for i, elem in enumerate(segv):
                if isinstance(elem, dict):
                    sid = get_val(elem.get("id", i), i, 0, 255)
                    changed |= self.apply_seg(elem, sid, tr_ms, preset_id)
        if any(not s.is_valid() for s in st.segments):
            st.purge_segments()
            changed = True
        return changed

    def _next_fx(self, cur, v):
        """Resolve fx value; '~', '~-', 'r' skip effects that are not implemented."""
        if isinstance(v, str) and v.strip() in ("~", "~-", "r"):
            avail = sorted(EFFECTS.keys())
            t = v.strip()
            if t == "r":
                return random.choice(avail)
            if t == "~":
                nxt = [i for i in avail if i > cur]
                return nxt[0] if nxt else avail[0]
            prv = [i for i in avail if i < cur]
            return prv[-1] if prv else avail[-1]
        f = get_val(v, cur, 0, MODE_COUNT - 1)
        return f

    def apply_seg(self, e, sid, tr_ms, preset_id):
        st = self.strip
        total = st.total
        segs = st.segments
        if sid >= len(segs):
            start = get_val(e.get("start", 0), 0, 0, total)
            stop = get_val(e.get("stop", total), total, 0, total)
            if "len" in e:
                stop = start + get_val(e["len"], 0, 0, total)
            if stop <= start or len(segs) >= self.cfg["max_segments"]:
                return False
            ns = Segment(st, len(segs), start, min(stop, total))
            ns.sel = True
            segs.append(ns)
            sid = ns.id
        seg = segs[sid]
        changed = False
        before = (seg.fx, seg.pal, [list(c) for c in seg.colors])
        geo = (seg.start, seg.stop, seg.grp, seg.spc, seg.of, seg.rev, seg.mi)

        if "start" in e or "stop" in e or "len" in e:
            start = get_val(e.get("start", seg.start), seg.start, 0, total)
            stop = get_val(e.get("stop", seg.stop), seg.stop, 0, total)
            if "len" in e:
                stop = start + get_val(e["len"], seg.length, 0, total)
            stop = min(stop, total)
            if stop <= start:
                if len([s for s in segs if s.is_valid()]) > 1:
                    seg.stop = 0          # purged after the whole request
                    return True
            else:
                seg.start, seg.stop = start, stop
        if "grp" in e:
            seg.grp = get_val(e["grp"], seg.grp, 1, 255)
        if "spc" in e:
            seg.spc = get_val(e["spc"], seg.spc, 0, 255)
        if "of" in e:
            seg.of = get_val(e["of"], seg.of, -32768, 32767)
        if "rev" in e:
            seg.rev = get_bool(e["rev"], seg.rev)
        if "mi" in e:
            seg.mi = get_bool(e["mi"], seg.mi)
        if (seg.start, seg.stop, seg.grp, seg.spc, seg.of, seg.rev, seg.mi) != geo:
            seg.update_geometry()
            changed = True

        if "n" in e:
            seg.name = str(e["n"] or "")[:32]
        if "sel" in e:
            seg.sel = get_bool(e["sel"], seg.sel)
        if "on" in e:
            seg.on = get_bool(e["on"], seg.on)
        if "frz" in e:
            seg.frz = get_bool(e["frz"], seg.frz)
        if "bri" in e:
            b = get_val(e["bri"], seg.bri, 0, 255)
            if b > 0:
                seg.bri = b
            seg.on = b > 0 if not isinstance(e["bri"], str) else seg.on
        if "cct" in e:
            seg.cct = get_val(e["cct"], seg.cct, 0, 255)
        if "set" in e:
            seg.set = get_val(e["set"], seg.set, 0, 3)

        col = e.get("col")
        if isinstance(col, list):
            for i, c in enumerate(col[:3]):
                nc = parse_color(c, seg.colors[i])
                if nc is not None:
                    seg.colors[i] = nc

        if "fx" in e:
            fx = self._next_fx(seg.fx, e["fx"])
            if fx != seg.fx:
                seg.fx = fx
                seg.reset_fx()
                seg.frz = False if "frz" not in e else seg.frz
                if e.get("fxdef"):
                    dfl = effect_defaults(fx)
                    seg.sx = dfl.get("sx", 128)
                    seg.ix = dfl.get("ix", 128)
                    seg.c1 = dfl.get("c1", 128)
                    seg.c2 = dfl.get("c2", 128)
                    seg.c3 = dfl.get("c3", 16)
                    seg.o1 = bool(dfl.get("o1", 0))
                    seg.o2 = bool(dfl.get("o2", 0))
                    seg.o3 = bool(dfl.get("o3", 0))
                    if "pal" in dfl:
                        seg.pal = clamp(dfl["pal"], 0, 255)
                    if "m12" in dfl:
                        seg.m12 = clamp(dfl["m12"], 0, 7)
                    if "rev" in dfl:
                        seg.rev = bool(dfl["rev"])
                        seg.update_geometry()
                    if "mi" in dfl:
                        seg.mi = bool(dfl["mi"])
                        seg.update_geometry()
        for k, hi in (("sx", 255), ("ix", 255), ("c1", 255), ("c2", 255), ("c3", 31)):
            if k in e:
                setattr(seg, k, get_val(e[k], getattr(seg, k), 0, hi))
        if "pal" in e:
            pmax = self.palettes.count - 1
            v = e["pal"]
            if isinstance(v, str) and v.strip() in ("~", "~-", "r"):
                seg.pal = get_val(v, seg.pal, 0, pmax)
            else:
                p = get_val(v, seg.pal, 0, 255)
                ncust = len(self.palettes.custom)
                if p > pmax and p < 256 - ncust:
                    p = 0
                seg.pal = p
        for k in ("o1", "o2", "o3"):
            if k in e:
                setattr(seg, k, get_bool(e[k], getattr(seg, k)))
        if "si" in e:
            seg.si = get_val(e["si"], seg.si, 0, 3)
        if "m12" in e:
            seg.m12 = get_val(e["m12"], seg.m12, 0, 7)

        iarr = e.get("i")
        if isinstance(iarr, list):
            self._set_individual(seg, iarr)
            changed = True

        after = (seg.fx, seg.pal, seg.colors)
        if after[0] != before[0] or after[1] != before[1] or after[2] != before[2]:
            seg.start_xfade(tr_ms)
            changed = True
        target = seg.bri if seg.on else 0
        if target != seg.op_to:
            seg.set_opacity_target(target, tr_ms)
            changed = True

        if e.get("rpt") and not preset_id:
            ln = seg.length
            nxt = dict(e)
            nxt.pop("rpt", None)
            start = seg.start
            rev = seg.rev
            for i in range(sid + 1, self.cfg["max_segments"]):
                start += ln
                if start >= total or ln <= 0:
                    break
                rev = not rev
                nxt.update(start=start, stop=min(total, start + ln), rev=rev)
                nxt.pop("id", None)
                self.apply_seg(nxt, i, tr_ms, preset_id)
        return True

    def _set_individual(self, seg, iarr):
        if not seg.frz:
            seg.frz = True
            seg.fill([0, 0, 0, 0])
        start = stop = 0
        nset = 0
        for item in iarr:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if nset == 0:
                    start = abs(int(item))
                else:
                    stop = abs(int(item))
                nset += 1
                continue
            c = parse_color(item, [0, 0, 0, 0]) if isinstance(item, (list, str)) else None
            if c is None:
                c = [0, 0, 0, 0]
            if nset < 2 or stop <= start:
                stop = start + 1
            seg.set_range(start, stop, c)
            start = stop
            nset = 0
        seg.xf_buf = None

    def reinit(self):
        """Re-open outputs (after config change or "rb")."""
        with self.strip.lock:
            self.strip.setup_outputs()
            for s in self.strip.segments:
                s.update_geometry()

    # ------------------------------------------------------------------ serialisation
    def seg_json(self, s, for_preset=False, bounds=True):
        d = {"id": s.id}
        if bounds:
            d.update(start=s.start, stop=s.stop, grp=s.grp, spc=s.spc, of=s.of)
        if not for_preset:
            d["len"] = s.length
        d.update(on=s.on, frz=s.frz, bri=s.bri if s.bri else 255, cct=s.cct, set=s.set)
        if s.name:
            d["n"] = s.name
        w = self.strip.has_white
        d["col"] = [list(c[:4]) if w else list(c[:3]) for c in s.colors]
        d.update(fx=s.fx, sx=s.sx, ix=s.ix, pal=s.pal, c1=s.c1, c2=s.c2, c3=s.c3, sel=s.sel, rev=s.rev, mi=s.mi,
                 o1=s.o1, o2=s.o2, o3=s.o3, si=s.si, m12=s.m12)
        return d

    def state_json(self):
        st = self.strip
        with st.lock:
            rem = -1
            if self.nl["on"]:
                rem = max(0, int((self._nl_t0 + self.nl["dur"] * 60000 - st.ms()) / 1000))
            return {
                "on": st.on, "bri": st.bri, "transition": st.transition,
                "ps": self.ps if self.ps > 0 else -1, "pl": self.pl, "ledmap": 0,
                "nl": dict(self.nl, rem=rem),
                "udpn": dict(self.udpn), "lor": self.lor, "mainseg": self._mainseg_id(),
                "seg": [self.seg_json(s) for s in st.segments],
            }

    def info_json(self):
        st = self.strip
        cfg = self.cfg
        lc = 1 | (2 if st.has_white else 0)
        try:
            vfs = os.statvfs(self.data_dir)
            fs_t = int(vfs.f_blocks * vfs.f_frsize / 1024)
            fs_u = int((vfs.f_blocks - vfs.f_bfree) * vfs.f_frsize / 1024)
        except OSError:
            fs_t, fs_u = 1, 0
        u = {"WLED-Pi": [__version__]}
        outs = []
        for o in st.outputs:
            outs.append(o.describe() + (" (ERROR)" if o.error else ""))
        u["Output"] = ["; ".join(outs) or "none"]
        t = cpu_temp()
        if t is not None:
            u["CPU temp"] = [t, " °C"]
        if st.model:
            u["Model"] = [st.model]
        now = time.localtime()
        return {
            "ver": WLED_VERSION, "vid": VERSION_ID, "cn": "Pi", "release": "WLED-Pi",
            "leds": {"count": st.total, "pwr": st.pwr, "fps": st.fps,
                     "maxpwr": cfg["abl"]["max_ma"] if cfg["abl"]["enabled"] else 0,
                     "maxseg": cfg["max_segments"], "bootps": cfg["defaults"]["boot_preset"],
                     "seglc": [lc for _ in st.segments], "lc": lc, "rgbw": st.has_white,
                     "wv": 2 if st.has_white else 0, "cct": 0},
            "str": False, "name": cfg["name"], "udpport": 21324, "simplifiedui": False,
            "live": False, "liveseg": -1, "lm": "", "lip": "", "ws": self.ws_count,
            "fxcount": MODE_COUNT, "palcount": self.palettes.count, "cpalcount": len(self.palettes.custom),
            "maps": [{"id": 0}], "wifi": wifi_info(),
            "fs": {"u": fs_u, "t": max(1, fs_t), "pmt": self.presets.mtime()},
            "ndc": 0, "arch": platform.machine(), "core": platform.release(),
            "lwip": "Python " + platform.python_version(), "clock": 0, "flash": 0,
            "freeheap": mem_available(), "uptime": int(time.time() - self.started),
            "time": "%d-%d-%d, %02d:%02d:%02d" % (now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour,
                                                  now.tm_min, now.tm_sec),
            "opt": 0, "brand": "WLED", "product": "WLED-Pi", "mac": mac_address(), "ip": local_ip(),
            "u": u,
        }

    def full_json(self):
        return {"state": self.state_json(), "info": self.info_json(),
                "effects": effect_names(), "palettes": self.palettes.names}

    def palx(self, page):
        per = 8
        n = self.palettes.count + len(self.palettes.custom)
        maxp = max(0, (n - 1) // per)
        page = clamp(page, 0, maxp)
        out = {}
        for i in range(page * per, min(n, page * per + per)):
            pid = i if i < self.palettes.count else 255 - (i - self.palettes.count)
            out[str(pid)] = self.palettes.preview(pid)
        return {"m": maxp, "p": out}

    def live_json(self):
        with self.strip.lock:
            lv, step = self.strip.live_frame(256)
            return {"leds": ["%02X%02X%02X" % (int(c[0]), int(c[1]), int(c[2])) for c in lv], "n": step}

    def live_ws_bytes(self):
        with self.strip.lock:
            lv, _ = self.strip.live_frame(1024)
            return b"L\x01" + lv.tobytes()

    def cfg_json(self):
        c = self.cfg
        ins = []
        for o in c["outputs"]:
            ins.append({"start": o["start"], "len": o["count"], "pin": [o["gpio"]], "order": o["order"],
                        "rev": o["reverse"], "skip": 0, "type": 30 if o["rgbw"] else 22, "ref": False,
                        "rgbwm": 0, "freq": 0})
        return {
            "rev": [1, 0], "vid": VERSION_ID,
            "id": {"mdns": "wled-pi", "name": c["name"], "inv": "Light", "sui": False},
            "nw": {"ins": [{"ssid": "", "pskl": 0, "ip": [0, 0, 0, 0], "gw": [0, 0, 0, 0], "sn": [255, 255, 255, 0]}]},
            "eth": {"type": 0}, "if": {"live": {"en": False}},
            "hw": {"led": {"total": self.strip.total, "maxpwr": c["abl"]["max_ma"] if c["abl"]["enabled"] else 0,
                           "ledma": c["abl"]["ma_per_led"], "cct": False, "cr": False, "cb": 0,
                           "fps": c["fps"], "rgbwm": 255, "ld": False, "ins": ins},
                   "btn": {"ins": []}, "ir": {"pin": -1}, "relay": {"pin": -1}},
            "light": {"scale-bri": 100, "pal-mode": 0, "aseg": False,
                      "gc": {"bri": 1.0, "col": c["gamma"]["value"] if c["gamma"]["color"] else 1.0,
                             "val": c["gamma"]["value"]},
                      "tr": {"mode": True, "dur": c["defaults"]["transition"], "pal": 0,
                             "rpc": c["random_palette_interval"]},
                      "nl": {"mode": self.nl["mode"], "dur": self.nl["dur"], "tbri": self.nl["tbri"]}},
            "def": {"ps": c["defaults"]["boot_preset"], "on": c["defaults"]["on"], "bri": c["defaults"]["bri"]},
            "um": {},
        }
