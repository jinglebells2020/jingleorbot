# facewatch — face-based presence monitoring on pinet-zero

**Date:** 2026-08-04 · **Target:** Raspberry Pi Zero 2 W (`pinet-zero`, 192.168.31.22), Camera Module 3 (IMX708), Pi OS Trixie aarch64, Python 3.13, 416MB RAM.

## What it does

Continuously watches through the Pi camera, recognizes enrolled people (multiple faces
per frame), and records **presence sessions**: "Enes arrived 14:02, left 14:47". Unknown
faces get sessions too, with saved snapshots, and can later be promoted to enrolled
people. A small web page on the LAN shows who is in view now, a session timeline, and
the people roster. Everything runs and stays on the Pi.

## Recognition engine

OpenCV's built-in face stack (no dlib, no extra ML runtime):

- **Detection:** YuNet (`face_detection_yunet_2023mar.onnx`, ~230KB) via
  `cv2.FaceDetectorYN` — returns *all* faces in frame with landmarks.
- **Embedding:** SFace (`face_recognition_sface_2021dec.onnx`, ~37MB) via
  `cv2.FaceRecognizerSF` — `alignCrop` + 128-d feature per face.
- **Matching:** cosine similarity against enrolled embeddings; threshold 0.363
  (OpenCV's published SFace operating point). Best-scoring person wins; below
  threshold ⇒ unknown.

Only pip dependency: `opencv-python-headless` (cp313/aarch64 wheel). numpy and
picamera2 come from apt via `--system-site-packages` venv.

## Architecture

One process (`/opt/facewatch`), because exactly one thing may own the camera and RAM
is scarce. Threads:

1. **Engine thread** — owns picamera2 (single 1280×720 RGB888 stream @ 5fps sensor
   rate, captured at ~1fps idle / faster when faces present; detection on a 640×360
   resize) and the two models. Also services enrollment requests from a queue, using
   the same camera frames (no contention). picamera2 `RGB888` is BGR byte order —
   passed to OpenCV as-is.
2. **Web thread(s)** — stdlib `ThreadingHTTPServer` (ticalc house style, no Flask) on
   port 8093. Read-only UI; mutating endpoints (enroll/promote/forget) are
   localhost-only, used by the CLI.

`store.py` is a camera-free presence state machine over SQLite (WAL) — unit-testable
off-device.

## Data model (SQLite, `/opt/facewatch/data/facewatch.db`)

- `people(id, name UNIQUE, created_at)`
- `embeddings(id, person_id→people, vec BLOB float32[128], source, created_at)` — several per person
- `sessions(id, person_id→people NULL=unknown, label, started_at, last_seen_at,
  ended_at NULL=open, best_score, frame_path, crop_path, emb BLOB)` — times are unix floats

Snapshots in `data/snaps/`: per session an annotated full frame (boxes + names) and a
face crop; refreshed when a better-scoring view arrives.

## Presence session logic

- Known face → refresh their open session, else open one (save snaps).
- Unknown face → matched by embedding against *open unknown sessions* (same 0.363
  threshold): a lingering stranger is one session, not many. Session keeps its first
  embedding (in DB) so `promote <session-id> <name>` can turn it into a person later.
- Sweeper closes sessions not refreshed for 60s; on service start, stale open
  sessions are closed. Old snaps pruned after 30 days (config).

## Interfaces

- **Web** `http://192.168.31.22:8093/` — "in view now" cards, session timeline with
  thumbnails, people roster; auto-refresh. Read-only.
- **CLI** `facewatch` (wrapper on the Pi, talks to localhost API):
  `status | people | sessions | enroll NAME [--shots N | --from IMG…] | promote ID NAME | forget NAME`

## Error handling

- Camera/model init failure → process exits; systemd restarts (`Restart=on-failure`).
- Camera contention: stop `facewatch` before running ticalc bridge / capture_print
  (documented; those services are currently inactive).
- RAM: `cv2.setNumThreads(3)`, single camera stream, no Flask; expect ~200MB RSS.
- No faces / detector returns None → normal idle path.

## Testing

- `test_store.py`: session state machine with synthetic embeddings (runs on Mac).
- On-Pi smoke test: models load, `/api/state` healthy, a downloaded test photo runs
  through detect→embed via the enroll-file path, RSS measured.

## Deployment

Repo `nazarkoz/facewatch/` → `rsync` to Pi → `sudo bash install.sh`: venv, pip
install, model download (GitHub LFS media URLs, size-verified), config, systemd unit
`facewatch.service` (User=enes), `/usr/local/bin/facewatch` wrapper.
