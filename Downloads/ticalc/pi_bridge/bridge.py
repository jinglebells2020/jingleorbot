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
import math
import time
import base64
import select
import socket
import string
import threading
from io import BytesIO
from datetime import datetime

import httpx
import anthropic
from anthropic import Anthropic


# ── Config ────────────────────────────────────────────────────────────
TTY_PATH        = "/dev/ttyGS0"          # USB CDC gadget device
AGENT_ID        = "agent_011CajPFqHWZYdaW67EB5wws"
ENVIRONMENT_ID  = "env_016tjM2kuU1M8K4DE9abitM2"
COLS            = 26                      # calc display columns
MAX_LINES       = 200                     # match calc-side buffer


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


# ── Camera (lazy init, idle release, watchdog) ───────────────────────
_CAMERA_IDLE_S = 30.0   # release camera after this many seconds idle
_CAPTURE_TIMEOUT_S = 15.0

_camera = None
_camera_lock = threading.Lock()
_camera_last_used = 0.0

def _close_camera_locked():
    """Caller must hold _camera_lock."""
    global _camera
    if _camera is not None:
        try: _camera.stop()
        except Exception as e: log(f"camera stop: {e}")
        try: _camera.close()
        except Exception as e: log(f"camera close: {e}")
        _camera = None

def _ensure_camera_locked():
    global _camera
    if _camera is None:
        from picamera2 import Picamera2
        from libcamera import controls
        _camera = Picamera2()
        config = _camera.create_still_configuration(
            main={"size": (4608, 2592)},
            buffer_count=1,
        )
        _camera.configure(config)
        _camera.set_controls({
            "AfMode": controls.AfModeEnum.Continuous,
            "AfSpeed": controls.AfSpeedEnum.Fast,
        })
        _camera.start()
        time.sleep(0.8)

def _do_capture_blocking() -> bytes:
    from libcamera import controls
    try:
        _camera.set_controls({"AfTrigger": controls.AfTriggerEnum.Start})
        time.sleep(0.6)
    except Exception:
        pass
    bio = BytesIO()
    _camera.capture_file(bio, format="jpeg")
    return bio.getvalue()

def capture_jpeg() -> bytes:
    """Watchdogged JPEG capture. Holds camera lock for the whole operation
    so the idle-releaser can't yank it mid-shot."""
    global _camera_last_used
    result = {}
    def worker():
        try:
            result["data"] = _do_capture_blocking()
        except BaseException as e:
            result["err"] = e

    with _camera_lock:
        _ensure_camera_locked()
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(_CAPTURE_TIMEOUT_S)
        if t.is_alive():
            log("capture timeout, killing camera")
            _close_camera_locked()  # forces the worker thread to error out
            t.join(2.0)
            raise RuntimeError("camera timeout")
        if "err" in result:
            _close_camera_locked()
            raise result["err"]
        _camera_last_used = time.monotonic()
        return result["data"]

def _camera_releaser_loop():
    """Closes the camera if it's been idle past _CAMERA_IDLE_S."""
    while True:
        time.sleep(5.0)
        with _camera_lock:
            if _camera is not None and \
               time.monotonic() - _camera_last_used > _CAMERA_IDLE_S:
                log(f"camera idle >{_CAMERA_IDLE_S:.0f}s, releasing")
                _close_camera_locked()


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
            _led_set(True); time.sleep(0.5)
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
_http = httpx.Client(
    transport=httpx.HTTPTransport(retries=0, socket_options=_keepalive_opts),
    timeout=httpx.Timeout(60.0, connect=15.0),
)
_anthropic = Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    timeout=60.0,
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

state = State()


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
    Retries once on transient errors (network blip / 5xx / rate limit)."""
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
            # Kernel CDC buffer full or host not reading — drop silently.
            # Either calc is unplugged or it'll catch up on the next poll.
            pass
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
        state.last_status = "Solved"
        reply("DONE")
        set_led("idle")
    except Exception as e:
        msg = str(e)[:80]
        state.lines = [f"Error: {msg[:24]}"]
        state.last_status = f"FAIL:{msg[:30]}"
        reply(f"FAIL:{msg[:30]}")
        set_led("error")


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
        reply(state.lines[idx] if 0 <= idx < len(state.lines) else "")
    elif cmd.startswith("EVAL "):
        do_eval(cmd[5:])
    elif cmd.startswith("ASKPHOTO "):
        do_ask(cmd[9:], with_photo=True)
    elif cmd.startswith("ASK "):
        do_ask(cmd[4:], with_photo=False)
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


def main():
    log("TiCalc Pi Bridge starting")
    threading.Thread(target=_led_loop, daemon=True, name="led").start()
    threading.Thread(target=_camera_releaser_loop, daemon=True, name="camrel").start()
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
