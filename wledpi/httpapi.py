"""WLED's legacy HTTP request API: /win&A=128&FX=5 ... (common parameters)."""
from urllib.parse import unquote_plus
from xml.sax.saxutils import escape


def parse_win(qs):
    qs = qs.lstrip("&?")
    out = []
    for part in qs.split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        out.append((k.strip().upper(), unquote_plus(v)))
    return out


def _int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _hexcol(v):
    h = v.strip().lstrip("hH#")
    if len(h) in (6, 8):
        return h
    return None


def apply_win(ctrl, qs, nested=False):
    params = parse_win(qs)
    st = ctrl.strip
    state = {}
    seg = {}
    col = [None, None, None]
    with st.lock:
        main = st.main_seg(ctrl._mainseg_id())
        cur = [list(c) for c in main.colors]
        sel_id = None
        for k, v in params:
            if k == "A":
                state["bri"] = max(0, min(255, _int(v)))
            elif k == "T":
                t = _int(v, 2)
                state["on"] = "t" if t == 2 else bool(t)
            elif k == "FX":
                seg["fx"] = v if v.startswith("~") or v == "r" else _int(v)
            elif k == "SX":
                seg["sx"] = _int(v)
            elif k == "IX":
                seg["ix"] = _int(v)
            elif k == "FP":
                seg["pal"] = v if v.startswith("~") or v == "r" else _int(v)
            elif k in ("X1", "X2", "X3"):
                seg["c" + k[1]] = _int(v)
            elif k in ("R", "G", "B", "W", "R2", "G2", "B2", "W2", "R3", "G3", "B3", "W3"):
                slot = 0 if len(k) == 1 else int(k[1]) - 1
                ch = "RGBW".index(k[0])
                if col[slot] is None:
                    col[slot] = list(cur[slot])
                col[slot][ch] = max(0, min(255, _int(v)))
            elif k in ("CL", "C2", "C3"):
                h = _hexcol(v)
                if h:
                    col[{"CL": 0, "C2": 1, "C3": 2}[k]] = h
            elif k == "PL":
                state["ps"] = _int(v)
            elif k == "NP":
                state["np"] = True
            elif k == "SS":
                sel_id = _int(v)
            elif k == "SM":
                state["mainseg"] = _int(v)
            elif k == "SB":
                seg["bri"] = _int(v)
            elif k == "SV":
                seg["sel"] = "t" if _int(v) == 2 else bool(_int(v))
            elif k == "S":
                seg["start"] = _int(v)
            elif k == "S2":
                seg["stop"] = _int(v)
            elif k == "GP":
                seg["grp"] = _int(v, 1)
            elif k == "SP":
                seg["spc"] = _int(v)
            elif k == "OF":
                seg["of"] = _int(v)
            elif k == "RV":
                seg["rev"] = "t" if _int(v) == 2 else bool(_int(v))
            elif k == "MI":
                seg["mi"] = "t" if _int(v) == 2 else bool(_int(v))
            elif k == "TT":
                state["transition"] = max(0, _int(v) // 100)
            elif k == "NL":
                m = _int(v)
                if m > 0:
                    state["nl"] = dict(state.get("nl", {}), on=True, dur=m)
                else:
                    state["nl"] = dict(state.get("nl", {}), on=False)
            elif k == "NT":
                state["nl"] = dict(state.get("nl", {}), tbri=_int(v))
            elif k == "NF":
                state["nl"] = dict(state.get("nl", {}), mode=_int(v))
            elif k == "RB":
                state["rb"] = True
        if any(c is not None for c in col):
            seg["col"] = [c if c is not None else [] for c in col]
        if seg:
            if sel_id is not None:
                seg["id"] = sel_id
            state["seg"] = seg
        elif sel_id is not None:
            state["mainseg"] = sel_id
        if state:
            ctrl.apply_state(state, keep_playlist=nested)


def xml_state(ctrl):
    st = ctrl.strip
    with st.lock:
        s = st.main_seg(ctrl._mainseg_id())
        c0, c1 = s.colors[0], s.colors[1]
        parts = ["<?xml version=\"1.0\" ?><vs>",
                 "<ac>%d</ac>" % (st.bri if st.on else 0)]
        parts += ["<cl>%d</cl>" % v for v in c0[:3]]
        parts += ["<cs>%d</cs>" % v for v in c1[:3]]
        parts += ["<ns>0</ns><nr>1</nr>",
                  "<nl>%d</nl>" % int(ctrl.nl["on"]), "<nf>%d</nf>" % ctrl.nl["mode"],
                  "<nd>%d</nd>" % ctrl.nl["dur"], "<nt>%d</nt>" % ctrl.nl["tbri"],
                  "<fx>%d</fx>" % s.fx, "<sx>%d</sx>" % s.sx, "<ix>%d</ix>" % s.ix, "<fp>%d</fp>" % s.pal,
                  "<wv>%d</wv>" % (c0[3] if st.has_white else -1), "<ws>%d</ws>" % c1[3],
                  "<ps>%d</ps>" % max(0, ctrl.ps), "<cy>%d</cy>" % int(ctrl.playlist is not None),
                  "<ds>%s</ds>" % escape(ctrl.cfg["name"]), "<ss>%d</ss>" % s.id, "</vs>"]
        return "".join(parts)
