#!/usr/bin/env bash
# Deploy the fine-tuned weapons model (knife+pistol) to the Pi.
# Run from the Mac: bash deploy_finetuned.sh
# Assumes `ssh pi` works (host alias in ~/.ssh/config) and the model is at
# models/weapon-yolov8n-320.onnx next to this script.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
MODEL="$SRC/models/weapon-yolov8n-320.onnx"
[ -f "$MODEL" ] || { echo "model missing: $MODEL"; exit 1; }

scp -q "$MODEL" pi:/tmp/weapons-ft.onnx
ssh pi '
  set -e
  sudo cp /opt/facewatch/models/weapon-yolov8n-320.onnx \
          /opt/facewatch/models/weapon-coco-backup.onnx 2>/dev/null || true
  sudo mv /tmp/weapons-ft.onnx /opt/facewatch/models/weapon-yolov8n-320.onnx
  sudo tee /opt/facewatch/config.toml > /dev/null <<EOF
# facewatch overrides — defaults in app/config.default.toml
[weapons]
conf = 0.45
classes = ["knife", "pistol"]
model_class_names = ["knife", "pistol"]
EOF
  sudo systemctl restart facewatch
  sleep 15
  journalctl -u facewatch --since "-30 sec" -o cat --no-pager \
      | grep -i "weapon detection" || true
  curl -s localhost:8093/api/state | /opt/facewatch/venv/bin/python -c \
      "import json,sys; h=json.load(sys.stdin)[\"health\"]; \
       print(\"alive:\", h[\"alive\"], \"weapons_on:\", h[\"weapons_on\"])"
'
echo "deployed. Test: hold a knife in view, or POST an image to /api/test_weapon on the Pi."
