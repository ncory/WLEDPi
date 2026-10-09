"""presets.json storage (WLED format) and playlists."""
import json
import logging
import os
import random
import shutil
import time

from .config import atomic_write_json

log = logging.getLogger("wledpi.presets")


class PresetStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "presets.json")
        self.data = {"0": {}}
        self._pmt = 0
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError("not an object")
            d.setdefault("0", {})
            self.data = d
            log.info("Loaded %d presets", len([k for k in d if k != "0"]))
        except FileNotFoundError:
            self.data = {"0": {}}
        except ValueError as e:
            bad = self.path + ".corrupt-%d" % int(time.time())
            log.error("presets.json unreadable (%s); moved to %s", e, bad)
            try:
                shutil.move(self.path, bad)
            except OSError:
                pass
            self.data = {"0": {}}

    def mtime(self):
        """Change marker the UI uses to reload presets; strictly increases on every write."""
        if self._pmt:
            return self._pmt
        try:
            return int(os.path.getmtime(self.path))
        except OSError:
            return 0

    def get(self, pid):
        p = self.data.get(str(pid))
        return p if isinstance(p, dict) and p else None

    def ids(self):
        out = []
        for k in self.data:
            try:
                i = int(k)
            except ValueError:
                continue
            if 0 < i < 251 and self.data[k]:
                out.append(i)
        return sorted(out)

    def save(self, pid, obj):
        self.data[str(int(pid))] = obj
        self.write()

    def delete(self, pid):
        if str(int(pid)) in self.data:
            del self.data[str(int(pid))]
            self.write()

    def write(self):
        atomic_write_json(self.path, self.data)
        self._pmt = max(int(time.time()), self.mtime() + 1)

    def raw(self):
        return json.dumps(self.data, separators=(",", ":"))

    def replace_all(self, d):
        if not isinstance(d, dict):
            raise ValueError("presets must be a JSON object")
        d.setdefault("0", {})
        self.data = d
        self.write()


class Playlist:
    """WLED playlist: {"ps":[ids], "dur":[tenths]|n, "transition":[tenths]|n, "repeat":n, "end":id, "r":bool}"""

    def __init__(self, obj, pid, default_tr):
        ps = obj.get("ps", [])
        if not isinstance(ps, list):
            ps = [ps]
        dur = obj.get("dur", 100)
        tr = obj.get("transition", default_tr)
        n = len(ps)
        durs = dur if isinstance(dur, list) else [dur] * n
        trs = tr if isinstance(tr, list) else [tr] * n
        self.entries = []
        for i, p in enumerate(ps):
            try:
                p = int(p)
            except (TypeError, ValueError):
                continue
            if 0 < p < 251:
                d = durs[i] if i < len(durs) else (durs[-1] if durs else 100)
                t = trs[i] if i < len(trs) else (trs[-1] if trs else default_tr)
                self.entries.append((p, max(0, int(d)), max(0, int(t))))
        self.repeat = max(0, int(obj.get("repeat", 0) or 0))
        self.end = int(obj.get("end", 0) or 0)
        self.shuffle = bool(obj.get("r", False))
        self.pid = pid
        self.order = list(range(len(self.entries)))
        if self.shuffle:
            random.shuffle(self.order)
        self.idx = -1
        self.cycles = 0
        self.next_at = 0

    def valid(self):
        return bool(self.entries)

    def advance(self, now):
        """Returns (preset_id, transition_tenths) to apply next, or None when finished."""
        self.idx += 1
        if self.idx >= len(self.order):
            self.cycles += 1
            if self.repeat and self.cycles >= self.repeat:
                return None
            self.idx = 0
            if self.shuffle:
                random.shuffle(self.order)
        p, d, t = self.entries[self.order[self.idx]]
        self.next_at = now + d * 100 if d > 0 else None
        return p, t

    def due(self, now):
        return self.next_at is not None and now >= self.next_at
