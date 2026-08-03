#!/usr/bin/env bash
# facewatch installer — run ON THE PI as root:  sudo bash install.sh
# Idempotent: safe to re-run after updating app files.
set -euo pipefail

ROOT=/opt/facewatch
SRC="$(cd "$(dirname "$0")" && pwd)"
ZOO=https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models
YUNET=face_detection_yunet_2023mar.onnx
SFACE=face_recognition_sface_2021dec.onnx

echo "== facewatch install =="
mkdir -p "$ROOT"/{app/static,data/snaps,models}

echo "-- venv + deps"
if [ ! -x "$ROOT/venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$ROOT/venv"
fi
"$ROOT/venv/bin/pip" install --quiet opencv-python-headless

echo "-- models"
fetch_model() {  # $1=zoo_subdir $2=name $3=min_bytes
    local dest="$ROOT/models/$2"
    if [ -f "$dest" ] && [ "$(stat -c%s "$dest")" -ge "$3" ]; then
        echo "   $2 already present"
        return
    fi
    curl -fL --retry 3 -o "$dest.tmp" "$ZOO/$1/$2"
    local size
    size="$(stat -c%s "$dest.tmp")"
    if [ "$size" -lt "$3" ]; then
        echo "   ERROR: $2 downloaded only $size bytes (LFS pointer?)" >&2
        exit 1
    fi
    mv "$dest.tmp" "$dest"
    echo "   $2 fetched ($size bytes)"
}
fetch_model face_detection_yunet   "$YUNET" 100000
fetch_model face_recognition_sface "$SFACE" 10000000

echo "-- app files"
install -m 644 "$SRC"/{main,engine,store,web,cli,weapons}.py "$ROOT/app/"
install -m 644 "$SRC/config.default.toml" "$ROOT/app/"
install -m 644 "$SRC/static/index.html" "$ROOT/app/static/"
if [ ! -f "$ROOT/config.toml" ]; then
    printf '# facewatch overrides — defaults in app/config.default.toml\n' \
        > "$ROOT/config.toml"
fi
chown -R enes:enes "$ROOT/data" "$ROOT/config.toml"

echo "-- CLI wrapper"
cat > /usr/local/bin/facewatch <<'EOF'
#!/bin/sh
exec /opt/facewatch/venv/bin/python /opt/facewatch/app/cli.py "$@"
EOF
chmod 755 /usr/local/bin/facewatch

echo "-- systemd"
install -m 644 "$SRC/systemd/facewatch.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable facewatch >/dev/null
systemctl restart facewatch

echo "== done. web: http://$(hostname -I | awk '{print $1}'):8093/  cli: facewatch status =="
