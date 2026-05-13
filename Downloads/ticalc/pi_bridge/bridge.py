#!/usr/bin/env python3
"""
TiCalc Pi Bridge — Raspberry Pi Zero 2 W replacement for the ESP32-S3-CAM.

The Pi acts as a USB CDC ACM device (via libcomposite gadget). The TI-84 CE
plugs in over USB-OTG and sends the same commands it sent to the ESP32. The
bridge captures from the Pi Camera and talks to Claude Managed Agents
directly — no Railway server in between.

Calc-facing protocol (unchanged from the ESP32 firmware):
    EVAL <expr>      local math eval, replies >result
    ASK <text>       text-only AI; status replies, then LINES/LINE n pull
    ASKPHOTO <text>  photo + AI; same flow as ASK
    LINES            >N   (count of answer lines)
    LINE <N>         >line_content
    GET              >last_status_string
All replies prefixed with > so calc filters out kernel/log noise.
"""

import os
import sys
import json
import math
import time
import base64
import select
import signal
import socket
import string
import tempfile
import threading
from io import BytesIO
from datetime import datetime

import httpx
import anthropic
from anthropic import Anthropic
from http.server import BaseHTTPRequestHandler, HTTPServer


# ── Config ────────────────────────────────────────────────────────────
TTY_PATH        = "/dev/ttyGS0"          # USB CDC gadget device
AGENT_ID        = "agent_011CajPFqHWZYdaW67EB5wws"
ENVIRONMENT_ID  = "env_016tjM2kuU1M8K4DE9abitM2"
COLS            = 26                      # calc display columns
MAX_LINES       = 200                     # match calc-side buffer

# Chain mode: ESP32 (talking to calc) POSTs /capture here; we also push the
# JPEG to MAC_UPLOAD_URL (the TUI listener on the laptop) when set. The URL is
# mutable at runtime — the web UI announces its current address via
# POST /set-mac-url, so Mac DHCP lease changes don't break the push.
HTTP_PORT       = 8080
CAPTURE_DELAY_S = 3.0
_mac_url_lock   = threading.Lock()
MAC_UPLOAD_URL  = os.environ.get("MAC_UPLOAD_URL", "").strip()


def get_mac_url():
    with _mac_url_lock:
        return MAC_UPLOAD_URL


def set_mac_url(url):
    global MAC_UPLOAD_URL
    url = (url or "").strip()
    with _mac_url_lock:
        prev = MAC_UPLOAD_URL
        MAC_UPLOAD_URL = url
    if url != prev:
        log(f"MAC_UPLOAD_URL updated: {url or '(cleared)'}")


# ── Math symbol → ASCII map (mirrors main.py's _UNI_MAP) ──────────────
_UNI_MAP = {
    "α":"alpha","β":"beta","γ":"gamma","δ":"delta","ε":"epsilon","ζ":"zeta",
    "η":"eta","θ":"theta","ι":"iota","κ":"kappa","λ":"lambda","μ":"mu",
    "ν":"nu","ξ":"xi","ο":"o","π":"pi","ρ":"rho","σ":"sigma","ς":"sigma",
    "τ":"tau","υ":"upsilon","φ":"phi","χ":"chi","ψ":"psi","ω":"omega",
    "Α":"A","Β":"B","Γ":"Gamma","Δ":"Delta","Ε":"E","Ζ":"Z","Η":"H","Θ":"Theta",
    "Ι":"I","Κ":"K","Λ":"Lambda","Μ":"M","Ν":"N","Ξ":"Xi","Ο":"O","Π":"Pi",
    "Ρ":"P","Σ":"Sigma","Τ":"T","Υ":"Y","Φ":"Phi","Χ":"X","Ψ":"Psi","Ω":"Omega",
    "₀":"_0","₁":"_1","₂":"_2","₃":"_3","₄":"_4","₅":"_5","₆":"_6","₇":"_7",
    "₈":"_8","₉":"_9","₊":"_+","₋":"_-","ₐ":"_a","ₑ":"_e","ᵢ":"_i","ⱼ":"_j",
    "ₙ":"_n","ₓ":"_x",
    "⁰":"^0","¹":"^1","²":"^2","³":"^3","⁴":"^4","⁵":"^5","⁶":"^6","⁷":"^7",
    "⁸":"^8","⁹":"^9","⁺":"^+","⁻":"^-","ⁿ":"^n","ⁱ":"^i",
    "≤":"<=","≥":">=","≠":"!=","≈":"~=","≡":"==","±":"+/-","∓":"-/+",
    "×":"*","·":"*","⋅":"*","÷":"/","√":"sqrt","∛":"cbrt",
    "∞":"inf","∂":"d","∇":"grad","∫":"integral","∮":"contour-int",
    "∑":"sum","∏":"prod","∝":"~","∈":"in","∉":"not in","∋":"contains",
    "∀":"forall","∃":"exists","∅":"empty",
    "°":"deg","′":"'","″":'"',"⊥":"perp","∥":"||","∠":"angle","△":"tri",
    "→":"->","←":"<-","↑":"^","↓":"v","⇒":"=>","⇐":"<=","⇔":"<=>","↔":"<->",
    "∧":"and","∨":"or","¬":"not","⊂":"subset","⊆":"subseteq","∪":"U","∩":"^",
    "“":'"',"”":'"',"‘":"'","’":"'","–":"-","—":"-","…":"...",
    " ":" "," ":" "," ":" "," ":" ",
}

def ascii_only(text: str) -> str:
    out = []
    for ch in text:
        if ch in _UNI_MAP:
            out.append(_UNI_MAP[ch])
        elif ord(ch) < 128:
            out.append(ch)
    return "".join(out)


def clean_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.replace("$$", "").replace("$", "").replace("\\(", "").replace("\\)", "")
    return ascii_only(text).strip()


# ── 26-column word wrap ──────────────────────────────────────────────
def wrap_lines(raw: str) -> list[str]:
    out = []
    for paragraph in raw.split("\n"):
        if not paragraph:
            out.append("")
            continue
        seg = paragraph
        while seg and len(out) < MAX_LINES:
            if len(seg) <= COLS:
                out.append(seg)
                break
            # find last space between cols 9 and COLS to break on
            cut = COLS
            for i in range(COLS - 1, 8, -1):
                if seg[i] == " ":
                    cut = i
                    break
            out.append(seg[:cut])
            seg = seg[cut:].lstrip()
    return out[:MAX_LINES]


# ── Math expression evaluator (same grammar as ESP32's parseExpr) ────
class _Parser:
    def __init__(self, s):
        self.s = s
        self.i = 0

    def peek(self):
        while self.i < len(self.s) and self.s[self.i] == " ":
            self.i += 1
        return self.s[self.i] if self.i < len(self.s) else ""

    def expect(self, ch):
        if self.peek() == ch:
            self.i += 1

    def parse_atom(self):
        self.peek()
        neg = False
        if self.peek() == "-":
            neg = True
            self.i += 1
        rest = self.s[self.i:]
        for name, fn in (("sin(", math.sin), ("cos(", math.cos), ("tan(", math.tan),
                         ("asin(", math.asin), ("acos(", math.acos), ("atan(", math.atan),
                         ("sqrt(", math.sqrt), ("abs(", abs),
                         ("log(", math.log10), ("ln(", math.log)):
            if rest.startswith(name):
                self.i += len(name)
                v = self.parse_expr()
                self.expect(")")
                v = fn(v)
                return -v if neg else v
        if rest.startswith("pi") and (len(rest) == 2 or not rest[2].isalnum()):
            self.i += 2
            v = math.pi
            return -v if neg else v
        if self.peek() == "(":
            self.i += 1
            v = self.parse_expr()
            self.expect(")")
            return -v if neg else v
        # number
        start = self.i
        while self.i < len(self.s) and self.s[self.i] in "0123456789.eE+-":
            # only allow leading +/- for exponent
            if self.s[self.i] in "+-" and self.i > start and self.s[self.i - 1] not in "eE":
                break
            self.i += 1
        if self.i == start:
            raise ValueError("expected number")
        v = float(self.s[start:self.i])
        return -v if neg else v

    def parse_pow(self):
        b = self.parse_atom()
        if self.peek() == "^":
            self.i += 1
            b = math.pow(b, self.parse_pow())   # right-associative
        return b

    def parse_term(self):
        v = self.parse_pow()
        while self.peek() in ("*", "/", "%"):
            op = self.peek(); self.i += 1
            r = self.parse_pow()
            if op == "*": v *= r
            elif op == "/": v /= r
            else: v = math.fmod(v, r)
        return v

    def parse_expr(self):
        v = self.parse_term()
        while self.peek() in ("+", "-"):
            op = self.peek(); self.i += 1
            r = self.parse_term()
            v = v + r if op == "+" else v - r
        return v


def eval_local(expr: str) -> str:
    try:
        p = _Parser(expr)
        v = p.parse_expr()
        if p.peek() != "":
            return "ERR"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if math.isnan(v):
            return "ERR"
        if v == math.floor(v) and abs(v) < 1e15:
            return f"{v:.0f}"
        return f"{v:.10g}"
    except Exception:
        return "ERR"


# ── Camera (subprocess to rpicam-still) ──────────────────────────────
# Why subprocess instead of Picamera2 in-process:
#  - Picamera2 forks an IPA helper child which inherits our NOTIFY_SOCKET
#    and (in some libcamera versions) interferes with Type=notify in ways
#    that cause our parent process to exit with status 0 mid-capture.
#  - Each rpicam-still call is a clean fresh process. Slightly slower
#    (~500ms cold start) but rock solid.
import subprocess
_CAPTURE_TIMEOUT_S = 15.0

def capture_jpeg() -> bytes:
    """Capture a JPEG using rpicam-still in a subprocess. Returns JPEG bytes.
    Uses a unique tempfile per call so concurrent captures don't clobber
    each other (handle_cmd dispatches requests onto worker threads).
    Preempts any active MJPEG streamer (rpicam-vid) so the camera is free."""
    _kill_streamer()
    fd, path = tempfile.mkstemp(prefix="ticalc_capture_", suffix=".jpg", dir="/tmp")
    os.close(fd)
    try:
        cmd = [
            "rpicam-still",
            "-o", path,
            "--width", "4608", "--height", "2592",
            # Continuous AF — motor tracks the subject through the whole
            # preview, so handheld motion / subject drift can't lock us on a
            # stale focus.
            "--autofocus-mode", "continuous",
            # Final AF cycle right before the shutter fires.
            "--autofocus-on-capture",
            # Full lens range — covers near (textbook close-ups) AND far
            # (desk surface). Don't restrict to macro: PDAF can stall at the
            # range boundary if the subject is even slightly outside.
            "--autofocus-range", "full",
            # NB: do NOT set --autofocus-window. The default (whole frame) was
            # what worked in early tests — restricting to a center window
            # missed the subject when text was off-center.
            # NB: do NOT set --shutter or --gain. Let AE auto-pick; manually
            # capping shutter blew out bright scenes earlier.
            # 5 s of preview gives continuous AF time to converge.
            "-t", "5000",
            "-n",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_CAPTURE_TIMEOUT_S,
            check=False,
        )
        if proc.returncode != 0 or not (os.path.exists(path) and os.path.getsize(path) > 0):
            tail = proc.stderr.decode("utf-8", errors="replace")[-200:]
            raise RuntimeError(f"rpicam-still failed: {tail}")
        with open(path, "rb") as f:
            data = f.read()
        # Keep a copy of the most recent capture for debugging (overwritten each
        # call). Lives on tmpfs so no SD wear.
        try:
            os.replace(path, "/tmp/ticalc_last_capture.jpg")
            path = None  # don't unlink in finally
        except OSError:
            pass
        return data
    finally:
        if path:
            try: os.unlink(path)
            except FileNotFoundError: pass

def _camera_releaser_loop():
    """Stub kept for thread compatibility — subprocess capture has no state."""
    while True:
        time.sleep(60)


# ── System telemetry ─────────────────────────────────────────────────
# Used by the web UI's "BRIDGE TELEMETRY" panel. All reads are cheap
# (/sys + /proc + one vcgencmd for throttle flags). No persistent state.

# Decoded names for `vcgencmd get_throttled` bits. Bits 0–3 are current
# state; bits 16–19 are "has happened since boot" (sticky history).
_THROTTLE_NOW = {
    0: "UNDERVOLT",
    1: "FREQ-CAP",
    2: "THROTTLED",
    3: "TEMP-LIMIT",
}
_THROTTLE_PAST = {
    16: "UNDERVOLT*",
    17: "FREQ-CAP*",
    18: "THROTTLED*",
    19: "TEMP-LIMIT*",
}


def _read_first_line(path):
    try:
        with open(path) as f:
            return f.readline().strip()
    except (OSError, FileNotFoundError):
        return None


def _read_pi_model():
    """`/sys/firmware/devicetree/base/model` is null-terminated."""
    try:
        with open("/sys/firmware/devicetree/base/model", "rb") as f:
            return f.read().rstrip(b"\x00").decode("utf-8", "replace")
    except (OSError, FileNotFoundError):
        return None


def _read_throttled():
    """`vcgencmd get_throttled` → hex bitfield. Returns (raw_int, [active flags], [past flags])."""
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"],
                           capture_output=True, text=True, timeout=2)
        s = (r.stdout or "").strip()
        # "throttled=0x50000" or "throttled=0x0"
        _, _, hexpart = s.partition("=")
        val = int(hexpart, 16)
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return None, [], []
    now = [name for bit, name in _THROTTLE_NOW.items() if val & (1 << bit)]
    past = [name for bit, name in _THROTTLE_PAST.items() if val & (1 << bit)]
    return val, now, past


def _read_wifi_rssi():
    """/proc/net/wireless line for wlan0. Returns dBm int or None.
    Format (3 columns of stats): `Link Quality Level Noise ...`.
    The Level value is signed dBm (e.g. -58)."""
    try:
        with open("/proc/net/wireless") as f:
            for line in f.readlines()[2:]:  # skip 2 header lines
                if ":" not in line:
                    continue
                name, _, rest = line.partition(":")
                if name.strip() != "wlan0":
                    continue
                parts = rest.split()
                # parts: [link, level, noise, ...]
                if len(parts) >= 2:
                    return int(float(parts[1]))
        return None
    except (OSError, ValueError):
        return None


def _read_meminfo():
    """Returns (used_mb, total_mb, used_pct) or (None, None, None)."""
    try:
        with open("/proc/meminfo") as f:
            kv = {}
            for line in f:
                k, _, rest = line.partition(":")
                v = rest.strip().split()[0]
                kv[k] = int(v)  # kB
        total = kv.get("MemTotal", 0)
        avail = kv.get("MemAvailable", kv.get("MemFree", 0))
        if not total:
            return None, None, None
        used_kb = total - avail
        return round(used_kb / 1024, 1), round(total / 1024, 1), round(used_kb * 100 / total, 1)
    except (OSError, KeyError, ValueError):
        return None, None, None


def _collect_system():
    """Snapshot of Pi health for the web UI. All-best-effort: any
    individual probe failure becomes a `None` field rather than an
    error response."""
    # CPU temp
    temp_c = None
    raw = _read_first_line("/sys/class/thermal/thermal_zone0/temp")
    if raw is not None:
        try: temp_c = round(int(raw) / 1000.0, 1)
        except ValueError: pass

    # CPU frequency (current)
    cpu_freq_mhz = None
    raw = _read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    if raw is not None:
        try: cpu_freq_mhz = round(int(raw) / 1000.0)
        except ValueError: pass

    # Load avg (1, 5, 15)
    load1 = load5 = load15 = None
    raw = _read_first_line("/proc/loadavg")
    if raw:
        try:
            parts = raw.split()
            load1, load5, load15 = float(parts[0]), float(parts[1]), float(parts[2])
        except (IndexError, ValueError): pass

    # Memory
    mem_used_mb, mem_total_mb, mem_used_pct = _read_meminfo()

    # Throttle bitfield
    throt_raw, throt_now, throt_past = _read_throttled()

    # WiFi
    rssi_dbm = _read_wifi_rssi()

    # Disk (root)
    disk_used_pct = None
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free  = st.f_bavail * st.f_frsize
        if total:
            disk_used_pct = round((1 - free / total) * 100, 1)
    except OSError:
        pass

    # Uptime
    uptime_s = None
    raw = _read_first_line("/proc/uptime")
    if raw:
        try: uptime_s = int(float(raw.split()[0]))
        except (IndexError, ValueError): pass

    return {
        "model": _read_pi_model(),
        "kernel": os.uname().release,
        "temp_c": temp_c,
        "cpu_freq_mhz": cpu_freq_mhz,
        "load1": load1, "load5": load5, "load15": load15,
        "mem_used_mb": mem_used_mb, "mem_total_mb": mem_total_mb, "mem_used_pct": mem_used_pct,
        "disk_used_pct": disk_used_pct,
        "throttled_raw": throt_raw,
        "throttled_now": throt_now,
        "throttled_past": throt_past,
        "rssi_dbm": rssi_dbm,
        "uptime_s": uptime_s,
    }


# ── MJPEG live stream ─────────────────────────────────────────────
# rpicam-vid in MJPEG mode emits concatenated JPEGs on stdout. We frame
# them with multipart/x-mixed-replace boundaries so a plain <img> tag in
# the browser renders the live feed. Only one streamer is allowed at a
# time; a capture preempts the stream by killing the subprocess.
_streamer_lock = threading.Lock()
_active_streamer = None    # subprocess.Popen | None

def _terminate_streamer(proc):
    """Gracefully shut down an rpicam-vid subprocess + its libcamera/IPA
    helpers (which share the process group when we spawn with
    start_new_session=True).

    SIGTERM the group first so libcamera can park the lens VCM, release
    the camera, and let the IPA child exit cleanly. SIGKILL only after a
    short wait. The abrupt SIGKILL path is what leaves the camera
    pipeline wedged and the SoC drawing power until reboot."""
    if proc is None:
        return
    pgid = None
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None

    def _sig(s):
        try:
            if pgid is not None:
                os.killpg(pgid, s)
            else:
                proc.send_signal(s)
        except (ProcessLookupError, OSError):
            pass

    _sig(signal.SIGTERM)
    try:
        proc.wait(timeout=1.2)
        return
    except subprocess.TimeoutExpired:
        pass
    # Didn't go quietly — escalate.
    _sig(signal.SIGKILL)
    try: proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired: pass


def _sweep_orphans():
    """Kill camera helpers (rpicam-*, libcamera/IPA, pisp_*) that have
    been re-parented to init (PPID=1). These are leaks from previously
    killed sessions; if we don't reap them, the camera pipeline stays
    warm and the next rpicam invocation often can't reset it.

    Safe with a live streamer: a freshly spawned rpicam-vid has the
    bridge as PPID (start_new_session doesn't change PPID), so it
    doesn't match here."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,ppid,comm"],
            capture_output=True, text=True, timeout=2
        ).stdout
    except Exception:
        return
    orphans = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, comm = parts
        if ppid != "1":
            continue
        comm_l = comm.lower()
        if (comm_l.startswith("rpicam")
                or "libcamera" in comm_l
                or comm_l.startswith("pisp_")):
            orphans.append((pid, comm))
    for pid, _ in orphans:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, OSError, ValueError):
            pass
    if orphans:
        log("swept orphaned camera helpers: "
            + ", ".join(f"{p}({c})" for p, c in orphans))


def _kill_streamer():
    global _active_streamer
    with _streamer_lock:
        proc = _active_streamer
        _active_streamer = None
    _terminate_streamer(proc)
    _sweep_orphans()

def _start_streamer(quality=60, width=1920, height=1080, fps=15,
                    rotation=0, hflip=False, vflip=False,
                    af_mode="continuous", lens_pos=None):
    """Returns a Popen with stdout=PIPE running rpicam-vid in MJPEG mode.

    Note on rotation: the IMX708's pipeline only supports 0° and 180°
    (libcamera errors "transforms requiring transpose not supported" for
    90/270). 180° is expressed as hflip+vflip. 90°/270° are handled
    downstream (CSS for the live view, PIL for saved buffer frames).

    af_mode: 'continuous' (default — VCM hunts every frame), 'auto' (single
        AF cycle at start, then locked), or 'manual' (uses lens_pos diopters).
    lens_pos: diopter (1/m). 0.0=infinity, larger=closer. Examples:
        0.0=∞, 2.0=50cm, 4.0=25cm, 5.0=20cm, 6.67=15cm, 8.33=12cm.
    """
    global _active_streamer
    _kill_streamer()
    quality = max(10, min(int(quality), 95))
    rot = int(rotation) % 360
    cmd = [
        "rpicam-vid",
        "--codec", "mjpeg",
        "--width", str(width), "--height", str(height),
        "--framerate", str(fps),
        "--quality", str(quality),
        "-t", "0",                # run until killed
        "-n",                     # no preview window
        "-o", "-",                # stdout
    ]
    if af_mode == "manual" and lens_pos is not None:
        # Lock the lens at this diopter — no AF motor activity, no AF analysis.
        # This is the cool-running mode for a fixed-distance setup.
        cmd += ["--autofocus-mode", "manual",
                "--lens-position", f"{float(lens_pos):.3f}"]
    elif af_mode == "auto":
        cmd += ["--autofocus-mode", "auto",
                "--autofocus-range", "full"]
    else:  # continuous (default)
        cmd += ["--autofocus-mode", "continuous",
                "--autofocus-range", "full"]
    # Only 180° collapses cleanly into camera flips. 90/270 go to the client.
    apply_hflip = bool(hflip) ^ (rot == 180)
    apply_vflip = bool(vflip) ^ (rot == 180)
    if apply_hflip:
        cmd.append("--hflip")
    if apply_vflip:
        cmd.append("--vflip")
    # start_new_session=True puts rpicam-vid in its own session / process
    # group. The libcamera-spawned IPA helper inherits that group, so a
    # later os.killpg() takes the whole pipeline down together — the key
    # to avoiding orphaned helpers that keep the camera warm.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    with _streamer_lock:
        _active_streamer = proc
    return proc


# ── Status LED (onboard ACT, falls back to silent if no perms) ───────
LED_PATH = "/sys/class/leds/ACT"

def _led_write(path: str, val: str) -> bool:
    try:
        with open(path, "w") as f: f.write(val)
        return True
    except Exception:
        return False

def _led_init():
    _led_write(f"{LED_PATH}/trigger", "none")

def _led_set(on: bool):
    _led_write(f"{LED_PATH}/brightness", "1" if on else "0")

# led state: "idle" | "busy" | "error"
_led_state = "idle"
_led_state_lock = threading.Lock()

def set_led(state: str):
    global _led_state
    with _led_state_lock:
        _led_state = state

def _led_loop():
    global _led_state
    if not os.path.isdir(LED_PATH):
        log(f"LED {LED_PATH} not present; skipping LED indicator")
        return
    _led_init()
    while True:
        with _led_state_lock:
            s = _led_state
        if s == "busy":
            # fast pulse so 3am-glance knows the bridge is alive AND
            # working on something (vs. solid-on which could mean wedged)
            _led_set(True); time.sleep(0.15)
            _led_set(False); time.sleep(0.15)
        elif s == "error":
            for _ in range(3):
                _led_set(True); time.sleep(0.1)
                _led_set(False); time.sleep(0.15)
            time.sleep(1.5)
            with _led_state_lock:
                if _led_state == "error": _led_state = "idle"
        else:  # idle heartbeat
            _led_set(True); time.sleep(0.05)
            _led_set(False); time.sleep(2.0)


# ── systemd watchdog (sd_notify) ─────────────────────────────────────
def _sd_notify(msg: bytes):
    """Write a sd_notify(3) message. No-op if not run under systemd notify."""
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(msg, sock_path)
        s.close()
    except Exception as e:
        log(f"sd_notify failed: {e}")

def _watchdog_loop():
    """Pings systemd watchdog at half the WatchdogSec interval."""
    interval = float(os.environ.get("WATCHDOG_USEC", "30000000")) / 2_000_000
    while True:
        time.sleep(interval)
        _sd_notify(b"WATCHDOG=1")


# ── Anthropic Claude Managed Agents client ───────────────────────────
# 60s request timeout so a dropped WiFi link doesn't hang the bridge for
# the kernel default (~minutes). Calc surfaces FAIL: timeout.
# TCP keepalives catch silently-dead WiFi sooner than a 60s read timeout
# would: idle 30s -> probe every 10s, declare dead after 3 misses.
_keepalive_opts = [
    (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
    (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30),
    (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10),
    (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3),
]
# 10-min budgets so long extended-thinking agent runs don't blow up the chain.
# Connect stays tight (15s) — a stuck connect should still fail fast.
_http = httpx.Client(
    transport=httpx.HTTPTransport(retries=0, socket_options=_keepalive_opts),
    timeout=httpx.Timeout(600.0, connect=15.0),
)
_anthropic = Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    timeout=600.0,
    http_client=_http,
)

# Errors we'll retry once on (transient: network blips, 5xx, rate limit)
_RETRYABLE = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


class State:
    def __init__(self):
        self.session_id = None
        self.lines = []
        self.last_frame = None
        self.last_status = "Ready"
        self.frame_count = 0
        # Manual-push queue: web UI sets this; next ASKPHOTO/ASK from the calc
        # returns this text *instead* of calling the Claude agent. One-shot —
        # cleared on delivery. Lets a human on the laptop drop a note onto the
        # calc's screen without ever touching the agent.
        self.pending_msg = None
        self.pending_msg_lock = threading.Lock()

state = State()


def take_pending_msg():
    """Pop the queued message if any, returning the text (or None)."""
    with state.pending_msg_lock:
        msg = state.pending_msg
        state.pending_msg = None
    return msg


def set_pending_msg(msg):
    msg = (msg or "").strip()
    with state.pending_msg_lock:
        state.pending_msg = msg or None
    if msg:
        log(f"queued message for calc: {msg[:180]}")
    else:
        log("cleared pending message")


def ensure_session() -> str:
    if state.session_id is None:
        s = _anthropic.beta.sessions.create(
            agent=AGENT_ID, environment_id=ENVIRONMENT_ID,
        )
        state.session_id = s.id
        log(f"created session {state.session_id}")
    return state.session_id


def _send_to_agent_once(content: list) -> str:
    sid = ensure_session()
    response_text = ""
    with _anthropic.beta.sessions.events.stream(sid) as stream:
        _anthropic.beta.sessions.events.send(
            sid, events=[{"type": "user.message", "content": content}]
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        response_text += block.text
            elif event.type == "session.status_idle":
                break
            elif event.type == "session.error":
                raise RuntimeError(event.error.message)
    return response_text

def send_to_agent(image_bytes: bytes | None, question: str,
                  status_cb=None) -> str:
    """Send a turn to the agent, return cleaned text answer.
    Retries once on transient errors (network blip / 5xx / rate limit).
    On any failure, drops the current session so the next call gets a
    fresh one — a corrupted server-side session would otherwise lock
    the bridge into perpetual failure."""
    content = []
    if image_bytes:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
            },
        })
    content.append({"type": "text", "text": question or "Solve this."})

    if status_cb: status_cb("WAITING")
    try:
        last_err = None
        for attempt in (1, 2):
            try:
                return clean_response(_send_to_agent_once(content))
            except _RETRYABLE as e:
                last_err = e
                log(f"API attempt {attempt} failed ({type(e).__name__}); "
                    f"{'retrying in 2s' if attempt == 1 else 'giving up'}")
                if attempt == 1:
                    time.sleep(2.0)
        raise last_err
    except Exception:
        # Drop session — next ensure_session() will create a fresh one.
        # Covers session.error events, repeated retryable failures, and
        # anything else (e.g. SDK validation errors against a stale sid).
        old_sid = state.session_id
        state.session_id = None
        if old_sid:
            log(f"cleared session {old_sid} after error")
        raise


# ── USB CDC IO helpers ───────────────────────────────────────────────
_tty = None
_tty_lock = threading.Lock()

def open_tty():
    """Open /dev/ttyGS0 non-blocking. Non-blocking matters because:
    - reads are driven by select() in the read loop
    - writes never wedge if the calc has unplugged or hasn't enumerated us yet
      (kernel CDC ACM blocks writes until the host opens the endpoint)"""
    global _tty
    import fcntl
    while True:
        try:
            fd = os.open(TTY_PATH, os.O_RDWR | os.O_NONBLOCK)
            _tty = os.fdopen(fd, "r+b", buffering=0)
            log(f"opened {TTY_PATH}")
            return
        except FileNotFoundError:
            log(f"{TTY_PATH} not present yet, retrying...")
            time.sleep(2)
        except Exception as e:
            log(f"open failed: {e}; retrying")
            time.sleep(2)

def reply(msg: str):
    line = (">" + msg + "\n").encode()
    with _tty_lock:
        try:
            _tty.write(line)
            _tty.flush()
        except BlockingIOError:
            # Kernel CDC buffer full or host not reading. We log so 3am
            # debug shows missed status updates / DONE / FAIL replies —
            # those drops cause the calc to read stale state next poll.
            log(f"reply dropped (kernel buffer full): {msg[:40]}")
        except Exception as e:
            log(f"reply failed: {e}")

def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── Command handlers ─────────────────────────────────────────────────
def status_cb_factory():
    """Returns a callback that emits S:<phase> reply lines as the agent
    request progresses. Mirrors the ESP32 status protocol so the calc
    can drive its phase indicator."""
    last = [None]
    def cb(label):
        if label != last[0]:
            reply(f"S:{label}")
            last[0] = label
    return cb


def do_eval(expr: str):
    res = eval_local(expr)
    if res == "ERR":
        state.lines = ["Syntax error"]
        state.last_status = "Error"
    else:
        state.lines = [res]
        state.last_status = "OK"
    reply(res)


def do_ask(question: str, with_photo: bool):
    state.last_status = "Asking"
    reply("OK")
    log(f"Q: {question[:180]}{'…' if len(question) > 180 else ''}  (photo={with_photo})")

    # Short-circuit: if the web UI queued a manual message, deliver it instead.
    pending = take_pending_msg()
    if pending is not None:
        log("delivering queued message to calc (skipping agent)")
        state.lines = wrap_lines(pending)
        for ln in pending.splitlines()[:30]:
            log(f"A: {ln[:200]}")
        state.last_status = "Pushed"
        reply("DONE")
        return

    cb = status_cb_factory()
    image_bytes = None
    set_led("busy")
    try:
        if with_photo:
            cb("CAMERA")
            image_bytes = capture_jpeg()
            state.last_frame = image_bytes
            state.frame_count += 1
        cb("WIFI")
        cb("UPLOAD")
        answer = send_to_agent(image_bytes, question, status_cb=cb)
        state.lines = wrap_lines(answer)
        for ln in answer.splitlines()[:30]:
            log(f"A: {ln[:200]}")
        if len(answer.splitlines()) > 30:
            log(f"A: …({len(answer.splitlines()) - 30} more lines)")
        state.last_status = "Solved"
        reply("DONE")
        set_led("idle")
    except Exception as e:
        msg = str(e)[:80]
        state.lines = [f"Error: {msg[:24]}"]
        state.last_status = f"FAIL:{msg[:30]}"
        reply(f"FAIL:{msg[:30]}")
        set_led("error")


# Tracks whether an ASK/ASKPHOTO is in flight. We dispatch those onto a
# worker thread so the read loop stays responsive (PING/GET/LINES/LINE n
# can be answered while a long-running request is pending). A second
# concurrent request gets BUSY rather than queueing — the calc UI is
# single-shot anyway.
_request_in_flight = threading.Lock()

def _spawn_ask(question: str, with_photo: bool):
    if not _request_in_flight.acquire(blocking=False):
        reply("BUSY")
        return
    def worker():
        try:
            do_ask(question, with_photo)
        finally:
            _request_in_flight.release()
    threading.Thread(target=worker, daemon=True, name="req").start()


def handle_cmd(cmd: str):
    cmd = cmd.strip()
    if not cmd:
        return
    log(f"< {cmd!r}")
    if cmd == "PING":
        reply("PONG")
    elif cmd == "GET":
        reply(state.last_status)
    elif cmd == "LINES":
        reply(str(len(state.lines)))
    elif cmd.startswith("LINE "):
        try:
            idx = int(cmd[5:])
        except ValueError:
            reply("")
            return
        # Snapshot to avoid index-out-of-range if the worker thread
        # rebinds state.lines between the bounds check and the read.
        lines = state.lines
        reply(lines[idx] if 0 <= idx < len(lines) else "")
    elif cmd.startswith("EVAL "):
        do_eval(cmd[5:])
    elif cmd.startswith("ASKPHOTO "):
        _spawn_ask(cmd[9:], with_photo=True)
    elif cmd.startswith("ASK "):
        _spawn_ask(cmd[4:], with_photo=False)
    else:
        log(f"unknown command: {cmd!r}")


# ── Read loop ────────────────────────────────────────────────────────
def _reopen_tty():
    """Close + reopen /dev/ttyGS0 (after calc unplug or kernel hiccup)."""
    global _tty
    try: _tty.close()
    except Exception: pass
    open_tty()

def read_loop():
    buf = b""
    while True:
        try:
            # block up to 1s for data, then loop so watchdog/LED stay alive
            ready, _, _ = select.select([_tty], [], [], 1.0)
            if not ready:
                continue
            chunk = _tty.read(1)
        except BlockingIOError:
            # raced with select; just loop
            continue
        except (OSError, ValueError) as e:
            # ValueError = read on closed file
            log(f"tty read failed: {e}; reopening")
            time.sleep(0.5)
            _reopen_tty()
            buf = b""
            continue
        if not chunk:
            # EOF — host (calc) disconnected; reopen so we don't busy-loop
            log("tty EOF (calc disconnected?); reopening")
            _reopen_tty()
            buf = b""
            continue
        if chunk in (b"\n", b"\r"):
            if buf:
                try:
                    handle_cmd(buf.decode("utf-8", errors="replace"))
                except Exception as e:
                    log(f"handler error: {e}")
                buf = b""
        else:
            buf += chunk
            if len(buf) > 4096:
                buf = b""  # runaway, reset


def http_capture_flow(question: str) -> str:
    """Triggered by HTTP POST /capture from the ESP32 in the chain setup.
    Sleeps CAPTURE_DELAY_S so the user can settle the calc/page, captures a
    JPEG, fires it off to the Mac TUI in the background, then blocks on the
    Claude agent and returns the text answer (which the ESP relays to the
    calc).

    Short-circuit: if a manual message has been queued via /push, that text
    is delivered to the calc instead of running the camera + agent."""
    pending = take_pending_msg()
    if pending is not None:
        log(f"delivering queued message to calc (skipping agent)")
        state.lines = wrap_lines(pending)
        state.last_status = "Pushed"
        return pending

    set_led("busy")
    try:
        time.sleep(CAPTURE_DELAY_S)
        image_bytes = capture_jpeg()
        state.last_frame = image_bytes
        state.frame_count += 1
        if get_mac_url():
            threading.Thread(target=_push_to_mac, args=(image_bytes,),
                             daemon=True, name="mac-push").start()
        else:
            log("warn: no MAC_UPLOAD_URL — image not pushed to Mac (POST /set-mac-url to set it)")
        answer = send_to_agent(image_bytes, question or "Solve this.")
        state.lines = wrap_lines(answer)
        state.last_status = "Solved"
        set_led("idle")
        return answer
    except Exception:
        set_led("error")
        raise


def _push_to_mac(jpeg_bytes: bytes):
    """Fire-and-forget upload of the latest capture to the Mac TUI's
    /upload listener. Failure here must never block the calc-facing flow."""
    url = get_mac_url()
    if not url:
        log("push to mac skipped: no MAC_UPLOAD_URL set")
        return
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"shot_{ts}.jpg"
        r = _http.post(url, content=jpeg_bytes,
                       headers={"Content-Type": "image/jpeg",
                                "X-Filename": filename},
                       timeout=15.0)
        log(f"pushed to mac: {r.status_code} ({len(jpeg_bytes)//1024}KB) -> {url}")
    except Exception as e:
        log(f"push to mac failed ({url}): {e}")


class _CaptureHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(f"http {self.client_address[0]} {fmt % args}")

    def do_GET(self):
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/system":
            body = json.dumps(_collect_system()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/push":
            # GET /push returns the currently queued message (for debugging /
            # the web UI to verify a queue exists).
            with state.pending_msg_lock:
                pending = state.pending_msg or ""
            body = pending.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/mac-url":
            # GET /mac-url returns where we'd push images.
            body = get_mac_url().encode("utf-8") or b""
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/stream.mjpeg"):
            self._serve_mjpeg()
        else:
            self.send_error(404)

    def _serve_mjpeg(self):
        """Start rpicam-vid and re-frame its MJPEG stdout as multipart/x-mixed-replace.
        Accepts ?q=NN for quality (10–95). Holds a private reference to its own
        rpicam-vid Popen so a newer concurrent client (which preempts us by calling
        _kill_streamer in _start_streamer) doesn't accidentally get killed by us
        on our way out."""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        quality = int(q.get("q", ["60"])[0]) if q.get("q") else 60
        # Resolution preset OR explicit w/h/fps. Presets balance the Pi Zero 2 W's
        # limited CPU vs pixel count — higher res means lower fps.
        preset = (q.get("res", [""])[0] or "").lower()
        presets = {
            "hd":     (1280, 720,  20),
            "fhd":    (1920, 1080, 15),
            "qhd":    (2304, 1296, 12),   # IMX708 native sensor mode
            "uhd":    (3840, 2160,  8),   # 4K — best effort
            "max":    (4608, 2592,  6),   # sensor max
        }
        if preset in presets:
            width, height, fps = presets[preset]
        else:
            width  = int(q.get("w",   ["1920"])[0])
            height = int(q.get("h",   ["1080"])[0])
            fps    = int(q.get("fps", ["15"])[0])
        rotation = int(q.get("rot",   ["0"])[0]) if q.get("rot")   else 0
        hflip    = q.get("hflip", ["0"])[0] in ("1", "true", "yes")
        vflip    = q.get("vflip", ["0"])[0] in ("1", "true", "yes")
        af_mode  = (q.get("af", ["continuous"])[0] or "continuous").lower()
        if af_mode not in ("continuous", "auto", "manual"):
            af_mode = "continuous"
        lens_pos = None
        if q.get("lens"):
            try: lens_pos = float(q.get("lens", ["0"])[0])
            except ValueError: lens_pos = None
        proc = _start_streamer(quality=quality, width=width, height=height, fps=fps,
                               rotation=rotation, hflip=hflip, vflip=vflip,
                               af_mode=af_mode, lens_pos=lens_pos)
        my_proc = proc
        af_desc = af_mode if af_mode != "manual" else f"manual@{lens_pos:.2f}D"
        log(f"mjpeg stream client connected: {self.client_address[0]} "
            f"{width}x{height}@{fps} q={quality} rot={rotation} "
            f"hflip={int(hflip)} vflip={int(vflip)} af={af_desc}")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            # Don't force Connection: close — leave the default and let the framing
            # carry the stream lifetime. Some browsers cut the connection early
            # when they see explicit "close" on multipart/x-mixed-replace.
            self.end_headers()
        except Exception:
            _kill_streamer()
            return

        SOI = b'\xff\xd8'
        EOI = b'\xff\xd9'
        buf = b""
        frames = 0
        try:
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                buf += chunk
                while True:
                    soi = buf.find(SOI)
                    if soi < 0:
                        # Drop garbage before any SOI
                        if len(buf) > 65536:
                            buf = b""
                        break
                    eoi = buf.find(EOI, soi + 2)
                    if eoi < 0:
                        # Incomplete frame; wait for more
                        if soi > 0:
                            buf = buf[soi:]
                        break
                    frame = buf[soi:eoi + 2]
                    buf = buf[eoi + 2:]
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                        frames += 1
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
        finally:
            # Only clear the global if we're still the active one (don't stomp
            # on a newer client's streamer). Always terminate our own proc.
            global _active_streamer
            with _streamer_lock:
                if _active_streamer is my_proc:
                    _active_streamer = None
            _terminate_streamer(my_proc)
            _sweep_orphans()
            log(f"mjpeg stream client gone: {self.client_address[0]} "
                f"({frames} frames) — camera released")

    def do_POST(self):
        if self.path == "/push":
            n = int(self.headers.get("Content-Length", "0") or "0")
            msg = self.rfile.read(n).decode("utf-8", errors="replace")
            set_pending_msg(msg)
            body = b"queued" if msg.strip() else b"cleared"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/set-mac-url":
            # Web UI announces its current address (handles Mac DHCP changes).
            # Body can be a full URL (http://.../upload) or just an IP[:port].
            n = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(n).decode("utf-8", errors="replace").strip()
            url = raw
            if url and "://" not in url:
                # bare IP or IP:port — assume http and /upload path
                if ":" not in url:
                    url = f"http://{url}:9090/upload"
                else:
                    url = f"http://{url}/upload"
            set_mac_url(url)
            body = (url or "(cleared)").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/capture":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        question = self.rfile.read(length).decode("utf-8", errors="replace").strip() if length else ""
        log(f"Q: {question[:180]}{'…' if len(question) > 180 else ''}")
        try:
            answer = http_capture_flow(question)
            # Log the answer line-by-line so it lines up nicely in the journal
            # stream the web UI tails. Truncate very long answers.
            for ln in answer.splitlines()[:30]:
                log(f"A: {ln[:200]}")
            if len(answer.splitlines()) > 30:
                log(f"A: …({len(answer.splitlines()) - 30} more lines)")
            body = answer.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            log(f"http /capture error: {e}")
            err = f"FAIL:{str(e)[:200]}".encode("utf-8")
            try:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass


def _http_server_loop():
    try:
        server = HTTPServer(("0.0.0.0", HTTP_PORT), _CaptureHandler)
        log(f"HTTP capture server on 0.0.0.0:{HTTP_PORT} "
            f"(initial mac upload url: {get_mac_url() or 'unset'})")
        server.serve_forever()
    except Exception as e:
        log(f"HTTP server died: {e}")


def main():
    log("TiCalc Pi Bridge starting")
    threading.Thread(target=_led_loop, daemon=True, name="led").start()
    threading.Thread(target=_camera_releaser_loop, daemon=True, name="camrel").start()
    threading.Thread(target=_http_server_loop, daemon=True, name="http").start()
    if os.environ.get("WATCHDOG_USEC"):
        threading.Thread(target=_watchdog_loop, daemon=True, name="wd").start()
    open_tty()
    # Tell systemd we're up BEFORE writing to the tty: a CDC ACM write
    # blocks if no host has enumerated us, which would cause Type=notify
    # to time out and systemd to kill us.
    _sd_notify(b"READY=1")
    reply("BOOT")
    read_loop()


if __name__ == "__main__":
    main()
