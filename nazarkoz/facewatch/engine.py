#!/usr/bin/env python3
"""facewatch engine — camera + YuNet detection + SFace recognition loop.

Owns the camera and both models. The web thread never touches the camera;
enrollment requests arrive through a queue and are serviced between frames
using the same capture stream.

picamera2 note: the "RGB888" format is B,G,R byte order in memory (DRM fourcc
naming), which is exactly OpenCV's BGR convention — no conversion needed.
"""

import queue
import threading
import time
import traceback

import cv2
import numpy as np

from store import Observation

JPEG_QUALITY = [int(cv2.IMWRITE_JPEG_QUALITY), 85]


class EnrollRequest:
    def __init__(self, kind, name, shots=5, image_bytes=None):
        self.kind = kind              # "camera" | "file"
        self.name = name
        self.shots = shots
        self.image_bytes = image_bytes
        self.done = threading.Event()
        self.result = None            # {"ok": bool, "detail": str}

    def finish(self, ok, detail):
        self.result = {"ok": ok, "detail": detail}
        self.done.set()


class Engine(threading.Thread):
    def __init__(self, store, cfg, models_dir, snaps_dir):
        super().__init__(name="engine", daemon=True)
        self.store = store
        self.cfg = cfg
        self.models_dir = models_dir
        self.snaps_dir = snaps_dir
        self.requests = queue.Queue()
        self.reload_embeddings_flag = threading.Event()
        self.stop_flag = threading.Event()
        # Health, read by the web thread:
        self.started_at = time.time()
        self.last_frame_at = 0.0
        self.last_tick_s = 0.0
        self.faces_in_view = 0
        self.error = None
        self._embeddings = []  # [(person_id, name, vec)]
        # Latest annotated view for the MJPEG stream:
        self._jpeg_lock = threading.Lock()
        self._latest_jpeg = None

    # ------------------------------------------------------------- lifecycle

    def run(self):
        try:
            self._init_models()
            self._init_camera()
            self._embeddings = self.store.load_embeddings()
            self._loop()
        except Exception:
            self.error = traceback.format_exc()
            raise

    def stop(self):
        self.stop_flag.set()

    def _init_models(self):
        rc = self.cfg["recognition"]
        dw, dh = self._detect_size()
        self.detector = cv2.FaceDetectorYN.create(
            str(self.models_dir / "face_detection_yunet_2023mar.onnx"), "",
            (dw, dh), rc["det_score_threshold"], 0.3, 50,
        )
        self.recognizer = cv2.FaceRecognizerSF.create(
            str(self.models_dir / "face_recognition_sface_2021dec.onnx"), ""
        )

    def _init_camera(self):
        from picamera2 import Picamera2  # import late: absent on dev Macs

        cam = self.cfg["camera"]
        self.picam = Picamera2()
        kwargs = {}
        if cam.get("hflip") or cam.get("vflip"):
            from libcamera import Transform
            kwargs["transform"] = Transform(hflip=int(bool(cam.get("hflip"))),
                                            vflip=int(bool(cam.get("vflip"))))
        config = self.picam.create_video_configuration(
            main={"size": (cam["width"], cam["height"]), "format": "RGB888"},
            controls={"FrameRate": cam["fps"]},
            buffer_count=3,  # default 6 costs ~13MB more CMA; RAM is scarce
            **kwargs,
        )
        self.picam.configure(config)
        self.picam.start()
        if cam.get("autofocus", True):
            try:
                from libcamera import controls as lc
                self.picam.set_controls({
                    "AfMode": lc.AfModeEnum.Continuous,
                    "AfSpeed": lc.AfSpeedEnum.Normal,
                })
                print("engine: continuous autofocus on", flush=True)
            except Exception as exc:  # camera without AF motor
                print("engine: autofocus unavailable (%s)" % exc, flush=True)
        time.sleep(1.0)  # AE/AWB settle

    def _detect_size(self):
        cam = self.cfg["camera"]
        dw = cam["detect_width"]
        dh = int(round(cam["height"] * dw / cam["width"]))
        return dw, dh

    # ------------------------------------------------------------------ loop

    def _loop(self):
        rc = self.cfg["recognition"]
        last_prune = 0.0
        while not self.stop_flag.is_set():
            t0 = time.time()
            frame = self.picam.capture_array("main")  # BGR, HxWx3
            self.last_frame_at = time.time()

            self._service_requests(frame)
            if self.reload_embeddings_flag.is_set():
                self.reload_embeddings_flag.clear()
                self._embeddings = self.store.load_embeddings()

            try:
                small, rows = self._detect(frame)
                self.faces_in_view = len(rows)
                results = []
                min_px = rc["min_face_px"]
                for face_row in rows:
                    obs = self._recognize(face_row, frame)
                    if obs is None:
                        continue
                    if obs.person_id is None and face_row[2] < min_px:
                        continue  # tiny faces make noise embeddings, not sessions
                    results.append((face_row, obs, self.store.observe(obs)))
                self._save_snaps(frame, results)
                self._publish_stream(small, results)
            except cv2.error as exc:
                # One bad frame must not kill the watcher.
                print("engine: skipped frame (%s)" % str(exc).splitlines()[-1],
                      flush=True)
            self.store.sweep()

            now = time.time()
            if now - last_prune > 86400:
                last_prune = now
                self.store.prune_snaps(
                    self.cfg["sessions"]["retention_days"], self.snaps_dir
                )

            self.last_tick_s = time.time() - t0
            interval = rc["busy_interval_s"] if rows else rc["idle_interval_s"]
            remaining = interval - self.last_tick_s
            if remaining > 0:
                self.stop_flag.wait(remaining)

    def _detect(self, frame):
        """Detect on a downscaled copy; returns (small_image, face_rows)."""
        dw, dh = self._detect_size()
        small = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
        self.detector.setInputSize((dw, dh))
        _, rows = self.detector.detect(small)
        return small, ([] if rows is None else list(rows))

    def _recognize(self, face_row, frame):
        """Embed one detected face and match it. Returns (Observation) or None.

        Detection ran on the downscaled image, but alignment/embedding use the
        full-resolution frame — 2x the face pixels makes far better embeddings.
        """
        try:
            row = face_row.copy()
            row[:14] *= self._scale()  # x,y,w,h + 5 landmark pairs
            aligned = self.recognizer.alignCrop(frame, row)
            feat = self.recognizer.feature(aligned).flatten()
        except cv2.error:
            return None
        best_pid, best_name, best_score = None, None, 0.0
        for pid, name, vec in self._embeddings:
            denom = float(np.linalg.norm(feat)) * float(np.linalg.norm(vec))
            score = float(np.dot(feat, vec)) / denom if denom else 0.0
            if score > best_score:
                best_pid, best_name, best_score = pid, name, score
        if best_score >= self.cfg["recognition"]["match_threshold"]:
            return Observation(best_pid, best_name, best_score, feat)
        return Observation(None, None, 0.0, feat)

    def _save_snaps(self, frame, results):
        """Save snapshot files for sessions that want one."""
        wants = [r for r in results if r[2].wants_snap]
        if not wants:
            return
        annotated = self._annotate(frame, results, self._scale())
        for face_row, obs, upd in wants:
            frame_name = "s%d_frame.jpg" % upd.session_id
            crop_name = "s%d_crop.jpg" % upd.session_id
            cv2.imwrite(str(self.snaps_dir / frame_name), annotated, JPEG_QUALITY)
            cv2.imwrite(
                str(self.snaps_dir / crop_name),
                self._crop(frame, face_row), JPEG_QUALITY,
            )
            self.store.set_snaps(upd.session_id, frame_name, crop_name)

    def _publish_stream(self, small, results):
        """Encode the live annotated view for /stream (every tick)."""
        img = self._annotate(small, results, 1.0) if results else small
        ok, buf = cv2.imencode(".jpg", img,
                               [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            with self._jpeg_lock:
                self._latest_jpeg = buf.tobytes()

    def get_jpeg(self):
        with self._jpeg_lock:
            return self._latest_jpeg

    def _scale(self):
        cam = self.cfg["camera"]
        return cam["width"] / float(cam["detect_width"])

    def _annotate(self, frame, results, k):
        out = frame.copy()
        H, W = out.shape[:2]
        for face_row, obs, upd in results:
            # YuNet can emit garbage coords on bad frames (AF hunting, motion
            # blur); unclamped they overflow OpenCV's int32 Point and throw.
            x, y, w, h = (int(v * k) for v in face_row[:4])
            x = max(0, min(x, W - 2)); y = max(0, min(y, H - 2))
            w = max(1, min(w, W - x - 1)); h = max(1, min(h, H - y - 1))
            color = (80, 220, 80) if obs.person_id is not None else (60, 140, 255)
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
            text = upd.label if obs.person_id is None else \
                "%s %.2f" % (upd.label, obs.score)
            cv2.putText(out, text, (x, max(18, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
        return out

    def _crop(self, frame, face_row, margin=0.35):
        k = self._scale()
        x, y, w, h = (v * k for v in face_row[:4])
        mx, my = w * margin, h * margin
        x0 = max(0, int(x - mx)); y0 = max(0, int(y - my))
        x1 = min(frame.shape[1], int(x + w + mx))
        y1 = min(frame.shape[0], int(y + h + my))
        return frame[y0:y1, x0:x1]

    # ------------------------------------------------------------ enrollment

    def _service_requests(self, frame):
        try:
            req = self.requests.get_nowait()
        except queue.Empty:
            return
        try:
            if req.kind == "camera":
                self._enroll_camera(req)
            else:
                self._enroll_file(req)
        except Exception as exc:  # report, don't kill the loop
            req.finish(False, "enroll failed: %s" % exc)

    def _largest_face_feat(self, image):
        dw = self.cfg["camera"]["detect_width"]
        dh = int(round(image.shape[0] * dw / image.shape[1]))
        small = cv2.resize(image, (dw, dh), interpolation=cv2.INTER_AREA)
        self.detector.setInputSize((dw, dh))
        _, rows = self.detector.detect(small)
        if rows is None or not len(rows):
            return None
        row = max(rows, key=lambda r: r[2] * r[3]).copy()
        row[:14] *= image.shape[1] / float(dw)  # back to full-res coords
        aligned = self.recognizer.alignCrop(image, row)
        return self.recognizer.feature(aligned).flatten()

    def _enroll_camera(self, req):
        feats = []
        for i in range(req.shots):
            frame = self.picam.capture_array("main")
            self.last_frame_at = time.time()
            feat = self._largest_face_feat(frame)
            if feat is not None:
                feats.append(feat)
            time.sleep(0.7)
        need = max(1, req.shots // 2)
        if len(feats) < need:
            req.finish(False, "only %d/%d shots had a detectable face — "
                       "get closer / better light" % (len(feats), req.shots))
            return
        pid = self.store.add_person(req.name)
        for i, feat in enumerate(feats):
            self.store.add_embedding(pid, feat, "camera:%d" % i)
        self._embeddings = self.store.load_embeddings()
        req.finish(True, "enrolled %s with %d embeddings" % (req.name, len(feats)))

    def _enroll_file(self, req):
        data = np.frombuffer(req.image_bytes, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            req.finish(False, "could not decode image")
            return
        feat = self._largest_face_feat(image)
        if feat is None:
            req.finish(False, "no face found in image")
            return
        pid = self.store.add_person(req.name)
        self.store.add_embedding(pid, feat, "file")
        self._embeddings = self.store.load_embeddings()
        req.finish(True, "enrolled %s from image" % req.name)

    def submit(self, req, timeout=30.0):
        """Called from the web thread; blocks until the engine services it."""
        self.requests.put(req)
        if not req.done.wait(timeout):
            return {"ok": False, "detail": "engine did not respond in %ss" % timeout}
        return req.result

    # ---------------------------------------------------------------- health

    def health(self):
        return {
            "alive": self.is_alive() and self.error is None,
            "error": (self.error or "").splitlines()[-1] if self.error else None,
            "uptime_s": time.time() - self.started_at,
            "last_frame_age_s": (time.time() - self.last_frame_at)
                                 if self.last_frame_at else None,
            "last_tick_s": round(self.last_tick_s, 3),
            "faces_in_view": self.faces_in_view,
            "enrolled_embeddings": len(self._embeddings),
        }
