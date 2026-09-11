#!/bin/bash
# Live I2C watch. Run this, then tighten the PiSugar standoffs while it scans -
# it prints the moment the board answers, so you get feedback without guessing.
#
#   ssh enes@192.168.1.120 '~/cardcam/watch-i2c.sh'
#
# PiSugar 3 = 0x57 (and often 0x68 for its RTC). Ctrl-C to stop.
BUS=${1:-1}
echo "watching i2c-$BUS ... tighten the screws now (Ctrl-C to stop)"
last=""
while true; do
  found=$(sudo i2cdetect -y -r "$BUS" 2>/dev/null \
          | tail -n +2 | cut -d: -f2 | tr ' ' '\n' | grep -v '^--$' | grep -v '^$' | tr '\n' ' ')
  if [ "$found" != "$last" ]; then
    if [ -z "$found" ]; then
      printf '%s  bus empty\n' "$(date +%H:%M:%S)"
    else
      printf '%s  FOUND: %s\n' "$(date +%H:%M:%S)" "$found"
    fi
    last="$found"
  fi
  sleep 2
done
