#!/bin/bash
# rescue-setup.sh - does everything custom.toml was supposed to do, from the
# FAT boot partition, in case the image ignored it.
#
# Armed from cmdline.txt with systemd.run. Disarms ITSELF first, so a bad run
# can never loop the boot. Logs to the boot partition, readable with no network.
set +e
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
LOG="$BOOT/rescue.log"

# ---- 1) DISARM IMMEDIATELY -------------------------------------------------
mount -o remount,rw "$BOOT" 2>/dev/null
sed -i 's| systemd\.run=[^ ]*||g; s| systemd\.run_success_action=[^ ]*||g; s| systemd\.unit=[^ ]*||g' "$BOOT/cmdline.txt" 2>/dev/null
sync

USERNAME='enes'
PASSWORD='DasFas10'
SSID='SpectrumSetup-D7_EXT'
PSK='uniteerrand912'
KEY1='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIzYji3T1myFLVKmu0T6s8mneDjSkJd4K3qrJSNdKCLf enes-pi'
KEY2='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH9qJxG4FrWHiYCI0w5gM/TWwi37u/wMSA86WU957Ju2 enes-mac'

{
  echo "=========== RESCUE SETUP $(date) ==========="
  mount -o remount,rw / 2>/dev/null

  echo "--- did custom.toml get applied at all? ---"
  ls -la "$BOOT"/custom.toml* 2>&1
  getent passwd "$USERNAME" || echo "!! user $USERNAME does NOT exist - custom.toml was ignored"
  ls -la /etc/NetworkManager/system-connections/ 2>&1

  echo "--- user ---"
  if ! getent passwd "$USERNAME" >/dev/null; then
    useradd -m -s /bin/bash "$USERNAME" 2>&1
    for g in sudo adm video audio i2c spi gpio dialout plugdev netdev users; do
      getent group "$g" >/dev/null && usermod -aG "$g" "$USERNAME" 2>&1
    done
  fi
  echo "$USERNAME:$PASSWORD" | chpasswd 2>&1
  echo "$USERNAME ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/010-"$USERNAME"-nopasswd
  chmod 440 /etc/sudoers.d/010-"$USERNAME"-nopasswd

  echo "--- ssh keys ---"
  HD=$(getent passwd "$USERNAME" | cut -d: -f6)
  mkdir -p "$HD/.ssh"
  printf '%s\n%s\n' "$KEY1" "$KEY2" >> "$HD/.ssh/authorized_keys"
  sort -u "$HD/.ssh/authorized_keys" -o "$HD/.ssh/authorized_keys"
  chmod 700 "$HD/.ssh"; chmod 600 "$HD/.ssh/authorized_keys"
  chown -R "$USERNAME:$USERNAME" "$HD/.ssh"

  echo "--- enable ssh ---"
  echo 'PasswordAuthentication yes' > /etc/ssh/sshd_config.d/99-passauth.conf
  systemctl enable --now ssh 2>&1 || systemctl enable --now sshd 2>&1

  echo "--- wifi profile ---"
  NMDIR=/etc/NetworkManager/system-connections
  mkdir -p "$NMDIR"
  cat > "$NMDIR/Spectrum.nmconnection" <<EOF
[connection]
id=Spectrum
uuid=$(cat /proc/sys/kernel/random/uuid)
type=wifi
interface-name=wlan0
autoconnect=true
autoconnect-priority=100

[wifi]
mode=infrastructure
ssid=$SSID
powersave=2

[wifi-security]
key-mgmt=wpa-psk
psk=$PSK

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default
EOF
  chmod 600 "$NMDIR/Spectrum.nmconnection"
  chown root:root "$NMDIR/Spectrum.nmconnection"

  mkdir -p /etc/NetworkManager/conf.d
  printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/wifi-powersave-off.conf

  echo "--- radio ---"
  rfkill unblock all 2>&1
  raspi-config nonint do_wifi_country US 2>&1
  iw reg set US 2>&1

  echo "--- bring up ---"
  systemctl enable --now NetworkManager 2>&1
  systemctl restart NetworkManager 2>&1
  sleep 12
  nmcli con reload 2>&1
  echo "--- visible networks ---"
  timeout 40 nmcli -f SSID,CHAN,SIGNAL,SECURITY dev wifi list --rescan yes 2>&1 | head -40
  timeout 45 nmcli con up Spectrum 2>&1
  sleep 8

  echo "--- rfkill ---";   rfkill list 2>&1
  echo "--- nmcli dev ---"; nmcli dev 2>&1
  echo "--- ip ---";       ip -br addr 2>&1
  echo "--- route ---";    ip route 2>&1
  echo "--- ping ---";     timeout 10 ping -c2 8.8.8.8 2>&1
  echo "=========== DONE $(date) ==========="
} >> "$LOG" 2>&1

sync
sleep 2
systemctl reboot 2>/dev/null || reboot 2>/dev/null || true
