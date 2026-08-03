#!/usr/bin/env python3
"""facewatch — face-based presence monitoring for pinet-zero.

Wire-up: config -> Store -> Engine thread (camera + models) -> web server
(main thread). See docs/superpowers/specs/2026-08-04-facewatch-design.md.
"""

import argparse
import signal
import sys
import tomllib
from pathlib import Path

import cv2

from store import Store
from engine import Engine
from web import make_server

DEFAULT_ROOT = Path("/opt/facewatch")
APP_DIR = Path(__file__).resolve().parent


def load_config(root):
    defaults = tomllib.loads((APP_DIR / "config.default.toml").read_text())
    user_path = root / "config.toml"
    if user_path.is_file():
        user = tomllib.loads(user_path.read_text())
        for section, values in user.items():
            defaults.setdefault(section, {}).update(values)
    return defaults


def main():
    ap = argparse.ArgumentParser(description="facewatch service")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                    help="runtime root holding data/, models/, config.toml")
    args = ap.parse_args()

    cfg = load_config(args.root)
    data_dir = args.root / "data"
    snaps_dir = data_dir / "snaps"
    snaps_dir.mkdir(parents=True, exist_ok=True)

    cv2.setNumThreads(3)  # leave a core for the web thread + OS

    store = Store(
        str(data_dir / "facewatch.db"),
        unknown_match_threshold=cfg["recognition"]["match_threshold"],
        absence_close_s=cfg["sessions"]["absence_close_s"],
    )
    engine = Engine(store, cfg, args.root / "models", snaps_dir)
    engine.start()

    server = make_server(cfg, store, engine, APP_DIR / "static", snaps_dir)

    def shutdown(signum, frame):
        engine.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print("facewatch: web on :%d, root %s" % (cfg["web"]["port"], args.root),
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
