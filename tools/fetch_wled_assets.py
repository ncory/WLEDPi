#!/usr/bin/env python3
"""Fetch WLED's original web UI and palette data for WLED-Pi.

Downloads the WLED source release (EUPL-1.2 licensed, not redistributed by
WLED-Pi), copies wled00/data/ (the web UI) to the www directory and extracts
the gradient palettes into palettes.json.

  sudo /opt/wled-pi/venv/bin/python /opt/wled-pi/tools/fetch_wled_assets.py
  ... --from-file WLED-0.15.3.tar.gz     (offline: tarball downloaded elsewhere)
  ... --from-dir ~/WLED                  (offline: a WLED git checkout)
"""
import argparse
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request

TAG = "v0.15.3"
URL = "https://github.com/wled/WLED/archive/refs/tags/{tag}.tar.gz"


def read_sources_from_tar(data):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1].startswith("wled00/"):
                continue
            f = tf.extractfile(m)
            if f:
                files[parts[1]] = f.read()
    return files


def read_sources_from_dir(root):
    files = {}
    base = os.path.join(root, "wled00")
    for dp, dn, fn in os.walk(base):
        for n in fn:
            full = os.path.join(dp, n)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as f:
                files[rel] = f.read()
    return files


WS_HOST_RE = re.compile(r"location\.hostname(?=[^;\n]{0,40}/ws)")


def patch_ui_file(name, data):
    """WLED's UI builds its WebSocket URL from location.hostname (no port). Use location.host so the
    UI also works when served on a port other than 80."""
    if not (name.endswith(".js") or name.endswith(".htm") or name.endswith(".html")):
        return data, 0
    text = data.decode("utf-8", "surrogateescape")
    new, n = WS_HOST_RE.subn("location.host", text)
    return new.encode("utf-8", "surrogateescape"), n


def install_ui(files, www_dir):
    prefix = "wled00/data/"
    ui = {k[len(prefix):]: v for k, v in files.items() if k.startswith(prefix)}
    if "index.htm" not in ui:
        raise SystemExit("wled00/data/index.htm not found in the WLED source")
    tmp = www_dir.rstrip("/") + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    patched = 0
    for rel, data in ui.items():
        data, n = patch_ui_file(rel, data)
        patched += n
        dst = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
    old = www_dir.rstrip("/") + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(www_dir):
        os.rename(www_dir, old)
    os.rename(tmp, www_dir)
    shutil.rmtree(old, ignore_errors=True)
    return len(ui), patched


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _ints(body):
    out = []
    for tok in re.split(r"[\s,]+", body.strip()):
        if not tok:
            continue
        out.append(int(tok, 0))
    return out


def parse_palettes(files):
    arrays = {}
    order = []
    names = None
    for name, data in files.items():
        if not name.endswith((".h", ".cpp")):
            continue
        raw = data.decode("utf-8", "replace")
        if names is None:
            m = re.search(r'JSON_palette_names\s*\[\s*\]\s*(?:PROGMEM\s*)?=\s*R"([^()\s\\]{0,16})\((.*?)\)\1"', raw, re.S)
            if m:
                try:
                    names = json.loads(m.group(2))
                except ValueError:
                    pass
        text = _strip_comments(raw)
        for m in re.finditer(r"(?:static\s+)?const\s+(?:byte|uint8_t)\s+(\w+)\s*\[\s*\]\s*(?:PROGMEM\s*)?=\s*\{([^}]*)\}",
                             text):
            try:
                arrays[m.group(1)] = _ints(m.group(2))
            except ValueError:
                pass
        for m in re.finditer(r"DEFINE_GRADIENT_PALETTE\s*\(\s*(\w+)\s*\)\s*\{([^}]*)\}", text):
            try:
                arrays[m.group(1)] = _ints(m.group(2))
            except ValueError:
                pass
        m = re.search(r"gGradientPalettes\s*\[\s*\]\s*(?:PROGMEM\s*)?=\s*\{([^}]*)\}", text)
        if m:
            order = [t.strip() for t in m.group(1).split(",") if t.strip()]
    grads = {}
    for i, ident in enumerate(order):
        a = arrays.get(ident)
        if not a or len(a) % 4 or a[-4] != 255:
            print("  warning: palette %s not found or malformed" % ident, file=sys.stderr)
            continue
        grads[str(13 + i)] = a
    return grads, names


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default=TAG)
    ap.add_argument("--www", default="/opt/wled-pi/www")
    ap.add_argument("--assets", default="/opt/wled-pi/assets/palettes.json")
    ap.add_argument("--from-file")
    ap.add_argument("--from-dir")
    a = ap.parse_args()

    if a.from_dir:
        files = read_sources_from_dir(a.from_dir)
    else:
        if a.from_file:
            with open(a.from_file, "rb") as f:
                data = f.read()
        else:
            url = URL.format(tag=a.tag)
            print("Downloading %s" % url)
            req = urllib.request.Request(url, headers={"User-Agent": "wled-pi"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        files = read_sources_from_tar(data)
    if not files:
        raise SystemExit("No wled00/ sources found")
    n, patched = install_ui(files, a.www)
    print("Installed %d web UI files to %s (%d WebSocket URL patch%s)" % (n, a.www, patched, "" if patched == 1 else "es"))
    grads, names = parse_palettes(files)
    os.makedirs(os.path.dirname(a.assets), exist_ok=True)
    out = {"source": "WLED %s" % a.tag, "gradients": grads}
    if names:
        out["names"] = names
    tmp = a.assets + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, a.assets)
    print("Extracted %d gradient palettes%s to %s" % (len(grads), " and %d names" % len(names) if names else "",
                                                       a.assets))
    if len(grads) < 50:
        print("Warning: fewer gradient palettes than expected; some will fall back to Rainbow", file=sys.stderr)


if __name__ == "__main__":
    main()
