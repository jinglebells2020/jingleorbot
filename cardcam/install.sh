#!/bin/bash
# Install cardcam on the Pi. Safe to re-run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> installing service"
sudo install -m 644 "$HERE/cardcam.service" /etc/systemd/system/cardcam.service
sudo systemctl daemon-reload
sudo systemctl enable --now cardcam.service
sleep 3
systemctl is-active --quiet cardcam.service \
  && echo "    cardcam.service running" \
  || { echo "    FAILED - journalctl -u cardcam -n 40"; exit 1; }

echo "==> binding the PiSugar button"
CFG=/etc/pisugar-server/config.json
if [ ! -f "$CFG" ]; then
  echo "    PiSugar daemon not installed - skipping."
  echo "    Attach the HAT, confirm 'i2cdetect -y 1' shows 0x57 or 0x75, then:"
  echo "      curl -sSL https://cdn.pisugar.com/release/pisugar-power-manager.sh | sudo bash"
  echo "    and re-run this script."
  exit 0
fi
command -v jq >/dev/null || sudo apt-get install -y jq
sudo cp "$CFG" "$CFG.bak-precardcam"
sudo jq '.single_tap_enable = true
       | .single_tap_shell  = "/home/enes/cardcam/cardcam-trigger"' \
     "$CFG.bak-precardcam" | sudo tee "$CFG" >/dev/null
sudo systemctl restart pisugar-server
echo "    single tap -> shutter. Backup at $CFG.bak-precardcam"
