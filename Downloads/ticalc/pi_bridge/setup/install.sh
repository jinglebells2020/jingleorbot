#!/bin/bash
# TiCalc Pi Bridge — one-shot installer for Raspberry Pi OS Lite (64-bit)
#
# What this does:
#  1. Installs system dependencies (python3, libcamera, picamera2, etc.)
#  2. Enables the dwc2 USB peripheral driver in /boot/firmware/config.txt
#     so the Pi can act as a USB device (gadget mode) instead of host
#  3. Loads libcomposite at boot (kernel module that builds CDC ACM gadgets
#     via sysfs)
#  4. Copies bridge.py + setup scripts to /opt/ticalc
#  5. Installs Python deps into a virtualenv at /opt/ticalc/venv
#  6. Installs and enables the two systemd services (gadget + bridge)
#
# After running, edit /etc/ticalc.env with your ANTHROPIC_API_KEY,
# then reboot. The bridge auto-starts.

set -eu

if [ "$EUID" -ne 0 ]; then
    echo "must be run as root: sudo $0" >&2
    exit 1
fi

# Resolve script location → repo location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR=/opt/ticalc

echo "==> 1. Installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip python3-libcamera \
                   python3-picamera2 libcamera-apps git

echo "==> 2. Enabling USB device (gadget) mode in /boot/firmware"
CONFIG=/boot/firmware/config.txt
CMDLINE=/boot/firmware/cmdline.txt
# Older Pi OS used /boot/ instead of /boot/firmware/
if [ ! -f "$CONFIG" ] && [ -f /boot/config.txt ]; then
    CONFIG=/boot/config.txt
    CMDLINE=/boot/cmdline.txt
fi

if ! grep -q "^dtoverlay=dwc2" "$CONFIG"; then
    echo "" >> "$CONFIG"
    echo "# TiCalc: enable USB device mode" >> "$CONFIG"
    echo "dtoverlay=dwc2,dr_mode=peripheral" >> "$CONFIG"
    echo "   added dtoverlay=dwc2 to $CONFIG"
fi

if ! grep -q "modules-load=dwc2" "$CMDLINE"; then
    # cmdline.txt is one line — append, don't add a newline
    sed -i 's| rootwait| rootwait modules-load=dwc2,libcomposite|' "$CMDLINE"
    echo "   added modules-load=dwc2,libcomposite to $CMDLINE"
fi

echo "==> 3. Copying bridge to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/bridge.py"        "$INSTALL_DIR/bridge.py"
cp "$REPO_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
mkdir -p "$INSTALL_DIR/setup"
cp "$SCRIPT_DIR/usb-gadget.sh"  "$INSTALL_DIR/setup/usb-gadget.sh"
chmod +x "$INSTALL_DIR/setup/usb-gadget.sh"

echo "==> 4. Installing Python deps into venv"
python3 -m venv "$INSTALL_DIR/venv" --system-site-packages
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# Patch the bridge service to use the venv's python
sed "s|/usr/bin/python3|$INSTALL_DIR/venv/bin/python|" \
    "$SCRIPT_DIR/ticalc-bridge.service" > /etc/systemd/system/ticalc-bridge.service
cp "$SCRIPT_DIR/ticalc-gadget.service" /etc/systemd/system/ticalc-gadget.service

echo "==> 5. Creating /etc/ticalc.env if missing"
if [ ! -f /etc/ticalc.env ]; then
    cat > /etc/ticalc.env <<'EOF'
# TiCalc bridge environment
# Set your Anthropic API key here, then reboot or `systemctl restart ticalc-bridge`
ANTHROPIC_API_KEY=
EOF
    chmod 600 /etc/ticalc.env
    echo "   created /etc/ticalc.env (edit it and set ANTHROPIC_API_KEY)"
else
    echo "   /etc/ticalc.env already exists, leaving alone"
fi

echo "==> 6. Enabling services"
systemctl daemon-reload
systemctl enable ticalc-gadget.service
systemctl enable ticalc-bridge.service

echo ""
echo "──────────────────────────────────────────────────────────────"
echo " Install complete."
echo ""
echo " Before reboot:"
echo "   1. Edit /etc/ticalc.env and set ANTHROPIC_API_KEY"
echo "   2. Configure WiFi (raspi-config or /etc/wpa_supplicant/...)"
echo ""
echo " Then reboot:  sudo reboot"
echo ""
echo " On boot the Pi should:"
echo "   - Bring up libcomposite USB CDC gadget at /dev/ttyGS0"
echo "   - Start ticalc-bridge.service"
echo "   - Be ready to accept calc commands when plugged into TI-84 USB"
echo ""
echo " Check status:    systemctl status ticalc-bridge"
echo " Watch logs:      journalctl -u ticalc-bridge -f"
echo "──────────────────────────────────────────────────────────────"
