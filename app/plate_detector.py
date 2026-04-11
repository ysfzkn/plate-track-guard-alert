"""License plate detection using EasyOCR with mock mode support."""

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


class EasyOCRDetector(BasePlateDetector):
    """Detects license plates using OpenCV contour detection + EasyOCR."""

    def __init__(self, confidence_threshold: float = 0.4):
        self.confidence_threshold = confidence_threshold
        self._reader = None
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            logger.info("Loading EasyOCR model (this may take a moment)...")
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=False)
            self._loaded = True
            logger.info("EasyOCR model loaded")

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        self._ensure_loaded()
        results: list[DetectionResult] = []

        # Preprocess
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.bilateralFilter(gray, 11, 17, 17)
        edges = cv2.Canny(blurred, 30, 200)

        # Find contours that could be plates
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

        # Deduplicate overlapping candidates
        candidates = self._merge_candidates(candidates)

        for x, y, w, h in candidates[:5]:  # Limit to top 5 candidates
            # Crop and pad slightly
            pad = 5
            y1 = max(0, y - pad)
            y2 = min(frame.shape[0], y + h + pad)
            x1 = max(0, x - pad)
            x2 = min(frame.shape[1], x + w + pad)
            crop = frame[y1:y2, x1:x2]

            if crop.size == 0:
                continue

            # Run OCR
            ocr_results = self._reader.readtext(crop)
            for bbox_points, text, conf in ocr_results:
                if conf < self.confidence_threshold:
                    continue

                normalized = normalize_plate(text)
                if len(normalized) < 5 or not is_valid_turkish_plate(normalized):
                    continue

                results.append(DetectionResult(
                    plate_text=text,
                    normalized_plate=normalized,
                    confidence=conf,
                    bbox=(x, y, w, h),
                    timestamp=datetime.now(),
                    frame=frame,
                ))

        # If no contour-based detection worked, try full-frame OCR as fallback
        if not results:
            ocr_results = self._reader.readtext(frame)
            for bbox_points, text, conf in ocr_results:
                if conf < self.confidence_threshold:
                    continue
                normalized = normalize_plate(text)
                if len(normalized) >= 5 and is_valid_turkish_plate(normalized):
                    # Approximate bounding box from OCR result
                    pts = np.array(bbox_points, dtype=np.int32)
                    bx, by, bw, bh = cv2.boundingRect(pts)
                    results.append(DetectionResult(
                        plate_text=text,
                        normalized_plate=normalized,
                        confidence=conf,
                        bbox=(bx, by, bw, bh),
                        timestamp=datetime.now(),
                        frame=frame,
                    ))

        return results

    @staticmethod
    def _merge_candidates(candidates: list[tuple], overlap_thresh: float = 0.5) -> list[tuple]:
        """Remove overlapping bounding boxes, keeping the largest."""
        if not candidates:
            return []
        candidates = sorted(candidates, key=lambda c: c[2] * c[3], reverse=True)
        keep = []
        for c in candidates:
            cx, cy, cw, ch = c
            overlaps = False
            for kx, ky, kw, kh in keep:
                # Check IoU
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


class YOLOv8Detector(BasePlateDetector):
    """Detects license plates using a fine-tuned YOLOv8 model + EasyOCR for text.

    Hybrid pipeline:
      1. YOLOv8 localizes the plate region in the frame (fast, accurate)
      2. EasyOCR reads the text from the cropped plate region

    This is more accurate than pure EasyOCR contour detection because
    the YOLO model is trained on actual frames from the gate camera.
    """

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
            logger.info("YOLOv8 model loaded successfully")
        except ImportError:
            raise ImportError(
                "ultralytics package not installed. Run: pip install ultralytics"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load YOLO weights '{self.weights_path}': {e}")

        logger.info("Loading EasyOCR for text extraction...")
        import easyocr
        self._ocr_reader = easyocr.Reader(["en"], gpu=False)
        logger.info("EasyOCR loaded — YOLO+OCR hybrid pipeline ready")
        self._loaded = True

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        self._ensure_loaded()
        results: list[DetectionResult] = []

        # Step 1: YOLO detection — find plate bounding boxes
        yolo_results = self._model.predict(
            source=frame,
            conf=self.confidence_threshold,
            verbose=False,
        )

        if not yolo_results or len(yolo_results[0].boxes) == 0:
            return results

        for box in yolo_results[0].boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            yolo_conf = float(box.conf[0])

            # Clamp to frame boundaries with slight padding
            pad = 5
            y1c = max(0, y1 - pad)
            y2c = min(frame.shape[0], y2 + pad)
            x1c = max(0, x1 - pad)
            x2c = min(frame.shape[1], x2 + pad)
            crop = frame[y1c:y2c, x1c:x2c]

            if crop.size == 0:
                continue

            # Step 2: EasyOCR — read text from the cropped plate region
            ocr_results = self._ocr_reader.readtext(crop)

            for _, text, ocr_conf in ocr_results:
                normalized = normalize_plate(text)
                if len(normalized) < 5 or not is_valid_turkish_plate(normalized):
                    continue

                # Combined confidence: geometric mean of YOLO and OCR
                combined_conf = (yolo_conf * ocr_conf) ** 0.5

                results.append(DetectionResult(
                    plate_text=text.strip(),
                    normalized_plate=normalized,
                    confidence=combined_conf,
                    bbox=(x1, y1, x2 - x1, y2 - y1),
                    timestamp=datetime.now(),
                    frame=frame,
                ))
                break  # One plate text per YOLO box is enough

        return results


class MockPlateDetector(BasePlateDetector):
    """Returns randomly generated plate detections for testing."""

    def __init__(self):
        self._detection_counter = 0

    def detect(self, frame: np.ndarray) -> list[DetectionResult]:
        # Only "detect" a plate ~30% of the time to simulate real behavior
        if random.random() > 0.3:
            return []

        self._detection_counter += 1

        # 70% authorized, 30% unauthorized
        if random.random() < 0.7:
            plate = random.choice(AUTHORIZED_PLATES)
        else:
            plate = random.choice(UNAUTHORIZED_PLATES)

        normalized = normalize_plate(plate)
        conf = random.uniform(0.65, 0.98)

        return [DetectionResult(
            plate_text=plate,
            normalized_plate=normalized,
            confidence=conf,
            bbox=(260, 340, 120, 30),
            timestamp=datetime.now(),
            frame=frame,
        )]
