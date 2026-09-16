#!/bin/bash
# Flash Raspberry Pi OS onto an SD card and drop in the first-boot config.
# macOS. Run with sudo. Refuses to touch anything that isn't a removable card.
#
#   sudo bash flash-card.sh disk8
#
# The image and custom.toml default to the files sitting next to this script,
# so the whole command fits on one line and cannot get split by a line wrap.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DISK="${1:?usage: flash-card.sh <disk-id>   e.g. flash-card.sh disk8}"
IMG="${2:-$HERE/raspios-lite.img.xz}"
TOML="${3:-$HERE/custom.toml}"

[ -f "$IMG" ]  || { echo "image not found: $IMG"; exit 1; }
[ -f "$TOML" ] || { echo "custom.toml not found: $TOML"; exit 1; }
echo "image  : $IMG"
echo "config : $TOML"
DEV="/dev/$DISK"

# ---- safety: never write to a non-removable or internal system disk ---------
info=$(diskutil info "$DEV")
removable=$(awk -F': *' '/Removable Media/{print $2}' <<<"$info" | xargs)
protocol=$(awk -F': *' '/Protocol/{print $2}'        <<<"$info" | xargs)
bytes=$(awk -F'[()]' '/Disk Size/{print $2}'          <<<"$info" | awk '{print $1}')

echo "target : $DEV"
echo "  protocol  : $protocol"
echo "  removable : $removable"
echo "  size      : $bytes bytes"

[ "$DISK" = "disk0" ] && { echo "REFUSING: disk0 is the system disk."; exit 1; }
case "$removable" in Removable|Yes) ;; *) echo "REFUSING: not removable media."; exit 1;; esac
[ "${bytes:-0}" -gt 128000000000 ] && { echo "REFUSING: >128GB, that is not your card."; exit 1; }

echo
read -r -p "This ERASES $DEV completely. Type ERASE to continue: " ok
[ "$ok" = "ERASE" ] || { echo "aborted."; exit 1; }

echo "==> unmounting"
diskutil unmountDisk "$DEV"

echo "==> writing image (several minutes; ctrl-T shows progress)"
# rdisk = raw device, far faster than the buffered one
xz -dc "$IMG" | dd of="/dev/r$DISK" bs=4m

echo "==> flushing"
sync

echo "==> remounting boot partition"
diskutil mountDisk "$DEV" >/dev/null 2>&1 || true
for _ in $(seq 1 30); do [ -d /Volumes/bootfs ] && break; sleep 1; done
[ -d /Volumes/bootfs ] || { echo "bootfs did not mount - reinsert the card and copy custom.toml by hand"; exit 1; }

echo "==> installing first-boot config"
cp "$TOML" /Volumes/bootfs/custom.toml
ls -la /Volumes/bootfs/custom.toml
sync

echo "==> ejecting"
diskutil eject "$DEV"
echo
echo "Done. Put the card in the Pi and power up."
echo "First boot applies the user, SSH keys and WiFi, then reboots itself."
