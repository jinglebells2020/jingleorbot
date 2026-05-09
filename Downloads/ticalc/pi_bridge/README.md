# TiCalc — Pi Zero 2 W Bridge

Replaces the ESP32-S3-CAM bridge. The Raspberry Pi Zero 2 W:

1. Acts as a USB CDC ACM device that the TI-84 CE talks to over USB-OTG.
2. Captures photos with the Pi Camera (CSI ribbon).
3. Calls the Claude Managed Agents API directly (no Railway server in between).

The calc-side `System32` program is **unchanged** — same protocol (`EVAL`, `ASK`, `ASKPHOTO`, `LINES`, `LINE n`, status replies prefixed with `>`).

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi Zero 2 W | 64-bit, 512MB RAM, built-in 2.4GHz WiFi |
| Pi Camera Module 3 (or v2 / HQ) | v3 has autofocus and is the recommended choice |
| 16GB+ microSD card | Pi OS Lite 64-bit |
| Micro-USB power supply (5V 2A+) | plug into PWR IN port |
| USB OTG cable (micro-USB to USB-A) | USB port → TI-84 |

## Wiring

- Pi Camera ribbon → CSI port on the Pi
- Pi `PWR IN` micro-USB → 5V wall supply (Pi *cannot* run from calc power; needs ~300-700mA)
- Pi `USB` micro-USB (the data port) → OTG cable → TI-84 USB-A

## Software setup

On a fresh Raspberry Pi OS Lite (64-bit):

```bash
git clone <this-repo> ~/ticalc && cd ~/ticalc/pi_bridge
sudo ./setup/install.sh   # installs deps, configures USB gadget, enables service
```

Then drop your `ANTHROPIC_API_KEY` into `/etc/ticalc.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

And configure WiFi via `raspi-config` or `/etc/wpa_supplicant/wpa_supplicant.conf`. For NYU enterprise WiFi:

```
network={
    ssid="nyu-legacy"
    key_mgmt=WPA-EAP
    eap=PEAP
    identity="<NetID>"
    password="<password>"
    phase2="auth=MSCHAPV2"
}
```

Reboot. The bridge should auto-start on boot, register itself as a USB CDC device when plugged into the calc, and respond to commands.

## Files

- `bridge.py` — main bridge process (USB CDC reader, camera capture, agent client)
- `setup/install.sh` — one-shot installer
- `setup/usb-gadget.sh` — sets up libcomposite USB CDC ACM gadget
- `setup/ticalc-bridge.service` — systemd unit
- `requirements.txt` — Python deps

## Protocol (same as ESP32 firmware)

| Calc command | Pi response |
|---|---|
| `EVAL <expr>` | `>` + result (e.g. `>4` for `EVAL 2+2`) |
| `ASK <text>` | status updates → answer lines available via `LINES`/`LINE n` |
| `ASKPHOTO <text>` | photo + status updates → answer lines |
| `LINES` | `>N` (number of answer lines) |
| `LINE <N>` | `>` + line content |
| `GET` | `>` + last status string |

Status updates during AI request: `S:CAMERA`, `S:WIFI`, `S:UPLOAD`, `S:WAITING`, `DONE`, `FAIL[:reason]`.

## Battery / power optimization

For battery (LiPo) operation, run on the Pi as root:

```bash
sudo ./setup/optimize-power.sh apply
sudo reboot
```

This masks the desktop env (lightdm + cascade), disables Bluetooth and
audio at the device-tree level, masks `nfs-blkmap` / `rpcbind` /
`avahi-daemon`, enables WiFi powersave, and drops swap. Calc-side
behavior is unchanged. Reverse with `sudo ./setup/optimize-power.sh
rollback && sudo reboot`. Inspect current state at any time with
`sudo ./setup/optimize-power.sh status`.

A/B-measure runtime from the Mac with `./setup/runtime-test.sh <pi-ip>
<label>` — polls SSH and writes a CSV row when the Pi powers off.

Design notes: `docs/superpowers/specs/2026-05-09-pi-battery-optimization-design.md`.
