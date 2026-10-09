#!/usr/bin/env bash
# Removes WLED-Pi. Settings and presets are kept unless --purge is given.
set -euo pipefail
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1
if [ "$(id -u)" -ne 0 ]; then exec sudo "$0" "$@"; fi
systemctl disable --now wled-pi 2>/dev/null || true
rm -f /etc/systemd/system/wled-pi.service /etc/avahi/services/wled-pi.service
systemctl daemon-reload
systemctl reload avahi-daemon 2>/dev/null || true
if [ -x /opt/wled-pi/venv/bin/python ]; then
  /opt/wled-pi/venv/bin/python - <<'PY' || true
import sys
sys.path.insert(0, "/opt/wled-pi")
from wledpi import bootcfg
if bootcfg.read_block():
    print("Removed WLED-Pi lines from", bootcfg.write_block([]), "(reboot to apply)")
PY
fi
rm -rf /opt/wled-pi
if [ "$PURGE" = 1 ]; then
  rm -rf /etc/wled-pi /var/lib/wled-pi
  echo "WLED-Pi removed, including settings and presets."
else
  echo "WLED-Pi removed. Settings (/etc/wled-pi) and presets (/var/lib/wled-pi) were kept; use --purge to delete them."
fi
