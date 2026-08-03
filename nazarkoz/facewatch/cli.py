#!/usr/bin/env python3
"""facewatch CLI — talks to the running service on localhost.

    facewatch status
    facewatch people
    facewatch sessions [--limit N]
    facewatch enroll "Name" [--shots 5]
    facewatch enroll "Name" --from photo1.jpg [photo2.jpg ...]
    facewatch promote <session-id> "Name"
    facewatch forget "Name"
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8093"


def call(method, path, body=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"ok": False, "detail": "HTTP %d" % exc.code}
    except urllib.error.URLError as exc:
        sys.exit("facewatch service unreachable (%s) — is it running?\n"
                 "  sudo systemctl status facewatch" % exc.reason)


def fmt_ts(ts):
    if not ts:
        return "-"
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts))


def fmt_dur(a, b):
    if not a:
        return "-"
    secs = int((b or time.time()) - a)
    if secs < 60:
        return "%ds" % secs
    if secs < 3600:
        return "%dm%02ds" % (secs // 60, secs % 60)
    return "%dh%02dm" % (secs // 3600, (secs % 3600) // 60)


def cmd_status(_args):
    state = call("GET", "/api/state")
    h = state["health"]
    print("engine: %s  uptime %s  last frame %.1fs ago  tick %.2fs"
          % ("OK" if h["alive"] else "DOWN (%s)" % h.get("error"),
             fmt_dur(time.time() - h["uptime_s"], None),
             h.get("last_frame_age_s") or -1, h["last_tick_s"]))
    print("faces in view now: %d   enrolled embeddings: %d"
          % (h["faces_in_view"], h["enrolled_embeddings"]))
    for s in state["in_view"]:
        print("  IN VIEW: %-16s since %s  (score %.2f, session %d)"
              % (s["label"], fmt_ts(s["started_at"]),
                 s["best_score"] or 0, s["id"]))


def cmd_people(_args):
    state = call("GET", "/api/state")
    if not state["people"]:
        print("nobody enrolled yet — try: facewatch enroll \"Enes\"")
        return
    print("%-20s %-6s %-9s %s" % ("name", "shots", "sessions", "last seen"))
    for p in state["people"]:
        print("%-20s %-6d %-9d %s"
              % (p["name"], p["shots"], p["session_count"], fmt_ts(p["last_seen"])))


def cmd_sessions(args):
    state = call("GET", "/api/state")
    rows = state["sessions"][: args.limit]
    print("%-5s %-16s %-15s %-15s %-8s %s"
          % ("id", "who", "arrived", "left", "dur", "score"))
    for s in rows:
        print("%-5d %-16s %-15s %-15s %-8s %s"
              % (s["id"], s["label"], fmt_ts(s["started_at"]),
                 fmt_ts(s["ended_at"]) if s["ended_at"] else "(here)",
                 fmt_dur(s["started_at"], s["ended_at"] or s["last_seen_at"]),
                 "%.2f" % s["best_score"] if s["best_score"] else "-"))


def cmd_enroll(args):
    if args.from_files:
        results = []
        for path in args.from_files:
            with open(path, "rb") as fh:
                data = fh.read()
            res = call("POST", "/api/enroll_file?name=%s"
                       % urllib.parse.quote(args.name), body=data)
            results.append("%s: %s" % (path, res["detail"]))
        print("\n".join(results))
    else:
        print("look at the camera — capturing %d shots..." % args.shots)
        res = call("POST", "/api/enroll?name=%s&shots=%d"
                   % (urllib.parse.quote(args.name), args.shots),
                   body=b"", timeout=args.shots * 2 + 30)
        print(res["detail"])
        if not res["ok"]:
            sys.exit(1)


def cmd_promote(args):
    res = call("POST", "/api/promote",
               body=json.dumps({"session_id": args.session_id,
                                "name": args.name}).encode())
    print(res["detail"])
    if not res["ok"]:
        sys.exit(1)


def cmd_forget(args):
    res = call("POST", "/api/forget",
               body=json.dumps({"name": args.name}).encode())
    print(res["detail"])
    if not res["ok"]:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(prog="facewatch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("people").set_defaults(func=cmd_people)
    p = sub.add_parser("sessions")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_sessions)
    p = sub.add_parser("enroll")
    p.add_argument("name")
    p.add_argument("--shots", type=int, default=5)
    p.add_argument("--from", dest="from_files", nargs="+", metavar="IMG")
    p.set_defaults(func=cmd_enroll)
    p = sub.add_parser("promote")
    p.add_argument("session_id", type=int)
    p.add_argument("name")
    p.set_defaults(func=cmd_promote)
    p = sub.add_parser("forget")
    p.add_argument("name")
    p.set_defaults(func=cmd_forget)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
