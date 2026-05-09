#!/bin/bash
# Configure the Pi Zero 2 W as a USB CDC ACM device (serial gadget).
# Run at boot before the bridge.py service starts.
#
# Result: /dev/ttyGS0 appears, and when the OTG cable is plugged into the
# TI-84 the calc enumerates this Pi as a standard USB serial device.

set -eu

modprobe libcomposite

GADGET=/sys/kernel/config/usb_gadget/ticalc

# If already configured, do nothing
if [ -d "$GADGET" ]; then
    exit 0
fi

mkdir -p "$GADGET"
cd "$GADGET"

# Linux Foundation generic IDs — calc identifies CDC by class, not VID/PID
echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "ticalc-pi-001"      > strings/0x409/serialnumber
echo "TiCalc"             > strings/0x409/manufacturer
echo "TiCalc Pi Bridge"   > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "CDC ACM"            > configs/c.1/strings/0x409/configuration
echo 250                  > configs/c.1/MaxPower

mkdir -p functions/acm.usb0
ln -s functions/acm.usb0 configs/c.1/

# Bind to the first USB device controller available. Fail loud if none —
# silently writing an empty UDC value "succeeds" but produces no
# /dev/ttyGS0, then the bridge spins forever in open_tty's retry loop.
UDC_DEV="$(ls /sys/class/udc 2>/dev/null | head -1)"
if [ -z "$UDC_DEV" ]; then
    echo "ERROR: no UDC available — check 'dtoverlay=dwc2,dr_mode=peripheral' is under [all] in /boot/firmware/config.txt and dwc2 module is loaded" >&2
    exit 1
fi
echo "$UDC_DEV" > UDC
echo "ticalc gadget bound to: $UDC_DEV"
