# Pi Bridge Battery Optimization — Design

**Date:** 2026-05-09
**Target:** Raspberry Pi Zero 2 W running `ticalc-bridge` (USB-CDC ↔ Claude API bridge for TI-84 CE)
**Goal:** Maximize idle LiPo runtime without breaking the calc-side protocol or active request latency.

---

## 1. Context

The Pi sits idle ~95% of the time, waiting on USB-CDC for `EVAL`/`ASK`/`ASKPHOTO` from the calc.
The user is moving from wall power to a 3.7 V LiPo + LX-LBC01 generic boost converter.
There is **no battery telemetry** (boost is passive, no I²C, no PMIC HAT EEPROM). Validation will be
end-to-end runtime measurements, not in-software mA readings.

User constraint set:
- **Strip the desktop env** — confirmed.
- **Keep WiFi always on** — no WiFi-on-demand toggling.
- **Optimization target: idle runtime** — accept some active-path tradeoffs only if minor.

## 2. Baseline (measured 2026-05-09)

| Subsystem | State | Power impact at idle |
|---|---|---|
| OS | Pi OS Trixie **Desktop** (Debian 13, kernel 6.12.75) | Heavy — `lightdm`, `labwc`, `pcmanfm`, `wf-panel`, `xdg-desktop-portal-gtk`, `polkit-mate` all running idle. ~120 MiB RAM held; 55 MiB swap in use. |
| CPU | ondemand 600–1000 MHz, 94–99% idle, 47.8 °C | Healthy — no fix needed. |
| Bluetooth | `UP RUNNING INQUIRY` — actively scanning | Continuously burning power for a feature the bridge does not use. |
| Audio | `dtparam=audio=on`, no consumer | Cruft. |
| NFS-blkmap, rpcbind, avahi-daemon | enabled+running | Cruft (no NFS, mDNS already blocked on NYU WiFi). |
| WiFi power_save | unknown (`iw` not installed) | brcmfmac default varies; needs explicit `nmcli` setting. |
| Battery telemetry | none — empty I²C, no `/proc/device-tree/hat`, no battery driver | Out of scope; validation is stopwatch-based. |
| `bridge.py` | running fine, 40 MiB RSS, 1h22m uptime, 7.97 s CPU consumed | No change needed for this pass. |

## 3. Constraints — what must NOT change

- USB-CDC protocol on `/dev/ttyGS0` and the systemd units `ticalc-bridge.service` and `ticalc-gadget.service` are untouched.
- `bridge.py` and its venv are untouched. (Future passes can revisit; not this one.)
- Camera (`dtoverlay=imx708`) and USB gadget overlay (`dtoverlay=dwc2,dr_mode=peripheral`) stay enabled.
- WiFi remains always-on for incoming SSH and outbound Claude API traffic.
- All changes must be reversible via a single rollback command.

## 4. Approach — "Lite-equivalent + safe toggles"

Five idempotent stages applied by one script. Stages 2 and 3 require one reboot together at the end; the rest take effect immediately.

### Stage 1 — Mask the desktop stack (no reboot)

```
systemctl mask --now lightdm
```

`lightdm` is the parent of the auto-login user session. Masking it stops `user@1001` and the entire
desktop cascade (`labwc`, `pcmanfm`, `wf-panel-pi`, `xdg-desktop-portal*`, `polkit-mate-authentication-agent-1`,
`gvfs-*`).

Reversible via `systemctl unmask --now lightdm`.

### Stage 2 — Disable Bluetooth (reboot needed)

Append to `/boot/firmware/config.txt` (under `[all]`, idempotent):

```
dtoverlay=disable-bt
```

Then:

```
systemctl mask --now bluetooth hciuart
```

`disable-bt` only kills the BT side of the BCM43430 combo chip; WiFi stays up. Removes the active
`INQUIRY` scan observed in baseline.

Reversible by removing the dtoverlay line, `systemctl unmask` both, and rebooting.

### Stage 3 — Disable audio (reboot — bundled with Stage 2)

In `/boot/firmware/config.txt`:

```
dtparam=audio=on  →  dtparam=audio=off
```

```
systemctl mask --now alsa-state
```

Tiny but free. Reversible by flipping the param back and rebooting.

### Stage 4 — Mask cruft services (no reboot)

```
systemctl mask --now nfs-blkmap rpcbind avahi-daemon
```

Bridge does not use NFS or mDNS. Avahi was the only `.local` resolver on the Pi; not used from the Mac
(NYU WiFi blocks mDNS anyway — known from earlier discovery scan).

### Stage 5 — WiFi powersave + drop swap (no reboot)

```
conn=$(nmcli -t -f NAME,DEVICE c show --active | awk -F: '$2 ~ /^wlan/{print $1; exit}')
nmcli c modify "$conn" wifi.powersave 3
nmcli c up "$conn"

swapoff -a
systemctl mask --now dphys-swapfile
```

`wifi.powersave 3` enables kernel-level powersave (PSPOLL/UAPSD). brcmfmac honors this on Pi
Zero 2 W. The setting applies cleanly only after the Stage 2/3 reboot — the script writes the
config now and lets the reboot pick it up rather than bouncing WiFi twice.

`swapoff` is safe once Stage 1 is in: desktop was the cause of the 55 MiB swap pressure. Bridge has
`MemoryMax=400 M` and uses ~40 MiB; without desktop there's ~150 MiB free → no swap needed.

If `dphys-swapfile.service` is not installed (some images use zram instead), the mask is a no-op —
the script handles this case silently.

### Bonus — install `iw`

```
apt-get install -y iw
```

Lets us verify powersave (`iw dev wlan0 get power_save`) and inspect link state. Cheap and useful.

## 5. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| brcmfmac powersave intermittent disconnect after long idle | low | `nmcli c modify <conn> wifi.powersave 2` rolls back just this one knob; everything else stays. |
| Avahi off → can't reach `pinet.local` | n/a | Already non-functional on NYU WiFi; we use IP via DHCP. |
| Bluetooth disable affects WiFi | none | `dtoverlay=disable-bt` is documented to keep WiFi enabled on the BCM43430. |
| swapoff under future memory pressure | low | Free RAM rises ~150 MiB after Stage 1; bridge `MemoryMax=400 M` already enforced. |
| Reboot unmasks something unexpectedly | low | Stages are persistent (mask survives reboot, dtoverlay/dtparam survive reboot). |
| Pi powers off before SSH session ends during runtime test | n/a | Expected end-state of the test. |

## 6. Validation — stopwatch protocol

No telemetry is available on this hardware. Each run is a full battery discharge.

**Protocol (per run):**

1. Charge LiPo to the same full-charge end voltage (use the same charger).
2. Cold-boot Pi from LiPo.
3. From this Mac, poll `ssh enes@<ip> true` once a minute. First success → mark `T_start`.
4. Close SSH; do not log in again.
5. Continue pinging from Mac every 5 minutes. Five consecutive failures → mark `T_end`.
6. Runtime = `T_end − T_start − 25 min` (subtract the failure-detect window).

**Comparison:**

- Run A: baseline (current state, before any changes).
- Apply all 5 stages → reboot.
- Run B: optimized.
- Improvement = `(B − A) / A`.

`runtime-test.sh` (on this Mac) automates the polling and writes a CSV log so you don't have to
babysit. Same room, same ambient temperature, same charger both runs.

## 7. Files

**New:**
- `pi_bridge/setup/optimize-power.sh` — one idempotent script with subcommands `apply | rollback | status`. Run on the Pi as root.
- `pi_bridge/setup/runtime-test.sh` — run on this Mac. Polls `ssh true` until N consecutive failures, prints runtime, appends to `runtime-log.csv`.

**Modified on the Pi (by `apply`):**
- `/boot/firmware/config.txt` — append `dtoverlay=disable-bt`; flip `dtparam=audio=on` → `off`.
- systemd unit state for: `lightdm`, `bluetooth`, `hciuart`, `alsa-state`, `nfs-blkmap`, `rpcbind`, `avahi-daemon`, `dphys-swapfile` (all masked).
- NetworkManager active connection profile: `wifi.powersave=3`.

**Modified in repo:**
- `pi_bridge/README.md` — short note clarifying that the Desktop-imaged install can be made Lite-equivalent via `optimize-power.sh apply`.

**Untouched:**
- `pi_bridge/bridge.py`
- `pi_bridge/setup/install.sh`
- `pi_bridge/setup/usb-gadget.sh`
- `pi_bridge/setup/ticalc-bridge.service`
- `pi_bridge/setup/ticalc-gadget.service`

## 8. Explicitly out of scope

- HDMI/KMS framebuffer disable — fiddly under KMS, near-zero savings with no monitor attached.
- LED policy change in `bridge.py` — touching tested code for marginal gain.
- CPU governor switch — already idling at 600 MHz with 94 %+ idle.
- GPU mem split change — not the bottleneck.
- zram swap — replaced by simply removing swap (cleaner solution given current memory pressure root cause).
- WiFi-on-demand — explicit user constraint to keep WiFi always on.
- Adding a real PMIC HAT — hardware change, separate project.

## 9. Acceptance criteria

- After `apply`, `bridge.py` continues to serve `EVAL`, `ASK`, and `ASKPHOTO` over USB-CDC unchanged.
- After `apply`, `iw dev wlan0 get power_save` reports `Power save: on`.
- After `apply`, `systemctl is-active lightdm bluetooth hciuart alsa-state nfs-blkmap rpcbind avahi-daemon dphys-swapfile` reports `inactive` for all.
- After `apply`, `hciconfig` reports no Bluetooth device after reboot.
- `optimize-power.sh rollback` returns the system to behaviorally-baseline state (services running again, dtoverlay removed, swap re-enabled). Verified by `optimize-power.sh status`.
- Runtime improvement on the LiPo: target ≥ 25 % vs. baseline. (Estimate based on stripping desktop + BT + WiFi powersave; not guaranteed without telemetry.)
