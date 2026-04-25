"""License plate detection and OCR engines.

Supported engines (configured via LPR_ENGINE in .env):
  - "fast_alpr"   : Best accuracy (96%). Self-contained detector + OCR. Recommended.
  - "yolo_easyocr": Custom YOLO plate detector + EasyOCR text reader. Needs trained weights.
  - "easyocr"     : OpenCV contour detection + EasyOCR. No extra models needed. Low accuracy.
  - "mock"        : Fake detections for development/testing.
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

from app.camera import AUTHORIZED_PLATES, UNAUTHORIZED_PLATES
from app.database import normalize_plate, is_valid_turkish_plate
from app.models import DetectionResult

logger = logging.getLogger("gateguard.app")


class BasePlateDetector(ABC):
    @abstractmethod
    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        ...


# ================================================================
#  ENGINE 1: fast-alpr (recommended — 96% accuracy on TR plates)
#  Self-contained: has its own YOLO detector + plate OCR model
#  No external YOLO weights needed.
#  Install: pip install fast-alpr[onnx]
# ================================================================

class FastALPRDetector(BasePlateDetector):
    """End-to-end ALPR using fast-alpr library.

    Uses a built-in YOLOv9 plate detector and a CCT-based OCR model
    trained on 220K+ global license plates. Best accuracy for Turkish plates.
    """

    def __init__(self, confidence_threshold: float = 0.25):
        self.confidence_threshold = confidence_threshold
        self._alpr = None
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        logger.info("Loading fast-alpr models (detector + OCR)...")
        try:
            from fast_alpr import ALPR
            self._alpr = ALPR(
                detector_model="yolo-v9-t-384-license-plate-end2end",
                ocr_model="cct-s-v2-global-model",
            )
            self._loaded = True
            logger.info("fast-alpr ready — detector + OCR pipeline loaded")
        except ImportError:
            raise ImportError(
                "fast-alpr not installed. Run: pip install fast-alpr[onnx]"
            )

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        self._ensure_loaded()
        results: list[DetectionResult] = []

        try:
            predictions = self._alpr.predict(frame)
        except Exception:
            logger.exception("fast-alpr prediction failed")
            return results

        for pred in predictions:
            # Extract plate text
            if hasattr(pred, "ocr"):
                raw_text = pred.ocr.text if hasattr(pred.ocr, "text") else str(pred.ocr)
                conf = pred.ocr.confidence if hasattr(pred.ocr, "confidence") else 0.0
            else:
                raw_text = str(pred)
                conf = 0.0

            # Handle list-type confidence (take mean)
            if isinstance(conf, (list, tuple)):
                conf = sum(conf) / len(conf) if conf else 0.0
            conf = float(conf)

            # Handle list-type text
            if isinstance(raw_text, (list, tuple)):
                raw_text = "".join(str(t) for t in raw_text)

            # Normalize and validate — reject partial/malformed plates
            normalized = normalize_plate(raw_text)
            if len(normalized) < 5 or not is_valid_turkish_plate(normalized):
                continue
            if conf < self.confidence_threshold:
                continue

            # Extract bounding box from detector result
            bbox = None
            if hasattr(pred, "detection") and hasattr(pred.detection, "bounding_box"):
                bb = pred.detection.bounding_box
                if hasattr(bb, "x1"):
                    bbox = (int(bb.x1), int(bb.y1), int(bb.x2 - bb.x1), int(bb.y2 - bb.y1))
            if bbox is None and hasattr(pred, "detection"):
                try:
                    det = pred.detection
                    if hasattr(det, "xyxy"):
                        x1, y1, x2, y2 = [int(v) for v in det.xyxy]
                        bbox = (x1, y1, x2 - x1, y2 - y1)
                except Exception:
                    pass

            results.append(DetectionResult(
                plate_text=raw_text.strip(),
                normalized_plate=normalized,
                confidence=conf,
                bbox=bbox,
                timestamp=datetime.now(),
                frame=frame,
            ))

        return results


# ================================================================
#  ENGINE 2: Custom YOLO + EasyOCR hybrid
#  Requires a trained YOLOv8 weights file for plate localization.
#  Install: pip install ultralytics easyocr
# ================================================================

class YOLOv8Detector(BasePlateDetector):
    """Two-stage pipeline: custom YOLOv8 for plate localization + EasyOCR for text."""

    def __init__(self, weights_path: str, confidence_threshold: float = 0.25):
        self.weights_path = weights_path
        self.confidence_threshold = confidence_threshold
        self._model = None
        self._ocr_reader = None
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        logger.info("Loading YOLOv8 model: %s", self.weights_path)
        try:
            from ultralytics import YOLO
            self._model = YOLO(self.weights_path)
        except ImportError:
            raise ImportError("ultralytics not installed. Run: pip install ultralytics")
        except Exception as e:
            raise RuntimeError(f"Failed to load YOLO weights '{self.weights_path}': {e}")

        logger.info("Loading EasyOCR for text extraction...")
        import easyocr
        self._ocr_reader = easyocr.Reader(["en"], gpu=False)
        logger.info("YOLO+EasyOCR hybrid pipeline ready")
        self._loaded = True

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        self._ensure_loaded()
        results: list[DetectionResult] = []

        yolo_results = self._model.predict(source=frame, conf=self.confidence_threshold, verbose=False)
        if not yolo_results or len(yolo_results[0].boxes) == 0:
            return results

        for box in yolo_results[0].boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            yolo_conf = float(box.conf[0])

            # Crop with padding
            pad = 5
            crop = frame[max(0, y1-pad):min(frame.shape[0], y2+pad),
                         max(0, x1-pad):min(frame.shape[1], x2+pad)]
            if crop.size == 0:
                continue

            # Upscale small crops for better OCR
            h, w = crop.shape[:2]
            if w < 300:
                scale = 300 / w
                crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

            ocr_results = self._ocr_reader.readtext(
                crop, allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            )

            for _, text, ocr_conf in ocr_results:
                normalized = normalize_plate(text)
                if len(normalized) < 5 or not is_valid_turkish_plate(normalized):
                    continue
                combined_conf = (yolo_conf * ocr_conf) ** 0.5
                results.append(DetectionResult(
                    plate_text=text.strip(),
                    normalized_plate=normalized,
                    confidence=combined_conf,
                    bbox=(x1, y1, x2 - x1, y2 - y1),
                    timestamp=datetime.now(),
                    frame=frame,
                ))
                break

        return results


# ================================================================
#  ENGINE 3: EasyOCR only (contour detection + OCR, no YOLO needed)
#  Lowest accuracy. Useful as a zero-setup fallback.
#  Install: pip install easyocr
# ================================================================

class EasyOCRDetector(BasePlateDetector):
    """OpenCV contour-based plate localization + EasyOCR text reading."""

    def __init__(self, confidence_threshold: float = 0.4):
        self.confidence_threshold = confidence_threshold
        self._reader = None
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            logger.info("Loading EasyOCR model...")
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=False)
            self._loaded = True
            logger.info("EasyOCR loaded")

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        self._ensure_loaded()
        results: list[DetectionResult] = []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.bilateralFilter(gray, 11, 17, 17)
        edges = cv2.Canny(blurred, 30, 200)

        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 1000 or area > 50000:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            aspect = w / h if h > 0 else 0
            if 1.5 < aspect < 6.0:
                candidates.append((x, y, w, h))

        candidates = self._merge_candidates(candidates)

        for x, y, w, h in candidates[:5]:
            pad = 5
            crop = frame[max(0, y-pad):min(frame.shape[0], y+h+pad),
                         max(0, x-pad):min(frame.shape[1], x+w+pad)]
            if crop.size == 0:
                continue

            ocr_results = self._reader.readtext(crop)
            for _, text, conf in ocr_results:
                if conf < self.confidence_threshold:
                    continue
                normalized = normalize_plate(text)
                if len(normalized) < 5 or not is_valid_turkish_plate(normalized):
                    continue
                results.append(DetectionResult(
                    plate_text=text, normalized_plate=normalized, confidence=conf,
                    bbox=(x, y, w, h), timestamp=datetime.now(), frame=frame,
                ))

        if not results:
            ocr_results = self._reader.readtext(frame)
            for bbox_points, text, conf in ocr_results:
                if conf < self.confidence_threshold:
                    continue
                normalized = normalize_plate(text)
                if len(normalized) >= 5 and is_valid_turkish_plate(normalized):
                    pts = np.array(bbox_points, dtype=np.int32)
                    bx, by, bw, bh = cv2.boundingRect(pts)
                    results.append(DetectionResult(
                        plate_text=text, normalized_plate=normalized, confidence=conf,
                        bbox=(bx, by, bw, bh), timestamp=datetime.now(), frame=frame,
                    ))

        return results

    @staticmethod
    def _merge_candidates(candidates, overlap_thresh=0.5):
        if not candidates:
            return []
        candidates = sorted(candidates, key=lambda c: c[2] * c[3], reverse=True)
        keep = []
        for c in candidates:
            cx, cy, cw, ch = c
            overlaps = False
            for kx, ky, kw, kh in keep:
                ix = max(cx, kx)
                iy = max(cy, ky)
                ix2 = min(cx + cw, kx + kw)
                iy2 = min(cy + ch, ky + kh)
                if ix < ix2 and iy < iy2:
                    inter = (ix2 - ix) * (iy2 - iy)
                    union = cw * ch + kw * kh - inter
                    if inter / union > overlap_thresh:
                        overlaps = True
                        break
            if not overlaps:
                keep.append(c)
        return keep


# ================================================================
#  ENGINE 4: Mock detector (for development and testing)
# ================================================================

class MockPlateDetector(BasePlateDetector):
    """Scripted plate detections for testing the multi-frame tracker.

    Each "vehicle pass" is simulated as 4 consecutive frames with the same
    plate text and a bbox that grows + shifts downward (simulating a car
    approaching the camera). After the pass, 4 silent frames let the
    tracker's idle_frames trigger finalization.

    Occasionally the mock injects OCR variance on one frame (e.g., last
    char misread) to exercise the consensus logic.
    """

    def __init__(self):
        self._script: list[tuple[str, tuple[int, int, int, int], float]] = []
        self._idx = 0
        self._gap_counter = 0
        self._scenario_idx = 0
        # Mix of authorized + unauthorized plates to exercise alarm flow
        self._scenarios = []
        for p in AUTHORIZED_PLATES[:4]:
            self._scenarios.append(p)
        for p in UNAUTHORIZED_PLATES[:3]:
            self._scenarios.append(p)

    def _build_script(self, plate: str):
        """Simulate 4 consecutive frames of a vehicle approaching the camera.

        bbox grows (width + height) and moves down (y increases) — mimics
        a car driving toward the gate.
        """
        norm = normalize_plate(plate)
        # OCR variance on frame 3 (one char swap) to test consensus
        variant_norm = norm[:-1] + ("9" if norm[-1] != "9" else "8")
        variant_raw = plate[:-1] + ("9" if plate[-1] != "9" else "8")

        script = [
            (plate,        (260, 200, 100, 32), 0.82),  # far
            (plate,        (250, 260, 120, 40), 0.88),  # closer
            (variant_raw,  (240, 330, 150, 50), 0.55),  # OCR variance
            (plate,        (230, 410, 180, 60), 0.92),  # close, sharp
        ]
        return script

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        # Silent gap lets the tracker finalize before next vehicle
        if self._gap_counter > 0:
            self._gap_counter -= 1
            return []

        # Start a new scenario if current script is done
        if self._idx >= len(self._script):
            plate = self._scenarios[self._scenario_idx % len(self._scenarios)]
            self._scenario_idx += 1
            self._script = self._build_script(plate)
            self._idx = 0

        raw, bbox, conf = self._script[self._idx]
        self._idx += 1
        if self._idx >= len(self._script):
            self._gap_counter = 4  # gap after last frame

        normalized = normalize_plate(raw)
        return [DetectionResult(
            plate_text=raw,
            normalized_plate=normalized,
            confidence=conf,
            bbox=bbox,
            timestamp=datetime.now(),
            frame=frame,
        )]
