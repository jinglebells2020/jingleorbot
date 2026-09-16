#!/usr/bin/env python3
"""cardcam-web - fire the shutter from a phone.

Deliberately a separate process from the capture daemon. It only writes a byte
into the same trigger FIFO, so it goes down the identical path as a GPIO button
press, and if this crashes the camera keeps working.

Stdlib only - nothing to install on the Pi.
"""

import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TRIGGER   = Path(os.environ.get("CARDCAM_TRIGGER", "/run/cardcam/trigger"))
PHOTO_DIR = Path(os.environ.get("CARDCAM_PHOTOS", Path.home() / "cardcam" / "photos"))
PORT      = int(os.environ.get("CARDCAM_WEB_PORT", "8080"))

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>cardcam</title>
<style>
  :root{--bg:#0d0f12;--panel:#171a1f;--line:#272c33;--ink:#e8eae6;--muted:#8d97a1;--hot:#e2523a}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
       min-height:100dvh;display:flex;flex-direction:column;gap:14px;
       padding:max(18px,env(safe-area-inset-top)) 18px max(18px,env(safe-area-inset-bottom))}
  header{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
  h1{margin:0;font-size:15px;font-weight:600;letter-spacing:.16em;text-transform:uppercase}
  #count{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
  #shot{appearance:none;border:none;width:100%;padding:30px 0;border-radius:14px;
        background:var(--hot);color:#fff;font-size:19px;font-weight:600;letter-spacing:.06em;
        cursor:pointer;transition:transform .08s ease,filter .15s ease}
  #shot:active{transform:scale(.985);filter:brightness(.88)}
  #shot[disabled]{filter:grayscale(.5) brightness(.65)}
  figure{margin:0;flex:1;display:flex;flex-direction:column;gap:8px;min-height:0}
  .frame{flex:1;min-height:180px;background:var(--panel);border:1px solid var(--line);
         border-radius:12px;overflow:hidden;display:flex;align-items:center;justify-content:center}
  .frame img{width:100%;height:100%;object-fit:contain;display:block}
  .frame p{color:var(--muted);font-size:13px;margin:0;padding:20px;text-align:center}
  figcaption{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);
             display:flex;justify-content:space-between;gap:10px}
  #msg{color:var(--muted)}
</style>
<header><h1>cardcam</h1><span id="count">&mdash;</span></header>
<button id="shot">SHUTTER</button>
<figure>
  <div class="frame" id="frame"><p>No photo yet. Tap the shutter.</p></div>
  <figcaption><span id="name">&mdash;</span><span id="msg"></span></figcaption>
</figure>
<script>
const $=i=>document.getElementById(i);
let latest=null;

async function status(){
  try{
    const r=await fetch('status',{cache:'no-store'});
    const j=await r.json();
    $('count').textContent=j.count+(j.count===1?' photo':' photos');
    if(j.latest&&j.latest!==latest){
      latest=j.latest;
      $('name').textContent=j.latest;
      $('frame').innerHTML='<img alt="latest photo" src="latest.jpg?t='+Date.now()+'">';
    }
  }catch(e){ $('msg').textContent='offline'; }
}

$('shot').addEventListener('click',async()=>{
  const b=$('shot'); b.disabled=true; $('msg').textContent='capturing...';
  try{
    const r=await fetch('shoot',{method:'POST'});
    if(!r.ok) throw new Error(await r.text());
    $('msg').textContent='';
    // the daemon needs about a second: capture plus the panel refresh
    setTimeout(status,1400); setTimeout(status,2600);
  }catch(e){ $('msg').textContent='failed - is cardcam running?'; }
  finally{ setTimeout(()=>{b.disabled=false;},900); }
});

status(); setInterval(status,5000);
</script>
"""


def newest():
    shots = sorted(PHOTO_DIR.glob("*.jpg"))
    return shots[-1] if shots else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):      # journald already timestamps
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/status":
            shots = list(PHOTO_DIR.glob("*.jpg"))
            n = newest()
            return self._send(200, json.dumps(
                {"count": len(shots), "latest": n.name if n else None}))
        if path == "/latest.jpg":
            n = newest()
            if not n:
                return self._send(404, json.dumps({"error": "no photos yet"}))
            return self._send(200, n.read_bytes(), "image/jpeg")
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/shoot":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            # Non-blocking: if the camera daemon isn't reading, fail fast with a
            # real message rather than hanging the phone for two seconds.
            fd = os.open(TRIGGER, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, b"w")
            finally:
                os.close(fd)
        except FileNotFoundError:
            return self._send(503, json.dumps({"error": "no trigger fifo - cardcam not running"}))
        except OSError as e:
            return self._send(503, json.dumps({"error": f"cardcam not listening ({e.strerror})"}))
        self._send(200, json.dumps({"ok": True}))


def main():
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ip = "?"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except OSError:
        pass
    print(f"cardcam-web on http://{ip}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
