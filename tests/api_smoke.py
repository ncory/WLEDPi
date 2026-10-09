#!/usr/bin/env python3
"""Start WLED-Pi (virtual LEDs) on a free port and exercise the JSON API, presets, playlists,
uploads, /win and the WebSocket. Usage: python3 tests/api_smoke.py"""
import base64, json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
TMP = tempfile.mkdtemp(); CFG = os.path.join(TMP, "config.json")
json.dump({"http": {"port": PORT}, "data_dir": os.path.join(TMP, "data"), "www_dir": os.path.join(TMP, "www"),
           "assets": os.path.join(TMP, "palettes.json"),
           "outputs": [{"driver": "virtual", "count": 120}, {"driver": "virtual", "count": 30, "start": 120, "rgbw": True}]},
          open(CFG, "w"))
proc = subprocess.Popen([sys.executable, "-m", "wledpi", "--config", CFG], cwd=ROOT,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
FAILED = []
_print = print
def print(*a):
    if a and str(a[0]).startswith("FAIL"):
        FAILED.append(a[0])
    _print(*a)
try:
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", PORT), 0.2).close(); break
        except OSError:
            time.sleep(0.1)
    B = "http://127.0.0.1:%d" % PORT
    def get(p):
        with urllib.request.urlopen(B + p) as r: return r.status, r.headers, r.read()
    def post(p, obj, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj).encode()
        req = urllib.request.Request(B + p, data=data, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r: return r.status, r.read()
        except urllib.error.HTTPError as e: return e.code, e.read()
    ok = lambda c, m: print(("PASS " if c else "FAIL ") + m)
    s, h, b = get("/json"); j = json.loads(b)
    ok(set(j) == {"state","info","effects","palettes"}, "/json keys")
    ok(len(j["effects"]) == 187 and j["effects"][0] == "Solid", "effects count %d" % len(j["effects"]))
    ok(len(j["palettes"]) == 71, "palettes %d" % len(j["palettes"]))
    ok(j["info"]["leds"]["count"] == 150 and j["info"]["leds"]["rgbw"] is True, "led count/rgbw")
    ok(len(j["state"]["seg"][0]["col"][0]) == 4, "rgbw col has 4 values")
    fx = json.loads(get("/json/fxdata")[2]); ok(len(fx) == 187 and fx[1].startswith("@"), "fxdata")
    px = json.loads(get("/json/palx?page=0")[2]); ok(px["m"] == 8 and "0" in px["p"] and px["p"]["1"] == ["r"]*4, "palx %s" % px["m"])
    s, b = post("/json/state", {"on": True, "bri": 200, "seg": [{"id": 0, "fx": 9, "sx": 200, "col": [[255,0,0],"00FF00",[0,0,255,10]]}], "v": True})
    st = json.loads(b); ok(st["bri"] == 200 and st["seg"][0]["fx"] == 9 and st["seg"][0]["col"][1] == [0,255,0,0], "post state v")
    s, b = post("/json/state", {"seg": [{"id": 1, "start": 100, "stop": 150, "fx": 2}]}); ok(json.loads(b) == {"success": True}, "add seg (no v)")
    st = json.loads(get("/json/state")[2]); ok(len(st["seg"]) == 2 and st["seg"][1]["start"] == 100, "2 segments")
    ok(st["seg"][1]["sel"] is True, "new segment is selected")
    s, b = post("/json/state", {"psave": 5, "n": "Rainbow test", "ib": True, "sb": True, "v": True}); ok(json.loads(b)["ps"] == 5, "psave -> ps 5")
    pr = json.loads(get("/presets.json")[2]); ok(pr["5"]["n"] == "Rainbow test" and len(pr["5"]["seg"]) == 32, "preset stored (seg slots %d)" % len(pr["5"]["seg"]))
    post("/json/state", {"seg": [{"id": 1, "stop": 0}]}); st = json.loads(get("/json/state")[2]); ok(len(st["seg"]) == 1 and st["ps"] == -1, "delete seg, ps cleared")
    post("/json/state", {"ps": 5}); time.sleep(0.1); st = json.loads(get("/json/state")[2]); ok(st["ps"] == 5 and len(st["seg"]) == 2, "apply preset restores 2 segs")
    s, b = post("/json/state", {"psave": 6, "n": "API", "o": True, "seg": {"fx": 12}, "on": True}); pr = json.loads(get("/presets.json")[2]); ok(pr["6"] == {"n": "API", "seg": {"fx": 12}, "on": True}, "API preset saved verbatim")
    st = json.loads(get("/json/state")[2]); ok(st["seg"][0]["fx"] == 9, "o:true does not apply")
    post("/json/state", {"psave": 7, "n": "PL", "o": True, "playlist": {"ps": [5, 6], "dur": [10, 10], "transition": 0, "repeat": 0}, "on": True})
    post("/json/state", {"ps": 7}); time.sleep(0.2); st = json.loads(get("/json/state")[2]); ok(st["pl"] == 7 and st["ps"] == 5, "playlist started pl=%s ps=%s" % (st["pl"], st["ps"]))
    time.sleep(1.2); st = json.loads(get("/json/state")[2]); ok(st["ps"] == 6, "playlist advanced to %s" % st["ps"])
    post("/json/state", {"pl": -1}); st = json.loads(get("/json/state")[2]); ok(st["pl"] == -1, "playlist stopped")
    # the WLED UI applies a preset as "pd" + its content, and stops a playlist with {"playlist":{}}
    post("/json/state", dict({k: v for k, v in pr["5"].items() if k != "n"}, pd=5)); st = json.loads(get("/json/state")[2]); ok(st["ps"] == 5, "pd sets ps %s" % st["ps"])
    post("/json/state", {"ps": 7}); post("/json/state", {"playlist": {}}); st = json.loads(get("/json/state")[2]); ok(st["pl"] == -1, "empty playlist stops playlist")
    post("/json/state", {"ps": 7}); post("/json/state", {"seg": {"sx": 10}}); a = json.loads(get("/json/state")[2])["pl"]
    post("/json/state", {"seg": {"fx": 9}}); st = json.loads(get("/json/state")[2]); ok(a == 7 and st["pl"] == -1, "fx by hand stops playlist (%s -> %s)" % (a, st["pl"]))
    post("/json/state", {"psave": 8, "n": "Win", "o": True, "win": "FX=9"}); post("/json/state", {"playlist": {"ps": [8], "dur": 50}})
    st = json.loads(get("/json/state")[2]); ok(st["pl"] >= 0, "win preset keeps playlist"); post("/json/state", {"playlist": {}})
    post("/json/state", {"seg": {"fx": "~"}}); st = json.loads(get("/json/state")[2]); ok(st["seg"][0]["fx"] == 13 or st["seg"][0]["fx"] == 10, "fx ~ next -> %s" % st["seg"][0]["fx"])
    post("/json/state", {"bri": "~-50"}); st = json.loads(get("/json/state")[2]); ok(st["bri"] == 150, "bri relative %s" % st["bri"])
    post("/json/state", {"on": "t"}); st = json.loads(get("/json/state")[2]); ok(st["on"] is False, "toggle off")
    post("/json/state", {"on": "t", "seg": [{"id": 0, "i": [0, "FF0000", 2, 5, [0, 255, 0]]}]}); st = json.loads(get("/json/state")[2]); ok(st["seg"][0]["frz"] is True, "individual LEDs freeze")
    time.sleep(0.15); lv = json.loads(get("/json/live")[2]); ok(lv["leds"][0] != "000000" and lv["leds"][1] == "000000" and lv["leds"][3] != "000000", "live view %s" % lv["leds"][:5])
    s, b = post("/json/state", None, raw=b"{'seg':{'frz':false},'v':true}"); ok(s == 200 and json.loads(b)["seg"][0]["frz"] is False, "single-quote JSON")
    s, h, b = get("/win&A=77&FX=0&CL=h0000FF&T=1"); ok(b"<ac>77</ac>" in b and b"<fx>0</fx>" in b and b"<cl>255</cl>" in b, "HTTP API /win")
    s, b = post("/json/state", {"nl": {"on": True, "dur": 1, "mode": 1, "tbri": 0}, "v": True}); ok(json.loads(b)["nl"]["on"] is True, "nightlight on")
    post("/json/state", {"nl": {"on": False}})
    # upload presets.json via multipart
    bd = "XyZ123"; content = json.dumps({"0": {}, "1": {"n": "Up", "on": True, "seg": [{"id": 0, "fx": 8}]}}).encode()
    body = ("--%s\r\nContent-Disposition: form-data; name=\"data\"; filename=\"/presets.json\"\r\nContent-Type: application/json\r\n\r\n" % bd).encode() + content + ("\r\n--%s--\r\n" % bd).encode()
    s, b = post("/upload", None, raw=body, ctype="multipart/form-data; boundary=" + bd); ok(s == 200, "upload presets %s %s" % (s, b))
    pr = json.loads(get("/presets.json")[2]); ok(list(pr) == ["0", "1"], "presets replaced")
    post("/json/state", {"psave": 9, "n": "Boot", "o": True, "on": True, "bootps": 9}); pr = json.loads(get("/presets.json")[2]); inf = json.loads(get("/json/info")[2])
    ok(pr["9"] == {"n": "Boot", "on": True} and inf["leds"]["bootps"] == 9 and json.load(open(CFG))["defaults"]["boot_preset"] == 9, "bootps sets boot preset")
    # custom palette upload
    pal = json.dumps({"palette": [0, "ff0000", 128, "00ff00", 255, "0000ff"]}).encode()
    body = ("--%s\r\nContent-Disposition: form-data; name=\"data\"; filename=\"/palette0.json\"\r\n\r\n" % bd).encode() + pal + ("\r\n--%s--\r\n" % bd).encode()
    s, b = post("/upload", None, raw=body, ctype="multipart/form-data; boundary=" + bd)
    inf = json.loads(get("/json/info")[2]); ok(inf["cpalcount"] == 1, "custom palette count")
    px = json.loads(get("/json/palx?page=8")[2]); ok("255" in px["p"], "palx custom id 255 %s" % list(px["p"]))
    post("/json/state", {"seg": {"pal": 255}}); st = json.loads(get("/json/state")[2]); ok(st["seg"][0]["pal"] == 255, "select custom palette")
    post("/json/state", {"rmcpal": True}); inf = json.loads(get("/json/info")[2]); ok(inf["cpalcount"] == 0 and not os.path.exists(os.path.join(TMP, "data", "palette0.json")), "rmcpal removes custom palette")
    s, h, b = get("/edit?edit=/version-info.json"); ok(json.loads(b).get("neverAsk") is True, "WLED usage-report prompt suppressed")
    s, h, b = get("/settings"); ok(b"WLED-Pi settings" in b, "settings page")
    s, h, b = get("/"); ok(b"minimal control page" in b, "fallback index")
    cfg = json.loads(get("/api/config")[2]); ok(cfg["config"]["outputs"][1]["rgbw"] is True and cfg["pi"] == 0, "api/config")
    cfg["config"]["fps"] = 50; s, b = post("/api/config", {"config": cfg["config"]}); r = json.loads(b); ok(r["ok"] and r["config"]["fps"] == 50, "save config")
    ok(json.load(open(CFG))["fps"] == 50, "config persisted")
    s, h, b = get("/json/si"); ok(set(json.loads(b)) == {"state", "info"}, "/json/si")
    # websocket
    def ws_connect():
        so = socket.create_connection(("127.0.0.1", PORT)); key = base64.b64encode(os.urandom(16)).decode()
        so.sendall(("GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
        resp = b""
        while b"\r\n\r\n" not in resp: resp += so.recv(1)
        return so, resp
    def ws_send(so, text, op=1):
        p = text.encode() if isinstance(text, str) else text; m = os.urandom(4)
        hdr = bytes([0x80 | op]) + (bytes([0x80 | len(p)]) if len(p) < 126 else bytes([0x80 | 126]) + len(p).to_bytes(2, "big"))
        so.sendall(hdr + m + bytes(b ^ m[i % 4] for i, b in enumerate(p)))
    def recvn(so, n):
        d = b""
        while len(d) < n: d += so.recv(n - len(d))
        return d
    def ws_recv(so):
        h = recvn(so, 2); n = h[1] & 0x7F
        if n == 126: n = int.from_bytes(recvn(so, 2), "big")
        elif n == 127: n = int.from_bytes(recvn(so, 8), "big")
        return h[0] & 0x0F, recvn(so, n)
    so, resp = ws_connect(); ok(b"101" in resp.split(b"\r\n")[0], "ws upgrade")
    op, d = ws_recv(so); j = json.loads(d); ok(op == 1 and "state" in j and j["info"]["ws"] >= 1, "ws initial state, ws=%d" % j["info"]["ws"])
    ws_send(so, "{'v':true}"); op, d = ws_recv(so); ok("state" in json.loads(d), "ws v:true single quotes")
    ws_send(so, json.dumps({"bri": 99, "v": True})); op, d = ws_recv(so); ok(json.loads(d)["state"]["bri"] == 99, "ws command")
    ws_send(so, json.dumps({"lv": True})); got = None
    for _ in range(10):
        op, d = ws_recv(so)
        if op == 2: got = d; break
    ok(got is not None and got[:2] == b"L\x01" and len(got) == 2 + 150 * 3, "ws live binary frame len %s" % (len(got) if got else None))
    so2, _ = ws_connect(); ws_recv(so2)
    post("/json/state", {"bri": 123})
    seen = False
    for _ in range(20):
        op, d = ws_recv(so2)
        if op == 1 and json.loads(d)["state"]["bri"] == 123: seen = True; break
    ok(seen, "broadcast to other ws client")
    ws_send(so, b"\x03\xe8", op=8); so.close(); so2.close()
    s, b = post("/json/state", {"rb": True}); ok(s == 200, "rb reinit")
    print("done")
finally:
    proc.terminate()
    proc.wait(5)
_print("%d failure(s)" % len(FAILED))
sys.exit(1 if FAILED else 0)
