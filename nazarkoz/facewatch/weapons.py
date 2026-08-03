#!/usr/bin/env python3
"""facewatch weapons — YOLO object detection (via cv2.dnn) + motion gauge.

Runs inside the engine thread, duty-cycled: the YOLO pass only fires when the
scene is interesting (motion or faces) and at most every interval_s. The model
is any YOLOv8/11-style ONNX with a static square input; the default is
COCO-pretrained YOLOv8n at 320px, filtered to weapon-ish classes. Swap the
.onnx (e.g. for a knife/pistol fine-tune) without touching this code — set
[weapons] classes to the class names of the new model.
"""

import threading
import time

import cv2
import numpy as np

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon",
    "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


class Detection:
    def __init__(self, label, conf, box):
        self.label = label
        self.conf = conf
        self.box = box  # (x, y, w, h) in the coords of the image given


class WeaponDetector:
    """YOLO ONNX through OpenCV DNN, filtered to configured class names."""

    def __init__(self, model_path, cfg):
        self.net = cv2.dnn.readNetFromONNX(str(model_path))
        self.input_px = cfg["input_px"]
        self.conf_threshold = cfg["conf"]
        self.wanted = set(cfg["classes"])
        names = cfg.get("model_class_names") or COCO_NAMES
        self.names = names
        self.last_ms = 0.0
        self._lock = threading.Lock()  # cv2.dnn Net forward isn't reentrant

    def detect(self, frame):
        """Full-frame BGR in, [Detection] in frame coords out."""
        with self._lock:
            return self._detect(frame)

    def _detect(self, frame):
        t0 = time.time()
        px = self.input_px
        h, w = frame.shape[:2]
        scale = min(px / w, px / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        letter = np.full((px, px, 3), 114, dtype=np.uint8)
        letter[:nh, :nw] = cv2.resize(frame, (nw, nh),
                                      interpolation=cv2.INTER_LINEAR)
        blob = cv2.dnn.blobFromImage(letter, 1 / 255.0, (px, px), swapRB=True)
        self.net.setInput(blob)
        out = self.net.forward()[0].T  # (anchors, 4+nc)

        boxes, confs, labels = [], [], []
        cls_scores = out[:, 4:]
        cls_ids = np.argmax(cls_scores, axis=1)
        cls_conf = cls_scores[np.arange(len(cls_ids)), cls_ids]
        keep = cls_conf >= self.conf_threshold
        for (cx, cy, bw, bh), conf, cid in zip(
                out[keep, :4], cls_conf[keep], cls_ids[keep]):
            name = self.names[cid] if cid < len(self.names) else str(cid)
            if name not in self.wanted:
                continue
            x = (cx - bw / 2) / scale
            y = (cy - bh / 2) / scale
            boxes.append([int(x), int(y), int(bw / scale), int(bh / scale)])
            confs.append(float(conf))
            labels.append(name)
        picked = cv2.dnn.NMSBoxes(boxes, confs, self.conf_threshold, 0.45) \
            if boxes else []
        dets = []
        for i in np.array(picked).flatten():
            x, y, bw, bh = boxes[i]
            x = max(0, min(x, w - 2)); y = max(0, min(y, h - 2))
            bw = max(1, min(bw, w - x - 1)); bh = max(1, min(bh, h - y - 1))
            dets.append(Detection(labels[i], confs[i], (x, y, bw, bh)))
        self.last_ms = (time.time() - t0) * 1000
        return dets


class MotionGauge:
    """Frame-differencing motion energy; flags sustained spikes (commotion)."""

    def __init__(self, cfg):
        self.threshold = cfg["commotion_threshold"]
        self.need_ticks = cfg["commotion_ticks"]
        self.prev = None
        self.energy = 0.0
        self.hot_ticks = 0

    def update(self, gray):
        """Feed the current grayscale small frame; returns True on commotion."""
        if self.prev is None or self.prev.shape != gray.shape:
            self.prev = gray
            return False
        diff = cv2.absdiff(gray, self.prev)
        self.prev = gray
        self.energy = float(np.mean(diff))
        if self.energy >= self.threshold:
            self.hot_ticks += 1
        else:
            self.hot_ticks = 0
        return self.hot_ticks >= self.need_ticks

    @property
    def active(self):
        return self.hot_ticks > 0
