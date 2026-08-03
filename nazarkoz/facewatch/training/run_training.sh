#!/usr/bin/env bash
# Fine-tune YOLOv8n on the Granada knife+pistol sets and export ONNX @320.
# Usage: run_training.sh <venv_bin> <dataset_yaml> <out_dir>
# The venv needs: ultralytics, onnx. Runs on Apple Silicon via MPS.
set -euo pipefail

VENV="$1"; DATA="$2"; OUT="$3"

"$VENV/yolo" detect train \
    model=yolov8n.pt data="$DATA" \
    imgsz=320 epochs=80 batch=32 device=mps workers=4 patience=15 \
    project="$OUT" name=weapons exist_ok=True plots=False

"$VENV/yolo" export model="$OUT/weapons/weights/best.pt" \
    format=onnx imgsz=320 opset=12

echo "ONNX: $OUT/weapons/weights/best.onnx"
