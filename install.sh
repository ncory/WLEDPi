#!/usr/bin/env bash
# WLED-Pi installer for Raspberry Pi OS (Trixie; Bookworm also works).
# Re-running it upgrades the code and keeps your settings and presets.
set -euo pipefail

PREFIX=/opt/wled-pi
ETC=/etc/wled-pi
DATA=/var/lib/wled-pi
PORT=""; GPIO=""; COUNT=""
ASSUME_YES=0; BOOT_CFG=ask; FETCH_UI=1; UI_FILE=""; UI_DIR=""; MDNS=1

usage() {
  cat <<EOF
Usage: sudo ./install.sh [options]
  --port N          web UI / API port (default 8080)
  --gpio N          LED data GPIO for the first output (default 12, keeps SPI free)
  --count N         number of LEDs on the first output (default 60)
  --yes             don't ask; also apply the boot config change (Pi 5 overlay / Pi 4 audio off)
  --no-boot-config  never touch /boot/firmware/config.txt
  --no-ui           skip downloading the WLED web UI
  --ui-file FILE    install the WLED UI from a downloaded WLED-*.tar.gz (offline install)
  --ui-dir DIR      install the WLED UI from a WLED git checkout (offline install)
  --no-mdns         don't publish the service via Avahi/mDNS
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --gpio) GPIO="$2"; shift 2;;
    --count) COUNT="$2"; shift 2;;
    --yes|-y) ASSUME_YES=1; [ "$BOOT_CFG" = ask ] && BOOT_CFG=yes; shift;;
    --no-boot-config) BOOT_CFG=no; shift;;
    --no-ui) FETCH_UI=0; shift;;
    --ui-file) UI_FILE="$(readlink -f "$2")"; shift 2;;
    --ui-dir) UI_DIR="$(readlink -f "$2")"; shift 2;;
    --no-mdns) MDNS=0; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown option: $1"; usage; exit 1;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then exec sudo "$0" "$@"; fi
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
ask()  { [ "$ASSUME_YES" = 1 ] && return 0; read -r -p "$1 [y/N] " a; [ "${a,,}" = y ] || [ "${a,,}" = yes ]; }

MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
case "$MODEL" in
  *"Raspberry Pi 5"*|*"Compute Module 5"*|*"Raspberry Pi 500"*) GEN=5;;
  *"Raspberry Pi"*|*"Compute Module"*) GEN=4;;
  *) GEN=0;;
esac
say "Detected: $MODEL"
[ "$GEN" = 0 ] && warn "Not a Raspberry Pi: LEDs will not be driven (virtual output only)."

# ---------------------------------------------------------------- packages
say "Installing system packages"
PKGS="python3 python3-venv python3-numpy"
[ "$GEN" = 4 ] && PKGS="$PKGS python3-dev gcc"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || warn "apt-get update failed; trying with the existing package lists"
# shellcheck disable=SC2086
apt-get install -y -qq --no-install-recommends $PKGS

# ---------------------------------------------------------------- code
if systemctl is-active --quiet wled-pi 2>/dev/null; then
  say "Stopping the running service"
  systemctl stop wled-pi
fi
say "Copying WLED-Pi to $PREFIX"
mkdir -p "$PREFIX"
rm -rf "$PREFIX/wledpi" "$PREFIX/web" "$PREFIX/tools"
cp -a "$SRC/wledpi" "$SRC/web" "$SRC/tools" "$PREFIX/"
cp -a "$SRC/README.md" "$SRC/LICENSE" "$SRC/config.example.json" "$SRC/uninstall.sh" "$PREFIX/" 2>/dev/null || true
find "$PREFIX/wledpi" -name '__pycache__' -prune -exec rm -rf {} +

if [ ! -x "$PREFIX/venv/bin/python" ]; then
  say "Creating Python environment"
  python3 -m venv --system-site-packages "$PREFIX/venv"
fi
if [ "$GEN" = 4 ]; then
  say "Installing rpi_ws281x (Pi 4 LED driver)"
  "$PREFIX/venv/bin/pip" install --quiet --upgrade rpi_ws281x || warn "rpi_ws281x install failed - LEDs won't light until it is installed"
fi
if [ "$GEN" = 5 ] && ! ls /boot/firmware/overlays/ws2812-pio.dtbo >/dev/null 2>&1; then
  warn "ws2812-pio overlay not found - update the system (sudo apt full-upgrade) and reboot first."
fi

# ---------------------------------------------------------------- config
mkdir -p "$ETC" "$DATA"
chmod 755 "$ETC" "$DATA"
"$PREFIX/venv/bin/python" - "$ETC/config.json" "${PORT:-}" "${GPIO:-}" "${COUNT:-}" <<'PY'
import json, os, sys
sys.path.insert(0, "/opt/wled-pi")
from wledpi import config
path, port, gpio, count = sys.argv[1:5]
fresh = not os.path.exists(path)
raw = {} if fresh else json.load(open(path))
cfg = config.normalize(raw)
if port:  cfg["http"]["port"] = int(port)
if gpio:  cfg["outputs"][0]["gpio"] = int(gpio)
if count: cfg["outputs"][0]["count"] = int(count)
config.save(path, cfg)
print(("Created " if fresh else "Updated ") + path)
PY
PORT_NOW="$("$PREFIX/venv/bin/python" -c "import json;print(json.load(open('$ETC/config.json'))['http']['port'])")"
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${PORT_NOW}\$"; then
  warn "Port $PORT_NOW is already in use by another program. Re-run with --port N to pick another."
fi

# ---------------------------------------------------------------- WLED web UI
if [ "$FETCH_UI" = 1 ]; then
  say "Installing the WLED web interface"
  ARGS=(--www "$PREFIX/www" --assets "$PREFIX/assets/palettes.json")
  [ -n "$UI_FILE" ] && ARGS+=(--from-file "$UI_FILE")
  [ -n "$UI_DIR" ] && ARGS+=(--from-dir "$UI_DIR")
  "$PREFIX/venv/bin/python" "$PREFIX/tools/fetch_wled_assets.py" "${ARGS[@]}" || \
    warn "Could not install the WLED UI (no internet?). A basic page is served until you run: sudo $PREFIX/venv/bin/python $PREFIX/tools/fetch_wled_assets.py"
fi

# ---------------------------------------------------------------- boot config
REBOOT=0
if [ "$GEN" != 0 ] && [ "$BOOT_CFG" != no ]; then
  STATUS="$("$PREFIX/venv/bin/python" - "$ETC/config.json" <<'PY'
import json, sys
sys.path.insert(0, "/opt/wled-pi")
from wledpi import config, bootcfg
from wledpi.drivers import pi_model
s = bootcfg.status(config.load(sys.argv[1]), pi_model())
print("ok" if s["in_sync"] or (not s["desired"] and not s["current"]) else "\n".join(s["desired"]) or "(remove)")
PY
)"
  if [ "$STATUS" != ok ]; then
    say "The LED driver needs these lines in $( [ -e /boot/firmware/config.txt ] && echo /boot/firmware/config.txt || echo /boot/config.txt ):"
    echo "$STATUS" | sed 's/^/      /'
    [ "$GEN" = 4 ] && echo "      (turns off the 3.5 mm analog audio, which shares the PWM hardware; HDMI audio is unaffected)"
    if [ "$BOOT_CFG" = yes ] || ask "Add them now? (a backup is kept)"; then
      "$PREFIX/venv/bin/python" - "$ETC/config.json" <<'PY'
import sys
sys.path.insert(0, "/opt/wled-pi")
from wledpi import config, bootcfg
from wledpi.drivers import pi_model
print("Updated", bootcfg.write_block(bootcfg.desired_lines(config.load(sys.argv[1]), pi_model())))
PY
      REBOOT=1
    else
      warn "Skipped. You can apply it later from the WLED-Pi settings page."
    fi
  fi
fi

# ---------------------------------------------------------------- systemd + mDNS
say "Installing the systemd service"
install -m 644 "$SRC/systemd/wled-pi.service" /etc/systemd/system/wled-pi.service
systemctl daemon-reload
systemctl enable wled-pi >/dev/null 2>&1

if [ "$MDNS" = 1 ] && [ -d /etc/avahi/services ]; then
  cat > /etc/avahi/services/wled-pi.service <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">WLED-Pi on %h</name>
  <service><type>_wled._tcp</type><port>${PORT_NOW}</port></service>
  <service><type>_http._tcp</type><port>${PORT_NOW}</port></service>
</service-group>
EOF
  systemctl reload avahi-daemon 2>/dev/null || true
fi

if [ "$REBOOT" = 1 ]; then
  say "Done. Reboot to activate the LED driver: sudo reboot"
  systemctl start wled-pi || true
else
  systemctl restart wled-pi
  say "Done."
fi
HOST="$(hostname)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "    Web UI:   http://${HOST}.local:${PORT_NOW}/   or   http://${IP}:${PORT_NOW}/"
echo "    Settings: http://${IP}:${PORT_NOW}/settings"
echo "    Logs:     journalctl -u wled-pi -f"
