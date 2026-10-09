#!/usr/bin/env python3
"""Run every effect for 120 frames at several strip lengths/options; fail on any exception.
Usage: python3 tests/fx_stress.py"""
import os, random, sys, tempfile, time, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np
from wledpi import config
from wledpi.state import Controller
from wledpi.effects import EFFECTS

tmp = tempfile.mkdtemp()
cfg = config.normalize({"data_dir": tmp, "assets": "/nonexistent", "www_dir": tmp,
                        "outputs": [{"driver": "virtual", "count": 150}], "defaults": {"restore_state": False}})
ctrl = Controller(cfg, None)
st = ctrl.strip
fails, slow = [], []
for fid in sorted(EFFECTS):
    for L, opts in [(150, {}), (1, {}), (2, {"mi": True}), (37, {"grp": 2, "spc": 1, "rev": True}),
                    (300, {"o1": True, "o2": True, "o3": True})]:
        cfg["outputs"][0]["count"] = L
        if st.total != L:
            ctrl.reinit()
            st.segments[0].start, st.segments[0].stop = 0, L
            st.segments[0].update_geometry()
        seg = {"id": 0, "fx": fid, "fxdef": True, "pal": random.choice([0, 1, 2, 3, 4, 5, 6, 11, 13, 35, 50]),
               "sx": random.randint(0, 255), "ix": random.randint(0, 255), "c1": random.randint(0, 255),
               "c2": random.randint(0, 255), "c3": random.randint(0, 31),
               "col": [[255, 0, 0], [0, 0, 255], [0, 255, 0]], "mi": False, "rev": False, "grp": 1, "spc": 0}
        seg.update(opts)
        ctrl.apply_state({"on": True, "bri": 255, "seg": [seg]}, tr_override=0)
        s = st.segments[0]
        t0 = time.perf_counter()
        try:
            for k in range(120):
                st.now = k * 24 + 1000
                EFFECTS[s.fx][2](s, st.now)
                s.call += 1
                assert s.buf.shape == (s.L, 4) and np.isfinite(s.buf).all()
                np.clip(s.buf, 0, 255, out=s.buf)
                st._finish(st.render())
        except Exception:
            fails.append((fid, EFFECTS[fid][0], L, opts, traceback.format_exc().splitlines()[-1]))
        dt = (time.perf_counter() - t0) / 120 * 1000
        if L == 300 and dt > 4:
            slow.append((fid, EFFECTS[fid][0], round(dt, 2)))
for f in fails:
    print("FAIL", f)
print("%d effects, %d failures; slow at 300 LEDs (ms/frame): %s" % (len(EFFECTS), len(fails), slow))
sys.exit(1 if fails else 0)
