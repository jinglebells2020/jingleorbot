# Pi Bridge Battery Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply five reversible system-config changes to the Pi Zero 2 W bridge to reduce idle draw, packaged as one idempotent script with `apply | rollback | status`.

**Architecture:** Single shell script on the Pi (`pi_bridge/setup/optimize-power.sh`) that masks unused services, edits `/boot/firmware/config.txt`, sets WiFi powersave via `nmcli`, and drops swap. A second shell script on the Mac (`pi_bridge/setup/runtime-test.sh`) drives the validation A/B by polling SSH until the Pi powers off and recording total runtime. No `bridge.py` or unit-file changes.

**Tech Stack:** bash, systemd, NetworkManager (`nmcli`), Pi config.txt dt-overlays, OpenSSH on the Mac side.

---

## Source spec

Implements [`docs/superpowers/specs/2026-05-09-pi-battery-optimization-design.md`](../specs/2026-05-09-pi-battery-optimization-design.md).
Re-read it before starting.

## Repo conventions in play

- Repo root is the user's home (`/Users/enes/`). The ticalc files live at `Downloads/ticalc/`. Only `Downloads/ticalc/pi_bridge/**` is currently tracked under that path; commits use absolute repo paths (e.g. `git add Downloads/ticalc/pi_bridge/setup/optimize-power.sh`).
- Pi SSH: `ssh -i ~/.ssh/id_ed25519_github enes@<ip>` (last seen `10.209.79.191`; DHCP — confirm with `ssh enes@<ip> hostname` returning `pinet`).
- The Pi exists; the bridge is running (`systemctl is-active ticalc-bridge` → `active`). Do NOT touch `/opt/ticalc/` from this plan.

## File structure

**Create:**
- `Downloads/ticalc/pi_bridge/setup/optimize-power.sh` — runs on the Pi as root. Subcommands `apply | rollback | status`. Idempotent.
- `Downloads/ticalc/pi_bridge/setup/runtime-test.sh` — runs on the Mac. Polls SSH until N consecutive failures, logs runtime to a CSV.

**Modify:**
- `Downloads/ticalc/pi_bridge/README.md` — short paragraph pointing at `optimize-power.sh apply` for power optimization.

**Untouched (do NOT modify):**
- `Downloads/ticalc/pi_bridge/bridge.py`
- `Downloads/ticalc/pi_bridge/setup/install.sh`
- `Downloads/ticalc/pi_bridge/setup/usb-gadget.sh`
- `Downloads/ticalc/pi_bridge/setup/ticalc-bridge.service`
- `Downloads/ticalc/pi_bridge/setup/ticalc-gadget.service`

## Test strategy

Shell config scripts can't really be unit-tested with mocks — every change is a system effect. We use a three-layer check per change:

1. **Static:** `bash -n optimize-power.sh` for syntax; `shellcheck` if available locally.
2. **Read-only oracle:** the script's own `status` subcommand prints current systemd / config.txt / nmcli / swap state. Run before & after each `apply` invocation; diff in the engineer's head.
3. **Runtime acceptance:** `bridge.py` continues to respond to `EVAL`/`ASK`/`ASKPHOTO`. Verified by:
   - `ssh enes@<ip> 'systemctl is-active ticalc-bridge ticalc-gadget && ls /dev/ttyGS0'` → both active, ttyGS0 present.

We are intentionally NOT building a bashunit/bats test file — the surface is too system-coupled and one-shot to justify the framework. Stages are split across tasks so problems surface stage-by-stage.

---

## Task 1: Scaffold `optimize-power.sh` with `status` only

This is read-only. Confirms the script can find every component before we touch any of them.

**Files:**
- Create: `Downloads/ticalc/pi_bridge/setup/optimize-power.sh`

- [ ] **Step 1: Write the script with only `status` implemented**

```bash
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
  printf '  %-20s %s\n' 'config.txt disable-bt:' \
    "$(grep -c '^dtoverlay=disable-bt' "$CONFIG_TXT" || echo 0) line(s)"
  printf '  %-20s %s\n' 'config.txt audio:' \
    "$(grep -m1 '^dtparam=audio=' "$CONFIG_TXT" 2>/dev/null || echo 'unset')"
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

cmd_apply()    { echo "TODO: implement apply"   >&2; exit 2; }
cmd_rollback() { echo "TODO: implement rollback" >&2; exit 2; }

case "${1:-status}" in
  apply)    cmd_apply ;;
  rollback) cmd_rollback ;;
  status)   cmd_status ;;
  *) echo "usage: $0 {apply|rollback|status}" >&2; exit 1 ;;
esac
```

Make it executable.

```bash
chmod +x /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh
```

- [ ] **Step 2: Static syntax check**

Run: `bash -n /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh`
Expected: exit 0, no output.

- [ ] **Step 3: Copy to Pi and run `status`**

Run from Mac:
```bash
scp -i ~/.ssh/id_ed25519_github \
  /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh \
  enes@10.209.79.191:/tmp/optimize-power.sh
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'sudo bash /tmp/optimize-power.sh status'
```

Expected: a block listing each unit (e.g. `lightdm: enabled/active`), `config.txt disable-bt: 0 line(s)`, `config.txt audio: dtparam=audio=on`, a wifi conn name, swap "55M used" or similar, `iw installed: no`, and both `ticalc-bridge: active` / `ticalc-gadget: active`.

If any line says `n/a` for a unit you expect (e.g. `dphys-swapfile: n/a`), that's fine — it just means the unit isn't installed; the script will treat that as "already OK" later.

- [ ] **Step 4: Commit Task 1**

```bash
cd /Users/enes
git add Downloads/ticalc/pi_bridge/setup/optimize-power.sh
git commit -m "ticalc/pi_bridge: scaffold optimize-power.sh with status subcommand

Read-only reporting first; apply/rollback come in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Implement `apply` for the no-reboot stages (1, 4, 5)

Stages that take effect immediately. Stage 1 (mask desktop), Stage 4 (cruft services), Stage 5 (WiFi powersave + drop swap). Stages 2/3 — the config.txt changes that need a reboot — come in Task 3, *before* we commit the user to a reboot.

**Files:**
- Modify: `Downloads/ticalc/pi_bridge/setup/optimize-power.sh`

- [ ] **Step 1: Add the helpers and `cmd_apply` body**

In `optimize-power.sh`, replace the placeholder `cmd_apply()` with this. Also add the two helpers `mask_unit` and `apt_install_iw` directly above `cmd_apply`.

```bash
mask_unit() {
  # Idempotent mask + stop. Silent no-op if the unit doesn't exist.
  local u=$1
  if systemctl list-unit-files --no-legend "$u" "$u.service" 2>/dev/null | grep -q .; then
    systemctl mask --now "$u" >/dev/null 2>&1 || true
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

  echo '==> Stage 2/5: disable Bluetooth — dt-overlay deferred (Task 3)'
  mask_unit bluetooth
  mask_unit hciuart

  echo '==> Stage 3/5: disable audio — dtparam deferred (Task 3)'
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
```

Note: `dtoverlay=disable-bt` and the audio dtparam edit are intentionally NOT added in this task. We commit and verify the no-reboot stages alone first, so any breakage is small and obvious.

- [ ] **Step 2: Static syntax check**

Run: `bash -n /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh`
Expected: exit 0, no output.

- [ ] **Step 3: Copy + apply on Pi**

Run from Mac:
```bash
scp -i ~/.ssh/id_ed25519_github \
  /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh \
  enes@10.209.79.191:/tmp/optimize-power.sh
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'sudo bash /tmp/optimize-power.sh apply'
```

Expected: stages logged, `apt-get install -y iw` runs (~5 s), `iw installed: yes` after. No reboot prompt.

- [ ] **Step 4: Verify on Pi**

```bash
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'sudo bash /tmp/optimize-power.sh status; \
   echo --- bridge alive ---; \
   systemctl is-active ticalc-bridge ticalc-gadget; \
   ls /dev/ttyGS0; \
   echo --- desktop dead? ---; \
   pgrep -af "lightdm|labwc|pcmanfm|wf-panel" || echo "no desktop procs"; \
   echo --- iw says ---; \
   iw dev wlan0 get power_save || true'
```

Expected:
- `lightdm: masked/inactive`, `bluetooth: masked/inactive`, `hciuart: masked/inactive`, `alsa-state: masked/inactive`, `nfs-blkmap: masked/inactive`, `rpcbind: masked/inactive`, `avahi-daemon: masked/inactive`, `dphys-swapfile: masked/inactive` (or `n/a/n/a` if not installed).
- `wifi.powersave: 3` (note: `iw` may still show `Power save: off` until the reboot in Task 3 — that's expected; the value is configured but not yet applied to a live connection).
- `swap state: no swap`.
- `iw installed: yes`.
- `ticalc-bridge: active`, `ticalc-gadget: active`, `/dev/ttyGS0` present.
- `no desktop procs`.

If `ticalc-bridge` is not active, **STOP** — something is wrong and you must not continue to the reboot stage.

- [ ] **Step 5: Commit Task 2**

```bash
cd /Users/enes
git add Downloads/ticalc/pi_bridge/setup/optimize-power.sh
git commit -m "ticalc/pi_bridge: optimize-power.sh apply for no-reboot stages

Stages 1, 4, 5 from the spec — mask desktop / cruft services,
WiFi powersave (configured, applies on reboot), drop swap, install iw.

config.txt edits (BT + audio) deferred to the next commit so the
reboot is committed to deliberately.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Add config.txt edits for stages 2 and 3, then reboot

Adds `dtoverlay=disable-bt` and flips `dtparam=audio=on` → `off`. These are persistent and require a reboot to take effect.

**Files:**
- Modify: `Downloads/ticalc/pi_bridge/setup/optimize-power.sh`

- [ ] **Step 1: Add config.txt helpers**

In `optimize-power.sh`, add these two helpers above `mask_unit`:

```bash
ensure_config_line() {
  # Append a line to config.txt if it isn't already present anywhere in the file.
  local line=$1
  if ! grep -qxF "$line" "$CONFIG_TXT"; then
    # Append at the end. The file already has an [all] section near the top
    # which is in scope for end-of-file content (Pi firmware reads to EOF).
    printf '%s\n' "$line" >> "$CONFIG_TXT"
    NEEDS_REBOOT=1
  fi
}

remove_config_line() {
  local line=$1
  if grep -qxF "$line" "$CONFIG_TXT"; then
    # \| ... \| is sed addressing using | as delimiter to avoid escaping /
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
```

- [ ] **Step 2: Wire the helpers into `cmd_apply`**

In `cmd_apply`, replace the two stub lines:

```bash
  echo '==> Stage 2/5: disable Bluetooth — dt-overlay deferred (Task 3)'
```

with:

```bash
  echo '==> Stage 2/5: disable Bluetooth (dt-overlay + mask)'
  ensure_config_line 'dtoverlay=disable-bt'
```

And replace:

```bash
  echo '==> Stage 3/5: disable audio — dtparam deferred (Task 3)'
```

with:

```bash
  echo '==> Stage 3/5: disable audio (dtparam=off + mask)'
  set_audio_param off
```

The order of `ensure_config_line` / `set_audio_param` *before* `mask_unit bluetooth/hciuart/alsa-state` is intentional — config.txt edits are the slower, higher-blast-radius operation; do them first and short-circuit on permission errors before masking units.

- [ ] **Step 3: Static syntax check + sanity diff of cmd_apply**

```bash
bash -n /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh
git -C /Users/enes diff Downloads/ticalc/pi_bridge/setup/optimize-power.sh
```

Verify the diff shows: two new helper functions, two edited lines inside `cmd_apply`, no other changes.

- [ ] **Step 4: Copy + apply on Pi (this run will set NEEDS_REBOOT=1)**

```bash
scp -i ~/.ssh/id_ed25519_github \
  /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh \
  enes@10.209.79.191:/tmp/optimize-power.sh
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'sudo bash /tmp/optimize-power.sh apply'
```

Expected:
- Stages 1, 4, 5 already-done (no errors; `mask --now` is idempotent).
- New: `==> Stage 2/5: disable Bluetooth (dt-overlay + mask)` — appends `dtoverlay=disable-bt` to `/boot/firmware/config.txt`.
- New: `==> Stage 3/5: disable audio (dtparam=off + mask)` — flips line in place.
- Final line: `REBOOT REQUIRED for stages 2 (BT) and 3 (audio).`

- [ ] **Step 5: Verify config.txt content (without rebooting yet)**

```bash
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'grep -E "^dtoverlay=disable-bt|^dtparam=audio" /boot/firmware/config.txt'
```

Expected exactly two lines (order may vary):
```
dtparam=audio=off
dtoverlay=disable-bt
```

If you see `dtparam=audio=on`, the sed didn't match — abort and inspect; do NOT reboot.

- [ ] **Step 6: Reboot the Pi and wait for SSH to come back**

```bash
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 'sudo reboot' || true
sleep 60
until ssh -i ~/.ssh/id_ed25519_github -o ConnectTimeout=5 enes@10.209.79.191 true 2>/dev/null; do
  printf '.'
  sleep 10
done
echo
echo 'Pi back up'
```

(NYU DHCP can hand the Pi a different IP after reboot. If `ssh` keeps failing for >3 min, re-discover with the TCP-22 sweep from `~/.claude/projects/-Users-enes/memory/pi_bridge_ssh.md`.)

- [ ] **Step 7: Verify post-reboot state — full acceptance**

```bash
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'sudo bash /tmp/optimize-power.sh status; \
   echo --- bluetooth gone? ---; \
   hciconfig 2>&1 | head -3 || echo "(hciconfig not installed)"; \
   ls /sys/class/bluetooth/ 2>&1; \
   echo --- iw says ---; \
   iw dev wlan0 get power_save; \
   echo --- bridge alive ---; \
   systemctl is-active ticalc-bridge ticalc-gadget; \
   ls /dev/ttyGS0'
```

Expected:
- `Power save: on` (after reboot, brcmfmac picks up the nmcli setting).
- `/sys/class/bluetooth/` empty or "No such file or directory" — BT chip not bound.
- `ticalc-bridge: active`, `ticalc-gadget: active`, `/dev/ttyGS0` present.
- All masked units still `masked/inactive`.

If `ticalc-bridge` is not active, **STOP**. Run `journalctl -u ticalc-bridge -n 30 --no-pager` to diagnose; rollback may be needed.

- [ ] **Step 8: Commit Task 3**

```bash
cd /Users/enes
git add Downloads/ticalc/pi_bridge/setup/optimize-power.sh
git commit -m "ticalc/pi_bridge: optimize-power.sh apply stages 2+3 (BT, audio)

Adds the config.txt-editing helpers and wires them into apply.
After running apply, /boot/firmware/config.txt gains
dtoverlay=disable-bt and flips dtparam=audio=on -> off; reboot
takes both effective.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Implement `rollback`

Mirror of `apply`. Tested on the Pi — must restore the system to a working desktop+BT+services configuration. Then we re-apply (we want to leave the Pi in the optimized state).

**Files:**
- Modify: `Downloads/ticalc/pi_bridge/setup/optimize-power.sh`

- [ ] **Step 1: Add `unmask_unit` helper and replace stub `cmd_rollback`**

Add above `cmd_apply`:

```bash
unmask_unit() {
  local u=$1
  if systemctl list-unit-files --no-legend "$u" "$u.service" 2>/dev/null | grep -q .; then
    systemctl unmask "$u" >/dev/null 2>&1 || true
    systemctl start  "$u" >/dev/null 2>&1 || true
  fi
}
```

Replace `cmd_rollback`:

```bash
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
```

- [ ] **Step 2: Static syntax check**

Run: `bash -n /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh`
Expected: exit 0.

- [ ] **Step 3: Copy + dry-test rollback on Pi**

```bash
scp -i ~/.ssh/id_ed25519_github \
  /Users/enes/Downloads/ticalc/pi_bridge/setup/optimize-power.sh \
  enes@10.209.79.191:/tmp/optimize-power.sh
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'sudo bash /tmp/optimize-power.sh rollback; \
   echo ---; \
   sudo bash /tmp/optimize-power.sh status'
```

Expected after `rollback`:
- All masked units now `enabled/active` (or `enabled/inactive` for one-shot units).
- `config.txt disable-bt: 0 line(s)`.
- `config.txt audio: dtparam=audio=on`.
- `wifi.powersave: 2`.
- swap state may be "no swap" still — `swapon -a` is a no-op if `/etc/fstab` has no swap entry; that's fine, the relevant rollback was unmasking dphys-swapfile so a future boot regenerates it.
- `REBOOT REQUIRED` printed.

`ticalc-bridge` and `ticalc-gadget` MUST still both be `active`. If they aren't, the rollback broke something — investigate before continuing.

- [ ] **Step 4: Re-apply (we want the Pi optimized)**

```bash
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'sudo bash /tmp/optimize-power.sh apply; \
   echo ---; \
   sudo bash /tmp/optimize-power.sh status'
```

Expected: same optimized state as the end of Task 3, plus `REBOOT REQUIRED` (because rollback added the `dtoverlay=disable-bt` and audio flip back).

- [ ] **Step 5: Reboot to settle**

```bash
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 'sudo reboot' || true
sleep 60
until ssh -i ~/.ssh/id_ed25519_github -o ConnectTimeout=5 enes@10.209.79.191 true 2>/dev/null; do
  sleep 10
done
ssh -i ~/.ssh/id_ed25519_github enes@10.209.79.191 \
  'iw dev wlan0 get power_save; systemctl is-active ticalc-bridge ticalc-gadget'
```

Expected: `Power save: on`, both services `active`.

- [ ] **Step 6: Commit Task 4**

```bash
cd /Users/enes
git add Downloads/ticalc/pi_bridge/setup/optimize-power.sh
git commit -m "ticalc/pi_bridge: optimize-power.sh rollback subcommand

Mirrors apply: unmasks every service, removes the BT dt-overlay,
flips audio back to on, restores nmcli wifi.powersave=2, unmasks
dphys-swapfile, and runs swapon -a.

Verified on-device: rollback then re-apply leaves the Pi in the
optimized state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Build `runtime-test.sh` for the Mac

Polls `ssh enes@<ip> true` from the Mac. First success marks `T_start`; five consecutive failures mark `T_end`. Logs a CSV row.

**Files:**
- Create: `Downloads/ticalc/pi_bridge/setup/runtime-test.sh`

- [ ] **Step 1: Write the script**

```bash
#!/bin/bash
# pi_bridge/setup/runtime-test.sh
#
# Run on the Mac. Polls SSH on the Pi until N consecutive failures,
# logging total runtime. Use this for the battery A/B test.
#
# Usage:
#   ./runtime-test.sh <ip> <label>
# e.g.
#   ./runtime-test.sh 10.209.79.191 baseline
#   ./runtime-test.sh 10.209.79.191 optimized

set -eu

PI_IP=${1:?usage: $0 <pi-ip> <label>}
LABEL=${2:?usage: $0 <pi-ip> <label>}
KEY=${SSH_KEY:-$HOME/.ssh/id_ed25519_github}
USER=${SSH_USER:-enes}
LOG=${LOG:-runtime-log.csv}
INTERVAL=${INTERVAL:-300}    # poll every 5 min once Pi is up
FAILS_TO_STOP=${FAILS_TO_STOP:-5}

ssh_alive() {
  ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 \
      -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR \
      "$USER@$PI_IP" true 2>/dev/null
}

ts() { date '+%H:%M:%S'; }
fmt_min() { printf '%dh%02dm' $(($1/60)) $(($1%60)); }

# Wait for first success
echo "[$(ts)] waiting for $USER@$PI_IP to come up..."
while ! ssh_alive; do sleep 30; done
T_START=$(date +%s)
echo "[$(ts)] T_start = $T_START — Pi is up"

# Steady-state polling
fails=0
last_success=$T_START
while [ "$fails" -lt "$FAILS_TO_STOP" ]; do
  sleep "$INTERVAL"
  if ssh_alive; then
    fails=0
    last_success=$(date +%s)
    elapsed_min=$(( (last_success - T_START) / 60 ))
    echo "[$(ts)] alive  (uptime $(fmt_min "$elapsed_min"))"
  else
    fails=$((fails+1))
    echo "[$(ts)] miss   $fails/$FAILS_TO_STOP"
  fi
done

T_END=$last_success
RUNTIME_SEC=$((T_END - T_START))
RUNTIME_MIN=$((RUNTIME_SEC/60))

echo "[$(ts)] Pi unreachable for $FAILS_TO_STOP polls."
echo "[$(ts)] Runtime: $(fmt_min "$RUNTIME_MIN") (${RUNTIME_SEC}s)"

# Append to CSV
if [ ! -f "$LOG" ]; then
  echo 'timestamp_utc,label,runtime_sec,runtime_min' > "$LOG"
fi
printf '%s,%s,%d,%d\n' \
  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$LABEL" "$RUNTIME_SEC" "$RUNTIME_MIN" \
  >> "$LOG"
echo "[$(ts)] appended to $LOG"
```

```bash
chmod +x /Users/enes/Downloads/ticalc/pi_bridge/setup/runtime-test.sh
```

- [ ] **Step 2: Static syntax check + brief liveness probe**

```bash
bash -n /Users/enes/Downloads/ticalc/pi_bridge/setup/runtime-test.sh
# 5-second sanity run with a tight interval — should see one "alive" then ctrl-c
( cd /tmp && \
  INTERVAL=5 FAILS_TO_STOP=999 \
  /Users/enes/Downloads/ticalc/pi_bridge/setup/runtime-test.sh \
    10.209.79.191 dryrun & PID=$!
  sleep 12; kill $PID 2>/dev/null )
```

Expected: a "Pi is up" line then one `alive (uptime 0h00m)` poll. Cancel before 5 misses are required.

- [ ] **Step 3: Commit Task 5**

```bash
cd /Users/enes
git add Downloads/ticalc/pi_bridge/setup/runtime-test.sh
git commit -m "ticalc/pi_bridge: runtime-test.sh for battery A/B measurement

Mac-side stopwatch driver: polls SSH every 5 min, marks runtime
between first success and 5 consecutive failures, appends CSV row.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: README note

Add a short paragraph to the existing `pi_bridge/README.md` so the optimization is discoverable.

**Files:**
- Modify: `Downloads/ticalc/pi_bridge/README.md`

- [ ] **Step 1: Append a section**

Append this to the end of `Downloads/ticalc/pi_bridge/README.md`:

```markdown

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
rollback && sudo reboot`. Inspect current state with `sudo
./setup/optimize-power.sh status`.

A/B-measure runtime from the Mac with `./setup/runtime-test.sh <pi-ip>
<label>` — polls SSH and writes a CSV row when the Pi powers off.

Design notes: `docs/superpowers/specs/2026-05-09-pi-battery-optimization-design.md`.
```

- [ ] **Step 2: Commit Task 6**

```bash
cd /Users/enes
git add Downloads/ticalc/pi_bridge/README.md
git commit -m "ticalc/pi_bridge: document optimize-power.sh / runtime-test.sh

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## After implementation: how to validate

(Implementer is the user — these tasks take hours and are not done by Claude.)

1. **Baseline run** *(do this BEFORE any apply happens, on a fresh charge)*
   - If you've already applied, run rollback + reboot first to get a clean baseline.
   - Charge LiPo to full. Boot Pi on LiPo.
   - On Mac: `cd /Users/enes/Downloads/ticalc/pi_bridge/setup && ./runtime-test.sh <ip> baseline`
   - Walk away. Returns when Pi powers off; row appended to `runtime-log.csv`.

2. **Apply optimization**
   - On Pi: `sudo /opt/ticalc/setup/optimize-power.sh apply && sudo reboot` (or scp the latest version of `optimize-power.sh` first if not deployed via `install.sh`).

3. **Optimized run**
   - Re-charge LiPo to the same end voltage.
   - On Mac: `./runtime-test.sh <ip> optimized`
   - Compare CSV rows. Acceptance threshold: ≥ 25 % improvement in `runtime_min`.

If improvement < 25 %, revisit risks in the spec — most likely culprit is brcmfmac powersave being effectively a no-op on this firmware, in which case other peripherals dominated the budget and the spec needs revising.

---

## Self-review checklist (run before handing this plan over)

- [ ] Spec section 4 — all five stages: covered by Task 2 (1, 4, 5) + Task 3 (2, 3).
- [ ] Spec section 5 — rollback risks: covered by Task 4.
- [ ] Spec section 6 — validation protocol: covered by Task 5 (script) + "After implementation" section (procedure).
- [ ] Spec section 7 — files to create/modify: every entry has a task. README at Task 6.
- [ ] Spec section 9 — acceptance criteria: each line has a verify step in Tasks 2, 3, 4, or post-validation.
- [ ] No placeholder strings (`TBD`, `TODO`, "implement later") except deliberate `cmd_apply`/`cmd_rollback` stubs in Task 1, which Task 2/4 fill in.
- [ ] Identifier consistency: `optimize-power.sh`, `runtime-test.sh`, `cmd_apply`, `cmd_rollback`, `cmd_status`, `mask_unit`, `unmask_unit`, `ensure_config_line`, `remove_config_line`, `set_audio_param`, `active_wifi_conn`, `unit_state`, `apt_install_iw`, `require_root` — used the same way in every task that references them.
- [ ] Each task ends with a commit. The commit message format matches recent repo style (`ticalc/pi_bridge: <imperative summary>` first line, body, Co-Authored-By trailer).
