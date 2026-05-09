#!/bin/bash
# pi_bridge/setup/optimize-power.sh
#
# Battery-runtime optimization for the Pi Zero 2 W ticalc bridge.
# Applies / rolls back the changes documented in
#   docs/superpowers/specs/2026-05-09-pi-battery-optimization-design.md
#
# Usage (run on the Pi as root):
#   sudo ./optimize-power.sh status     # show current state, no changes
#   sudo ./optimize-power.sh apply      # apply all five stages
#   sudo ./optimize-power.sh rollback   # undo

set -eu

CONFIG_TXT=/boot/firmware/config.txt
NEEDS_REBOOT=0

require_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "must be run as root: sudo $0 $*" >&2
    exit 1
  fi
}

active_wifi_conn() {
  nmcli -t -f NAME,DEVICE c show --active 2>/dev/null \
    | awk -F: '$2 ~ /^wlan/{print $1; exit}'
}

unit_state() {
  # prints "<enabled>/<active>" for a unit, "n/a" if not installed
  local u=$1
  if systemctl list-unit-files --no-legend "$u" "$u.service" 2>/dev/null | grep -q .; then
    printf '%s/%s' \
      "$(systemctl is-enabled "$u" 2>&1)" \
      "$(systemctl is-active "$u" 2>&1)"
  else
    printf 'n/a'
  fi
}

cmd_status() {
  echo "=== ticalc-bridge battery optimization status ==="
  for u in lightdm bluetooth hciuart alsa-state nfs-blkmap rpcbind avahi-daemon dphys-swapfile; do
    printf '  %-20s %s\n' "$u:" "$(unit_state "$u")"
  done
  echo
  local bt_lines
  bt_lines=$(awk '/^dtoverlay=disable-bt$/{c++}END{print c+0}' "$CONFIG_TXT")
  printf '  %-20s %s line(s)\n' 'config.txt disable-bt:' "$bt_lines"
  local audio_line
  audio_line=$(grep -m1 '^dtparam=audio=' "$CONFIG_TXT" 2>/dev/null || echo 'unset')
  printf '  %-20s %s\n' 'config.txt audio:' "$audio_line"
  echo
  local conn
  conn=$(active_wifi_conn || true)
  if [ -n "${conn:-}" ]; then
    printf '  %-20s %s\n' 'wifi conn:' "$conn"
    printf '  %-20s %s\n' 'wifi.powersave:' \
      "$(nmcli -g 802-11-wireless.powersave c show "$conn" 2>/dev/null || echo 'unknown')"
  else
    echo '  wifi conn:           none active'
  fi
  printf '  %-20s %s\n' 'swap state:' \
    "$(swapon --show=NAME,SIZE,USED --noheadings 2>/dev/null | head -1 || echo 'no swap')"
  printf '  %-20s %s\n' 'iw installed:' \
    "$(command -v iw >/dev/null && echo yes || echo no)"
  echo
  printf '  %-20s %s\n' 'ticalc-bridge:' "$(systemctl is-active ticalc-bridge 2>&1)"
  printf '  %-20s %s\n' 'ticalc-gadget:' "$(systemctl is-active ticalc-gadget 2>&1)"
}

cmd_apply()    { echo "TODO: implement apply"   >&2; exit 2; }
cmd_rollback() { echo "TODO: implement rollback" >&2; exit 2; }

case "${1:-status}" in
  apply)    cmd_apply ;;
  rollback) cmd_rollback ;;
  status)   cmd_status ;;
  *) echo "usage: $0 {apply|rollback|status}" >&2; exit 1 ;;
esac
