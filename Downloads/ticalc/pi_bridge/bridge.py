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
import string
import threading
from io import BytesIO
from datetime import datetime

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


# ── Camera (lazy init via picamera2) ─────────────────────────────────
_camera = None

def _ensure_camera():
    global _camera
    if _camera is None:
        from picamera2 import Picamera2  # local import: only needed at runtime
        _camera = Picamera2()
        # Capture at near-max for OCR clarity. Pi Camera v3 native = 4608x2592.
        config = _camera.create_still_configuration(
            main={"size": (2304, 1296)},  # half-res = sharp + fast
            buffer_count=1,
        )
        _camera.configure(config)
        _camera.start()
        time.sleep(0.5)  # let auto-exposure/AWB settle

def capture_jpeg() -> bytes:
    _ensure_camera()
    bio = BytesIO()
    _camera.capture_file(bio, format="jpeg")
    return bio.getvalue()


# ── Anthropic Claude Managed Agents client ───────────────────────────
_anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


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


def send_to_agent(image_bytes: bytes | None, question: str,
                  status_cb=None) -> str:
    """Send a turn to the agent, return cleaned text answer.
    status_cb(label) is called with status strings during the request."""
    sid = ensure_session()

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
    return clean_response(response_text)


# ── USB CDC IO helpers ───────────────────────────────────────────────
_tty = None
_tty_lock = threading.Lock()

def open_tty():
    global _tty
    while True:
        try:
            _tty = open(TTY_PATH, "r+b", buffering=0)
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
    try:
        if with_photo:
            cb("CAMERA")
            image_bytes = capture_jpeg()
            state.last_frame = image_bytes
            state.frame_count += 1
        cb("WIFI")  # we're already on WiFi; signal the calc anyway
        cb("UPLOAD")
        answer = send_to_agent(image_bytes, question, status_cb=cb)
        state.lines = wrap_lines(answer)
        state.last_status = "Solved"
        reply("DONE")
    except Exception as e:
        msg = str(e)[:80]
        state.lines = [f"Error: {msg[:24]}"]
        state.last_status = f"FAIL:{msg[:30]}"
        reply(f"FAIL:{msg[:30]}")


def handle_cmd(cmd: str):
    cmd = cmd.strip()
    if not cmd:
        return
    log(f"< {cmd!r}")
    if cmd == "GET":
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
def read_loop():
    buf = b""
    while True:
        try:
            chunk = _tty.read(1)
        except OSError as e:
            log(f"read failed: {e}; reopening tty")
            time.sleep(1)
            open_tty()
            buf = b""
            continue
        if not chunk:
            time.sleep(0.01)
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
    open_tty()
    reply("BOOT")
    read_loop()


if __name__ == "__main__":
    main()
