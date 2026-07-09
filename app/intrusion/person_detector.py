"""PersonDetector — YOLOv8 person-class detection with per-camera tracking.

Key design:
  - Single YOLO model instance (shared) to keep memory low across cameras.
  - Per-camera track state so IDs don't cross-contaminate.
  - Model inference serialized via lock (Ultralytics isn't thread-safe).
  - Returns PersonObservation objects with globally-unique track IDs
    (camera_id * 1_000_000 + local_track_id).

When Module 2 is disabled or ultralytics isn't installed, this module
will still import but `PersonDetector(...)` construction will raise.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import numpy as np

from app.intrusion.models import PersonObservation

logger = logging.getLogger("gateguard.app")


class PersonDetector:
    """YOLOv8 + ByteTrack person detection across multiple cameras.

    Strategy for multi-camera tracking:
      - One shared YOLO model (loaded lazily on first detect()).
      - Ultralytics' built-in tracker is state-per-model, which would mix
        IDs across cameras. Workaround: call model.track() with
        `persist=True` and post-process by offsetting local track IDs
        with a per-camera base (track_id + camera_id * 1_000_000).
      - For strict isolation we'd need per-camera trackers; the offset
        scheme gives us globally unique IDs that the classifier can key
        on without collisions, which is sufficient for loitering/cooldown.
    """

    CAMERA_ID_OFFSET = 1_000_000   # track_id keys: local_id + camera_id * OFFSET

    # Frame down-sampling: 1080p frames are overkill for person detection — YOLO
    # itself resizes to imgsz internally (default 640). Doing a fast bicubic
    # resize on the source frame BEFORE handing it to YOLO cuts memory copies
    # and makes per-frame box scaling explicit. We resize so the long edge equals
    # imgsz and rescale bboxes back to original coordinates.
    INFERENCE_IMG_SIZE = 640

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        tracker_config: str = "bytetrack.yaml",
        use_gpu: bool | str = False,        # True/False or "auto"
        confidence: float = 0.5,
        inference_size: int | None = None,
    ):
        self.model_path = model_path
        self.tracker_config = tracker_config
        # GPU auto-detection: try CUDA, fall back to CPU silently
        if use_gpu == "auto" or (isinstance(use_gpu, str) and use_gpu.lower() == "auto"):
            self.device = self._detect_best_device()
        elif use_gpu and str(use_gpu).lower() != "false":
            self.device = "cuda"
        else:
            self.device = "cpu"
        # HARD SAFETY: never pass a CUDA device when CUDA isn't actually usable.
        # On a CPU-only torch build (torch-*+cpu) or a non-CUDA GPU, requesting
        # device="cuda" makes EVERY inference raise "Invalid CUDA 'device=0'",
        # which detect()/detect_raw swallow → silently zero detections. Fall back
        # to CPU so the system works regardless of a wrong USE_GPU_FOR_PERSON.
        if self.device == "cuda":
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning(
                        "USE_GPU requested but CUDA unavailable (torch=%s) — "
                        "falling back to CPU for person detection.",
                        getattr(torch, "__version__", "?"),
                    )
                    self.device = "cpu"
            except Exception:
                self.device = "cpu"
        self.confidence = confidence
        self.inference_size = inference_size or self.INFERENCE_IMG_SIZE
        self._model = None
        self._lock = threading.Lock()
        self._loaded = False
        self._is_onnx = str(model_path).lower().endswith(".onnx")

    @staticmethod
    def _detect_best_device() -> str:
        """Probe CUDA availability — returns 'cuda' if usable, else 'cpu'.
        Silent on failure so that bare CPU machines work out-of-the-box.
        """
        try:
            import torch
            if torch.cuda.is_available() and torch.cuda.device_count() > 0:
                logger.info("GPU detected: %s", torch.cuda.get_device_name(0))
                return "cuda"
        except Exception:
            pass
        logger.info("No GPU available — using CPU")
        return "cpu"

    @staticmethod
    def _resize_for_inference(frame: np.ndarray, target: int) -> tuple[np.ndarray, float]:
        """Resize so the long edge == target. Returns (resized, scale).
        scale is the factor to MULTIPLY by to map resized coords back to original.
        """
        h, w = frame.shape[:2]
        long_edge = max(h, w)
        if long_edge <= target:
            return frame, 1.0
        scale_down = target / long_edge
        new_w = int(w * scale_down)
        new_h = int(h * scale_down)
        try:
            import cv2 as _cv2
            resized = _cv2.resize(frame, (new_w, new_h), interpolation=_cv2.INTER_LINEAR)
        except Exception:
            return frame, 1.0
        return resized, 1.0 / scale_down   # multiply resized coords by this

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            logger.info(
                "Loading YOLO person detector: model=%s device=%s conf=%.2f",
                self.model_path, self.device, self.confidence,
            )
            try:
                from ultralytics import YOLO
            except ImportError as e:
                raise ImportError(
                    "ultralytics not installed. Run: uv sync --extra intrusion"
                ) from e

            # On a CPU-only box, cap torch's intra-op threads so person
            # inference doesn't fight the plate engine + camera threads for
            # every core. Reads the same knob as ONNX (config).
            if self.device == "cpu":
                try:
                    import torch
                    from config import settings as _s
                    torch.set_num_threads(max(1, int(_s.ONNX_INTRA_OP_THREADS)))
                except Exception:
                    pass

            # If an .onnx model was requested but isn't present, fall back to a
            # .pt so the system still starts (ultralytics can run .pt on CPU).
            from pathlib import Path as _Path
            model_path = self.model_path
            if self._is_onnx and not _Path(model_path).exists():
                fallback = _Path(model_path).with_suffix(".pt")
                if not fallback.exists():
                    fallback = _Path("yolov8n.pt")
                logger.warning(
                    "ONNX model %s missing — falling back to %s", model_path, fallback,
                )
                model_path = str(fallback)
                self._is_onnx = False

            self._model = YOLO(model_path)
            # Warm up with a dummy frame to load weights
            dummy = np.zeros((320, 320, 3), dtype=np.uint8)
            try:
                self._model.predict(dummy, classes=[0], device=self.device, verbose=False)
            except Exception:
                logger.exception("Warm-up inference failed (continuing anyway)")
            self._loaded = True
            logger.info("Person detector ready.")

    def preload(self) -> None:
        """Eagerly load the model at startup (fail fast on a low-RAM box)."""
        self._ensure_loaded()

    def detect_animals(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """One-shot detection of cats/dogs (COCO classes 15, 16). Used by the
        motion detector to suppress fallback alarms on pets. Returns bboxes only.
        Thread-safe via the same model lock as `detect()`.
        """
        self._ensure_loaded()
        if frame is None or frame.size == 0:
            return []
        small, scale = self._resize_for_inference(frame, self.inference_size)
        with self._lock:
            try:
                kwargs = dict(classes=[15, 16], conf=0.35, verbose=False)
                if not self._is_onnx:
                    kwargs["device"] = self.device
                results = self._model.predict(small, **kwargs)
            except Exception:
                logger.exception("YOLO animal detect failed")
                return []
        boxes_out: list[tuple[int, int, int, int]] = []
        if not results:
            return boxes_out
        boxes = getattr(results[0], "boxes", None)
        if boxes is None or len(boxes) == 0:
            return boxes_out
        try:
            xyxy = boxes.xyxy.cpu().numpy()
        except AttributeError:
            xyxy = np.asarray(boxes.xyxy)
        for box in xyxy:
            x1, y1, x2, y2 = [int(v * scale) for v in box]
            w, h = x2 - x1, y2 - y1
            if w > 0 and h > 0:
                boxes_out.append((x1, y1, w, h))
        return boxes_out

    def detect_raw(
        self,
        frame: np.ndarray,
        timestamp: Optional[datetime] = None,
    ) -> list[PersonObservation]:
        """Tracking-free detection — for one-off images/test videos.

        Uses model.predict() instead of model.track(), so every detection is
        returned regardless of ByteTrack state. Each observation gets a
        synthetic negative track_id (not persistent across frames).
        """
        self._ensure_loaded()
        if frame is None or frame.size == 0:
            return []
        if timestamp is None:
            timestamp = datetime.now()

        small, scale = self._resize_for_inference(frame, self.inference_size)
        with self._lock:
            try:
                kwargs = dict(classes=[0], conf=self.confidence, verbose=False)
                if not self._is_onnx:
                    kwargs["device"] = self.device
                results = self._model.predict(small, **kwargs)
            except Exception:
                logger.exception("YOLO predict() failed")
                return []

        observations: list[PersonObservation] = []
        if not results:
            return observations
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return observations
        try:
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
        except AttributeError:
            xyxy = np.asarray(boxes.xyxy)
            confs = np.asarray(boxes.conf)

        for i, (box, conf) in enumerate(zip(xyxy, confs)):
            x1, y1, x2, y2 = [int(v * scale) for v in box]
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0:
                continue
            observations.append(PersonObservation(
                track_id=-(i + 1),
                bbox=(x1, y1, w, h),
                confidence=float(conf),
                timestamp=timestamp,
                camera_id=0,
            ))
        return observations

    def detect(
        self,
        frame: np.ndarray,
        camera_id: int,
        timestamp: Optional[datetime] = None,
    ) -> list[PersonObservation]:
        """Run detection + tracking for a single frame.

        Thread-safe: the YOLO model is guarded by a lock. Callers can
        invoke this from multiple camera loops concurrently.
        """
        self._ensure_loaded()
        if frame is None or frame.size == 0:
            return []

        if timestamp is None:
            timestamp = datetime.now()

        small, scale = self._resize_for_inference(frame, self.inference_size)
        with self._lock:
            try:
                kwargs = dict(
                    persist=True,
                    classes=[0],
                    tracker=self.tracker_config,
                    conf=self.confidence,
                    verbose=False,
                )
                if not self._is_onnx:
                    kwargs["device"] = self.device
                results = self._model.track(small, **kwargs)
            except Exception:
                logger.exception("YOLO track() failed for camera %d", camera_id)
                return []

        observations: list[PersonObservation] = []
        if not results:
            return observations

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return observations

        # Convert tensors -> numpy
        try:
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
        except AttributeError:
            xyxy = np.asarray(boxes.xyxy)
            confs = np.asarray(boxes.conf)

        # Track IDs: may be None when ByteTrack hasn't confirmed yet (new detections,
        # sparse frames, motion gaps). Fall back to synthetic per-frame IDs so raw
        # detections are never dropped silently.
        if boxes.id is None:
            # Synthetic IDs from bbox position hash — stable within a frame only.
            # Using negative space to avoid collision with real tracker IDs.
            track_ids = np.array(
                [-(abs(hash((int(b[0]), int(b[1])))) % 1_000_000) - 1 for b in xyxy],
                dtype=int,
            )
        else:
            try:
                track_ids = boxes.id.cpu().numpy().astype(int)
            except AttributeError:
                track_ids = np.asarray(boxes.id, dtype=int)

        for box, conf, tid in zip(xyxy, confs, track_ids):
            x1, y1, x2, y2 = [int(v * scale) for v in box]
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0:
                continue
            unique_tid = int(tid) + camera_id * self.CAMERA_ID_OFFSET
            observations.append(PersonObservation(
                track_id=unique_tid,
                bbox=(x1, y1, w, h),
                confidence=float(conf),
                timestamp=timestamp,
                camera_id=camera_id,
                frame=frame,
            ))

        return observations

    def is_loaded(self) -> bool:
        return self._loaded
