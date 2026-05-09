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
