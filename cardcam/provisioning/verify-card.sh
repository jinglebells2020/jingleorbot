#!/bin/bash
# Is the rootfs actually intact on the card? Reads the ext4 superblock.
# The Pi never reached userspace, which is what a damaged root filesystem
# looks like from outside.  sudo bash verify-card.sh disk8
set -uo pipefail
DISK="${1:-disk8}"
echo "== unmounting =="
diskutil unmountDisk "/dev/$DISK" >/dev/null

echo "== ext4 superblock magic (want 53ef) =="
magic=$(dd if="/dev/r${DISK}s2" bs=1024 skip=1 count=1 2>/dev/null | xxd -s 56 -l 2 | awk '{print $2}')
echo "   got: ${magic:-<nothing read>}"
[ "$magic" = "53ef" ] && echo "   -> rootfs superblock OK" || echo "   -> ROOTFS BAD or unreadable"

echo "== can the card be read all the way to the end? =="
end=$(( $(diskutil info "/dev/$DISK" | awk -F'[()]' '/Disk Size/{print $2}' | awk '{print $1}') / 1048576 - 4 ))
if dd if="/dev/r$DISK" bs=1m skip="$end" count=2 of=/dev/null 2>/dev/null; then
  echo "   -> read OK at ${end}MB (card is not truncated/faking capacity)"
else
  echo "   -> READ FAILED near the end - the card is bad"
fi
diskutil mountDisk "/dev/$DISK" >/dev/null 2>&1 || true
echo "== done =="
