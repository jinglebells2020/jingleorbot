# cardcam

Tap the PiSugar button, take a photo, show it on the e-Paper.

## Why it's a service

Cold-starting picamera2 costs about three seconds on a Zero 2 W. The daemon keeps
the camera configured and running, so a tap only pays for the capture itself.

## The trigger is a FIFO

`/run/cardcam/trigger`. Anything that writes a byte fires the shutter:

    ./cardcam-trigger          # what PiSugar calls
    printf x > /run/cardcam/trigger

That means the PiSugar button isn't load-bearing. To add a GPIO button later,
point a `gpiozero.Button(26).when_pressed` at the same FIFO — no change here.

## Install

    ./install.sh

Needs the e-Paper driver at `~/epdlib/waveshare_epd/` and `python3-picamera2`.

## Layout

| | |
|---|---|
| `cardcam.py` | the daemon |
| `cardcam-trigger` | one-line shutter, called by PiSugar |
| `cardcam.service` | systemd unit |
| photos | `~/cardcam/photos/YYYYmmdd-HHMMSS.jpg` |

## Logs

    journalctl -u cardcam -f
