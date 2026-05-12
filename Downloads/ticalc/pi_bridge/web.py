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
        self.batches_saved = 0
        self.last_batch = None
        self.last_batch_at = None
        self.start_time = time.time()
        # Rolling JPEG buffer + its own lock so the proxy can update it
        # without contending with the bigger state lock.
        self.buffer_lock = threading.Lock()
        self.frame_buffer = collections.deque(maxlen=BUFFER_SIZE)
        self.frames_seen = 0

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
    return {
        "pi": pi,
        "buffer": {"count": buf_count, "max": BUFFER_SIZE, "frames_seen": frames_seen},
        "batches_saved": batches,
        "last_batch": last,
        "last_batch_at": last_at,
        "save_dir": str(SAVE_DIR),
        "uptime": int(time.time() - state.start_time),
    }


def push_status():
    snap = build_status()
    snap["kind"] = "status"
    with state.lock:
        subs = list(state.subscribers)
    for q in subs:
        try: q.put_nowait(snap)
        except queue.Full: pass


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
  --font-mono:  "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --panel-clip: polygon(12px 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%, 0 12px);
  --panel-clip-inner: polygon(11px 0, 100% 0, 100% calc(100% - 11px), calc(100% - 11px) 100%, 0 100%, 0 11px);
}

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
  grid-template-rows: auto auto 1fr auto;
  gap: 12px;
  padding: 14px;
  background-image: radial-gradient(rgba(76, 201, 240, 0.11) 1px, transparent 1px);
  background-size: 32px 32px;
}

h1 { font-size: 13px; margin: 0; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: var(--text); }
h2 { font-size: 10px; margin: 0; font-weight: 500; letter-spacing: 0.18em; text-transform: uppercase; color: var(--muted); }

/* Panel chrome — chamfered HUD frame */
.panel {
  position: relative;
  padding: 26px 14px 14px;
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
  box-shadow: 0 4px 32px rgba(76, 201, 240, 0.04);
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
.row { display: grid; grid-template-columns: 1fr 320px; gap: 12px; min-height: 0; }
@media (max-width: 900px) { .row { grid-template-columns: 1fr; } }

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
  min-height: 460px;
  height: 60vh; max-height: 75vh;
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
#log .m { color: var(--text); }
#log .m::before { content: "▸ "; color: var(--muted); }

#shots {
  display: grid;
  grid-template-columns: 1fr;
  gap: 6px;
  overflow-y: auto;
  flex: 1 1 0;
  min-height: 0;
}
.shot-row {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: stretch;
  gap: 0;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  transition: border-color 150ms ease-out, transform 150ms ease-out, background 150ms ease-out;
}
.shot-row:hover { border-color: var(--cyan); transform: translateX(4px); background: rgba(76, 201, 240, 0.06); }
.shot-link {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  color: var(--text);
  text-decoration: none;
  font-size: 12px;
}
.shot-link::before { content: "▣"; color: var(--cyan); font-size: 12px; }
.shot-row .name { color: var(--cyan); font-weight: 500; letter-spacing: 0.04em; }
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
.hud-actions { grid-column: 8; display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }

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

<section class="panel" style="max-height: 200px;">
  <span class="panel-tab">// LOG-00 · TX</span>
  <div id="log"></div>
</section>

<div id="shortcuts-help" class="shortcuts-help">
  <div class="sh-card">
    <div class="sh-title">// KEYBOARD SHORTCUTS</div>
    <dl>
      <dt>Space</dt><dd>Toggle live feed</dd>
      <dt>C</dt><dd>Execute capture (full buffer)</dd>
      <dt>S</dt><dd>Snap (single frame)</dd>
      <dt>R</dt><dd>Rotate 90°</dd>
      <dt>H / V</dt><dd>Toggle H-flip / V-flip</dd>
      <dt>?</dt><dd>Toggle this help</dd>
      <dt>Esc</dt><dd>Close help</dd>
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
  const wasNearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const div = document.createElement('div');
  const srcKey = String(ev.src || '').toLowerCase();
  const srcClass = ['cam', 'net', 'sys', 'stream'].includes(srcKey) ? srcKey : '';
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
      return (
        `<div class="shot-row" data-name="${n}">
          <a class="shot-link" href="/batch/${encodeURIComponent(b.name)}" target="_blank">
            <span class="name">${n}</span>
            <span class="meta">${b.frames} fr · ${esc(b.ago)}</span>
          </a>
          <button class="row-btn" data-action="rename" data-name="${n}" title="Rename">✎</button>
          <button class="row-btn row-btn-del" data-action="delete" data-name="${n}" title="Delete">×</button>
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
            f'<a class="thumb" href="/batchfile/{name}/{f.name}" target="_blank">'
            f'<img src="/batchfile/{name}/{f.name}" alt="{f.name}"></a>'
            for f in frames
        )
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
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; background: var(--bg-deep); color: var(--text); font-family: var(--font-mono); font-size: 13px; }}
body {{ padding: 14px; background-image: radial-gradient(rgba(76,201,240,0.06) 1px, transparent 1px); background-size: 32px 32px; min-height: 100vh; }}
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
}}
.thumb img {{
  display: block;
  width: 100%; height: 140px;
  object-fit: cover;
}}
.thumb:hover {{ border-color: var(--cyan); transform: scale(1.02); }}
.empty {{ color: var(--muted); padding: 16px 0; letter-spacing: 0.14em; text-transform: uppercase; font-size: 11px; }}
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
