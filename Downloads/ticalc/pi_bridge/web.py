#!/usr/bin/env python3
"""ticalc Pi camera — streaming + frame-buffer capture.

Live MJPEG view of the Pi camera with adjustable quality and resolution.
Keeps the last 15 frames in a rolling in-memory buffer; click "Capture"
and the whole buffer gets dumped to ~/Downloads/ticalc-shots/capture_<ts>/
as numbered JPEGs.

Run:
    python3 /Users/enes/Downloads/ticalc/pi_bridge/web.py
Then open http://localhost:9090/
"""

import collections
import datetime
import http.client
import io
import json
import mimetypes
import queue
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ── Wiring ─────────────────────────────────────────────────────────
PI_HOST      = "10.209.79.191"
PI_HTTP_PORT = 8080
LISTEN       = ("0.0.0.0", 9090)
SAVE_DIR     = Path.home() / "Downloads" / "ticalc-shots"
BUFFER_SIZE  = 15


# ── Shared state ───────────────────────────────────────────────────
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.log = collections.deque(maxlen=600)
        self.subscribers = set()           # SSE client queues
        self.pi_status = {"ping": "?", "http": "?", "checked": None}
        self.pi_system = None              # /api/system snapshot or None
        self.pi_system_at = None           # datetime of last successful fetch
        self.batches_saved = 0
        self.last_batch = None
        self.last_batch_at = None
        self.start_time = time.time()
        # Rolling JPEG buffer + its own lock so the proxy can update it
        # without contending with the bigger state lock.
        self.buffer_lock = threading.Lock()
        self.frame_buffer = collections.deque(maxlen=BUFFER_SIZE)
        self.frames_seen = 0
        # ESP32 button-signal monitor — populated by /api/button POSTs.
        # button_history keeps the last 200 events (press/release/hello),
        # which the dashboard uses to draw a logic-analyzer trace over the
        # last 60s and a scrollable recent-events list. Poll heartbeats
        # are not recorded — they'd swamp the buffer without adding info.
        self.button_history = collections.deque(maxlen=200)
        self.button = {
            "state": "unknown",          # "pressed" | "released" | "unknown"
            "last_event": None,          # "press" | "release" | "hello" | "poll"
            "last_at": None,             # ISO time of last event (for sub)
            "last_at_ts": None,          # epoch seconds (for "Xs ago" math)
            "press_count": 0,
            "last_duration_ms": None,    # duration of most-recent completed press
            "client_ip": None,
            "uptime_ms": None,           # ESP uptime at last event
            "rssi": None,                # WiFi RSSI at last event (dBm)
            "esp_ip": None,              # IP the ESP self-reported
        }
        self._press_started_at = None    # epoch seconds; cleared on release

state = State()


def now_str():
    return datetime.datetime.now().strftime("%H:%M:%S")


def push(src, msg):
    evt = {"t": now_str(), "src": src, "msg": msg, "kind": "log"}
    with state.lock:
        state.log.append(evt)
        subs = list(state.subscribers)
    for q in subs:
        try: q.put_nowait(evt)
        except queue.Full: pass


def build_status():
    with state.lock:
        pi = dict(state.pi_status)
        batches = state.batches_saved
        last = state.last_batch
        last_at = state.last_batch_at.strftime("%H:%M:%S") if state.last_batch_at else None
    pi["checked"] = pi["checked"].strftime("%H:%M:%S") if pi.get("checked") else None
    with state.buffer_lock:
        buf_count = len(state.frame_buffer)
        frames_seen = state.frames_seen
    now = time.time()
    with state.lock:
        sys_snap = state.pi_system
        sys_at = state.pi_system_at.strftime("%H:%M:%S") if state.pi_system_at else None
        btn = dict(state.button)
        # Only ship history newer than 5 min — older events aren't useful
        # for the 60s trace chart or the recent-events list, and keep
        # the SSE payload bounded.
        cutoff = now - 300
        history = [dict(e) for e in state.button_history if e["t"] >= cutoff]
    # Compute "Xs ago" sub for the SIGNAL panel; the wall-clock age is more
    # useful than the absolute time when the dashboard reconnects.
    if btn.get("last_at_ts") is not None:
        btn["age_s"] = max(0, int(now - btn["last_at_ts"]))
    else:
        btn["age_s"] = None
    return {
        "pi": pi,
        "buffer": {"count": buf_count, "max": BUFFER_SIZE, "frames_seen": frames_seen},
        "batches_saved": batches,
        "last_batch": last,
        "last_batch_at": last_at,
        "save_dir": str(SAVE_DIR),
        "uptime": int(now - state.start_time),
        "pi_system": sys_snap,
        "pi_system_at": sys_at,
        "button": btn,
        "button_history": history,
        "server_now": now,
    }


def push_status():
    snap = build_status()
    snap["kind"] = "status"
    with state.lock:
        subs = list(state.subscribers)
    for q in subs:
        try: q.put_nowait(snap)
        except queue.Full: pass


def record_button_event(payload, client_ip):
    """Update button state from an ESP32 /api/button POST. Returns the
    log-friendly message string. Handles press/release/hello/poll events;
    completes a press by computing duration on the matching release."""
    evt = str(payload.get("event", "")).lower().strip()
    uptime_ms = payload.get("uptime_ms")
    rssi      = payload.get("rssi")
    esp_ip    = payload.get("ip")
    now = time.time()
    iso_now = datetime.datetime.now().strftime("%H:%M:%S")
    msg = None
    with state.lock:
        b = state.button
        b["client_ip"] = client_ip
        if uptime_ms is not None:
            try: b["uptime_ms"] = int(uptime_ms)
            except (TypeError, ValueError): pass
        if rssi is not None:
            try: b["rssi"] = int(rssi)
            except (TypeError, ValueError): pass
        if esp_ip:
            b["esp_ip"] = str(esp_ip)
        b["last_event"] = evt or "?"
        b["last_at"]    = iso_now
        b["last_at_ts"] = now
        if evt == "press":
            b["state"] = "pressed"
            b["press_count"] += 1
            state._press_started_at = now
            msg = f"PRESS  · #{b['press_count']}  · uptime {b['uptime_ms']}ms"
        elif evt == "release":
            b["state"] = "released"
            held_ms = payload.get("held_ms")
            try:
                if held_ms is not None:
                    held_ms = int(held_ms)
            except (TypeError, ValueError):
                held_ms = None
            if held_ms is not None and held_ms >= 0:
                # ESP-measured (between debounced edges) — beats arrival-time math.
                b["last_duration_ms"] = held_ms
            elif state._press_started_at is not None:
                b["last_duration_ms"] = int((now - state._press_started_at) * 1000)
            state._press_started_at = None
            dur = b["last_duration_ms"]
            msg = f"RELEASE · {dur}ms held" if dur is not None else "RELEASE"
        elif evt == "hello":
            # boot/handshake — adopt initial level if reported
            init = str(payload.get("level", "")).lower()
            if init in ("pressed", "released"):
                b["state"] = init
            msg = f"HELLO  · esp@{b['esp_ip'] or client_ip}  · rssi {b['rssi']}dBm"
        elif evt == "poll":
            # heartbeat — keeps last_at fresh; no log spam
            msg = None
        else:
            msg = f"event={evt!r} (ignored)"
        # Record press/release/hello in the rolling history for the trace
        # chart. Polls are skipped (signal: noise too low).
        if evt in ("press", "release", "hello"):
            state.button_history.append({
                "t": now,
                "evt": evt,
                "duration_ms": b.get("last_duration_ms") if evt == "release" else None,
            })
    if msg is not None:
        push("btn", msg)
    push_status()


# ── Pi status poller ───────────────────────────────────────────────
def _pi_check_once():
    out = {"ping": "?", "http": "?", "checked": datetime.datetime.now()}
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "2000", PI_HOST],
                           capture_output=True, timeout=3)
        out["ping"] = "up" if r.returncode == 0 else "down"
    except Exception:
        out["ping"] = "err"
    if out["ping"] != "up":
        return out
    try:
        c = http.client.HTTPConnection(PI_HOST, PI_HTTP_PORT, timeout=2)
        c.request("GET", "/health")
        r = c.getresponse()
        out["http"] = "ok" if r.status == 200 else f"http {r.status}"
        r.read(); c.close()
    except Exception as e:
        out["http"] = f"down ({type(e).__name__})"
    return out


def pi_poller_loop():
    while True:
        info = _pi_check_once()
        with state.lock:
            state.pi_status = info
        push_status()
        time.sleep(8)


def _pi_system_check_once():
    """One-shot fetch of /api/system from the Pi. Returns the parsed JSON
    dict on success, or None on any failure (Pi down, malformed body,
    timeout). Cheap: ~1KB JSON over LAN."""
    try:
        c = http.client.HTTPConnection(PI_HOST, PI_HTTP_PORT, timeout=2)
        c.request("GET", "/api/system")
        r = c.getresponse()
        if r.status != 200:
            r.read(); c.close(); return None
        body = r.read(); c.close()
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def pi_system_poller_loop():
    """Drives the BRIDGE TELEMETRY panel. Polls /api/system every 5s.
    Skips the request entirely while the Pi ping is known-down to avoid
    piling up 2s timeouts when the Pi is offline."""
    while True:
        with state.lock:
            ping = state.pi_status.get("ping", "?")
        if ping == "up":
            info = _pi_system_check_once()
            if info is not None:
                with state.lock:
                    state.pi_system = info
                    state.pi_system_at = datetime.datetime.now()
                push_status()
        time.sleep(5)


# ── Frame parser helper (extracts complete JPEGs from a byte stream) ─
def _emit_frames_into_buffer(scratch, chunk):
    """Append `chunk` to `scratch`; push any complete JPEGs to the rolling
    buffer. Returns the updated scratch (may have a partial frame left over)."""
    scratch += chunk
    SOI = b"\xff\xd8"; EOI = b"\xff\xd9"
    while True:
        s = scratch.find(SOI)
        if s < 0:
            # Trim runaway garbage if no SOI ever found
            if len(scratch) > 65536:
                scratch = b""
            return scratch
        e = scratch.find(EOI, s + 2)
        if e < 0:
            # Partial JPEG — keep from SOI onward
            if s > 0:
                scratch = scratch[s:]
            return scratch
        frame = scratch[s:e + 2]
        scratch = scratch[e + 2:]
        with state.buffer_lock:
            state.frame_buffer.append(frame)
            state.frames_seen += 1


# ── Capture-buffer: dump rolling buffer to disk ────────────────────
def _reorient_jpeg(jpeg_bytes, rot, hflip, vflip):
    """Apply rotation (0/90/180/270) + flips to a JPEG; returns new bytes.
    Falls back to original bytes if PIL isn't available or rot/flip are no-ops.
    Rotation here is applied AFTER what the camera already did, so this only
    needs to handle the 90°/270° cases plus any client-only flip requests."""
    rot = int(rot) % 360
    if not HAS_PIL or (rot == 0 and not hflip and not vflip):
        return jpeg_bytes
    try:
        im = Image.open(io.BytesIO(jpeg_bytes))
        # Order matches what the user expects: rotate first, then flip.
        if rot == 90:   im = im.transpose(Image.ROTATE_90)
        elif rot == 180: im = im.transpose(Image.ROTATE_180)
        elif rot == 270: im = im.transpose(Image.ROTATE_270)
        if hflip: im = im.transpose(Image.FLIP_LEFT_RIGHT)
        if vflip: im = im.transpose(Image.FLIP_TOP_BOTTOM)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue()
    except Exception as e:
        push("sys", f"reorient failed: {e}")
        return jpeg_bytes


def capture_buffer(rot=0, hflip=False, vflip=False):
    with state.buffer_lock:
        frames = list(state.frame_buffer)
    if not frames:
        push("sys", "capture-buffer: buffer empty (start live view first)")
        return None, 0
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = SAVE_DIR / f"capture_{ts}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    # Note: the camera already applied 180° (via hflip+vflip on the Pi) for us
    # if requested. We only need to apply the rotation amount that hardware
    # couldn't do (90/270) plus any client-only flip toggling above 180°.
    # Since we asked the Pi to xor hflip/vflip with (rot==180), undo that here
    # so the saved file matches what the client expects.
    cam_h = bool(hflip) ^ (rot == 180)
    cam_v = bool(vflip) ^ (rot == 180)
    # Frames coming back from the buffer have cam_h/cam_v already applied.
    # We still need to rotate by 90/270 (if applicable) and apply any extra
    # flips the camera didn't do. After camera-applied flips, the remaining
    # client-side rotation is rot if rot in {90,270} else 0 (180 is fully
    # absorbed by camera). The H/V flip net is already what user asked for,
    # so no extra flip needed here unless rot is 90/270 + flip requested.
    extra_rot = 90 if rot == 90 else 270 if rot == 270 else 0
    extra_h = hflip and extra_rot != 0
    extra_v = vflip and extra_rot != 0
    if extra_rot != 0 or extra_h or extra_v:
        push("sys", f"reorienting frames: rot={extra_rot}° hflip={extra_h} vflip={extra_v}")
    for i, jpeg in enumerate(frames, 1):
        out = _reorient_jpeg(jpeg, extra_rot, extra_h, extra_v) if (extra_rot or extra_h or extra_v) else jpeg
        (batch_dir / f"frame_{i:02d}.jpg").write_bytes(out)
    push("sys", f"saved {len(frames)} frames -> {batch_dir.name}")
    with state.lock:
        state.batches_saved += 1
        state.last_batch = batch_dir.name
        state.last_batch_at = datetime.datetime.now()
    push_status()
    subprocess.Popen(["open", str(batch_dir)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return batch_dir, len(frames)


def capture_snap(rot=0, hflip=False, vflip=False):
    """Save the most recent frame only — single-frame snapshot.
    Reuses the same orientation reasoning as capture_buffer."""
    with state.buffer_lock:
        if not state.frame_buffer:
            push("sys", "snap: buffer empty (start live view first)")
            return None
        jpeg = state.frame_buffer[-1]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = SAVE_DIR / f"snap_{ts}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    extra_rot = 90 if rot == 90 else 270 if rot == 270 else 0
    extra_h = hflip and extra_rot != 0
    extra_v = vflip and extra_rot != 0
    out = _reorient_jpeg(jpeg, extra_rot, extra_h, extra_v) if (extra_rot or extra_h or extra_v) else jpeg
    (batch_dir / "frame_01.jpg").write_bytes(out)
    push("sys", f"snap -> {batch_dir.name}")
    with state.lock:
        state.batches_saved += 1
        state.last_batch = batch_dir.name
        state.last_batch_at = datetime.datetime.now()
    push_status()
    return batch_dir


def delete_batch(name):
    """Delete a capture/snap directory. Validates that it lives under SAVE_DIR
    and matches our expected name prefix; refuses anything else."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return False, "bad name"
    if not (name.startswith("capture_") or name.startswith("snap_")):
        return False, "not a capture directory"
    d = SAVE_DIR / name
    try:
        d_real = d.resolve()
        save_real = SAVE_DIR.resolve()
    except OSError as e:
        return False, str(e)
    if save_real not in d_real.parents:
        return False, "outside save dir"
    if not d_real.is_dir():
        return False, "no such batch"
    for f in d_real.glob("*.jpg"):
        try: f.unlink()
        except OSError: pass
    try:
        d_real.rmdir()
    except OSError as e:
        return False, f"rmdir failed: {e}"
    push("sys", f"deleted {name}")
    push_status()
    return True, None


def rename_batch(name, new_name):
    """Rename a batch dir. New name must match safe pattern."""
    if not name or "/" in name or "\\" in name:
        return False, "bad name"
    if not new_name:
        return False, "empty new name"
    # Allow letters, digits, dash, underscore, dot; cap length
    import re
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", new_name):
        return False, "new name has invalid chars (allowed: letters, digits, . _ -)"
    if not (name.startswith("capture_") or name.startswith("snap_")):
        return False, "not a capture directory"
    src = SAVE_DIR / name
    dst = SAVE_DIR / new_name
    if not src.is_dir():
        return False, "no such batch"
    if dst.exists():
        return False, "target name already exists"
    try:
        src.rename(dst)
    except OSError as e:
        return False, f"rename failed: {e}"
    push("sys", f"renamed {name} -> {new_name}")
    push_status()
    return True, None


# ── HTML ───────────────────────────────────────────────────────────
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ticalc camera</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap');

:root {
  --bg-deep:    #04060a;
  --bg-panel:   #0a0e1a;
  --bg-raised:  #0f1422;
  --border:     #1c2538;
  --border-hi:  #2a3a5c;
  --ibm-blue:   #4589ff;
  --cyan:       #4cc9f0;
  --amber:      #ffb700;
  --green:      #38d65e;
  --red:        #ff5d6c;
  --text:       #c8d4e8;
  --muted:      #5b6985;
  --dim:        #3a4459;
  --grid-dot:   rgba(76, 201, 240, 0.11);
  --panel-shadow: 0 4px 32px rgba(76, 201, 240, 0.04);
  --font-mono:  "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --panel-clip: polygon(12px 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%, 0 12px);
  --panel-clip-inner: polygon(11px 0, 100% 0, 100% calc(100% - 11px), calc(100% - 11px) 100%, 0 100%, 0 11px);
}

:root.light {
  /* Light-mode HUD. Same chamfered chrome / Plex Mono / dot grid, just
   * tokens inverted. Accents are deepened so cyan/green/amber/red stay
   * legible on a near-white background. Phosphor glows are toned down
   * (and zeroed on the LED-style elements that would otherwise smudge). */
  --bg-deep:    #eef1f6;
  --bg-panel:   #e1e6ef;
  --bg-raised:  #f8fafd;
  --border:     #b8c2d3;
  --border-hi:  #8a99b0;
  --ibm-blue:   #1f56c2;
  --cyan:       #0a7099;
  --amber:      #8a5e00;
  --green:      #1d7a35;
  --red:        #b32938;
  --text:       #0a0e1a;
  --muted:      #5b6985;
  --dim:        #a0adc0;
  --grid-dot:   rgba(31, 86, 194, 0.11);
  --panel-shadow: 0 4px 16px rgba(10, 14, 26, 0.06);
}
:root.light .hud-timer { text-shadow: none; }
:root.light .vital.ok .vital-v { text-shadow: none; }
:root.light .armed-pip { text-shadow: none; }
:root.light .vital-segs > i.lit,
:root.light .buf-leds > i.lit,
:root.light .rec-dot::before,
:root.light .armed-pip::before { box-shadow: none; }
:root.light #live-placeholder { color: #c8d4e8; }   /* placeholder on the still-black live wrap */

* { box-sizing: border-box; }

html, body {
  margin: 0; height: 100%;
  background: var(--bg-deep);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 400;
  font-feature-settings: "ss02", "zero";
}

body {
  display: grid;
  /* rows: header · vitals · telemetry · main (live+captures) */
  grid-template-rows: auto auto auto 1fr;
  gap: 12px;
  padding: 14px;
  background-image: radial-gradient(var(--grid-dot) 1px, transparent 1px);
  background-size: 32px 32px;
  min-height: 100vh;
  transition: background-color 200ms ease-out, color 200ms ease-out;
}

h1 { font-size: 13px; margin: 0; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: var(--text); }
h2 { font-size: 10px; margin: 0; font-weight: 500; letter-spacing: 0.18em; text-transform: uppercase; color: var(--muted); }

/* Panel chrome — chamfered HUD frame */
.panel {
  position: relative;
  /* 32px top padding leaves clear space below the absolutely-positioned
   * panel-tab (top: 4, ~14px tall → ~18px) and content below it.
   * Earlier 26px crowded the tab against the first content row, most
   * visible on the TX LOG where rows are 12px tall and dense. */
  padding: 32px 14px 14px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  isolation: isolate;
}
.panel::before {
  content: "";
  position: absolute; inset: 0;
  background: var(--border);
  clip-path: var(--panel-clip);
  z-index: -2;
  transition: background 180ms ease-out;
}
.panel::after {
  content: "";
  position: absolute; inset: 1px;
  background: var(--bg-panel);
  clip-path: var(--panel-clip-inner);
  z-index: -1;
  box-shadow: var(--panel-shadow);
}
.panel:hover::before { background: var(--border-hi); }

.panel-tab {
  position: absolute;
  top: 4px;
  left: 18px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--cyan);
  background: var(--bg-deep);
  padding: 2px 8px;
  z-index: 1;
  white-space: nowrap;
}

/* Buttons */
button {
  background: var(--bg-raised);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 0;
  padding: 9px 14px;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  transition: border-color 150ms ease-out, color 150ms ease-out, background 150ms ease-out;
}
button:hover { border-color: var(--cyan); color: var(--cyan); }
button:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
button.primary { background: var(--cyan); color: #03121b; border-color: var(--cyan); font-weight: 600; }
button.primary:hover { color: #03121b; background: #6fd6f3; }
button.capture {
  background: var(--cyan); color: #03121b; border-color: var(--cyan); font-weight: 600;
  min-width: 220px; text-align: center;
}
button.capture:hover { color: #03121b; background: #6fd6f3; }
button.capture.busy { background: var(--amber); border-color: var(--amber); color: #1a1108; }
button.capture.done { background: var(--green); border-color: var(--green); color: #03150a; }
button.capture:disabled,
button.capture.disarmed {
  background: var(--bg-raised);
  color: var(--muted);
  border-color: var(--border);
  cursor: not-allowed;
}
button.snap {
  background: var(--bg-raised); color: var(--cyan); border-color: var(--cyan); font-weight: 500;
  min-width: 120px; text-align: center;
}
button.snap:hover { background: rgba(76, 201, 240, 0.10); color: var(--cyan); }
button.snap.busy { background: var(--amber); border-color: var(--amber); color: #1a1108; }
button.snap.done { background: var(--green); border-color: var(--green); color: #03150a; }
button.snap:disabled {
  background: var(--bg-raised); color: var(--muted); border-color: var(--border);
  cursor: not-allowed;
}
button.primary { min-width: 150px; text-align: center; }
button:disabled { opacity: 0.65; cursor: not-allowed; }

/* Layout grid */
.row {
  display: grid;
  grid-template-columns: 1fr 360px;
  gap: 12px;
  min-height: 0;
  /* Don't let either column stretch to match the other's intrinsic height
   * — captures has 30 batches × ~180px which would otherwise drag the live
   * panel into a 5000-px tall slab. Each side takes its natural height;
   * captures scrolls internally. */
  align-items: start;
}
.row > section { max-height: 80vh; }
@media (max-width: 900px) {
  .row { grid-template-columns: 1fr; align-items: stretch; }
  .row > section { max-height: none; }
}

/* Provisional new-token styling for existing component classes (refined in later tasks) */
.pills { display: flex; flex-wrap: wrap; gap: 8px; }
.pill {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0;
  padding: 5px 10px;
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
}
.pill .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.pill.ok .dot { background: var(--green); }
.pill.bad .dot { background: var(--red); }
.pill.unknown .dot { background: var(--amber); }
.pill b { color: var(--cyan); font-weight: 500; }

.title-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }

.live-controls {
  display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
  margin-bottom: 10px;
  font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted);
}
.live-controls label { display: inline-flex; align-items: center; gap: 6px; }
.live-controls select,
.live-controls input[type=range] {
  background: var(--bg-raised);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 0;
  padding: 4px 7px;
  font: inherit;
  font-size: 11px;
  text-transform: none;
}
.live-controls select:focus,
.live-controls input[type=range]:focus { outline: none; border-color: var(--cyan); }
.qv { color: var(--cyan); min-width: 30px; text-align: right; font-weight: 500; }

#live-wrap {
  position: relative;
  width: 100%;
  /* All preset resolutions are 16:9 (IMX708 modes) — lock to that so the
   * corner reticles snap to the actual image corners instead of sitting
   * on letterbox bars. Width comes from the .row 1fr column; height
   * follows the ratio. No max-height — if you want it smaller on a tall
   * monitor, narrow the window. */
  aspect-ratio: 16 / 9;
  background: #000;
  overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
#live-img { width: 100%; height: 100%; object-fit: contain; display: block;
  transform-origin: center center; transition: transform 0.15s ease; }
#live-placeholder {
  position: absolute;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

/* Fullscreen */
#live-wrap:fullscreen {
  width: 100vw;
  height: 100vh;
  max-height: 100vh;
  aspect-ratio: auto;
  background: #000;
}
#live-wrap:fullscreen .reticle { width: 36px; height: 36px; }
#live-wrap:fullscreen .crosshair { width: 44px; height: 44px; }
#live-wrap:fullscreen .hud-params { font-size: 12px; padding: 5px 12px; }
#live-wrap:fullscreen .rec-dot,
#live-wrap:fullscreen .armed-pip { font-size: 12px; }
.live-fullscreen {
  position: absolute;
  /* bottom-right keeps it clear of the REC indicator (top-right) and the
   * armed pip (top-left); matches typical video-player chrome. */
  bottom: 12px; right: 12px;
  width: 30px; height: 30px;
  background: rgba(4, 6, 10, 0.6);
  border: 1px solid var(--border);
  color: var(--cyan);
  display: inline-flex;
  align-items: center; justify-content: center;
  font-size: 16px;
  cursor: pointer;
  padding: 0;
  z-index: 4;
  transition: border-color 150ms ease-out, background 150ms ease-out;
}
.live-fullscreen:hover { border-color: var(--cyan); background: rgba(76, 201, 240, 0.10); }
#live-wrap:fullscreen .live-fullscreen { bottom: 22px; right: 22px; width: 38px; height: 38px; font-size: 18px; }

.buf-meter { display: inline-flex; align-items: center; gap: 8px; }
.buf-bar { width: 80px; height: 6px; background: var(--bg-deep); border: 1px solid var(--border); overflow: hidden; }
.buf-bar > div { height: 100%; background: var(--green); transition: width 150ms ease-out; }

#log {
  flex: 1 1 0;
  min-height: 0;
  overflow-y: auto;
  font-size: 11px;
  padding-right: 4px;
  letter-spacing: 0.04em;
}
#log .line {
  display: grid;
  grid-template-columns: 72px 70px 1fr;
  align-items: baseline;
  gap: 10px;
  padding: 2px 0;
  border-left: 2px solid transparent;
  padding-left: 8px;
  margin-left: -8px;
  transition: border-color 600ms ease-out, background 600ms ease-out;
}
#log .line .m { white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }
#log .line.fresh {
  border-left-color: var(--cyan);
  background: rgba(76, 201, 240, 0.06);
}
#log .t {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  font-size: 10px;
  letter-spacing: 0.12em;
}
#log .s {
  display: inline-block;
  padding: 1px 7px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.04);
  color: var(--muted);
  text-align: center;
  justify-self: start;
}
#log .s.cam    { color: var(--cyan);     background: rgba(76, 201, 240, 0.10); }
#log .s.net    { color: var(--ibm-blue); background: rgba(69, 137, 255, 0.10); }
#log .s.sys    { color: var(--amber);    background: rgba(255, 183, 0,  0.10); }
#log .s.stream { color: var(--green);    background: rgba(56, 214, 94,  0.10); }
#log .s.btn    { color: var(--red);      background: rgba(255, 93, 108, 0.12); }
#log .m { color: var(--text); }
#log .m::before { content: "▸ "; color: var(--muted); }

#shots {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
  overflow-y: auto;
  flex: 1 1 0;
  min-height: 0;
}
.shot-row {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  transition: border-color 150ms ease-out, background 150ms ease-out;
  min-width: 0;
}
.shot-row:hover { border-color: var(--cyan); background: rgba(76, 201, 240, 0.06); }
.shot-head {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: stretch;
  min-width: 0;
}
.shot-link {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  color: var(--text);
  text-decoration: none;
  font-size: 12px;
  min-width: 0;
}
.shot-link::before { content: "▣"; color: var(--cyan); font-size: 12px; }
.shot-row .name {
  color: var(--cyan); font-weight: 500; letter-spacing: 0.04em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
}
.shot-row .meta {
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  white-space: nowrap;
}
.row-btn {
  background: transparent;
  border: none;
  border-left: 1px solid var(--border);
  color: var(--muted);
  padding: 0 10px;
  min-width: 28px;
  cursor: pointer;
  font-size: 13px;
  letter-spacing: 0;
  text-transform: none;
  transition: color 150ms ease-out, background 150ms ease-out;
}
.row-btn:hover { color: var(--cyan); background: rgba(76, 201, 240, 0.08); }
.row-btn-del:hover { color: var(--red); background: rgba(255, 93, 108, 0.10); }

.shot-grid {
  display: grid;
  gap: 3px;
  padding: 5px;
  background: var(--bg-deep);
  border-top: 1px solid var(--border);
}
.shot-grid[data-cols="1"] { grid-template-columns: 1fr; }
.shot-grid[data-cols="2"] { grid-template-columns: repeat(2, 1fr); }
.shot-grid[data-cols="3"] { grid-template-columns: repeat(3, 1fr); }
.shot-grid[data-cols="4"] { grid-template-columns: repeat(4, 1fr); }
.shot-grid[data-cols="5"] { grid-template-columns: repeat(5, 1fr); }
.shot-grid .grid-frame {
  position: relative;
  display: block;
  aspect-ratio: 4 / 3;
  border: 1px solid transparent;
  background: #000;
  overflow: hidden;
  transition: border-color 150ms ease-out, transform 150ms ease-out, box-shadow 150ms ease-out;
}
.shot-grid .grid-frame img {
  display: block;
  width: 100%; height: 100%;
  object-fit: cover;
  pointer-events: none;
}
.shot-grid .grid-frame:hover {
  border-color: var(--cyan);
  transform: scale(1.06);
  box-shadow: 0 4px 12px rgba(76, 201, 240, 0.25);
  z-index: 2;
}
.shot-grid .grid-frame::after {
  /* tiny frame-index badge in the corner */
  content: attr(data-i);
  position: absolute;
  bottom: 2px; right: 2px;
  font-size: 9px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--cyan);
  background: rgba(4, 6, 10, 0.78);
  padding: 0 4px;
  pointer-events: none;
}
.shot-grid[data-cols="1"] .grid-frame::after { font-size: 11px; padding: 1px 6px; }
.empty { color: var(--muted); font-style: normal; padding: 4px 0; font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; }

/* Scrollbar polish */
*::-webkit-scrollbar { width: 6px; height: 6px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb { background: var(--border); }
*::-webkit-scrollbar-thumb:hover { background: var(--border-hi); }

/* HUD header */
.hud-header {
  display: grid;
  grid-template-columns: 18px auto auto auto auto auto 1fr auto 18px;
  align-items: center;
  gap: 10px;
  padding: 8px 0 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 6px;
  position: relative;
}
.hud-cap {
  position: relative;
  height: 1px;
  background: var(--border);
  align-self: end;
  margin-bottom: 4px;
}
.hud-cap-l { grid-column: 1; }
.hud-cap-r { grid-column: 9; }
.hud-cap::before {
  content: "";
  position: absolute;
  bottom: -1px;
  width: 14px;
  height: 14px;
  border-bottom: 1px solid var(--border);
}
.hud-cap-l::before { left: 0; border-left: 1px solid var(--border); transform: skewX(-30deg); transform-origin: bottom right; }
.hud-cap-r::before { right: 0; border-right: 1px solid var(--border); transform: skewX(30deg); transform-origin: bottom left; }
.hud-host { color: var(--muted); font-size: 11px; letter-spacing: 0.14em; font-variant-numeric: tabular-nums; }
.hud-sep { color: var(--dim); font-size: 12px; }
.hud-title { font-size: 14px; font-weight: 600; letter-spacing: 0.18em; color: var(--text); }
.hud-timer {
  color: var(--cyan);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.16em;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 8px rgba(76, 201, 240, 0.4);
}
.hud-status {
  color: var(--cyan);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  margin-left: 14px;
  opacity: 0;
  transition: opacity 200ms ease-out;
}
.hud-status.show       { opacity: 1; }
.hud-status.show-cyan  { color: var(--cyan); }
.hud-status.show-green { color: var(--green); }
.hud-status.show-amber { color: var(--amber); }
.hud-status.show-red   { color: var(--red); }
.hud-actions { grid-column: 8; display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; align-items: center; }
.header-icon {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  color: var(--cyan);
  width: 36px; height: 36px;
  padding: 0;
  font-size: 16px;
  letter-spacing: 0;
  text-transform: none;
  min-width: 0;
  display: inline-flex; align-items: center; justify-content: center;
}
.header-icon:hover { border-color: var(--cyan); background: rgba(76, 201, 240, 0.10); }
.header-icon-danger { color: var(--amber); }
.header-icon-danger:hover { color: var(--red); border-color: var(--red); background: rgba(255, 93, 108, 0.10); }
.header-icon.busy { color: var(--amber); border-color: var(--amber); animation: breathe-fast 0.8s ease-in-out infinite; }

@media (max-width: 1280px) {
  .hud-header {
    grid-template-columns: 18px auto auto auto auto auto 1fr 18px;
    grid-template-rows: auto auto;
    row-gap: 8px;
  }
  .hud-cap-r { grid-column: 8; }
  .hud-actions {
    grid-column: 1 / -1;
    grid-row: 2;
    justify-content: flex-start;
  }
}
@media (max-width: 900px) {
  .hud-header { grid-template-columns: 1fr; gap: 4px; padding: 8px; }
  .hud-cap { display: none; }
  .hud-actions { grid-column: 1; grid-row: auto; justify-content: flex-start; }
}

/* Vitals strip */
.vitals {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
@media (max-width: 900px) {
  .vitals { grid-template-columns: repeat(2, 1fr); }
}
.vital {
  position: relative;
  padding: 14px 16px 14px;
  isolation: isolate;
  --vital-edge: var(--border);
}
.vital::before {
  content: "";
  position: absolute; inset: 0;
  background: var(--vital-edge);
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
  z-index: -2;
  transition: background 180ms ease-out;
}
.vital::after {
  content: "";
  position: absolute; inset: 1px;
  background: var(--bg-panel);
  clip-path: polygon(7px 0, 100% 0, 100% calc(100% - 7px), calc(100% - 7px) 100%, 0 100%, 0 7px);
  z-index: -1;
}
.vital.ok      { --vital-edge: rgba(56, 214, 94, 0.55); }
.vital.bad     { --vital-edge: rgba(255, 93, 108, 0.65); }
.vital.unknown { --vital-edge: rgba(255, 183, 0, 0.5); }
.vital-k {
  display: block;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
}
.vital-v {
  display: block;
  margin-top: 4px;
  font-size: 18px;
  font-weight: 500;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
}
.vital.ok      .vital-v { color: var(--green); text-shadow: 0 0 10px rgba(56, 214, 94, 0.35); }
.vital.bad     .vital-v { color: var(--red); }
.vital.unknown .vital-v { color: var(--amber); }
.vital-sub {
  display: block;
  margin-top: 2px;
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}
.vital-dot {
  position: absolute;
  top: 14px; right: 16px;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--muted);
  box-shadow: 0 0 0 2px var(--bg-panel);
}
.vital.ok      .vital-dot { background: var(--green); animation: breathe 2s ease-in-out infinite; }
.vital.bad     .vital-dot { background: var(--red); }
.vital.unknown .vital-dot { background: var(--amber); animation: breathe-fast 1s ease-in-out infinite; }
@keyframes breathe       { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
@keyframes breathe-fast  { 0%, 100% { opacity: 0.5; }  50% { opacity: 1; } }

/* Buffer mini-segments in the BUFFER vital tile */
.vital-segs {
  position: absolute;
  top: 16px; right: 16px;
  display: grid;
  grid-template-columns: repeat(15, 4px);
  gap: 2px;
  align-items: center;
}
.vital-segs > i {
  display: block;
  width: 4px; height: 10px;
  background: var(--dim);
}
.vital-segs > i.lit { background: var(--cyan); box-shadow: 0 0 4px rgba(76, 201, 240, 0.55); }
.vital.ok .vital-segs > i.lit { background: var(--green); box-shadow: 0 0 4px rgba(56, 214, 94, 0.55); }

/* BRIDGE TELEMETRY panel */
.panel.telemetry { padding: 26px 16px 14px; }
.tele-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 22px;
}
@media (max-width: 1100px) { .tele-grid { grid-template-columns: repeat(3, 1fr); gap: 18px; } }
@media (max-width: 600px)  { .tele-grid { grid-template-columns: repeat(2, 1fr); gap: 14px; } }
.tele {
  display: flex; flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.tele-k {
  font-size: 9px;
  font-weight: 500;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
}
.tele-v {
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: 0.02em;
}
.tele-sub {
  font-size: 9px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tele-bar {
  display: block;
  height: 4px;
  background: var(--bg-deep);
  border: 1px solid var(--border);
  margin-top: 3px;
}
.tele-bar i {
  display: block;
  height: 100%;
  background: var(--cyan);
  transition: width 250ms ease-out, background 250ms ease-out;
}
.tele-bars {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  margin-top: 4px;
  height: 10px;
}
.tele-bars i {
  display: block;
  width: 4px;
  background: var(--dim);
}
.tele-bars i:nth-child(1) { height: 30%; }
.tele-bars i:nth-child(2) { height: 55%; }
.tele-bars i:nth-child(3) { height: 78%; }
.tele-bars i:nth-child(4) { height: 100%; }
.tele-bars i.lit { background: var(--cyan); box-shadow: 0 0 3px rgba(76, 201, 240, 0.5); }
.tele-meta {
  position: absolute;
  bottom: 6px; right: 14px;
  font-size: 9px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--dim);
  cursor: help;
  z-index: 1;
}

/* Offline overlay — covers the grid when /api/system isn't reachable */
.tele-overlay {
  position: absolute;
  inset: 22px 14px 10px;
  display: none;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: linear-gradient(
    180deg,
    rgba(10, 14, 26, 0.92) 0%,
    rgba(10, 14, 26, 0.92) 100%
  );
  z-index: 2;
  text-align: center;
}
.panel.telemetry.offline .tele-overlay { display: flex; }
.panel.telemetry.offline .tele-grid { filter: blur(1px) opacity(0.25); pointer-events: none; }
.tele-overlay-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.24em;
  color: var(--red);
  text-shadow: 0 0 10px rgba(255, 93, 108, 0.35);
}
.panel.telemetry.offline.degraded .tele-overlay-title { color: var(--amber); text-shadow: 0 0 10px rgba(255, 183, 0, 0.35); }
.tele-overlay-sub {
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
}
.tele-overlay-sub code {
  color: var(--cyan);
  font-family: inherit;
  font-size: inherit;
  letter-spacing: 0.04em;
  text-transform: none;
}

/* HUD overlay on the live video */
.hud-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 2;
  transition: opacity 600ms ease-out;
}
.hud-overlay.dim { opacity: 0.22; }
#live-wrap:hover .hud-overlay.dim { opacity: 1; }
.reticle {
  position: absolute;
  width: 22px; height: 22px;
  border-color: var(--cyan);
  border-style: solid;
  opacity: 0.7;
}
.r-tl { top: 10px;    left: 10px;    border-width: 1px 0 0 1px; }
.r-tr { top: 10px;    right: 10px;   border-width: 1px 1px 0 0; }
.r-bl { bottom: 10px; left: 10px;    border-width: 0 0 1px 1px; }
.r-br { bottom: 10px; right: 10px;   border-width: 0 1px 1px 0; }
.crosshair {
  position: absolute;
  top: 50%; left: 50%;
  width: 28px; height: 28px;
  transform: translate(-50%, -50%);
  opacity: 0.35;
}
.crosshair::before, .crosshair::after {
  content: "";
  position: absolute;
  background: var(--cyan);
}
.crosshair::before { top: 50%; left: 0; right: 0; height: 1px; transform: translateY(-50%); }
.crosshair::after  { left: 50%; top: 0; bottom: 0; width: 1px; transform: translateX(-50%); }
.rec-dot {
  position: absolute;
  top: 14px; right: 18px;
  display: none;
  align-items: center;
  gap: 7px;
  color: var(--cyan);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
.rec-dot.on { display: inline-flex; }
.rec-dot::before {
  content: "";
  display: inline-block;
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--cyan);
  box-shadow: 0 0 6px rgba(76, 201, 240, 0.75);
  animation: rec-pulse 1.2s ease-in-out infinite;
}
.rec-dot::after { content: "REC"; }
@keyframes rec-pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }

.armed-pip {
  position: absolute;
  top: 14px; left: 18px;
  display: none;
  align-items: center;
  gap: 7px;
  color: var(--green);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  text-shadow: 0 0 8px rgba(56, 214, 94, 0.5);
}
.armed-pip.on { display: inline-flex; }
.armed-pip::before {
  content: "";
  display: inline-block;
  width: 8px; height: 8px;
  background: var(--green);
  box-shadow: 0 0 6px rgba(56, 214, 94, 0.75);
}
.armed-pip::after { content: "ARMED · 15"; }
.hud-params {
  position: absolute;
  bottom: 12px; left: 14px;
  color: var(--cyan);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  background: rgba(4, 6, 10, 0.6);
  padding: 3px 8px;
  border: 1px solid rgba(76, 201, 240, 0.25);
}
#live-placeholder { z-index: 3; }
#live-img { z-index: 1; }

/* Pill toggles for H-flip / V-flip */
.live-controls input[type=checkbox] { display: none; }
.live-controls .pill-tog {
  display: inline-flex; align-items: center; gap: 6px;
  cursor: pointer; user-select: none;
}
.live-controls .pill-tog .pill-slot {
  display: inline-flex;
  width: 30px; height: 16px;
  background: var(--bg-raised);
  border: 1px solid var(--border);
  position: relative;
  transition: background 150ms, border-color 150ms;
}
.live-controls .pill-tog .pill-slot::before {
  content: "";
  position: absolute;
  top: 1px; left: 1px;
  width: 12px; height: 12px;
  background: var(--muted);
  transition: left 150ms, background 150ms;
}
.live-controls .pill-tog input:checked + .pill-slot {
  background: rgba(76, 201, 240, 0.18);
  border-color: var(--cyan);
}
.live-controls .pill-tog input:checked + .pill-slot::before {
  left: 15px;
  background: var(--cyan);
  box-shadow: 0 0 6px rgba(76, 201, 240, 0.5);
}

/* Buffer LED row in controls strip */
.buf-leds {
  display: inline-grid;
  grid-template-columns: repeat(15, 6px);
  gap: 2px;
  align-items: center;
}
.buf-leds > i {
  display: block;
  width: 6px; height: 12px;
  background: var(--dim);
}
.buf-leds > i.lit { background: var(--cyan); box-shadow: 0 0 4px rgba(76, 201, 240, 0.6); }
.buf-leds.full > i.lit { background: var(--green); box-shadow: 0 0 6px rgba(56, 214, 94, 0.55); animation: breathe 2s ease-in-out infinite; }
#bufcount { color: var(--cyan); font-weight: 500; font-variant-numeric: tabular-nums; }
.buf-meter { font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted); }

/* Keyboard shortcuts overlay */
.shortcuts-help {
  position: fixed; inset: 0;
  display: none;
  align-items: center; justify-content: center;
  background: rgba(4, 6, 10, 0.78);
  z-index: 100;
  backdrop-filter: blur(2px);
}
.shortcuts-help.show { display: flex; }
.shortcuts-help .sh-card {
  position: relative;
  padding: 32px 36px 28px;
  min-width: 360px;
  isolation: isolate;
}
.shortcuts-help .sh-card::before {
  content: ""; position: absolute; inset: 0;
  background: var(--cyan);
  clip-path: var(--panel-clip);
  z-index: -2;
}
.shortcuts-help .sh-card::after {
  content: ""; position: absolute; inset: 1px;
  background: var(--bg-panel);
  clip-path: var(--panel-clip-inner);
  z-index: -1;
}
.shortcuts-help .sh-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--cyan);
  margin-bottom: 18px;
}
.shortcuts-help dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px 20px;
  margin: 0;
}
.shortcuts-help dt {
  color: var(--cyan);
  font-weight: 500;
  font-size: 12px;
  letter-spacing: 0.06em;
}
.shortcuts-help dd {
  color: var(--text);
  margin: 0;
  font-size: 11px;
  letter-spacing: 0.04em;
}
.shortcuts-help .sh-foot {
  margin-top: 22px;
  font-size: 9px;
  letter-spacing: 0.2em;
  color: var(--muted);
  text-align: center;
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .vital-dot,
  .buf-leds.full > i.lit,
  .vital-segs > i.lit {
    animation: none !important;
  }
  .rec-dot::before { animation: none !important; opacity: 1 !important; }
  .hud-status { transition: none !important; }
  #log .line { transition: none !important; }
  #shots a, button, .panel, .panel::before { transition: none !important; }
}
</style>
</head>
<body>

<header class="hud-header">
  <span class="hud-cap hud-cap-l"></span>
  <span class="hud-host">{{PI_HOST}}</span>
  <span class="hud-sep">·</span>
  <h1 class="hud-title">TICALC.CAMERA</h1>
  <span class="hud-sep">·</span>
  <span class="hud-timer" id="mtimer">T+ 00:00:00</span>
  <span class="hud-status" id="bootcap"></span>
  <span class="hud-cap hud-cap-r"></span>
  <div class="hud-actions">
    <button class="header-icon" id="theme-toggle" type="button" title="Toggle theme (L)" aria-label="Toggle theme">☾</button>
    <button class="header-icon header-icon-danger" id="reboot-pi" type="button" title="Reboot Pi" aria-label="Reboot Pi">⟲</button>
    <button class="primary" id="live">▶ INIT FEED</button>
    <button class="snap" id="snap" disabled>◉ SNAP</button>
    <button class="capture" id="capture" disabled>◉ BUFFER NOT ARMED</button>
  </div>
</header>
<div class="vitals" id="vitals">
  <div class="vital" data-key="link">
    <span class="vital-k">Link</span>
    <span class="vital-v" id="v-link">--</span>
    <span class="vital-sub" id="v-link-sub"></span>
    <span class="vital-dot" id="v-link-dot"></span>
  </div>
  <div class="vital" data-key="http">
    <span class="vital-k">HTTP</span>
    <span class="vital-v" id="v-http">--</span>
    <span class="vital-sub" id="v-http-sub"></span>
    <span class="vital-dot" id="v-http-dot"></span>
  </div>
  <div class="vital" data-key="buffer">
    <span class="vital-k">Buffer</span>
    <span class="vital-v" id="v-buf">0/15</span>
    <span class="vital-sub" id="v-buf-sub">awaiting feed</span>
    <span class="vital-segs" id="v-buf-segs"></span>
  </div>
  <div class="vital" data-key="batches">
    <span class="vital-k">Batches</span>
    <span class="vital-v" id="v-bat">0</span>
    <span class="vital-sub" id="v-bat-sub">no saves yet</span>
    <span class="vital-dot" id="v-bat-dot"></span>
  </div>
</div>

<section class="panel telemetry">
  <span class="panel-tab" id="tele-tab">// SYS-02 · BRIDGE TELEMETRY</span>
  <div class="tele-overlay" id="tele-overlay">
    <span class="tele-overlay-title" id="tele-overlay-title">BRIDGE OFFLINE</span>
    <span class="tele-overlay-sub" id="tele-overlay-sub">awaiting <code>/api/system</code> from <span id="tele-overlay-host">--</span></span>
  </div>
  <div class="tele-grid">
    <div class="tele">
      <span class="tele-k">Temp</span>
      <span class="tele-v" id="t-temp">--</span>
      <span class="tele-bar"><i id="t-temp-fill" style="width:0%"></i></span>
    </div>
    <div class="tele">
      <span class="tele-k">CPU</span>
      <span class="tele-v" id="t-cpu">--</span>
      <span class="tele-sub" id="t-cpu-sub">load --</span>
    </div>
    <div class="tele">
      <span class="tele-k">Mem</span>
      <span class="tele-v" id="t-mem">--</span>
      <span class="tele-bar"><i id="t-mem-fill" style="width:0%"></i></span>
    </div>
    <div class="tele">
      <span class="tele-k">WiFi</span>
      <span class="tele-v" id="t-wifi">--</span>
      <span class="tele-bars" id="t-wifi-bars"><i></i><i></i><i></i><i></i></span>
    </div>
    <div class="tele">
      <span class="tele-k">Throttle</span>
      <span class="tele-v" id="t-throt">--</span>
      <span class="tele-sub" id="t-throt-sub"></span>
    </div>
    <div class="tele">
      <span class="tele-k">Disk</span>
      <span class="tele-v" id="t-disk">--</span>
      <span class="tele-bar"><i id="t-disk-fill" style="width:0%"></i></span>
    </div>
  </div>
  <span class="tele-meta" id="t-meta" title=""></span>
</section>

<div class="row">
  <section class="panel">
    <span class="panel-tab">// CAM-01 · LIVE</span>
    <div class="live-controls">
      <span>Live · <span id="liveres">1920×1080 @ 15fps</span></span>
      <label>Resolution
        <select id="resselect">
          <option value="hd">720p · ~20 fps</option>
          <option value="fhd" selected>1080p · ~15 fps</option>
          <option value="qhd">1296p · ~12 fps</option>
          <option value="uhd">4K (2160p) · ~8 fps</option>
          <option value="max">2592p (sensor max) · ~6 fps</option>
        </select>
      </label>
      <label>Quality
        <input type="range" id="qslider" min="20" max="90" step="5" value="60" style="width: 110px;">
        <span class="qv" id="qval">60</span>
      </label>
      <label>Orientation
        <select id="rotselect">
          <option value="0">0°</option>
          <option value="90">90° ↻</option>
          <option value="180">180°</option>
          <option value="270">270° ↺</option>
        </select>
        <button id="rotbtn" title="Rotate 90° clockwise" type="button" style="padding: 3px 7px; margin-left: 2px;">↻</button>
        <label class="pill-tog" style="margin-left: 6px;"><input type="checkbox" id="hflip"><span class="pill-slot"></span>H-Flip</label>
        <label class="pill-tog"><input type="checkbox" id="vflip"><span class="pill-slot"></span>V-Flip</label>
      </label>
      <label>Focus
        <select id="afselect" title="continuous = AF every frame (hot); auto = AF once at start; manual = locked at specified distance (coolest)">
          <option value="continuous">Continuous AF</option>
          <option value="auto">Auto (one shot)</option>
          <option value="manual">Manual (locked)</option>
        </select>
        <span id="lens-controls" style="display:none; align-items:center; gap:4px;">
          <input type="range" id="lens" min="0" max="10" step="0.25" value="5" style="width:90px;">
          <span class="qv" id="lensval" style="min-width:60px; text-align:left;">5.0D · 20cm</span>
        </span>
      </label>
      <span class="buf-meter">Buffer
        <span class="buf-leds" id="bufleds"></span>
        <span id="bufcount">0/15</span>
      </span>
    </div>
    <div id="live-wrap">
      <img id="live-img" alt="live view">
      <span id="live-placeholder">// AWAITING FEED — PRESS INIT FEED</span>
      <button class="live-fullscreen" id="live-fullscreen" title="Toggle fullscreen (F)" type="button">⛶</button>
      <div class="hud-overlay" aria-hidden="true">
        <span class="reticle r-tl"></span>
        <span class="reticle r-tr"></span>
        <span class="reticle r-bl"></span>
        <span class="reticle r-br"></span>
        <span class="crosshair"></span>
        <span class="rec-dot" id="recdot"></span>
        <span class="armed-pip" id="armedpip"></span>
        <span class="hud-params" id="hudparams">--</span>
      </div>
    </div>
  </section>
  <section class="panel">
    <span class="panel-tab">// REC-09 · CAPTURES <span id="batchcount" style="color: var(--muted); margin-left: 6px;"></span></span>
    <div id="shots"><div class="empty">// NO CAPTURES — INIT FEED &amp; EXECUTE CAPTURE</div></div>
  </section>
</div>

<div id="shortcuts-help" class="shortcuts-help">
  <div class="sh-card">
    <div class="sh-title">// KEYBOARD SHORTCUTS</div>
    <dl>
      <dt>Space</dt><dd>Toggle live feed</dd>
      <dt>C</dt><dd>Execute capture (full buffer)</dd>
      <dt>S</dt><dd>Snap (single frame)</dd>
      <dt>R</dt><dd>Rotate 90°</dd>
      <dt>H / V</dt><dd>Toggle H-flip / V-flip</dd>
      <dt>F</dt><dd>Toggle fullscreen live view</dd>
      <dt>L</dt><dd>Toggle light / dark theme</dd>
      <dt>?</dt><dd>Toggle this help</dd>
      <dt>Esc</dt><dd>Close help / exit fullscreen</dd>
    </dl>
    <div class="sh-foot">PRESS ? OR ESC TO CLOSE</div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const log = $('log');
const shots = $('shots');

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function appendLog(ev) {
  // The TX LOG panel was removed; appendLog becomes a no-op so the SSE
  // handler, capture/snap click handlers, and refreshShots() error
  // branches can keep calling it. Events still arrive via SSE — they
  // just aren't shown in the UI anymore. The browser devtools console
  // gets a debug-level echo so they're not entirely invisible.
  if (!log) {
    try { console.debug(`[${ev.src || '?'}] ${ev.msg || ''}`); } catch (_) {}
    return;
  }
  const wasNearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const div = document.createElement('div');
  const srcKey = String(ev.src || '').toLowerCase();
  const srcClass = ['cam', 'net', 'sys', 'stream', 'btn'].includes(srcKey) ? srcKey : '';
  div.className = 'line fresh';
  div.innerHTML =
    `<span class="t">${esc(ev.t)}</span>` +
    `<span class="s ${srcClass}">${esc(ev.src)}</span>` +
    `<span class="m">${esc(ev.msg)}</span>`;
  log.appendChild(div);
  setTimeout(() => div.classList.remove('fresh'), 700);
  while (log.childElementCount > 300) log.removeChild(log.firstChild);
  if (wasNearBottom) log.scrollTop = log.scrollHeight;
}

function renderStatus(s) {
  // ── LINK ─────────────────────────────────────────────
  const linkEl = $('v-link').parentElement;
  linkEl.classList.remove('ok', 'bad', 'unknown');
  if (s.pi.ping === 'up') {
    linkEl.classList.add('ok');
    $('v-link').textContent = 'UP';
    $('v-link-sub').textContent = 'reachable';
  } else if (s.pi.ping === '?' || s.pi.ping == null) {
    linkEl.classList.add('unknown');
    $('v-link').textContent = '--';
    $('v-link-sub').textContent = 'awaiting probe';
  } else {
    linkEl.classList.add('bad');
    $('v-link').textContent = String(s.pi.ping).toUpperCase();
    $('v-link-sub').textContent = 'no reply';
  }

  // ── HTTP ─────────────────────────────────────────────
  const httpEl = $('v-http').parentElement;
  httpEl.classList.remove('ok', 'bad', 'unknown');
  if (s.pi.http === 'ok') {
    httpEl.classList.add('ok');
    $('v-http').textContent = 'OK';
    $('v-http-sub').textContent = 'port 8080';
  } else if (s.pi.http === '?' || s.pi.http == null) {
    httpEl.classList.add('unknown');
    $('v-http').textContent = '--';
    $('v-http-sub').textContent = 'awaiting probe';
  } else {
    httpEl.classList.add('bad');
    $('v-http').textContent = String(s.pi.http).toUpperCase();
    $('v-http-sub').textContent = 'upstream fault';
  }

  // ── BUFFER ───────────────────────────────────────────
  const bufEl = $('v-buf').parentElement;
  bufEl.classList.remove('ok', 'bad', 'unknown');
  $('v-buf').textContent = `${s.buffer.count}/${s.buffer.max}`;
  if (s.buffer.count >= s.buffer.max) {
    bufEl.classList.add('ok');
    $('v-buf-sub').textContent = `armed · ${s.buffer.frames_seen} seen`;
  } else if (s.buffer.count > 0) {
    $('v-buf-sub').textContent = `filling · ${s.buffer.frames_seen} seen`;
  } else {
    bufEl.classList.add('unknown');
    $('v-buf-sub').textContent = 'awaiting feed';
  }
  renderBufSegs(s.buffer.count, s.buffer.max);

  // ── BATCHES ──────────────────────────────────────────
  const batEl = $('v-bat').parentElement;
  batEl.classList.remove('ok', 'bad', 'unknown');
  $('v-bat').textContent = String(s.batches_saved);
  if (s.batches_saved > 0) {
    batEl.classList.add('ok');
    $('v-bat-sub').textContent = s.last_batch
      ? `${s.last_batch} @ ${s.last_batch_at || ''}`
      : `${s.batches_saved} saved`;
  } else {
    $('v-bat-sub').textContent = 'no saves yet';
  }

  // ── Live UI mirrors ──────────────────────────────────
  $('bufcount').textContent = `${s.buffer.count}/${s.buffer.max}`;
  renderBufLeds(s.buffer.count, s.buffer.max);
  updateCaptureBtn(s);
  updateArmedPip(s);
  if (typeof s.uptime === 'number') {
    _uptimeAnchor = { server_s: s.uptime, client_ms: Date.now() };
    tickTimer();
  }
  renderTelemetry(s);
  handleBoot(s);  // also recovers / shows on link state changes
}

function updateCaptureBtn(s) {
  // Don't fight in-flight busy/done states (handled by click handler)
  if (captureBtn.classList.contains('busy') || captureBtn.classList.contains('done')) return;
  if (s.buffer.count === 0) {
    captureBtn.disabled = true;
    captureBtn.classList.add('disarmed');
    captureBtn.innerHTML = '◉ BUFFER NOT ARMED';
  } else {
    captureBtn.disabled = false;
    captureBtn.classList.remove('disarmed');
    captureBtn.innerHTML = `◉ EXECUTE CAPTURE <span id="bufN">${s.buffer.count}</span>`;
  }
  const snapBtnEl = $('snap');
  if (snapBtnEl && !snapBtnEl.classList.contains('busy') && !snapBtnEl.classList.contains('done')) {
    snapBtnEl.disabled = s.buffer.count === 0;
  }
}

function updateArmedPip(s) {
  const pip = $('armedpip');
  if (pip) pip.classList.toggle('on', s.buffer.count >= s.buffer.max);
}

function humanizeSeconds(s) {
  if (s == null) return '?';
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m`;
  if (s < 86400) return `${Math.floor(s/3600)}h${Math.floor((s%3600)/60)}m`;
  return `${Math.floor(s/86400)}d${Math.floor((s%86400)/3600)}h`;
}

function renderTelemetry(s) {
  const sys = s.pi_system;
  const meta = $('t-meta');
  const panel = document.querySelector('.panel.telemetry');
  const overlayTitle = $('tele-overlay-title');
  const overlayHost  = $('tele-overlay-host');

  if (!sys) {
    panel.classList.add('offline');
    // Two cases to surface:
    //   pi.ping !== 'up' → Pi unreachable (network / power)
    //   pi.ping === 'up' but no pi_system → bridge.py needs the /api/system update
    const piUp = s.pi && s.pi.ping === 'up';
    panel.classList.toggle('degraded', piUp);
    if (overlayTitle) overlayTitle.textContent = piUp ? 'TELEMETRY UNAVAILABLE' : 'BRIDGE OFFLINE';
    if (overlayHost)  overlayHost.textContent  = s.pi && s.pi.checked
      ? `(last probe ${s.pi.checked})` : '--';
    // Reset the underlying cells (so a recovery transition looks clean).
    ['t-temp', 't-cpu', 't-mem', 't-wifi', 't-throt', 't-disk'].forEach(id => {
      const el = $(id); if (el) { el.textContent = '--'; el.style.color = ''; }
    });
    ['t-temp-fill', 't-mem-fill', 't-disk-fill'].forEach(id => {
      const el = $(id); if (el) el.style.width = '0%';
    });
    $('t-cpu-sub').textContent = 'load --';
    $('t-throt-sub').textContent = '';
    $('t-wifi-bars').innerHTML = '<i></i><i></i><i></i><i></i>';
    if (meta) {
      meta.textContent = s.pi_system_at ? `stale · last ${s.pi_system_at}` : '';
      meta.title = 'awaiting first telemetry snapshot from Pi';
    }
    return;
  }
  panel.classList.remove('offline', 'degraded');

  // Temp — bar maps 20–80°C to 0–100%. Color: green<55, amber<70, red≥70.
  if (sys.temp_c != null) {
    const t = sys.temp_c;
    $('t-temp').textContent = `${t.toFixed(1)}°C`;
    const color = t < 55 ? 'var(--green)' : (t < 70 ? 'var(--amber)' : 'var(--red)');
    $('t-temp').style.color = color;
    const pct = Math.max(0, Math.min(100, ((t - 20) / 60) * 100));
    const fill = $('t-temp-fill');
    fill.style.width = pct + '%';
    fill.style.background = color;
  }

  // CPU clock + load avg
  if (sys.cpu_freq_mhz != null) $('t-cpu').textContent = `${sys.cpu_freq_mhz} MHz`;
  if (sys.load1 != null) {
    $('t-cpu-sub').textContent =
      `load ${sys.load1.toFixed(2)} · ${sys.load5.toFixed(2)} · ${sys.load15.toFixed(2)}`;
  }

  // Memory
  if (sys.mem_used_mb != null && sys.mem_total_mb != null) {
    $('t-mem').textContent = `${Math.round(sys.mem_used_mb)} / ${Math.round(sys.mem_total_mb)} MB`;
    const fill = $('t-mem-fill');
    fill.style.width = (sys.mem_used_pct || 0) + '%';
    fill.style.background = sys.mem_used_pct > 85 ? 'var(--amber)' : 'var(--cyan)';
  }

  // WiFi RSSI + 4 bars
  if (sys.rssi_dbm != null) {
    const r = sys.rssi_dbm;
    $('t-wifi').textContent = `${r} dBm`;
    let bars = 0;
    if (r >= -55) bars = 4;
    else if (r >= -65) bars = 3;
    else if (r >= -75) bars = 2;
    else if (r >= -85) bars = 1;
    const color = bars >= 3 ? 'var(--green)' : (bars >= 2 ? 'var(--cyan)' : (bars >= 1 ? 'var(--amber)' : 'var(--red)'));
    $('t-wifi').style.color = color;
    const cells = $('t-wifi-bars').children;
    for (let i = 0; i < cells.length; i++) cells[i].classList.toggle('lit', i < bars);
  } else {
    $('t-wifi').textContent = '--';
    $('t-wifi').style.color = '';
  }

  // Throttle
  const now = sys.throttled_now || [];
  const past = sys.throttled_past || [];
  if (now.length === 0) {
    $('t-throt').textContent = 'OK';
    $('t-throt').style.color = 'var(--green)';
  } else {
    $('t-throt').textContent = now.join(' · ');
    $('t-throt').style.color = 'var(--red)';
  }
  $('t-throt-sub').textContent = past.length ? `past: ${past.join(' · ')}` : '';

  // Disk
  if (sys.disk_used_pct != null) {
    const d = sys.disk_used_pct;
    $('t-disk').textContent = `${d.toFixed(1)}%`;
    const fill = $('t-disk-fill');
    fill.style.width = d + '%';
    fill.style.background = d > 90 ? 'var(--red)' : (d > 75 ? 'var(--amber)' : 'var(--cyan)');
  }

  // Footer meta: timestamp visible, model + kernel + uptime in hover tooltip
  if (meta) {
    meta.textContent = s.pi_system_at ? `T+${s.pi_system_at}` : '';
    const bits = [];
    if (sys.model)   bits.push(sys.model);
    if (sys.kernel)  bits.push(`kernel ${sys.kernel}`);
    if (sys.uptime_s != null) bits.push(`uptime ${humanizeSeconds(sys.uptime_s)}`);
    meta.title = bits.join(' · ');
  }
}

function renderBufSegs(count, max) {
  const seg = $('v-buf-segs');
  if (seg.childElementCount !== max) {
    seg.innerHTML = '';
    for (let i = 0; i < max; i++) seg.appendChild(document.createElement('i'));
  }
  const kids = seg.children;
  for (let i = 0; i < kids.length; i++) {
    kids[i].classList.toggle('lit', i < count);
  }
}

// Buffer LED row (in the live-controls strip)
function renderBufLeds(count, max) {
  const row = $('bufleds');
  if (!row) return;
  if (row.childElementCount !== max) {
    row.innerHTML = '';
    for (let i = 0; i < max; i++) row.appendChild(document.createElement('i'));
  }
  for (let i = 0; i < row.children.length; i++) {
    row.children[i].classList.toggle('lit', i < count);
  }
  row.classList.toggle('full', count >= max);
}

// HUD params readout — minimal "what's recording" view (rotation/flips
// are visible from the rotated image itself and live in the toolbar).
function updateHudParams() {
  const params = $('hudparams');
  if (!params) return;
  const res = (RES_LABELS[resselect.value] || resselect.value).split(' @ ')[0];
  const af = afselect.value === 'manual'
    ? `AF MAN ${diopterLabel(lensSlider.value)}`
    : (afselect.value === 'continuous' ? 'AF CONT' : 'AF AUTO');
  params.textContent = `${res} · Q${_q} · ${af}`;
}

// REC dot visibility (toggled by setLive)
function setRecDot(on) { $('recdot').classList.toggle('on', !!on); }

// Theme toggle (light / dark)
const themeBtn = $('theme-toggle');
function applyTheme(t) {
  document.documentElement.classList.toggle('light', t === 'light');
  if (themeBtn) {
    themeBtn.textContent = (t === 'light') ? '☀' : '☾';
    themeBtn.title = `Toggle theme (L) — currently ${t}`;
  }
}
function toggleTheme() {
  const cur = document.documentElement.classList.contains('light') ? 'light' : 'dark';
  const next = (cur === 'light') ? 'dark' : 'light';
  applyTheme(next);
  try { localStorage.setItem('ticalc.theme', next); } catch (_) {}
}
themeBtn.addEventListener('click', toggleTheme);
// Apply saved theme on load (default = dark)
try {
  const saved = localStorage.getItem('ticalc.theme');
  if (saved === 'light') applyTheme('light');
  else applyTheme('dark');
} catch (_) { applyTheme('dark'); }

// Reboot Pi — POST /api/pi-reboot via the Mac proxy
const rebootBtn = $('reboot-pi');
rebootBtn.addEventListener('click', async () => {
  if (!confirm('Reboot the Pi?\\nLive feed will drop and the bridge takes ~30s to come back.')) return;
  const orig = rebootBtn.innerHTML;
  rebootBtn.disabled = true;
  rebootBtn.classList.add('busy');
  rebootBtn.innerHTML = '⏻';
  try {
    const r = await fetch('/api/pi-reboot', { method: 'POST' });
    if (r.ok) {
      appendLog({ t: '', src: 'sys', msg: 'Pi reboot requested' });
      rebootBtn.innerHTML = '✓';
    } else {
      const txt = await r.text();
      appendLog({ t: '', src: 'sys', msg: `reboot failed (${r.status}): ${txt}` });
      rebootBtn.innerHTML = '⚠';
    }
  } catch (err) {
    appendLog({ t: '', src: 'sys', msg: `reboot error: ${err}` });
    rebootBtn.innerHTML = '⚠';
  } finally {
    setTimeout(() => {
      rebootBtn.disabled = false;
      rebootBtn.classList.remove('busy');
      rebootBtn.innerHTML = orig;
    }, 4000);
  }
});

// Fullscreen toggle for the live view
const liveWrap = $('live-wrap');
const fsBtn = $('live-fullscreen');
function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  } else {
    (liveWrap.requestFullscreen ? liveWrap.requestFullscreen() : Promise.reject())
      .catch(err => appendLog({ t: '', src: 'sys', msg: `fullscreen denied: ${err && err.message || err}` }));
  }
}
fsBtn.addEventListener('click', toggleFullscreen);
document.addEventListener('fullscreenchange', () => {
  fsBtn.title = document.fullscreenElement ? 'Exit fullscreen (F or Esc)' : 'Toggle fullscreen (F)';
});

// HUD overlay dim — fade reticles/crosshair after a few seconds of feed
let _hudDimTimer = null;
function scheduleHudDim() {
  clearTimeout(_hudDimTimer);
  const ov = document.querySelector('.hud-overlay');
  if (ov) ov.classList.remove('dim');
  _hudDimTimer = setTimeout(() => {
    const ov2 = document.querySelector('.hud-overlay');
    if (ov2 && _live_on) ov2.classList.add('dim');
  }, 3000);
}
function cancelHudDim() {
  clearTimeout(_hudDimTimer);
  const ov = document.querySelector('.hud-overlay');
  if (ov) ov.classList.remove('dim');
}

async function refreshShots() {
  try {
    const r = await fetch('/shots');
    const list = await r.json();
    $('batchcount').textContent = list.length ? `${list.length} total` : '';
    if (!list.length) {
      shots.innerHTML = '<div class="empty">// NO CAPTURES — INIT FEED &amp; EXECUTE CAPTURE</div>';
      return;
    }
    shots.innerHTML = list.slice(0, 30).map(b => {
      const n = esc(b.name);
      const enc = encodeURIComponent(b.name);
      // Grid columns scale with frame count: 1 frame → big hero shot,
      // 2–4 → one row of that many, 5+ → 5-col grid (so 15 → 3 rows × 5).
      const cols = b.frames <= 1 ? 1 : (b.frames <= 4 ? b.frames : 5);
      // Frame_NN.jpg naming from capture_buffer / capture_snap.
      // Frames lazy-load so a 30-batch × 15-frame list doesn't fire 450 requests on first paint.
      const grid = Array.from({ length: b.frames }, (_, i) => {
        const ix = String(i + 1).padStart(2, '0');
        const f = `frame_${ix}.jpg`;
        return `<a class="grid-frame" data-i="${ix}"`
             + ` href="/batchfile/${enc}/${f}" target="_blank">`
             + `<img src="/batchfile/${enc}/${f}" loading="lazy" alt="${f}"></a>`;
      }).join('');
      return (
        `<div class="shot-row" data-name="${n}">
          <div class="shot-head">
            <a class="shot-link" href="/batch/${enc}" target="_blank">
              <span class="name">${n}</span>
              <span class="meta">${b.frames} fr · ${esc(b.ago)}</span>
            </a>
            <button class="row-btn" data-action="rename" data-name="${n}" title="Rename">✎</button>
            <button class="row-btn row-btn-del" data-action="delete" data-name="${n}" title="Delete">×</button>
          </div>
          ${b.frames > 0 ? `<div class="shot-grid" data-cols="${cols}">${grid}</div>` : ''}
        </div>`
      );
    }).join('');
  } catch (e) {}
}

shots.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const name = btn.dataset.name;
  const action = btn.dataset.action;
  if (action === 'delete') {
    if (!confirm(`Delete ${name}? Frames will be permanently removed.`)) return;
    try {
      const r = await fetch(`/api/delete-batch?name=${encodeURIComponent(name)}`, { method: 'POST' });
      if (!r.ok) {
        const txt = await r.text();
        appendLog({ t: '', src: 'sys', msg: `delete failed: ${txt}` });
      } else {
        refreshShots();
      }
    } catch (err) {
      appendLog({ t: '', src: 'sys', msg: `delete error: ${err}` });
    }
  } else if (action === 'rename') {
    const next = prompt(`Rename ${name} to:`, name);
    if (!next || next === name) return;
    try {
      const r = await fetch(`/api/rename-batch?name=${encodeURIComponent(name)}&new_name=${encodeURIComponent(next)}`, { method: 'POST' });
      if (!r.ok) {
        const txt = await r.text();
        appendLog({ t: '', src: 'sys', msg: `rename failed: ${txt}` });
      } else {
        refreshShots();
      }
    } catch (err) {
      appendLog({ t: '', src: 'sys', msg: `rename error: ${err}` });
    }
  }
});
setInterval(refreshShots, 4000);
refreshShots();

// ── Mission timer — server uptime, drift-corrected per status push ─
let _uptimeAnchor = null; // { server_s, client_ms }
function tickTimer() {
  const tEl = $('mtimer');
  if (!_uptimeAnchor) { tEl.textContent = 'T+ --:--:--'; return; }
  const elapsed = Math.floor((Date.now() - _uptimeAnchor.client_ms) / 1000);
  const s = _uptimeAnchor.server_s + elapsed;
  const hh = String(Math.floor(s / 3600)).padStart(2, '0');
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  tEl.textContent = `T+ ${hh}:${mm}:${ss}`;
}
tickTimer();
setInterval(tickTimer, 1000);

// ── Boot caption — bound to first status, recovers on link change ──
let _firstStatus = true;
let _bootShowing = false;

function setBootCapState(s) {
  const cap = $('bootcap');
  cap.classList.remove('show-cyan', 'show-green', 'show-amber', 'show-red');
  if (s.pi.ping === 'up' && s.pi.http === 'ok') {
    cap.classList.add('show-green');
    cap.textContent = '// LINK ESTABLISHED';
  } else if (s.pi.ping === 'up') {
    cap.classList.add('show-amber');
    cap.textContent = '// HTTP FAULT';
  } else {
    cap.classList.add('show-red');
    cap.textContent = '// LINK LOST';
  }
}

function handleBoot(s) {
  const cap = $('bootcap');
  const linkOk = s.pi.ping === 'up' && s.pi.http === 'ok';
  const skipPulse = sessionStorage.getItem('ticalc.booted') === '1';

  if (_firstStatus) {
    _firstStatus = false;
    if (!skipPulse) {
      _bootShowing = true;
      cap.classList.add('show', 'show-cyan');
      cap.textContent = '// INITIALIZING TELEMETRY…';
      setTimeout(() => { cap.classList.remove('show-cyan'); setBootCapState(s); }, 500);
      setTimeout(() => {
        sessionStorage.setItem('ticalc.booted', '1');
        if (s.pi.ping === 'up' && s.pi.http === 'ok') {
          cap.classList.remove('show'); _bootShowing = false;
        }
      }, 1800);
    } else if (!linkOk) {
      _bootShowing = true;
      cap.classList.add('show');
      setBootCapState(s);
    }
    return;
  }
  // Subsequent snapshots: recover or update message
  if (_bootShowing) {
    if (linkOk) { cap.classList.remove('show'); _bootShowing = false; }
    else setBootCapState(s);
  } else if (!linkOk) {
    _bootShowing = true;
    cap.classList.add('show');
    setBootCapState(s);
  }
}

// ── Live view ───────────────────────────────────────────────────
const liveBtn = $('live');
const liveImg = $('live-img');
const livePlaceholder = $('live-placeholder');
const qslider = $('qslider');
const qval = $('qval');
const resselect = $('resselect');
const liveres = $('liveres');
const RES_LABELS = {
  hd:  '1280×720 @ ~20fps', fhd: '1920×1080 @ ~15fps',
  qhd: '2304×1296 @ ~12fps', uhd: '3840×2160 (4K) @ ~8fps',
  max: '4608×2592 (sensor max) @ ~6fps',
};
let _live_on = false, _q = parseInt(qslider.value, 10);
let _suppressErrUntil = 0, _retryTimer = null;

// ── Orientation + focus (persists across reloads) ─────────────
const rotselect = $('rotselect');
const rotbtn = $('rotbtn');
const hflipCb = $('hflip');
const vflipCb = $('vflip');
const afselect = $('afselect');
const lensSlider = $('lens');
const lensVal = $('lensval');
const lensControls = $('lens-controls');
try {
  const saved = JSON.parse(localStorage.getItem('ticalc.orient') || '{}');
  if (saved.rot   != null) rotselect.value = String(saved.rot);
  if (saved.hflip != null) hflipCb.checked = !!saved.hflip;
  if (saved.vflip != null) vflipCb.checked = !!saved.vflip;
  if (saved.af)            afselect.value = saved.af;
  if (saved.lens  != null) lensSlider.value = String(saved.lens);
} catch (e) {}
function saveOrient() {
  try {
    localStorage.setItem('ticalc.orient', JSON.stringify({
      rot:   parseInt(rotselect.value, 10),
      hflip: hflipCb.checked,
      vflip: vflipCb.checked,
      af:    afselect.value,
      lens:  parseFloat(lensSlider.value),
    }));
  } catch (e) {}
}
// Diopters to distance label
function diopterLabel(d) {
  d = parseFloat(d);
  if (d <= 0.05) return `${d.toFixed(1)}D · ∞`;
  const m = 1 / d;
  if (m >= 1)  return `${d.toFixed(1)}D · ${m.toFixed(2)}m`;
  return `${d.toFixed(1)}D · ${Math.round(m * 100)}cm`;
}
function updateLensLabel() {
  lensVal.textContent = diopterLabel(lensSlider.value);
}
function updateAfMode() {
  lensControls.style.display = (afselect.value === 'manual') ? 'inline-flex' : 'none';
}
updateLensLabel(); updateAfMode(); updateHudParams();

function streamUrl() {
  liveres.textContent = RES_LABELS[resselect.value] || resselect.value;
  const rot = parseInt(rotselect.value, 10) || 0;
  const hf  = hflipCb.checked ? 1 : 0;
  const vf  = vflipCb.checked ? 1 : 0;
  const af  = afselect.value;
  const lens = parseFloat(lensSlider.value);
  let lensQ = '';
  if (af === 'manual') lensQ = `&lens=${lens}`;
  return `/stream.mjpeg?res=${resselect.value}&q=${_q}&rot=${rot}&hflip=${hf}&vflip=${vf}&af=${af}${lensQ}&ts=${Date.now()}`;
}
// Apply visual rotation to the live <img>. Hardware does 0/180; we add the
// remainder (90/270) here. Hflip/vflip in 90/270 modes also need a client
// flip because they're meaningless to send to the camera at those rotations.
function applyLiveTransform() {
  const rot = parseInt(rotselect.value, 10) || 0;
  const cssRot = (rot === 90 || rot === 270) ? rot : 0;
  let scaleX = 1, scaleY = 1;
  if (cssRot !== 0) {
    if (hflipCb.checked) scaleX = -1;
    if (vflipCb.checked) scaleY = -1;
  }
  liveImg.style.transform =
    `rotate(${cssRot}deg) scale(${scaleX}, ${scaleY})`;
}
function setLive(on) {
  _live_on = on;
  if (_retryTimer) { clearTimeout(_retryTimer); _retryTimer = null; }
  if (on) {
    livePlaceholder.textContent = 'connecting…';
    livePlaceholder.style.display = '';
    liveImg.src = streamUrl();
    liveBtn.innerHTML = '■ HALT FEED';
    setRecDot(true);
    updateHudParams();
    scheduleHudDim();
  } else {
    _suppressErrUntil = Date.now() + 1500;
    liveImg.removeAttribute('src');
    livePlaceholder.style.display = '';
    livePlaceholder.textContent = '// AWAITING FEED — PRESS INIT FEED';
    liveBtn.innerHTML = '▶ INIT FEED';
    setRecDot(false);
    cancelHudDim();
  }
}
function restart() { _suppressErrUntil = Date.now() + 1500; liveImg.src = streamUrl(); }
liveBtn.addEventListener('click', () => setLive(!_live_on));
liveImg.addEventListener('load', () => { livePlaceholder.style.display = 'none'; if (_retryTimer) { clearTimeout(_retryTimer); _retryTimer = null; } });
liveImg.addEventListener('error', () => {
  if (Date.now() < _suppressErrUntil || !_live_on) return;
  livePlaceholder.textContent = 'stream paused — reconnecting…';
  livePlaceholder.style.display = '';
  if (_retryTimer) clearTimeout(_retryTimer);
  _retryTimer = setTimeout(() => { if (_live_on) restart(); }, 2000);
});
qslider.addEventListener('input', () => { qval.textContent = qslider.value; });
qslider.addEventListener('change', () => {
  _q = parseInt(qslider.value, 10);
  if (_live_on) { livePlaceholder.textContent = `quality → ${_q}…`; livePlaceholder.style.display = ''; setTimeout(restart, 180); }
  updateHudParams();
});
resselect.addEventListener('change', () => {
  if (_live_on) { livePlaceholder.textContent = `switching to ${RES_LABELS[resselect.value]}…`; livePlaceholder.style.display = ''; restart(); }
  else { liveres.textContent = RES_LABELS[resselect.value]; }
  updateHudParams();
});

function applyOrientation(restartIfLive) {
  saveOrient();
  applyLiveTransform();
  if (_live_on && restartIfLive) {
    // Only restart the stream when the camera-side state changed (180° via
    // hflip+vflip on the Pi). 90/270 rotations are pure CSS — no restart.
    const rot = parseInt(rotselect.value, 10) || 0;
    if (rot === 0 || rot === 180) {
      livePlaceholder.textContent = `orientation → ${rot}°…`;
      livePlaceholder.style.display = '';
      restart();
    }
  }
}
// Apply CSS transform once on load
applyLiveTransform();
rotselect.addEventListener('change', () => { applyOrientation(true); updateHudParams(); });
hflipCb.addEventListener('change',  () => { applyOrientation(true); updateHudParams(); });
vflipCb.addEventListener('change',  () => { applyOrientation(true); updateHudParams(); });
afselect.addEventListener('change', () => {
  updateAfMode();
  saveOrient();
  if (_live_on) {
    livePlaceholder.textContent = `focus → ${afselect.value}…`;
    livePlaceholder.style.display = '';
    restart();
  }
  updateHudParams();
});
let _lensTimer = null;
lensSlider.addEventListener('input', () => { updateLensLabel(); updateHudParams(); });
lensSlider.addEventListener('change', () => {
  saveOrient();
  if (_live_on && afselect.value === 'manual') {
    if (_lensTimer) clearTimeout(_lensTimer);
    livePlaceholder.textContent = `focus → ${diopterLabel(lensSlider.value)}…`;
    livePlaceholder.style.display = '';
    _lensTimer = setTimeout(restart, 200);
  }
});
rotbtn.addEventListener('click', () => {
  const cur = parseInt(rotselect.value, 10) || 0;
  rotselect.value = String((cur + 90) % 360);
  applyOrientation(true);
});

// ── Snap (single frame) ─────────────────────────────────────────
const snapBtn = $('snap');
snapBtn.addEventListener('click', async () => {
  if (snapBtn.disabled) return;
  const orig = snapBtn.innerHTML;
  snapBtn.disabled = true;
  snapBtn.classList.add('busy');
  snapBtn.innerHTML = '■ SNAPPING…';
  try {
    const rot = parseInt(rotselect.value, 10) || 0;
    const hf  = hflipCb.checked ? 1 : 0;
    const vf  = vflipCb.checked ? 1 : 0;
    const r = await fetch(`/api/snap?rot=${rot}&hflip=${hf}&vflip=${vf}`, { method: 'POST' });
    if (!r.ok) {
      const txt = await r.text();
      appendLog({ t: '', src: 'sys', msg: `snap failed (${r.status}): ${txt}` });
      snapBtn.classList.remove('busy');
      snapBtn.innerHTML = r.status === 409 ? '⚠ EMPTY' : '⚠ FAILED';
    } else {
      snapBtn.classList.remove('busy');
      snapBtn.classList.add('done');
      snapBtn.innerHTML = '✓ SAVED';
      refreshShots();
    }
  } catch (err) {
    appendLog({ t: '', src: 'sys', msg: `snap error: ${err}` });
    snapBtn.classList.remove('busy');
    snapBtn.innerHTML = '⚠ FAILED';
  } finally {
    setTimeout(() => {
      snapBtn.classList.remove('busy', 'done');
      snapBtn.innerHTML = orig;
      // disabled is restored by next renderStatus based on buffer
    }, 1400);
  }
});

// ── Capture buffer ──────────────────────────────────────────────
const captureBtn = $('capture');
captureBtn.addEventListener('click', async () => {
  if (captureBtn.disabled) return;
  const orig = captureBtn.innerHTML;
  captureBtn.disabled = true;
  captureBtn.classList.add('busy');
  captureBtn.classList.remove('done');
  captureBtn.innerHTML = '■ SAVING…';
  try {
    const rot = parseInt(rotselect.value, 10) || 0;
    const hf  = hflipCb.checked ? 1 : 0;
    const vf  = vflipCb.checked ? 1 : 0;
    const r = await fetch(`/api/capture-buffer?rot=${rot}&hflip=${hf}&vflip=${vf}`, { method: 'POST' });
    if (!r.ok) {
      const txt = await r.text();
      appendLog({ t: '', src: 'sys', msg: `capture failed (${r.status}): ${txt}` });
      captureBtn.classList.remove('busy');
      captureBtn.innerHTML = r.status === 409 ? '⚠ BUFFER EMPTY' : '⚠ FAILED';
    } else {
      const o = await r.json();
      captureBtn.classList.remove('busy');
      captureBtn.classList.add('done');
      captureBtn.innerHTML = `✓ ${o.saved} FRAMES`;
      refreshShots();
    }
  } catch (e) {
    appendLog({ t: '', src: 'sys', msg: `capture error: ${e}` });
    captureBtn.classList.remove('busy');
    captureBtn.innerHTML = '⚠ FAILED';
  } finally {
    setTimeout(() => {
      captureBtn.disabled = false;
      captureBtn.classList.remove('busy', 'done');
      captureBtn.innerHTML = orig;
    }, 1800);
  }
});

// ── SSE ─────────────────────────────────────────────────────────
const ev = new EventSource('/events');
ev.onmessage = (m) => {
  try {
    const o = JSON.parse(m.data);
    if (o.kind === 'log') appendLog(o);
    else if (o.kind === 'status') renderStatus(o);
  } catch(e) {}
};
fetch('/api/status').then(r => r.json()).then(renderStatus);

// ── Keyboard shortcuts ─────────────────────────────────────────
window.addEventListener('keydown', (e) => {
  // Skip if the user is typing in a form field
  const tag = (e.target.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || e.target.isContentEditable) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === ' ' || e.code === 'Space') {
    e.preventDefault(); liveBtn.click();
  } else if (k === 'c') {
    if (!captureBtn.disabled) captureBtn.click();
  } else if (k === 's') {
    const snapBtn = $('snap');
    if (snapBtn && !snapBtn.disabled) snapBtn.click();
  } else if (k === 'r') {
    rotbtn.click();
  } else if (k === 'h') {
    hflipCb.checked = !hflipCb.checked; hflipCb.dispatchEvent(new Event('change'));
  } else if (k === 'v') {
    vflipCb.checked = !vflipCb.checked; vflipCb.dispatchEvent(new Event('change'));
  } else if (k === 'f') {
    e.preventDefault(); toggleFullscreen();
  } else if (k === 'l') {
    toggleTheme();
  } else if (k === '?') {
    $('shortcuts-help').classList.toggle('show');
  } else if (k === 'escape') {
    $('shortcuts-help').classList.remove('show');
  }
});
$('shortcuts-help').addEventListener('click', (e) => {
  if (e.target.id === 'shortcuts-help') $('shortcuts-help').classList.remove('show');
});
</script>
</body>
</html>
"""


# ── HTTP handler ───────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): return

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/" or p == "/index.html":
            body = INDEX_HTML.replace("{{PI_HOST}}", PI_HOST)
            self._send(200, body, "text/html; charset=utf-8")
        elif p == "/events":
            self._serve_sse()
        elif p == "/api/status":
            self._send(200, json.dumps(build_status()), "application/json")
        elif p == "/shots":
            self._send(200, json.dumps(self._list_batches()), "application/json")
        elif p.startswith("/batch/"):
            self._serve_batch_index(unquote(p[len("/batch/"):]))
        elif p.startswith("/batchfile/"):
            self._serve_batch_file(unquote(p[len("/batchfile/"):]))
        elif p == "/stream.mjpeg":
            self._proxy_stream(self.path)
        elif p == "/health":
            self._send(200, "ok")
        else:
            self._send(404, "not found")

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        q = parse_qs(urlparse(self.path).query)
        if p == "/api/capture-buffer":
            rot   = int(q.get("rot",   ["0"])[0])
            hflip = q.get("hflip", ["0"])[0] in ("1", "true", "yes")
            vflip = q.get("vflip", ["0"])[0] in ("1", "true", "yes")
            batch_dir, n = capture_buffer(rot=rot, hflip=hflip, vflip=vflip)
            if not batch_dir:
                self._send(409, "buffer empty — start the live view first")
                return
            self._send(200, json.dumps({"saved": n, "name": batch_dir.name}),
                       "application/json")
            return
        if p == "/api/snap":
            rot   = int(q.get("rot",   ["0"])[0])
            hflip = q.get("hflip", ["0"])[0] in ("1", "true", "yes")
            vflip = q.get("vflip", ["0"])[0] in ("1", "true", "yes")
            batch_dir = capture_snap(rot=rot, hflip=hflip, vflip=vflip)
            if not batch_dir:
                self._send(409, "buffer empty — start the live view first")
                return
            self._send(200, json.dumps({"saved": 1, "name": batch_dir.name}),
                       "application/json")
            return
        if p == "/api/delete-batch":
            name = q.get("name", [""])[0]
            ok, err = delete_batch(name)
            if not ok:
                self._send(400, err or "delete failed")
                return
            self._send(200, json.dumps({"deleted": name}), "application/json")
            return
        if p == "/api/rename-batch":
            name = q.get("name", [""])[0]
            new_name = q.get("new_name", [""])[0]
            ok, err = rename_batch(name, new_name)
            if not ok:
                self._send(400, err or "rename failed")
                return
            self._send(200, json.dumps({"renamed_to": new_name}), "application/json")
            return
        if p == "/api/pi-reboot":
            # Proxy a reboot request to the Pi bridge. The Pi's /api/reboot
            # schedules `sudo reboot` after a short delay so it can reply
            # first. Requires NOPASSWD: /sbin/reboot in the Pi's sudoers.
            try:
                c = http.client.HTTPConnection(PI_HOST, PI_HTTP_PORT, timeout=3)
                c.request("POST", "/api/reboot", body=b"")
                r = c.getresponse()
                body = r.read()
                c.close()
                if r.status == 200:
                    push("sys", "Pi reboot requested via web UI")
                    self._send(200, body, "application/json")
                else:
                    self._send(r.status, body or b"reboot failed")
            except Exception as e:
                self._send(502, f"reboot proxy failed: {e}")
            return
        if p == "/api/button":
            # ESP32 GPIO42 button events: press / release / hello / poll.
            # Body is small JSON; cap reads at 2KB so a misbehaving client
            # can't park a giant POST and stall the request thread.
            try:
                clen = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                clen = 0
            clen = min(clen, 2048)
            raw = self.rfile.read(clen) if clen > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(payload, dict):
                    payload = {"event": str(payload)}
            except (ValueError, UnicodeDecodeError):
                payload = {"event": (raw.decode("utf-8", "replace").strip() or "?")}
            client_ip = self.client_address[0] if self.client_address else None
            record_button_event(payload, client_ip)
            self._send(200, json.dumps({"ok": True}), "application/json")
            return
        self._send(404, "not found")

    # ── Batch listing & file serve ─────────────────────────────────
    def _list_batches(self):
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        candidates = list(SAVE_DIR.glob("capture_*")) + list(SAVE_DIR.glob("snap_*"))
        out = []
        for d in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir(): continue
            frames = sorted(d.glob("frame_*.jpg"))
            age = time.time() - d.stat().st_mtime
            out.append({"name": d.name, "frames": len(frames),
                        "mtime": d.stat().st_mtime,
                        "ago": _humanize(age)})
        return out

    def _serve_batch_index(self, name):
        d = SAVE_DIR / Path(name).name
        if not d.is_dir():
            self._send(404, "no such batch"); return
        frames = sorted(d.glob("frame_*.jpg"))
        safe_name = name.replace("<", "&lt;").replace(">", "&gt;")
        items = "".join(
            f'<a class="thumb" data-idx="{i}" href="/batchfile/{name}/{f.name}">'
            f'<img src="/batchfile/{name}/{f.name}" alt="{f.name}" loading="lazy"></a>'
            for i, f in enumerate(frames)
        )
        frame_urls = json.dumps([f"/batchfile/{name}/{f.name}" for f in frames])
        frame_names = json.dumps([f.name for f in frames])
        html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_name} — ticalc batch</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap');
:root {{
  --bg-deep: #04060a; --bg-panel: #0a0e1a; --bg-raised: #0f1422;
  --border: #1c2538; --border-hi: #2a3a5c;
  --cyan: #4cc9f0; --text: #c8d4e8; --muted: #5b6985; --dim: #3a4459;
  --font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --panel-clip: polygon(12px 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%, 0 12px);
  --panel-clip-inner: polygon(11px 0, 100% 0, 100% calc(100% - 11px), calc(100% - 11px) 100%, 0 100%, 0 11px);
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; background: var(--bg-deep); color: var(--text); font-family: var(--font-mono); font-size: 13px; }}
body {{ padding: 14px; background-image: radial-gradient(rgba(76,201,240,0.11) 1px, transparent 1px); background-size: 32px 32px; min-height: 100vh; }}
.hud-header {{
  display: flex; align-items: center; gap: 10px;
  padding: 6px 14px 8px; border-bottom: 1px solid var(--border); margin-bottom: 14px;
  flex-wrap: wrap;
}}
.hud-title {{ font-size: 14px; font-weight: 600; letter-spacing: 0.18em; }}
.hud-sub   {{ color: var(--muted); font-size: 11px; letter-spacing: 0.14em; }}
.hud-sep   {{ color: var(--dim); font-size: 12px; }}
.back-tab {{
  color: var(--cyan); text-decoration: none;
  border: 1px solid var(--border); padding: 4px 10px;
  font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase;
  transition: border-color 150ms ease-out, background 150ms ease-out;
}}
.back-tab:hover {{ border-color: var(--cyan); background: rgba(76,201,240,0.06); }}
.frames {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 10px;
}}
.thumb {{
  display: block;
  border: 1px solid var(--border);
  background: #000;
  transition: border-color 150ms ease-out, transform 150ms ease-out;
  cursor: zoom-in;
}}
.thumb img {{ display: block; width: 100%; height: 140px; object-fit: cover; }}
.thumb:hover {{ border-color: var(--cyan); transform: scale(1.02); }}
.empty {{ color: var(--muted); padding: 16px 0; letter-spacing: 0.14em; text-transform: uppercase; font-size: 11px; }}

/* Lightbox */
.lb {{
  position: fixed; inset: 0;
  display: none;
  align-items: center; justify-content: center;
  background: rgba(4, 6, 10, 0.92);
  z-index: 100;
  backdrop-filter: blur(3px);
  cursor: zoom-out;
}}
.lb.on {{ display: flex; }}
.lb-img-wrap {{
  position: relative;
  max-width: 92vw;
  max-height: 84vh;
  isolation: isolate;
  cursor: default;
}}
.lb-img-wrap::before {{
  content: ""; position: absolute; inset: 0;
  background: var(--cyan);
  clip-path: var(--panel-clip);
  z-index: -2;
}}
.lb-img-wrap::after {{
  content: ""; position: absolute; inset: 1px;
  background: #000;
  clip-path: var(--panel-clip-inner);
  z-index: -1;
}}
.lb-img {{ display: block; max-width: 92vw; max-height: 84vh; padding: 4px; }}
.lb-bar {{
  position: fixed; left: 0; right: 0; bottom: 18px;
  display: flex; align-items: center; justify-content: center;
  gap: 18px;
  font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--text);
  pointer-events: none;
}}
.lb-bar > * {{ pointer-events: auto; }}
.lb-bar .name {{ color: var(--cyan); font-weight: 500; }}
.lb-bar .idx  {{ color: var(--muted); }}
.lb-nav {{
  position: fixed; top: 50%; transform: translateY(-50%);
  background: rgba(4, 6, 10, 0.55);
  border: 1px solid var(--border);
  color: var(--cyan);
  width: 44px; height: 64px;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer;
  font-size: 22px;
  user-select: none;
  transition: border-color 150ms ease-out, background 150ms ease-out;
}}
.lb-nav:hover {{ border-color: var(--cyan); background: rgba(76, 201, 240, 0.10); }}
.lb-prev {{ left: 18px; }}
.lb-next {{ right: 18px; }}
.lb-close {{
  position: fixed; top: 18px; right: 18px;
  background: rgba(4, 6, 10, 0.55);
  border: 1px solid var(--border);
  color: var(--muted);
  padding: 6px 10px;
  font: inherit; font-size: 10px;
  letter-spacing: 0.18em; text-transform: uppercase;
  cursor: pointer;
  transition: border-color 150ms ease-out, color 150ms ease-out;
}}
.lb-close:hover {{ border-color: var(--cyan); color: var(--cyan); }}
@media (max-width: 700px) {{
  .lb-nav {{ width: 36px; height: 52px; font-size: 18px; }}
  .lb-prev {{ left: 8px; }} .lb-next {{ right: 8px; }}
}}
</style>
</head>
<body>
  <header class="hud-header">
    <a class="back-tab" href="/">// ← RETURN TO BRIDGE</a>
    <span class="hud-sep">·</span>
    <span class="hud-sub">TICALC.CAMERA / BATCH</span>
    <span class="hud-sep">·</span>
    <span class="hud-title">{safe_name}</span>
    <span class="hud-sep">·</span>
    <span class="hud-sub">{len(frames)} FRAMES</span>
  </header>
  <div class="frames">
    {items or '<div class="empty">// no frames in this batch</div>'}
  </div>
  <div class="lb" id="lb" aria-hidden="true">
    <div class="lb-img-wrap"><img class="lb-img" id="lbimg" alt=""></div>
    <button class="lb-nav lb-prev" id="lbprev" title="Previous (←)">◀</button>
    <button class="lb-nav lb-next" id="lbnext" title="Next (→)">▶</button>
    <button class="lb-close" id="lbclose" title="Close (Esc)">✕ CLOSE</button>
    <div class="lb-bar">
      <span class="idx" id="lbidx">--</span>
      <span class="name" id="lbname">--</span>
    </div>
  </div>
  <script>
    const FRAME_URLS  = {frame_urls};
    const FRAME_NAMES = {frame_names};
    const lb     = document.getElementById('lb');
    const lbImg  = document.getElementById('lbimg');
    const lbIdx  = document.getElementById('lbidx');
    const lbName = document.getElementById('lbname');
    let cur = -1;
    function open(i) {{
      if (!FRAME_URLS.length) return;
      cur = (i + FRAME_URLS.length) % FRAME_URLS.length;
      lbImg.src = FRAME_URLS[cur];
      lbName.textContent = FRAME_NAMES[cur];
      lbIdx.textContent = `${{(cur+1).toString().padStart(2,'0')}} / ${{FRAME_URLS.length.toString().padStart(2,'0')}}`;
      lb.classList.add('on');
      lb.setAttribute('aria-hidden', 'false');
    }}
    function close() {{
      lb.classList.remove('on');
      lb.setAttribute('aria-hidden', 'true');
      lbImg.removeAttribute('src');
      cur = -1;
    }}
    document.querySelectorAll('.thumb').forEach(a => {{
      a.addEventListener('click', (e) => {{
        e.preventDefault();
        open(parseInt(a.dataset.idx, 10));
      }});
    }});
    document.getElementById('lbprev').addEventListener('click', (e) => {{ e.stopPropagation(); open(cur - 1); }});
    document.getElementById('lbnext').addEventListener('click', (e) => {{ e.stopPropagation(); open(cur + 1); }});
    document.getElementById('lbclose').addEventListener('click', (e) => {{ e.stopPropagation(); close(); }});
    lb.addEventListener('click', (e) => {{
      // Click on backdrop closes; clicks on the image wrap don't.
      if (e.target === lb) close();
    }});
    window.addEventListener('keydown', (e) => {{
      if (!lb.classList.contains('on')) return;
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowLeft')  open(cur - 1);
      else if (e.key === 'ArrowRight') open(cur + 1);
    }});
  </script>
</body>
</html>"""
        self._send(200, html, "text/html; charset=utf-8")

    def _serve_batch_file(self, rel):
        parts = Path(rel).parts
        if len(parts) != 2:
            self._send(404, "bad path"); return
        f = SAVE_DIR / parts[0] / parts[1]
        if not f.is_file() or not f.name.endswith(".jpg"):
            self._send(404, "not found"); return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype)

    # ── Stream proxy with side-channel buffer feed ─────────────────
    def _proxy_stream(self, path):
        """Raw TCP tunnel to Pi's /stream.mjpeg. Parses every chunk for
        complete JPEGs and pushes them into the rolling frame buffer so a
        click on Capture can grab the last N frames."""
        try:
            sock = socket.create_connection((PI_HOST, PI_HTTP_PORT), timeout=10)
        except Exception as e:
            self._send(502, f"upstream connect failed: {e}")
            return
        try:
            req = (f"GET {path} HTTP/1.0\r\n"
                   f"Host: {PI_HOST}:{PI_HTTP_PORT}\r\n"
                   f"Accept: */*\r\n"
                   f"Connection: close\r\n\r\n").encode("utf-8")
            sock.sendall(req)
            sock.settimeout(None)

            # We need to split off the response headers before we start feeding
            # the buffer (otherwise we'd find the status line garbage and miss
            # the SOI/EOI of the first JPEG). Read until we see CRLFCRLF.
            head = b""
            while b"\r\n\r\n" not in head:
                ch = sock.recv(4096)
                if not ch:
                    return
                head += ch
                if len(head) > 32768:
                    return
            hsplit = head.find(b"\r\n\r\n") + 4
            header_bytes = head[:hsplit]
            body_so_far  = head[hsplit:]

            # Forward headers + initial body bytes to the browser
            try:
                self.wfile.write(header_bytes)
                if body_so_far:
                    self.wfile.write(body_so_far)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

            scratch = body_so_far
            scratch = _emit_frames_into_buffer(b"", scratch)

            while True:
                try:
                    chunk = sock.recv(8192)
                except OSError:
                    break
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
                scratch = _emit_frames_into_buffer(scratch, chunk)
                push_status_throttled()
        finally:
            try: sock.close()
            except Exception: pass

    # ── SSE ────────────────────────────────────────────────────────
    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = queue.Queue(maxsize=500)
        with state.lock:
            state.subscribers.add(q)
            backlog = list(state.log)[-100:]
        try:
            snap = build_status(); snap["kind"] = "status"
            self._sse_send(snap)
            for evt in backlog:
                self._sse_send(evt)
            while True:
                try:
                    evt = q.get(timeout=15)
                    self._sse_send(evt)
                except queue.Empty:
                    try:
                        self.wfile.write(b": ka\n\n"); self.wfile.flush()
                    except Exception:
                        break
        except Exception:
            pass
        finally:
            with state.lock:
                state.subscribers.discard(q)

    def _sse_send(self, obj):
        line = "data: " + json.dumps(obj) + "\n\n"
        self.wfile.write(line.encode("utf-8")); self.wfile.flush()


# Throttle status broadcasts to ~2 Hz while frames pour in.
_status_throttle_last = [0.0]
def push_status_throttled():
    now = time.monotonic()
    if now - _status_throttle_last[0] > 0.5:
        _status_throttle_last[0] = now
        push_status()


def _humanize(seconds):
    s = int(seconds)
    if s < 5:   return "just now"
    if s < 60:  return f"{s}s ago"
    if s < 3600: return f"{s//60}m ago"
    if s < 86400: return f"{s//3600}h ago"
    return f"{s//86400}d ago"


# ── Entry point ────────────────────────────────────────────────────
def main():
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=pi_poller_loop, daemon=True, name="pi-poll").start()
    threading.Thread(target=pi_system_poller_loop, daemon=True, name="pi-sys").start()
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(LISTEN, _Handler)
    print(f"ticalc camera UI: http://localhost:{LISTEN[1]}/")
    push("sys", f"web UI on http://localhost:{LISTEN[1]}/  ·  buffer size {BUFFER_SIZE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
