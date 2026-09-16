#!/bin/bash
# Provision a fresh Raspberry Pi OS Lite card for cardcam.
# Run ON THE PI after the first boot. Idempotent - safe to re-run.
set -euo pipefail
say() { printf '\n==> %s\n' "$*"; }

say "boot config: SPI, I2C, camera"
BOOT=/boot/firmware; [ -d "$BOOT" ] || BOOT=/boot
sudo cp "$BOOT/config.txt" "$BOOT/config.txt.bak-precardcam" 2>/dev/null || true
sudo sed -i 's/^#\?dtparam=spi=.*/dtparam=spi=on/;      t; $a dtparam=spi=on'      "$BOOT/config.txt"
sudo sed -i 's/^#\?dtparam=i2c_arm=.*/dtparam=i2c_arm=on/; t; $a dtparam=i2c_arm=on' "$BOOT/config.txt"
grep -q '^dtoverlay=imx708' "$BOOT/config.txt" || echo 'dtoverlay=imx708' | sudo tee -a "$BOOT/config.txt" >/dev/null
# PWM audio off GPIO18 so the e-Paper PWR pin keeps it. Enable when the speaker is wired.
grep -q 'audremap' "$BOOT/config.txt" || \
  printf '\n# cardcam: uncomment once the DFRobot speaker is wired\n#dtoverlay=audremap,pins_12_13\n' \
  | sudo tee -a "$BOOT/config.txt" >/dev/null

say "i2c-dev (dtparam alone does NOT create /dev/i2c-1)"
echo i2c-dev | sudo tee /etc/modules-load.d/i2c-dev.conf >/dev/null
sudo modprobe i2c-dev || true

say "packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3-picamera2 python3-spidev python3-gpiozero python3-lgpio python3-pil \
  i2c-tools rpicam-apps git

say "groups"
sudo usermod -aG i2c,spi,gpio,video,dialout "$USER"

say "wifi power-save off (the Zero 2 W drops off the network otherwise)"
sudo mkdir -p /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave = 2\n' \
  | sudo tee /etc/NetworkManager/conf.d/wifi-powersave-off.conf >/dev/null

say "persistent journal (so a crash is diagnosable after reboot)"
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\n' \
  | sudo tee /etc/systemd/journald.conf.d/persistent.conf >/dev/null
sudo mkdir -p /var/log/journal

say "e-Paper driver (2.13in V4) - the full repo is ~500MB and times out on a Zero"
D="$HOME/epdlib/waveshare_epd"; mkdir -p "$D"
B=https://raw.githubusercontent.com/waveshareteam/e-Paper/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd
for f in __init__.py epdconfig.py epd2in13_V4.py epd2in13_V3.py epd2in13_V2.py; do
  curl -fsSL -o "$D/$f" "$B/$f"
done
echo "   $(ls "$D" | wc -l) driver files"

say "cardcam service"
sudo install -m 644 "$HOME/cardcam/cardcam.service" /etc/systemd/system/cardcam.service
sudo systemctl daemon-reload
sudo systemctl enable cardcam.service

cat <<'DONE'

==> provisioning complete. Reboot to apply SPI/I2C and the group changes:
      sudo reboot

    After it comes back:
      rpicam-hello --list-cameras     # camera detected?
      i2cdetect -y 1                  # 0x57 = PiSugar 3
      systemctl status cardcam
      ~/cardcam/cardcam-trigger       # take a photo
DONE
