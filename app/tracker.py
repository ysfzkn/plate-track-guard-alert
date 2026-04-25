"""Track-based multi-frame consensus for license plate detection.

Replaces the earlier single-frame commit logic. Detections are accumulated
into Tracks; each Track commits at most ONE passage after:
  - consecutive misses exceed idle_frames (vehicle left view), OR
  - track duration exceeds max_duration_sec (force close)

Consensus selects the best plate reading from all accumulated observations.
Direction is classified from bbox area change (primary) with Y-position
fallback for ambiguous cases.

See: C:\\Users\\asus\\.claude\\plans\\replicated-watching-horizon.md
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

from app.database import is_valid_turkish_plate, levenshtein_distance
from app.models import DetectionResult

logger = logging.getLogger("gateguard.app")


@dataclass
class Reading:
    """A single OCR observation within a Track."""
    plate_text: str                 # raw OCR text (preserved for screenshot filename)
    normalized: str                 # normalize_plate() result
    confidence: float
    timestamp: datetime
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    frame: Optional[np.ndarray] = field(default=None, repr=False)
    valid: bool = False             # cached is_valid_turkish_plate(normalized)


@dataclass
class Track:
    """A candidate vehicle passage, accumulating readings across frames."""
    id: int
    created_at: datetime
    last_seen_at: datetime
    readings: list[Reading] = field(default_factory=list)
    missed_count: int = 0

    @property
    def first_bbox(self) -> tuple[int, int, int, int]:
        return self.readings[0].bbox

    @property
    def last_bbox(self) -> tuple[int, int, int, int]:
        return self.readings[-1].bbox

    @property
    def last_reading(self) -> Reading:
        return self.readings[-1]


class PlateTracker:
    """Stateful multi-frame tracker for plate detections.

    Call update(detections) per processed frame, then reap(now) to get
    tracks ready for commit.
    """

    def __init__(
        self,
        iou_threshold: float = 0.25,
        fuzzy_tolerance: int = 2,
        idle_frames: int = 2,
        max_duration_sec: float = 15.0,
        min_frames_for_commit: int = 2,
        direction_area_ratio: float = 1.2,
        entry_size_change: str = "approach",  # "approach" | "recede"
        entry_y_direction: str = "down",      # "down" | "up"
    ):
        self.iou_threshold = iou_threshold
        self.fuzzy_tolerance = fuzzy_tolerance
        self.idle_frames = idle_frames
        self.max_duration_sec = max_duration_sec
        self.min_frames_for_commit = min_frames_for_commit
        self.direction_area_ratio = direction_area_ratio
        self.entry_size_change = entry_size_change
        self.entry_y_direction = entry_y_direction

        self._tracks: list[Track] = []
        self._next_id: int = 1

    # ------------------------------------------------------------------
    # Main lifecycle
    # ------------------------------------------------------------------

    def update(self, detections: list[DetectionResult]) -> None:
        """Associate detections with existing tracks or create new ones."""
        now = datetime.now()

        # Track which existing tracks got a detection this frame
        matched_track_ids: set[int] = set()

        # Greedy IoU-first association
        for det in detections:
            if det.bbox is None:
                # Without a bbox we can't use IoU; fall back to fuzzy text match only
                track = self._find_by_fuzzy_text(det)
            else:
                track = self._find_matching_track(det)

            if track is not None:
                matched_track_ids.add(track.id)
                self._append_reading(track, det, now)
            else:
                new_track = self._create_track(det, now)
                matched_track_ids.add(new_track.id)

        # Increment missed_count on any track not touched this frame
        for t in self._tracks:
            if t.id not in matched_track_ids:
                t.missed_count += 1

    def reap(self, now: datetime) -> list[Track]:
        """Return tracks ready for commit (idle or max duration exceeded).

        Removes returned tracks from internal list.
        """
        finalized: list[Track] = []
        remaining: list[Track] = []
        for t in self._tracks:
            age = (now - t.created_at).total_seconds()
            if t.missed_count >= self.idle_frames or age >= self.max_duration_sec:
                finalized.append(t)
            else:
                remaining.append(t)
        self._tracks = remaining
        return finalized

    # ------------------------------------------------------------------
    # Association helpers
    # ------------------------------------------------------------------

    def _find_matching_track(self, det: DetectionResult) -> Optional[Track]:
        """Find the best active track for this detection (IoU primary, fuzzy fallback)."""
        best: Optional[Track] = None
        best_iou = 0.0
        for t in self._tracks:
            iou = self._iou(det.bbox, t.last_bbox)
            if iou >= self.iou_threshold and iou > best_iou:
                best = t
                best_iou = iou
        if best is not None:
            return best

        # Fuzzy text fallback: same-plate despite low IoU (e.g., bbox jumped due to motion)
        return self._find_by_fuzzy_text(det)

    def _find_by_fuzzy_text(self, det: DetectionResult) -> Optional[Track]:
        """Find a track whose last reading is fuzzy-close in plate text AND spatially near."""
        if not det.normalized_plate:
            return None
        for t in self._tracks:
            last = t.last_reading
            dist = levenshtein_distance(det.normalized_plate, last.normalized)
            if dist > self.fuzzy_tolerance:
                continue
            if det.bbox is None or last.bbox is None:
                return t
            if self._bbox_center_dist(det.bbox, last.bbox) < 150:
                return t
        return None

    def _append_reading(self, track: Track, det: DetectionResult, now: datetime) -> None:
        reading = Reading(
            plate_text=det.plate_text,
            normalized=det.normalized_plate,
            confidence=det.confidence,
            timestamp=det.timestamp or now,
            bbox=det.bbox if det.bbox is not None else (0, 0, 0, 0),
            frame=det.frame,
            valid=is_valid_turkish_plate(det.normalized_plate),
        )
        track.readings.append(reading)
        track.last_seen_at = now
        track.missed_count = 0

    def _create_track(self, det: DetectionResult, now: datetime) -> Track:
        track = Track(
            id=self._next_id,
            created_at=now,
            last_seen_at=now,
            readings=[],
            missed_count=0,
        )
        self._next_id += 1
        self._append_reading(track, det, now)
        self._tracks.append(track)
        return track

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iou(a: Optional[tuple], b: Optional[tuple]) -> float:
        """IoU between two (x, y, w, h) boxes. Returns 0 on any issue."""
        if a is None or b is None:
            return 0.0
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
            return 0.0
        ix1, iy1 = max(ax, bx), max(ay, by)
        ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter == 0:
            return 0.0
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _bbox_center_dist(a: tuple, b: tuple) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        cx_a, cy_a = ax + aw / 2, ay + ah / 2
        cx_b, cy_b = bx + bw / 2, by + bh / 2
        return ((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2) ** 0.5

    # ------------------------------------------------------------------
    # Consensus (best reading selection)
    # ------------------------------------------------------------------

    def pick_best_reading(self, track: Track) -> Optional[Reading]:
        """Choose the best reading from a Track for commit.

        Rules:
          - Require >= min_frames_for_commit VALID readings, else return None.
          - Group valid readings by normalized plate; majority vote wins.
          - Within the winning group, return the highest-confidence reading
            (so screenshot uses the sharpest frame).
        """
        valid = [r for r in track.readings if r.valid]
        if len(valid) < self.min_frames_for_commit:
            return None

        groups: dict[str, list[Reading]] = defaultdict(list)
        for r in valid:
            groups[r.normalized].append(r)

        winning_group = max(
            groups.values(),
            key=lambda g: (len(g), sum(r.confidence for r in g)),
        )
        return max(winning_group, key=lambda r: r.confidence)

    # ------------------------------------------------------------------
    # Direction classification
    # ------------------------------------------------------------------

    def classify_direction(self, track: Track) -> str:
        """Return 'entry' | 'exit' | 'unknown' based on bbox movement."""
        if len(track.readings) < 2:
            return "unknown"

        first_bbox = track.first_bbox
        last_bbox = track.last_bbox

        # --- Primary signal: bbox area change ---
        area_first = max(1, first_bbox[2] * first_bbox[3])
        area_last = max(1, last_bbox[2] * last_bbox[3])
        ratio = area_last / area_first

        signal: Optional[str] = None
        if ratio >= self.direction_area_ratio:
            signal = "approach"
        elif ratio <= 1.0 / self.direction_area_ratio:
            signal = "recede"
        else:
            # --- Secondary: Y-center delta ---
            y_first = first_bbox[1] + first_bbox[3] / 2
            y_last = last_bbox[1] + last_bbox[3] / 2
            dy = y_last - y_first
            if abs(dy) < 15:
                return "unknown"
            signal = "down" if dy > 0 else "up"

        # --- Map signal to entry/exit via config ---
        if signal in ("approach", "recede"):
            return "entry" if signal == self.entry_size_change else "exit"
        else:  # "down" or "up"
            return "entry" if signal == self.entry_y_direction else "exit"

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def active_track_count(self) -> int:
        return len(self._tracks)
