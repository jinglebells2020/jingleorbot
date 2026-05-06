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

# Bind to the first USB device controller available
ls /sys/class/udc | head -1 > UDC

echo "ticalc gadget bound to: $(cat UDC)"
