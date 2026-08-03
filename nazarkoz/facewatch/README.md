# facewatch

Face-based presence monitoring on `pinet-zero` (Pi Zero 2 W + Camera Module 3).
Watches continuously, recognizes enrolled people (any number of faces per frame),
and records presence sessions — arrived / left / duration — with snapshots.
All local: OpenCV YuNet (detection) + SFace (128-d embeddings), no cloud, no dlib.

Design: `../docs/superpowers/specs/2026-08-04-facewatch-design.md`

## Deploy

```
rsync -a facewatch/ enes@192.168.31.22:~/facewatch/
ssh enes@192.168.31.22 'sudo bash ~/facewatch/install.sh'
```

## Use

- Web: **http://192.168.31.22:8093/** — in-view-now, timeline, people.
- Enroll yourself (sit in front of the camera): `facewatch enroll "Enes"`
- Or from photos: `facewatch enroll "Enes" --from a.jpg b.jpg`
- Name a logged stranger: `facewatch sessions` → `facewatch promote <id> "Danil"`
- `facewatch status | people | sessions | forget NAME`

## Notes

- The camera has one owner. Stop facewatch before using capture_print / the
  ticalc bridge camera: `sudo systemctl stop facewatch`.
- Tests: `python3 test_store.py` (session state machine, runs on the Mac).
- Config overrides: `/opt/facewatch/config.toml`, then restart the service.
