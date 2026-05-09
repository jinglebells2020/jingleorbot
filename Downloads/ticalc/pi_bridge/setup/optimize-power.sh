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

ensure_config_line() {
  # Append a line to config.txt if it isn't already present anywhere in the file.
  local line=$1
  if ! grep -qxF "$line" "$CONFIG_TXT"; then
    printf '%s\n' "$line" >> "$CONFIG_TXT"
    NEEDS_REBOOT=1
  fi
}

remove_config_line() {
  local line=$1
  if grep -qxF "$line" "$CONFIG_TXT"; then
    sed -i "\|^$(printf '%s' "$line" | sed 's|[][\.*^$/]|\\&|g')\$|d" "$CONFIG_TXT"
    NEEDS_REBOOT=1
  fi
}

set_audio_param() {
  # Flips dtparam=audio=on/off in place. Idempotent.
  local target=$1   # on | off
  local current
  current=$(grep -m1 '^dtparam=audio=' "$CONFIG_TXT" | cut -d= -f3 || true)
  if [ -z "$current" ]; then
    printf 'dtparam=audio=%s\n' "$target" >> "$CONFIG_TXT"
    NEEDS_REBOOT=1
  elif [ "$current" != "$target" ]; then
    sed -i "s/^dtparam=audio=$current\$/dtparam=audio=$target/" "$CONFIG_TXT"
    NEEDS_REBOOT=1
  fi
}

mask_unit() {
  # Idempotent mask + stop. Silent no-op if the unit doesn't exist.
  local u=$1
  if systemctl list-unit-files --no-legend "$u" "$u.service" 2>/dev/null | grep -q .; then
    systemctl mask --now "$u" >/dev/null 2>&1 || true
  fi
}

unmask_unit() {
  # Idempotent unmask. Start is --no-block: graphical units like lightdm can
  # block synchronous starts indefinitely on a headless Pi, and we don't need
  # to wait for them anyway — next reboot or systemd's own retry handles it.
  local u=$1
  if systemctl list-unit-files --no-legend "$u" "$u.service" 2>/dev/null | grep -q .; then
    systemctl unmask "$u" >/dev/null 2>&1 || true
    systemctl start --no-block "$u" >/dev/null 2>&1 || true
  fi
}

apt_install_iw() {
  if ! command -v iw >/dev/null; then
    apt-get update -qq
    apt-get install -y iw
  fi
}

cmd_apply() {
  require_root

  echo '==> Stage 1/5: mask desktop (lightdm + cascade)'
  mask_unit lightdm

  echo '==> Stage 2/5: disable Bluetooth (dt-overlay + mask)'
  ensure_config_line 'dtoverlay=disable-bt'
  mask_unit bluetooth
  mask_unit hciuart

  echo '==> Stage 3/5: disable audio (dtparam=off + mask)'
  set_audio_param off
  mask_unit alsa-state

  echo '==> Stage 4/5: mask cruft services'
  mask_unit nfs-blkmap
  mask_unit rpcbind
  mask_unit avahi-daemon

  echo '==> Stage 5/5: WiFi powersave + drop swap'
  local conn
  conn=$(active_wifi_conn || true)
  if [ -n "${conn:-}" ]; then
    nmcli c modify "$conn" wifi.powersave 3
    echo "    wifi.powersave=3 set on '$conn' (active after reboot)"
  else
    echo '    no active wifi connection; skipping powersave set'
  fi
  swapoff -a 2>/dev/null || true
  mask_unit dphys-swapfile

  echo '==> Bonus: install iw'
  apt_install_iw

  echo
  if [ "$NEEDS_REBOOT" = 1 ]; then
    echo 'REBOOT REQUIRED for stages 2 (BT) and 3 (audio).'
    echo 'Run:  sudo reboot'
  else
    echo 'Done — Task 3 will add the reboot-requiring changes.'
  fi
}

cmd_rollback() {
  require_root

  echo '==> Rolling back stage 1: unmask lightdm'
  unmask_unit lightdm

  echo '==> Rolling back stage 2: re-enable Bluetooth'
  remove_config_line 'dtoverlay=disable-bt'
  unmask_unit bluetooth
  unmask_unit hciuart

  echo '==> Rolling back stage 3: re-enable audio'
  set_audio_param on
  unmask_unit alsa-state

  echo '==> Rolling back stage 4: re-enable cruft services'
  unmask_unit nfs-blkmap
  unmask_unit rpcbind
  unmask_unit avahi-daemon

  echo '==> Rolling back stage 5: WiFi default + swap'
  local conn
  conn=$(active_wifi_conn || true)
  if [ -n "${conn:-}" ]; then
    nmcli c modify "$conn" wifi.powersave 2
  fi
  unmask_unit dphys-swapfile
  swapon -a 2>/dev/null || true

  echo
  if [ "$NEEDS_REBOOT" = 1 ]; then
    echo 'REBOOT REQUIRED to re-enable BT/audio drivers.'
  else
    echo 'Done — services back online.'
  fi
}

case "${1:-status}" in
  apply)    cmd_apply ;;
  rollback) cmd_rollback ;;
  status)   cmd_status ;;
  *) echo "usage: $0 {apply|rollback|status}" >&2; exit 1 ;;
esac
