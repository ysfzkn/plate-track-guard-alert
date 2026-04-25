"""MotionDetector — fallback detection for objects YOLO cannot classify.

Use case: a person climbs over a perimeter wall and YOLO's COCO 'person' class
fails to fire (occlusion, distance, unusual pose, low light). We still want to
catch this. The challenge is distinguishing a real intruder-shaped motion blob
from cats, dogs, branches swaying in wind, birds, falling leaves, etc.

Pipeline (per camera, per frame):
  1. MOG2 background subtraction → foreground mask
       MOG2 adapts to gradual lighting and uses Gaussian mixtures per pixel.
       detectShadows=True lets us strip shadow pixels (gray-128 in mask).
  2. Morphological cleanup: open (remove specks) + close (fill body gaps).
  3. findContours → bounding boxes for each blob.
  4. Drop blobs that overlap (IoU > 0.4) any concurrent YOLO person/cat/dog box.
     YOLO already handled those; we only emit residual motion.
  5. Cross-frame blob tracker (centroid + IoU) accumulates per-blob features:
       - lifetime (frames)
       - total centroid displacement (pixels)
       - aspect-ratio history (to compute stability)
       - mean bbox area
  6. "Human-likeness score" combines:
       - aspect ratio in human range [0.25, 1.6]  (vertical/compact)
       - bbox height ≥ MIN_HEIGHT_FRAC of frame   (birds/leaves out)
       - lifetime ≥ MIN_LIFETIME_FRAMES           (flicker out)
       - displacement in [DISP_MIN, DISP_MAX]     (static/erratic out)
       - low aspect-ratio variance                (cars/branches out)
     Each criterion contributes to a 0–1 score; >= 0.55 → emit observation.

Output: synthetic PersonObservation with `track_id` in a reserved negative
range so existing classifier logic (frame-count, loitering, cooldown) reuses
the same plumbing as YOLO-based detections.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

from app.intrusion.models import PersonObservation

logger = logging.getLogger("gateguard.app")


# Track IDs for motion-based detections live in a high negative range so they
# never collide with YOLO ByteTrack IDs (which are positive and offset by camera).
MOTION_TRACK_ID_BASE = -1_000_000


@dataclass
class _MotionBlob:
    """Stateful tracker for a single motion blob across frames."""
    blob_id: int
    bbox: tuple[int, int, int, int]    # current (x, y, w, h)
    first_seen: datetime
    last_seen: datetime
    first_centroid: tuple[float, float]
    last_centroid: tuple[float, float]
    centroid_path_len: float = 0.0
    aspect_history: deque = field(default_factory=lambda: deque(maxlen=15))
    area_history: deque = field(default_factory=lambda: deque(maxlen=15))
    confirmed_emitted: bool = False
    miss_count: int = 0   # consecutive frames without a match

    @property
    def lifetime_frames(self) -> int:
        return len(self.area_history)


class MotionDetector:
    """Per-camera motion-based human likelihood detector."""

    # ── Tunables (frame-fraction based so resolution-independent) ─
    MIN_AREA_FRAC = 0.005          # 0.5% of frame — smaller is rejected as noise
    MAX_AREA_FRAC = 0.50           # 50% of frame — bigger is wall light flicker
    MIN_HEIGHT_FRAC = 0.08         # 8% of frame height
    ASPECT_MIN = 0.25              # h/w >= 0.25 → not a horizontal sliver
    ASPECT_MAX = 1.60              # also reject very tall thin poles? (1.6 = human-ish)
    MIN_LIFETIME_FRAMES = 5
    DISP_MIN_FRAC = 0.02           # min movement: 2% of frame diagonal over lifetime
    DISP_MAX_FRAC = 0.60           # max movement: bird/erratic if more than 60%
    SCORE_THRESHOLD = 0.55
    YOLO_OVERLAP_IOU = 0.30        # blob considered "claimed" by YOLO if IoU > this
    BLOB_MATCH_IOU = 0.20          # cross-frame match threshold
    BLOB_MISS_DROP = 5             # frames

    def __init__(
        self,
        camera_id: int,
        history: int = 250,
        var_threshold: float = 32.0,
        detect_shadows: bool = True,
    ):
        if not _CV2_OK:
            raise ImportError("opencv-python required for MotionDetector")
        self.camera_id = camera_id
        self._mog = cv2.createBackgroundSubtractorMOG2(
            history=history,
            varThreshold=var_threshold,
            detectShadows=detect_shadows,
        )
        self._kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

        self._blobs: dict[int, _MotionBlob] = {}
        self._next_blob_id = 1
        self._lock = threading.Lock()

    def detect(
        self,
        frame: np.ndarray,
        person_obs: list[PersonObservation],
        animal_boxes: list[tuple[int, int, int, int]],
        timestamp: Optional[datetime] = None,
    ) -> list[PersonObservation]:
        """Run motion analysis. Returns synthetic PersonObservations for blobs
        that pass the human-likeness filter and aren't already explained by
        YOLO person/cat/dog detections.

        animal_boxes: list of (x,y,w,h) for cats/dogs detected by YOLO this frame.
        """
        if frame is None or frame.size == 0:
            return []
        if timestamp is None:
            timestamp = datetime.now()

        h, w = frame.shape[:2]
        diagonal = float(np.sqrt(w * w + h * h))
        frame_area = float(w * h)

        # 1. Foreground mask
        mask = self._mog.apply(frame)
        # MOG2 marks shadows as 127; binarize to keep only hard foreground
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)

        # 2. Morphological cleanup
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel_close)

        # 3. Contours → blob bboxes
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[int, int, int, int, float]] = []   # (x,y,w,h,area)
        min_area = self.MIN_AREA_FRAC * frame_area
        max_area = self.MAX_AREA_FRAC * frame_area
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area or area > max_area:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            if bh < self.MIN_HEIGHT_FRAC * h:
                continue
            candidates.append((x, y, bw, bh, area))

        if not candidates:
            self._age_blobs()
            return []

        # 4. Drop candidates that overlap YOLO-known objects (person/animal)
        known_boxes: list[tuple[int, int, int, int]] = []
        for o in person_obs:
            known_boxes.append(o.bbox)
        known_boxes.extend(animal_boxes)
        unclaimed: list[tuple[int, int, int, int, float]] = []
        for cand in candidates:
            cb = (cand[0], cand[1], cand[2], cand[3])
            if any(_iou(cb, kb) > self.YOLO_OVERLAP_IOU for kb in known_boxes):
                continue
            unclaimed.append(cand)

        if not unclaimed:
            self._age_blobs()
            return []

        # 5. Match unclaimed blobs to existing trackers; create new for unmatched
        observations: list[PersonObservation] = []
        with self._lock:
            matched_ids = set()
            for x, y, bw, bh, area in unclaimed:
                cb = (x, y, bw, bh)
                centroid = (x + bw / 2.0, y + bh / 2.0)
                best_id = None
                best_iou = self.BLOB_MATCH_IOU
                for bid, blob in self._blobs.items():
                    if bid in matched_ids:
                        continue
                    iou = _iou(cb, blob.bbox)
                    if iou > best_iou:
                        best_iou = iou
                        best_id = bid

                if best_id is not None:
                    blob = self._blobs[best_id]
                    dx = centroid[0] - blob.last_centroid[0]
                    dy = centroid[1] - blob.last_centroid[1]
                    blob.centroid_path_len += float(np.hypot(dx, dy))
                    blob.last_centroid = centroid
                    blob.last_seen = timestamp
                    blob.bbox = cb
                    blob.miss_count = 0
                    blob.aspect_history.append(bh / max(1, bw))
                    blob.area_history.append(area)
                    matched_ids.add(best_id)
                else:
                    blob = _MotionBlob(
                        blob_id=self._next_blob_id,
                        bbox=cb,
                        first_seen=timestamp,
                        last_seen=timestamp,
                        first_centroid=centroid,
                        last_centroid=centroid,
                    )
                    blob.aspect_history.append(bh / max(1, bw))
                    blob.area_history.append(area)
                    self._blobs[self._next_blob_id] = blob
                    self._next_blob_id += 1
                    matched_ids.add(blob.blob_id)

            # 6. Score and emit
            for blob in self._blobs.values():
                if blob.blob_id not in matched_ids:
                    continue   # only emit on the frame we actually saw it
                if blob.lifetime_frames < self.MIN_LIFETIME_FRAMES:
                    continue

                score, breakdown = self._score(blob, w, h, diagonal)
                if score < self.SCORE_THRESHOLD:
                    continue

                # Synthetic track_id stable for this blob's life (so the
                # downstream classifier's frame-count / loitering / cooldown
                # logic associates frames correctly).
                track_id = MOTION_TRACK_ID_BASE - blob.blob_id - self.camera_id * 10_000

                observations.append(PersonObservation(
                    track_id=track_id,
                    bbox=blob.bbox,
                    confidence=float(min(0.99, 0.40 + 0.50 * score)),
                    timestamp=timestamp,
                    camera_id=self.camera_id,
                ))
                if not blob.confirmed_emitted:
                    logger.info(
                        "MotionDetector cam=%d blob=%d emitted (score=%.2f, %s)",
                        self.camera_id, blob.blob_id, score, breakdown,
                    )
                    blob.confirmed_emitted = True

            # Age out unseen blobs
            self._age_blobs()

        return observations

    # ── Internals ──────────────────────────────────────────────────

    def _score(
        self,
        blob: _MotionBlob,
        w: int,
        h: int,
        diagonal: float,
    ) -> tuple[float, dict]:
        """Compute 0..1 human-likeness score from blob features."""
        x, y, bw, bh = blob.bbox

        # Aspect ratio: prefer values near 1.5–2.5 (standing human)
        aspect = bh / max(1, bw)
        if aspect < self.ASPECT_MIN or aspect > self.ASPECT_MAX:
            aspect_score = 0.0
        else:
            # Triangular peak at 1.8 (typical standing human)
            aspect_score = max(0.0, 1.0 - abs(aspect - 1.8) / 1.5)

        # Height relative to frame
        height_frac = bh / float(h)
        if height_frac < self.MIN_HEIGHT_FRAC:
            height_score = 0.0
        else:
            height_score = min(1.0, height_frac / 0.4)   # full credit at 40%

        # Lifetime
        life_score = min(1.0, blob.lifetime_frames / 10.0)

        # Displacement: must move enough to be alive but not so much to be a bird
        displacement = blob.centroid_path_len / max(1.0, diagonal)
        if displacement < self.DISP_MIN_FRAC:
            disp_score = 0.0   # static branch / no motion
        elif displacement > self.DISP_MAX_FRAC:
            disp_score = 0.2   # erratic / non-human
        else:
            # Sweet spot: displacement / lifetime ratio not too high
            per_frame = displacement / max(1, blob.lifetime_frames)
            disp_score = max(0.0, 1.0 - abs(per_frame - 0.015) / 0.05)

        # Aspect stability — low variance is human-like
        if len(blob.aspect_history) >= 4:
            arr = np.fromiter(blob.aspect_history, dtype=float)
            stability = 1.0 / (1.0 + float(arr.std()) * 2.0)
        else:
            stability = 0.5

        # Weighted combination (tuned by hand for perimeter scenarios)
        score = (
            0.30 * aspect_score
            + 0.20 * height_score
            + 0.15 * life_score
            + 0.20 * disp_score
            + 0.15 * stability
        )
        breakdown = {
            "aspect": round(aspect_score, 2),
            "height": round(height_score, 2),
            "life": round(life_score, 2),
            "disp": round(disp_score, 2),
            "stab": round(stability, 2),
            "raw_aspect": round(aspect, 2),
            "raw_displacement": round(displacement, 3),
        }
        return score, breakdown

    def _age_blobs(self) -> None:
        """Drop blobs that haven't been seen in BLOB_MISS_DROP frames."""
        with self._lock:
            for blob in self._blobs.values():
                blob.miss_count += 1
            stale = [bid for bid, b in self._blobs.items()
                     if b.miss_count > self.BLOB_MISS_DROP]
            for bid in stale:
                del self._blobs[bid]


# ── Helpers ─────────────────────────────────────────────────────

def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """IoU of two (x, y, w, h) bboxes."""
    ax1, ay1, aw, ah = a
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx1, by1, bw, bh = b
    bx2, by2 = bx1 + bw, by1 + bh
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0
