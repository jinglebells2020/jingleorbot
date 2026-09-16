#!/usr/bin/env python3
"""cardcam - tap the PiSugar button, take a photo, show it on the e-Paper.

Runs as a service so the camera stays warm. Capturing from cold costs about
three seconds on a Zero 2 W, which is the difference between a shutter and a
wait. The trigger itself is a FIFO, so anything that can write a byte can fire
the shutter: PiSugar's tap hook, a GPIO button, ssh, a cron job.
"""

import logging
import os
import select
import signal
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "epdlib"))

from PIL import Image, ImageDraw, ImageFont, ImageOps
from picamera2 import Picamera2
from waveshare_epd import epd2in13_V4

try:
    from libcamera import controls
except ImportError:
    controls = None

TRIGGER   = Path(os.environ.get("CARDCAM_TRIGGER", "/run/cardcam/trigger"))
PHOTO_DIR = Path(os.environ.get("CARDCAM_PHOTOS", Path.home() / "cardcam" / "photos"))

# 2304x1296 is the sensor's 2x2 binned mode. Full 4608x2592 also works but a
# Zero 2 W only has 512 MB, and the capture takes roughly four times as long.
# A Zero 2 W has 416 MB usable and is already swapping. 2304x1296 buffers plus
# a decoded PIL copy is enough to push it into thrash, and the hardware watchdog
# then resets the board. 1536x864 is the same binned mode facewatch uses.
CAPTURE_SIZE = tuple(
    int(v) for v in os.environ.get("CARDCAM_SIZE", "1536x864").split("x")
)

# The sensor is mounted sideways in the enclosure. Degrees counter-clockwise
# applied to the photo before it is saved or shown.
ROTATE = int(os.environ.get("CARDCAM_ROTATE", "0"))

# Two taps inside this window are one press. PiSugar's own debounce is looser
# than this; the guard is here for the GPIO-button case.
DEBOUNCE_S = 1.5

PANEL_W, PANEL_H = 250, 122          # 2.13" V4, landscape
FONT_DIR = "/usr/share/fonts/truetype/dejavu"

log = logging.getLogger("cardcam")


def load_font(name, size):
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
    except OSError:
        return ImageFont.load_default()


def pisugar_battery(timeout=0.4):
    """Battery percent from the PiSugar daemon, or None if it isn't running."""
    try:
        with socket.create_connection(("127.0.0.1", 8423), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"get battery\n")
            reply = s.recv(256).decode("utf-8", "replace")
        for token in reply.replace("\n", " ").split():
            try:
                return float(token)
            except ValueError:
                continue
    except (OSError, socket.timeout):
        pass
    return None


def preview(path):
    """Decode the saved JPEG straight to preview size - cheap on a 416 MB box."""
    img = Image.open(path)
    # draft() lets libjpeg downscale during decode, so we never hold the
    # full-size bitmap in memory just to shrink it.
    img.draft("L", (PANEL_W * 3, PANEL_H * 3))
    img = img.convert("L")
    if ROTATE:
        img = img.rotate(ROTATE, expand=True)
    return fit_to_panel(img)


def fit_to_panel(src):
    """Centre-crop to the panel's aspect, then dither to 1-bit."""
    target = PANEL_W / PANEL_H
    w, h = src.size
    if w / h > target:
        new_w = int(h * target)
        box = ((w - new_w) // 2, 0, (w + new_w) // 2, h)
    else:
        new_h = int(w / target)
        box = (0, (h - new_h) // 2, w, (h + new_h) // 2)
    img = src.crop(box).resize((PANEL_W, PANEL_H), Image.LANCZOS).convert("L")
    # E-paper has no midtones, so stretch the range before Floyd-Steinberg
    # decides what becomes black.
    img = ImageOps.autocontrast(img, cutoff=2)
    return img.convert("1")


def caption(img, text):
    """Timestamp in the bottom-left, on a white plate so it stays readable."""
    d = ImageDraw.Draw(img)
    font = load_font("DejaVuSans.ttf", 11)
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = 3, PANEL_H - h - 7
    d.rectangle((x - 2, y - 2, x + w + 3, y + h + 3), fill=255)
    d.text((x, y), text, font=font, fill=0)
    return img


class Display:
    def __init__(self):
        self.epd = epd2in13_V4.EPD()

    def _wake(self):
        # V4 has a fast init that skips part of the LUT load.
        init = getattr(self.epd, "init_fast", None) or self.epd.init
        init()

    def show(self, img):
        self._wake()
        self.epd.display(self.epd.getbuffer(img))
        self.epd.sleep()

    def message(self, title, *lines):
        img = Image.new("1", (PANEL_W, PANEL_H), 255)
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, PANEL_W - 1, PANEL_H - 1), outline=0)
        d.text((12, 12), title, font=load_font("DejaVuSans-Bold.ttf", 22), fill=0)
        body = load_font("DejaVuSans.ttf", 13)
        for i, line in enumerate(lines):
            d.text((12, 48 + i * 18), line, font=body, fill=0)
        self.show(img)

    def close(self):
        try:
            self.epd.sleep()
        except Exception:
            pass


class Camera:
    def __init__(self):
        self.cam = Picamera2()
        cfg = self.cam.create_still_configuration(main={"size": CAPTURE_SIZE})
        self.cam.configure(cfg)
        self.cam.start()
        # Camera Module 3 has autofocus and it is off unless asked for.
        # Continuous keeps it ready, so a tap doesn't wait for a focus sweep.
        if controls is not None:
            try:
                self.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
            except Exception:
                log.warning("no autofocus on this sensor")
        time.sleep(2)          # let AE/AWB settle once, not on every shot

    def capture(self, path):
        # capture_file streams the JPEG to disk without a PIL copy in RAM.
        self.cam.capture_file(str(path))
        if ROTATE:
            img = Image.open(path).rotate(ROTATE, expand=True)
            img.save(path, "JPEG", quality=90)
            img.close()
        return path

    def close(self):
        try:
            self.cam.stop()
        except Exception:
            pass


def ensure_fifo(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_fifo():
        path.unlink()
    if not path.exists():
        os.mkfifo(path, 0o666)
    os.chmod(path, 0o666)      # PiSugar's hook runs as root, but be explicit


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    ensure_fifo(TRIGGER)

    display = Display()

    running = True

    def stop(signum, _frame):
        nonlocal running
        running = False
        log.info("signal %s, shutting down", signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    # Wait for the camera rather than dying. A loose CSI ribbon used to crash
    # the service on start, and systemd's restart loop then chewed through CPU
    # every five seconds. Now the panel says what is wrong and the daemon picks
    # the camera up by itself once the cable is reseated.
    camera = None
    warned = False
    while running and camera is None:
        try:
            log.info("opening camera")
            camera = Camera()
        except Exception as e:
            log.warning("camera unavailable: %s", e)
            if not warned:
                display.message("NO CAMERA", "check the CSI ribbon", "retrying every 10s")
                warned = True
            for _ in range(20):
                if not running:
                    break
                time.sleep(0.5)
    if camera is None:
        display.close()
        log.info("stopped before the camera appeared")
        return

    count = len(list(PHOTO_DIR.glob("*.jpg")))
    battery = pisugar_battery()
    display.message(
        "CARDCAM",
        "ready - tap to shoot",
        f"{count} photo{'s' if count != 1 else ''} on card"
        + (f"   {battery:.0f}%" if battery is not None else ""),
    )
    log.info("ready, waiting on %s", TRIGGER)

    # A plain open() on a FIFO blocks until a writer appears, which would make
    # SIGTERM unable to reach us and force systemd to SIGKILL on every stop.
    # Open non-blocking and poll with a short timeout instead. The second,
    # write-side handle is ours alone: it keeps the FIFO from reporting EOF
    # every time a trigger process exits.
    rfd = os.open(TRIGGER, os.O_RDONLY | os.O_NONBLOCK)
    wfd = os.open(TRIGGER, os.O_WRONLY)

    last = 0.0
    try:
        while running:
            try:
                ready, _, _ = select.select([rfd], [], [], 0.5)
            except OSError:
                continue
            if not ready:
                continue
            try:
                if not os.read(rfd, 4096):
                    continue
            except BlockingIOError:
                continue

            if not running:
                break

            now = time.monotonic()
            if now - last < DEBOUNCE_S:
                log.info("ignored bounce")
                continue
            last = now

            stamp = datetime.now()
            path = PHOTO_DIR / f"{stamp:%Y%m%d-%H%M%S}.jpg"
            try:
                t0 = time.monotonic()
                camera.capture(path)
                shot = time.monotonic() - t0

                frame = preview(path)
                label = f"{stamp:%H:%M:%S}"
                battery = pisugar_battery()
                if battery is not None:
                    label += f"   {battery:.0f}%"
                display.show(caption(frame, label))

                count += 1
                log.info("%s  (capture %.1fs, total %.1fs)",
                         path.name, shot, time.monotonic() - t0)
            except Exception:
                log.exception("capture failed")
                try:
                    display.message("CARDCAM", "capture failed", "see journalctl")
                except Exception:
                    pass
    finally:
        for fd in (rfd, wfd):
            try:
                os.close(fd)
            except OSError:
                pass
        camera.close()
        display.close()
        log.info("stopped")


if __name__ == "__main__":
    main()
