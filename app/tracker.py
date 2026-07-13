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
    """A single OCR observation within a Track.

    NOTE: Readings deliberately do NOT store the source frame. On a low-RAM
    box a full 1080p frame is ~6 MB; keeping one per reading across a
    multi-frame track (and several concurrent tracks) was the single biggest
    plate-side memory consumer. Instead the Track keeps only ONE frame — the
    sharpest (highest-confidence valid) observation — in `best_frame`, which
    is all the screenshot needs.
    """
    plate_text: str                 # raw OCR text (preserved for screenshot filename)
    normalized: str                 # normalize_plate() result
    confidence: float
    timestamp: datetime
    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    valid: bool = False             # cached is_valid_turkish_plate(normalized)
    # Per-character OCR confidence aligned 1:1 with `normalized` (or None).
    # Used by the per-character consensus vote in pick_best_reading.
    char_confidences: Optional[list[float]] = None


@dataclass
class Track:
    """A candidate vehicle passage, accumulating readings across frames."""
    id: int
    created_at: datetime
    last_seen_at: datetime
    readings: list[Reading] = field(default_factory=list)
    missed_count: int = 0
    # Single retained frame: the highest-confidence VALID reading seen so far,
    # used only for the evidence screenshot. Kept paired with its own bbox so
    # the overlay box matches the frame. See Reading docstring for rationale.
    best_frame: Optional[np.ndarray] = field(default=None, repr=False)
    best_bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    best_confidence: float = -1.0

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
        center_dist_factor: float = 4.0,      # center-distance assoc window = factor * plate size
        single_commit_conf: float = 0.9,      # commit on ONE reading if conf >= this (fast pass)
    ):
        self.iou_threshold = iou_threshold
        self.fuzzy_tolerance = fuzzy_tolerance
        self.idle_frames = idle_frames
        self.max_duration_sec = max_duration_sec
        self.min_frames_for_commit = min_frames_for_commit
        self.direction_area_ratio = direction_area_ratio
        self.entry_size_change = entry_size_change
        self.entry_y_direction = entry_y_direction
        self.center_dist_factor = center_dist_factor
        self.single_commit_conf = single_commit_conf

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
        """Find the best active track for this detection.

        On a ramp the plate moves a lot between processed frames, so IoU alone
        fragments one vehicle into many tracks. We associate with three signals,
        strongest first: (1) IoU, (2) center-distance within a plate-size-scaled
        window, (3) fuzzy plate text. Same plate text is the strongest evidence
        that it's the same vehicle, so text-match is allowed across a generous
        spatial gap.
        """
        best: Optional[Track] = None
        best_iou = 0.0
        for t in self._tracks:
            iou = self._iou(det.bbox, t.last_bbox)
            if iou >= self.iou_threshold and iou > best_iou:
                best = t
                best_iou = iou
        if best is not None:
            return best

        # Center-distance fallback (plate jumped between frames but it's the same car)
        if det.bbox is not None:
            cand: Optional[Track] = None
            cand_d: Optional[float] = None
            for t in self._tracks:
                if t.last_bbox is None or t.last_bbox[2] <= 0:
                    continue
                d = self._bbox_center_dist(det.bbox, t.last_bbox)
                win = self.center_dist_factor * max(
                    det.bbox[2], det.bbox[3], t.last_bbox[2], t.last_bbox[3], 1)
                if d <= win and (cand_d is None or d < cand_d):
                    cand, cand_d = t, d
            if cand is not None:
                return cand

        # Fuzzy text fallback: same plate despite low IoU / large jump
        return self._find_by_fuzzy_text(det)

    def _find_by_fuzzy_text(self, det: DetectionResult) -> Optional[Track]:
        """Find a track whose last reading is fuzzy-close in plate text.

        Same (fuzzy) plate text => almost certainly the same vehicle, so the
        spatial gate is generous (scaled by plate size) rather than a tight
        fixed pixel distance — a ramp plate can travel far between frames.
        """
        if not det.normalized_plate:
            return None
        for t in self._tracks:
            last = t.last_reading
            dist = levenshtein_distance(det.normalized_plate, last.normalized)
            if dist > self.fuzzy_tolerance:
                continue
            if det.bbox is None or last.bbox is None:
                return t
            # generous, plate-size-scaled spatial window for a strong text match
            win = max(300.0, self.center_dist_factor * 2.0 *
                      max(det.bbox[2], det.bbox[3], 1))
            if self._bbox_center_dist(det.bbox, last.bbox) < win:
                return t
        return None

    def _append_reading(self, track: Track, det: DetectionResult, now: datetime) -> None:
        bbox = det.bbox if det.bbox is not None else (0, 0, 0, 0)
        valid = is_valid_turkish_plate(det.normalized_plate)
        reading = Reading(
            plate_text=det.plate_text,
            normalized=det.normalized_plate,
            confidence=det.confidence,
            timestamp=det.timestamp or now,
            bbox=bbox,
            valid=valid,
            char_confidences=getattr(det, "char_confidences", None),
        )
        track.readings.append(reading)
        track.last_seen_at = now
        track.missed_count = 0

        # Retain only the sharpest VALID frame for the evidence screenshot.
        # One explicit copy per "new best" decouples it from the camera's
        # per-tick buffer so the shared frame can be GC'd immediately.
        if valid and det.frame is not None and det.confidence > track.best_confidence:
            track.best_frame = det.frame.copy()
            track.best_bbox = bbox
            track.best_confidence = det.confidence

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

        Rules (in order):
          - PER-CHARACTER CONSENSUS: when several readings agree on the plate
            except for a few flickering characters (the classic D↔K / N↔M
            confusions), vote each character position independently, weighted by
            the OCR's per-character confidence. This recovers the correct plate
            even when no single frame read every character right.
          - WHOLE-PLATE consensus fallback: the majority plate (with
            >= min_frames_for_commit agreeing readings) wins.
          - FAST-PASS fallback: a vehicle that crosses quickly may yield only
            ONE readable frame. A single reading with confidence >=
            single_commit_conf is trustworthy enough to commit alone. This
            recovers fast passes WITHOUT committing low-confidence night
            "guesses" (inconsistent hallucinations ~0.5-0.6, below the bar).
        """
        valid = [r for r in track.readings if r.valid]
        if not valid:
            return None

        # 1) Per-character confidence-weighted consensus over a tight cluster.
        winner = self._consensus_by_character(valid)

        if winner is None:
            # 2) Whole-plate consensus = the WINNING plate (majority group) must
            # itself have >= min_frames_for_commit readings. Requiring only the
            # TOTAL valid count let two DIFFERENT low-quality reads (e.g. night
            # hallucinations '66VH468' + '34FX043', each seen once) commit a
            # garbage plate. Those need 2+ readings AGREEING on the same plate.
            groups: dict[str, list[Reading]] = defaultdict(list)
            for r in valid:
                groups[r.normalized].append(r)
            winning_group = max(
                groups.values(),
                key=lambda g: (len(g), sum(r.confidence for r in g)),
            )
            if len(winning_group) >= self.min_frames_for_commit:
                winner = max(winning_group, key=lambda r: r.confidence)
            else:
                # 3) Fast-pass fallback: a single VERY high-confidence read is
                # trustworthy even without a repeat (fast vehicle, one clear frame).
                strongest = max(valid, key=lambda r: r.confidence)
                if strongest.confidence >= self.single_commit_conf:
                    winner = strongest

        if winner is None:
            return None

        # 4) Dropped-edge-character recovery: if the winner is a strict prefix
        # or suffix of a LONGER valid plate that was read with comparable
        # confidence, prefer the longer one. Targets the detector cropping one
        # edge char (e.g. "34GPF89" when the plate is "34GPF891"), where the
        # cropped short read can out-count the single full read.
        return self._prefer_longer_reading(valid, winner)

    def _prefer_longer_reading(
        self, valid: list[Reading], winner: Reading
    ) -> Reading:
        """Prefer a longer valid plate that `winner` is a prefix/suffix of.

        Guardrails keep this to the genuine dropped-edge-char case and reject an
        OCR hallucination that merely appended a character:
          - the longer plate is exactly ONE character longer,
          - `winner` is a strict prefix (trailing char lost) or suffix (leading
            char lost) of it,
          - the longer plate is a valid Turkish plate, and
          - its best reading is about as confident as the winner (a real full
            read is usually the SHARPEST frame; a hallucinated extra char is not).
        """
        wn = winner.normalized
        best_long: dict[str, Reading] = {}
        for r in valid:
            rn = r.normalized
            if len(rn) != len(wn) + 1:
                continue
            if not (rn.startswith(wn) or rn.endswith(wn)):
                continue
            if not is_valid_turkish_plate(rn):
                continue
            cur = best_long.get(rn)
            if cur is None or r.confidence > cur.confidence:
                best_long[rn] = r
        if not best_long:
            return winner
        cand = max(best_long.values(), key=lambda r: r.confidence)
        if cand.confidence >= winner.confidence - 0.05 and cand.confidence >= 0.50:
            logger.info(
                "Plate consensus: preferring longer '%s' over '%s' "
                "(dropped-edge-char recovery, conf %.2f vs %.2f)",
                cand.normalized, wn, cand.confidence, winner.confidence,
            )
            return cand
        return winner

    def _consensus_by_character(
        self, valid: list[Reading]
    ) -> Optional[Reading]:
        """Vote the plate one character position at a time across readings.

        Only readings that nearly agree with the strongest one (same length,
        within fuzzy_tolerance edits) join the vote — so we resolve a couple of
        flickering characters without fabricating a plate out of two unrelated
        hallucinations. Each position picks the character with the greatest
        summed weight, where weight is the OCR's per-character confidence when
        available, else the whole-plate confidence.

        Returns a synthesized Reading carrying the voted plate (anchored to the
        strongest reading for bbox/timestamp/screenshot), or None to defer to
        the whole-plate / fast-pass paths.
        """
        anchor = max(valid, key=lambda r: r.confidence)
        L = len(anchor.normalized)
        cluster = [
            r for r in valid
            if len(r.normalized) == L
            and levenshtein_distance(r.normalized, anchor.normalized)
            <= self.fuzzy_tolerance
        ]
        if len(cluster) < self.min_frames_for_commit:
            return None

        # Per-character voting only earns its keep when the OCR exposes
        # per-character confidence (fast-alpr). Without it, each position's
        # weight degrades to the whole-plate confidence, where two mediocre
        # frames (0.6+0.6) can outvote one sharp frame (0.92) and SYNTHESIZE a
        # wrong plate — worse than the whole-plate / fast-pass paths. Defer to
        # those whenever no reading in the cluster carries per-char data.
        if not any(r.char_confidences for r in cluster):
            return None

        out_chars: list[str] = []
        for i in range(L):
            tally: dict[str, float] = defaultdict(float)
            for r in cluster:
                if r.char_confidences and i < len(r.char_confidences):
                    w = r.char_confidences[i]
                else:
                    w = r.confidence
                tally[r.normalized[i]] += w
            out_chars.append(max(tally.items(), key=lambda kv: kv[1])[0])

        voted_norm = "".join(out_chars)
        if not is_valid_turkish_plate(voted_norm):
            return None

        # Reuse the anchor's raw text/filename only if the vote didn't change
        # the plate; otherwise carry the voted text so the record is consistent.
        # When the plate changed, the anchor's char_confidences no longer align
        # 1:1 with the voted string, so drop them (keep the Reading consistent).
        changed = anchor.normalized != voted_norm
        return Reading(
            plate_text=voted_norm if changed else anchor.plate_text,
            normalized=voted_norm,
            confidence=anchor.confidence,
            timestamp=anchor.timestamp,
            bbox=anchor.bbox,
            valid=True,
            char_confidences=None if changed else anchor.char_confidences,
        )

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


class PlateCommitDedup:
    """Collapse fragmented commits of the SAME vehicle into one passage.

    Even with the improved association, a ramp pass with OCR variance
    (e.g. 34GDZ11 vs 34GDZ114) can still finalize as a few short tracks within
    a few seconds. This keeps a short rolling memory of recently-committed
    plates; a new commit whose plate fuzzy-matches a very recent one is treated
    as the same vehicle. The caller decides whether to drop it outright or to
    UPGRADE the kept record when the new fragment is more confident / has a
    definite (non-"unknown") direction.
    """

    def __init__(self, window_sec: float = 30.0, fuzzy_tolerance: int = 2):
        self.window_sec = window_sec
        self.fuzzy_tolerance = fuzzy_tolerance
        # plate_norm -> {"time", "confidence", "direction"}
        self._recent: dict[str, dict] = {}

    def _prune(self, now: datetime) -> None:
        stale = [p for p, v in self._recent.items()
                 if (now - v["time"]).total_seconds() > self.window_sec]
        for p in stale:
            del self._recent[p]

    def check(self, normalized: str, now: datetime, confidence: float,
              direction: str) -> tuple[bool, bool]:
        """Returns (is_duplicate, should_upgrade_existing).

        is_duplicate: a fuzzy-matching plate was committed within the window.
        should_upgrade_existing: this fragment is a better representative
        (definite direction where the kept one was 'unknown', or higher
        confidence) — the caller may update the kept record instead of adding.
        """
        self._prune(now)
        match_key = None
        for p in self._recent:
            if levenshtein_distance(normalized, p) <= self.fuzzy_tolerance:
                match_key = p
                break
        if match_key is None:
            self._recent[normalized] = {"time": now, "confidence": confidence,
                                        "direction": direction}
            return (False, False)
        kept = self._recent[match_key]
        kept["time"] = now   # extend the window while the car is still around
        upgrade = (
            (kept["direction"] in ("unknown", "") and direction not in ("unknown", ""))
            or (confidence > kept["confidence"] + 0.05)
        )
        if upgrade:
            kept["confidence"] = max(kept["confidence"], confidence)
            if direction not in ("unknown", ""):
                kept["direction"] = direction
        return (True, upgrade)
