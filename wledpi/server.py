"""Small asyncio HTTP/1.1 + WebSocket server (stdlib only) exposing WLED's API and web UI."""
import asyncio
import base64
import copy
import gzip
import hashlib
import json
import logging
import os
import re
import subprocess
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, WLED_VERSION, bootcfg
from .config import normalize, save as save_config
from .drivers import pi_generation, pin_warnings, resolve_driver, pio_nodes
from .effects import effect_names, effect_data
from .httpapi import apply_win, xml_state
from .state import loads_lenient

log = logging.getLogger("wledpi.http")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_BODY = 8 * 1024 * 1024
STATUS = {101: "Switching Protocols", 200: "OK", 204: "No Content", 304: "Not Modified", 400: "Bad Request",
          403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
          500: "Internal Server Error"}
MIME = {".htm": "text/html; charset=utf-8", ".html": "text/html; charset=utf-8", ".js": "application/javascript",
        ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
        ".jpg": "image/jpeg", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
        ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8", ".gz": "application/gzip"}
COMPRESSIBLE = ("text/", "application/javascript", "application/json", "image/svg+xml")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


class Req:
    def __init__(self, method, target, version, headers):
        self.method = method
        self.target = target
        self.version = version
        self.headers = headers
        u = urlsplit(target)
        self.path = unquote(u.path) or "/"
        self.raw_query = u.query
        self.query = {k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        self.body = b""


class Resp:
    def __init__(self, status=200, body=b"", ctype="text/plain; charset=utf-8", headers=None, after=None):
        self.status = status
        self.body = body.encode() if isinstance(body, str) else body
        self.ctype = ctype
        self.headers = headers or {}
        self.after = after
        self.cache = "no-cache"


def jresp(obj, status=200):
    return Resp(status, json.dumps(obj, separators=(",", ":")), "application/json")


class WsClient:
    def __init__(self, writer):
        self.w = writer
        self.live = False
        self.closed = False

    def _frame(self, op, payload):
        n = len(payload)
        if n < 126:
            hdr = bytes([0x80 | op, n])
        elif n < 65536:
            hdr = bytes([0x80 | op, 126]) + n.to_bytes(2, "big")
        else:
            hdr = bytes([0x80 | op, 127]) + n.to_bytes(8, "big")
        return hdr + payload

    def send(self, op, payload):
        if self.closed:
            return
        try:
            if self.w.transport.get_write_buffer_size() > 2 * 1024 * 1024:
                return  # slow client: drop frames rather than buffering forever
            self.w.write(self._frame(op, payload))
        except Exception:  # noqa
            self.closed = True

    def text(self, s):
        self.send(1, s.encode() if isinstance(s, str) else s)

    def binary(self, b):
        self.send(2, b)


async def read_ws_frame(reader):
    h = await reader.readexactly(2)
    fin = bool(h[0] & 0x80)
    op = h[0] & 0x0F
    masked = h[1] & 0x80
    n = h[1] & 0x7F
    if n == 126:
        n = int.from_bytes(await reader.readexactly(2), "big")
    elif n == 127:
        n = int.from_bytes(await reader.readexactly(8), "big")
    if n > 1024 * 1024:
        raise ConnectionError("websocket frame too large")
    mask = await reader.readexactly(4) if masked else None
    data = await reader.readexactly(n) if n else b""
    if mask and n:
        m = (mask * (n // 4 + 1))[:n]
        data = (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")
    return fin, op, data


class WledServer:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.cfg = ctrl.cfg
        self.clients = set()
        self.loop = None
        self._bc_pending = False
        self._stop = None
        self.exit_action = None      # None | "restart" | "reboot"
        self._gz = {}

    # ------------------------------------------------------------------ lifecycle
    async def run(self):
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self.ctrl.listeners.append(lambda: self.loop.call_soon_threadsafe(self.schedule_broadcast))
        bind = self.cfg["http"]["bind"]
        host = None if bind in ("0.0.0.0", "", "*") else bind
        srv = await asyncio.start_server(self.handle_conn, host, self.cfg["http"]["port"], reuse_address=True,
                                         limit=1 << 16)
        log.info("Web UI on port %d", self.cfg["http"]["port"])
        tasks = [asyncio.ensure_future(self._live_loop()), asyncio.ensure_future(self._persist_loop())]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            srv.close()
            for c in list(self.clients):
                c.closed = True
                try:
                    c.w.close()
                except Exception:  # noqa
                    pass
            try:
                await asyncio.wait_for(srv.wait_closed(), 2)
            except Exception:  # noqa
                pass
            self.ctrl.persist_if_needed(quiet_s=0)

    def stop(self, action=None):
        if action:
            self.exit_action = action
        if self._stop:
            self._stop.set()

    async def _persist_loop(self):
        while True:
            await asyncio.sleep(2)
            try:
                self.ctrl.persist_if_needed()
            except Exception:  # noqa
                log.exception("persist failed")

    async def _live_loop(self):
        last = -1
        while True:
            await asyncio.sleep(0.04)
            lv = [c for c in self.clients if c.live and not c.closed]
            if not lv:
                continue
            fn = self.ctrl.strip.frame_no
            if fn == last:
                continue
            last = fn
            data = self.ctrl.live_ws_bytes()
            for c in lv:
                c.binary(data)

    def schedule_broadcast(self):
        if self._bc_pending or not self.clients:
            return
        self._bc_pending = True
        self.loop.call_later(0.06, self._broadcast)

    def _broadcast(self):
        self._bc_pending = False
        if not self.clients:
            return
        txt = self.si_text()
        for c in list(self.clients):
            c.text(txt)

    def si_text(self):
        return json.dumps({"state": self.ctrl.state_json(), "info": self.ctrl.info_json()}, separators=(",", ":"))

    # ------------------------------------------------------------------ HTTP plumbing
    async def handle_conn(self, reader, writer):
        try:
            while True:
                try:
                    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=60)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError, asyncio.LimitOverrunError,
                        ConnectionError):
                    break
                req = self._parse_head(head)
                if req is None:
                    await self._send(writer, None, Resp(400, "Bad request"), False)
                    break
                te = req.headers.get("transfer-encoding", "").lower()
                if "chunked" in te:
                    req.body = await self._read_chunked(reader)
                else:
                    try:
                        n = int(req.headers.get("content-length", "0") or 0)
                    except ValueError:
                        n = 0
                    if n > MAX_BODY:
                        await self._send(writer, req, Resp(413, "Too large"), False)
                        break
                    req.body = await reader.readexactly(n) if n > 0 else b""
                if req.headers.get("upgrade", "").lower() == "websocket" and req.path == "/ws":
                    await self.handle_ws(req, reader, writer)
                    return
                try:
                    resp = await self.dispatch(req)
                except Exception:  # noqa
                    log.exception("Error handling %s %s", req.method, req.target)
                    resp = Resp(500, "Internal error")
                conn = req.headers.get("connection", "").lower()
                keep = (req.version == "HTTP/1.1" and conn != "close") or conn == "keep-alive"
                await self._send(writer, req, resp, keep)
                if resp.after:
                    resp.after()
                if not keep:
                    break
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa
                pass

    @staticmethod
    def _parse_head(head):
        try:
            lines = head.decode("latin-1").split("\r\n")
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            return None
        headers = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return Req(method.upper(), target, version.strip(), headers)

    @staticmethod
    async def _read_chunked(reader):
        out = bytearray()
        while True:
            line = await reader.readuntil(b"\r\n")
            size = int(line.split(b";")[0].strip() or b"0", 16)
            if size == 0:
                await reader.readuntil(b"\r\n")
                return bytes(out)
            out += await reader.readexactly(size)
            await reader.readexactly(2)
            if len(out) > MAX_BODY:
                raise ConnectionError("body too large")

    async def _send(self, writer, req, resp, keep):
        body = resp.body
        hdrs = {"Content-Type": resp.ctype, "Access-Control-Allow-Origin": "*",
                "Cache-Control": resp.cache, "Connection": "keep-alive" if keep else "close"}
        if req is not None and len(body) > 1024 and resp.status == 200 and \
                any(resp.ctype.startswith(c) for c in COMPRESSIBLE) and \
                "gzip" in req.headers.get("accept-encoding", "") and "Content-Encoding" not in resp.headers:
            key = resp.headers.pop("X-Gz-Key", None)
            gz = self._gz.get(key) if key else None
            if gz is None:
                gz = gzip.compress(body, 6)
                if key:
                    self._gz[key] = gz
            body = gz
            hdrs["Content-Encoding"] = "gzip"
            hdrs["Vary"] = "Accept-Encoding"
        resp.headers.pop("X-Gz-Key", None)
        hdrs.update(resp.headers)
        hdrs["Content-Length"] = str(len(body))
        head = "HTTP/1.1 %d %s\r\n" % (resp.status, STATUS.get(resp.status, "OK"))
        head += "".join("%s: %s\r\n" % kv for kv in hdrs.items()) + "\r\n"
        writer.write(head.encode("latin-1"))
        if body and (req is None or req.method != "HEAD"):
            writer.write(body)
        await writer.drain()

    # ------------------------------------------------------------------ websocket
    async def handle_ws(self, req, reader, writer):
        key = req.headers.get("sec-websocket-key")
        if not key:
            await self._send(writer, req, Resp(400, "Bad websocket request"), False)
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        await writer.drain()
        c = WsClient(writer)
        self.clients.add(c)
        self.ctrl.ws_count = len(self.clients)
        try:
            c.text(self.si_text())
            frag, frag_op = [], 0
            while not c.closed:
                fin, op, data = await read_ws_frame(reader)
                if op == 0:
                    frag.append(data)
                    if not fin:
                        continue
                    op, data, frag = frag_op, b"".join(frag), []
                elif op in (1, 2) and not fin:
                    frag, frag_op = [data], op
                    continue
                if op == 8:
                    c.send(8, data[:2])
                    break
                if op == 9:
                    c.send(10, data)
                    continue
                if op == 1:
                    self._ws_text(c, data.decode("utf-8", "replace"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:  # noqa
            log.exception("websocket error")
        finally:
            c.closed = True
            self.clients.discard(c)
            self.ctrl.ws_count = len(self.clients)
            try:
                writer.close()
            except Exception:  # noqa
                pass

    def _ws_text(self, c, text):
        t = text.strip()
        if t.startswith("p") or not t:
            c.text("pong")
            return
        try:
            obj = loads_lenient(t)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        if "lv" in obj:
            c.live = bool(obj["lv"])
        if set(obj) - {"v", "lv", "time"}:
            self.ctrl.apply_state(obj)
            if "psave" in obj or "pdel" in obj:
                pass
        if obj.get("v"):
            c.text(self.si_text())

    # ------------------------------------------------------------------ routing
    async def dispatch(self, req):
        m, p = req.method, req.path
        if m == "OPTIONS":
            return Resp(204, b"", headers={"Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                                           "Access-Control-Allow-Headers": "*"})
        if p.startswith("/win"):
            qs = req.target.split("/win", 1)[1]
            if "?" in qs:
                qs = qs.split("?", 1)[1]
            apply_win(self.ctrl, qs)
            return Resp(200, xml_state(self.ctrl), "text/xml")
        if p.startswith("/json"):
            return self._json(req)
        if p.startswith("/api/"):
            return self._api(req)
        if p == "/presets.json":
            return Resp(200, self.ctrl.presets.raw(), "application/json")
        if p == "/cfg.json":
            return jresp(self.ctrl.cfg_json())
        if re.fullmatch(r"/palette\d\.json", p) or p in ("/skin.css", "/ledmap.json"):
            f = os.path.join(self.ctrl.data_dir, p[1:])
            if os.path.isfile(f):
                return self._file(req, f)
            if p == "/skin.css":
                return Resp(200, "", "text/css")
            return Resp(404, "Not found")
        if p == "/upload" and m in ("POST", "PUT"):
            return self._upload(req)
        if p == "/edit":
            return self._edit(req)
        if p.startswith("/settings"):
            return self._file(req, os.path.join(WEB_DIR, "settings.html"))
        if p in ("/update", "/update.htm"):
            return self._file(req, os.path.join(WEB_DIR, "update.html"))
        if p in ("/liveview", "/liveview.htm"):
            return self._static(req, "liveview.htm")
        if p in ("/liveview2D", "/liveviewws2D.htm"):
            return self._static(req, "liveviewws2D.htm")
        if p in ("/reset", "/restart"):
            return Resp(200, '<meta http-equiv="refresh" content="6;url=/"><body style="background:#222;'
                             'color:#fff;font-family:sans-serif">Restarting WLED-Pi&hellip;</body>',
                        "text/html; charset=utf-8", after=lambda: self.stop("restart"))
        if m not in ("GET", "HEAD"):
            return Resp(405, "Method not allowed")
        return self._static(req, p)

    def _body_json(self, req):
        if not req.body:
            return {}
        return loads_lenient(req.body)

    def _json(self, req):
        p = req.path.rstrip("/")
        ctrl = self.ctrl
        if req.method == "POST":
            if p == "/json/cfg":
                return jresp({"success": True})
            try:
                obj = self._body_json(req)
            except ValueError:
                return jresp({"error": 9}, 400)
            if not isinstance(obj, dict):
                return jresp({"error": 9}, 400)
            ctrl.apply_state(obj)
            if not obj.get("v"):
                return jresp({"success": True})
            if p == "/json/state":
                return jresp(ctrl.state_json())
            if p == "/json/si":
                return jresp({"state": ctrl.state_json(), "info": ctrl.info_json()})
            return jresp(ctrl.full_json())
        if p == "/json":
            return jresp(ctrl.full_json())
        if p == "/json/state":
            return jresp(ctrl.state_json())
        if p == "/json/info":
            return jresp(ctrl.info_json())
        if p == "/json/si":
            return jresp({"state": ctrl.state_json(), "info": ctrl.info_json()})
        if p in ("/json/effects", "/json/eff"):
            return jresp(effect_names())
        if p == "/json/fxdata":
            return jresp(effect_data())
        if p in ("/json/palettes", "/json/pal"):
            return jresp(ctrl.palettes.names)
        if p == "/json/palx":
            try:
                page = int(req.query.get("page", "0"))
            except ValueError:
                page = 0
            return jresp(ctrl.palx(page))
        if p == "/json/live":
            return jresp(ctrl.live_json())
        if p == "/json/nodes":
            return jresp({"nodes": []})
        if p == "/json/net":
            return jresp({"networks": []})
        if p == "/json/cfg":
            return jresp(ctrl.cfg_json())
        return jresp({"error": 4}, 404)

    # ------------------------------------------------------------------ files
    def _file(self, req, full, ctype=None):
        try:
            st = os.stat(full)
        except OSError:
            return Resp(404, "Not found")
        etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
        if req.headers.get("if-none-match") == etag:
            r = Resp(304, b"", headers={"ETag": etag})
            return r
        with open(full, "rb") as f:
            data = f.read()
        ext = os.path.splitext(full)[1].lower()
        r = Resp(200, data, ctype or MIME.get(ext, "application/octet-stream"),
                 headers={"ETag": etag, "X-Gz-Key": "%s:%s" % (full, etag)})
        return r

    def _static(self, req, rel):
        base = os.path.realpath(self.cfg["www_dir"])
        rel = rel.lstrip("/") or "index.htm"
        full = os.path.realpath(os.path.join(base, rel))
        if full != base and not full.startswith(base + os.sep):
            return Resp(403, "Forbidden")
        if os.path.isdir(full):
            full = os.path.join(full, "index.htm")
        if os.path.isfile(full):
            return self._file(req, full)
        if rel == "cpal.htm" and os.path.isfile(os.path.join(base, "cpal", "cpal.htm")):
            return self._file(req, os.path.join(base, "cpal", "cpal.htm"))
        if rel in ("index.htm", "index.html"):
            return self._file(req, os.path.join(WEB_DIR, "fallback.html"))
        if rel == "favicon.ico":
            return Resp(204, b"")
        return Resp(404, "Not found")

    def _upload(self, req):
        ctype = req.headers.get("content-type", "")
        files = []
        if ctype.startswith("multipart/"):
            msg = BytesParser(policy=policy.HTTP).parsebytes(
                b"Content-Type: " + ctype.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + req.body)
            if msg.is_multipart():
                for part in msg.iter_parts():
                    fn = part.get_filename()
                    if fn:
                        files.append((fn, part.get_payload(decode=True) or b""))
        else:
            fn = req.query.get("name") or req.query.get("path")
            if fn:
                files.append((fn, req.body))
        if not files:
            return Resp(400, "No file")
        msgs = []
        for fn, data in files:
            name = os.path.basename(fn.replace("\\", "/"))
            if name == "presets.json":
                try:
                    obj = json.loads(data.decode("utf-8"))
                    self.ctrl.presets.replace_all(obj)
                except (ValueError, UnicodeDecodeError) as e:
                    return Resp(400, "presets.json is not valid: %s" % e)
                msgs.append("presets.json")
            elif re.fullmatch(r"palette\d\.json", name) or name in ("skin.css",):
                if name.endswith(".json"):
                    try:
                        json.loads(data.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        return Resp(400, "%s is not valid JSON" % name)
                tmp = os.path.join(self.ctrl.data_dir, "." + name + ".tmp")
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, os.path.join(self.ctrl.data_dir, name))
                msgs.append(name)
            else:
                return Resp(400, "Uploading %s is not supported by WLED-Pi" % name)
        with self.ctrl.strip.lock:
            self.ctrl.palettes.load_custom(self.ctrl.data_dir)
        self.ctrl.changed()
        return Resp(200, "File Uploaded! (%s)" % ", ".join(msgs))

    def _edit(self, req):
        if req.method == "GET" and req.query.get("edit", "").lstrip("/") == "version-info.json":
            # WLED's UI reads this file to decide whether to offer sending a hardware report to WLED's usage
            # statistics. A Pi is not a WLED device, so always answer "never ask".
            return jresp({"version": WLED_VERSION, "neverAsk": True})
        path = req.query.get("path") or req.query.get("delete") or ""
        func = req.query.get("func", "")
        if req.method == "DELETE" or func == "delete" or "delete" in req.query:
            name = os.path.basename(path)
            if not re.fullmatch(r"palette\d\.json", name):
                return Resp(400, "Only custom palette files can be deleted here")
            try:
                os.remove(os.path.join(self.ctrl.data_dir, name))
            except FileNotFoundError:
                return Resp(404, "Not found")
            with self.ctrl.strip.lock:
                self.ctrl.palettes.load_custom(self.ctrl.data_dir)
            self.ctrl.changed()
            return Resp(200, "Deleted")
        return Resp(404, "The file editor is not available in WLED-Pi")

    # ------------------------------------------------------------------ settings API
    def _api(self, req):
        p = req.path
        ctrl = self.ctrl
        model = ctrl.strip.model
        if p == "/api/config" and req.method == "GET":
            return jresp(self._config_status())
        if p == "/api/config" and req.method == "POST":
            try:
                body = self._body_json(req)
                new = normalize(body.get("config", body))
            except (ValueError, AttributeError, TypeError) as e:
                return jresp({"ok": False, "error": "Invalid settings: %s" % e}, 400)
            for k in ("data_dir", "www_dir", "assets"):
                new[k] = ctrl.cfg[k]            # paths are not editable from the web page
            old = copy.deepcopy(ctrl.cfg)
            if ctrl.cfg_path:
                try:
                    save_config(ctrl.cfg_path, new)
                except OSError as e:
                    return jresp({"ok": False, "error": "Could not save %s: %s" % (ctrl.cfg_path, e)}, 500)
            with ctrl.strip.lock:
                ctrl.cfg.clear()
                ctrl.cfg.update(new)
                if new["outputs"] != old["outputs"] or new["gamma"] != old["gamma"]:
                    ctrl.reinit()
                if new["defaults"]["transition"] != old["defaults"]["transition"]:
                    ctrl.strip.transition = new["defaults"]["transition"]
            ctrl.changed()
            st = self._config_status()
            st["ok"] = True
            st["restart_needed"] = new["http"] != old["http"]
            return jresp(st)
        if p == "/api/bootconfig" and req.method == "POST":
            lines = bootcfg.desired_lines(ctrl.cfg, model)
            try:
                path = bootcfg.write_block(lines)
            except OSError as e:
                return jresp({"ok": False, "error": "Could not write boot config: %s" % e}, 500)
            return jresp({"ok": True, "path": path, "lines": lines, "reboot_required": True})
        if p == "/api/restart" and req.method == "POST":
            return Resp(200, '{"ok":true}', "application/json", after=lambda: self.stop("restart"))
        if p == "/api/reboot" and req.method == "POST":
            return Resp(200, '{"ok":true}', "application/json", after=lambda: self.stop("reboot"))
        if p == "/api/status":
            return jresp(self._config_status())
        return jresp({"error": "not found"}, 404)

    def _config_status(self):
        ctrl = self.ctrl
        model = ctrl.strip.model
        gen = pi_generation(model)
        outs = []
        for i, o in enumerate(ctrl.cfg["outputs"]):
            live = ctrl.strip.outputs[i] if i < len(ctrl.strip.outputs) else None
            outs.append({"driver": resolve_driver(o["driver"], model),
                         "status": live.describe() if live else "",
                         "error": live.error if live else None,
                         "warnings": pin_warnings(o["gpio"])})
        return {"config": ctrl.cfg, "version": __version__, "model": model or "unknown", "pi": gen,
                "outputs": outs, "boot": bootcfg.status(ctrl.cfg, model),
                "pio_nodes": pio_nodes() if gen == 5 else [], "presets": len(ctrl.presets.ids()),
                "ui_installed": os.path.isfile(os.path.join(ctrl.cfg["www_dir"], "index.htm"))}


def reboot_system():
    try:
        subprocess.Popen(["systemctl", "reboot"])
    except OSError as e:
        log.error("reboot failed: %s", e)
