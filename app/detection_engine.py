"""Detection engine — orchestrates camera -> ALPR -> tracker -> DB lookup -> alarm.

Uses the track-based multi-frame consensus pipeline from app/tracker.py.
A passage is committed ONCE per vehicle track, after the vehicle leaves
the detection window or the track's max duration is reached.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from app.database import Database, levenshtein_distance
from app.inference_pool import get_inference_pool, get_io_pool, get_plate_pool
from app.models import PassageRecord
from app.motion_gate import MotionGate
from app.screenshot import save_screenshot
from app.tracker import PlateTracker, Track


def _dir_winner(votes: dict) -> str:
    """Most-voted DEFINITE direction; 'unknown' only if no definite vote."""
    definite = {d: w for d, w in votes.items() if d not in ("unknown", "")}
    return max(definite, key=definite.get) if definite else "unknown"

if TYPE_CHECKING:
    from app.alarm_manager import AlarmManager
    from app.camera import CameraStream, MockCamera
    from app.plate_detector import BasePlateDetector
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger("gateguard.app")
passages_logger = logging.getLogger("gateguard.passages")


class DetectionEngine:
    def __init__(
        self,
        camera: "CameraStream | MockCamera",
        detector: "BasePlateDetector",
        db: Database,
        alarm: "AlarmManager",
        ws_manager: "ConnectionManager",
        process_fps: int = 2,
        fuzzy_tolerance: int = 1,
        screenshot_dir: str = "static/screenshots",
        # Tracker config
        min_frames_for_commit: int = 2,
        track_idle_frames: int = 2,
        track_max_duration_sec: float = 15.0,
        track_iou_threshold: float = 0.25,
        track_fuzzy_tolerance: int = 2,
        direction_area_ratio: float = 1.2,
        entry_size_change: str = "approach",
        entry_y_direction: str = "down",
        # Motion gate (skip ALPR on static frames)
        motion_gate_enabled: bool = True,
        motion_skip_threshold: float = 0.005,
        mandatory_detect_every_n: int = 10,
        passage_dedup_sec: float = 30.0,
        single_commit_conf: float = 0.9,
    ):
        self.camera = camera
        self.detector = detector
        self.db = db
        self.alarm = alarm
        self.ws_manager = ws_manager
        self.process_fps = process_fps
        self.fuzzy_tolerance = fuzzy_tolerance  # for DB lookup (different from tracker fuzzy)
        self.screenshot_dir = screenshot_dir

        # Skip the (expensive) ALPR call on essentially-static frames. The gate
        # itself forces a detection every Nth static frame so a slowly
        # approaching vehicle or lighting drift is never missed.
        self._motion_gate = MotionGate(
            skip_threshold=motion_skip_threshold,
            mandatory_every_n=mandatory_detect_every_n,
        ) if motion_gate_enabled else None

        # Collapse fragmented tracks of the same vehicle into one passage
        # (ramp motion + OCR variance can finalize several short tracks).
        # plate -> {"id", "ts", "votes": {dir: frames}, "direction"}
        self._dedup_window = passage_dedup_sec
        self._dedup_tol = track_fuzzy_tolerance
        self._recent_commits: dict = {}

        self.tracker = PlateTracker(
            iou_threshold=track_iou_threshold,
            fuzzy_tolerance=track_fuzzy_tolerance,
            idle_frames=track_idle_frames,
            max_duration_sec=track_max_duration_sec,
            min_frames_for_commit=min_frames_for_commit,
            direction_area_ratio=direction_area_ratio,
            entry_size_change=entry_size_change,
            entry_y_direction=entry_y_direction,
            single_commit_conf=single_commit_conf,
        )

        self._running = False
        self._task: asyncio.Task | None = None

        # ── Health metrics ──
        self._last_loop_at: float = 0.0          # any tick (success or crash)
        self._last_success_at: float = 0.0       # only after _process_frame succeeded
        self._loop_count: int = 0
        self._error_count: int = 0
        self._consecutive_errors: int = 0
        # If consecutive errors exceed this, we mark the engine "degraded"
        # so /api/health can surface it. Manual restart still required.
        self.MAX_CONSECUTIVE_ERRORS = 5
        # If no success for this long, also "degraded"
        self.STALL_TIMEOUT_SEC = 30.0

    def get_health(self) -> dict:
        """Snapshot of detection engine health — surfaced via /api/health."""
        now = time.time()
        success_ago = (now - self._last_success_at) if self._last_success_at else None
        loop_ago = (now - self._last_loop_at) if self._last_loop_at else None
        is_stalled = (
            success_ago is not None
            and success_ago > self.STALL_TIMEOUT_SEC
            and self._loop_count > 0
        )
        return {
            "running": self._running,
            "loop_count": self._loop_count,
            "error_count": self._error_count,
            "consecutive_errors": self._consecutive_errors,
            "last_success_ago_sec": round(success_ago, 1) if success_ago is not None else None,
            "last_loop_ago_sec": round(loop_ago, 1) if loop_ago is not None else None,
            "stalled": is_stalled,
            "degraded": is_stalled or self._consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS,
        }

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Detection engine started (fps=%d, entry_size=%s, entry_y=%s, min_frames=%d)",
            self.process_fps,
            self.tracker.entry_size_change,
            self.tracker.entry_y_direction,
            self.tracker.min_frames_for_commit,
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Detection engine stopped")

    async def _loop(self):
        interval = 1.0 / self.process_fps
        while self._running:
            self._last_loop_at = time.time()
            self._loop_count += 1
            try:
                await self._process_frame()
                self._last_success_at = time.time()
                # Reset error streak on any success
                if self._consecutive_errors > 0:
                    logger.info(
                        "Detection loop recovered after %d errors",
                        self._consecutive_errors,
                    )
                    self._consecutive_errors = 0
            except Exception:
                self._error_count += 1
                self._consecutive_errors += 1
                logger.exception(
                    "Detection loop error (%d consecutive, %d total)",
                    self._consecutive_errors, self._error_count,
                )
                if self._consecutive_errors == self.MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "Detection loop has %d consecutive errors — health flagged DEGRADED",
                        self._consecutive_errors,
                    )
            await asyncio.sleep(interval)

    async def _process_frame(self):
        """One tick: detect -> update tracker -> reap finalized tracks -> commit."""
        now = datetime.now()
        frame = self.camera.get_frame()

        detections = []
        if frame is not None and not (
            self._motion_gate is not None and self._motion_gate.should_skip(frame)
        ):
            loop = asyncio.get_event_loop()
            try:
                # Dedicated plate pool → the barrier read never queues behind the
                # intrusion cameras (low, consistent latency at the gate).
                detections = await loop.run_in_executor(
                    get_plate_pool(), self.detector.detect, frame,
                )
            except Exception:
                logger.exception("Detector crashed")
                detections = []

        # Attach current frame to each detection for screenshot-on-commit
        for det in detections:
            if det.frame is None:
                det.frame = frame

        # Phase 1: Update tracker with this frame's detections
        self.tracker.update(detections)

        # Phase 2: Reap tracks that should be finalized now
        finalized_tracks = self.tracker.reap(now)

        # Phase 3: Commit each finalized track (if it qualifies)
        for track in finalized_tracks:
            await self._commit_track(track)

    async def _commit_track(self, track: Track):
        """Turn a finalized track into a passage record + alarm + broadcast."""
        best = self.tracker.pick_best_reading(track)
        if best is None:
            # Not enough valid readings — silently drop
            valid_count = sum(1 for r in track.readings if r.valid)
            logger.info(
                "Track %d dropped: %d valid readings of %d total (need %d)",
                track.id, valid_count, len(track.readings),
                self.tracker.min_frames_for_commit,
            )
            return

        direction = self.tracker.classify_direction(track)
        weight = max(1, len(track.readings))

        # Dedup: one vehicle that fragmented into several short tracks (ramp
        # motion + OCR variance) is logged ONCE. A fuzzy-matching plate within
        # the window is merged into the existing record; its direction is a
        # frame-weighted VOTE across fragments (a short fragment can misclassify
        # entry vs exit), and the kept passage's direction is corrected in the DB
        # when the winning vote changes.
        now_ts = best.timestamp
        for p in list(self._recent_commits):
            if (now_ts - self._recent_commits[p]["ts"]).total_seconds() > self._dedup_window:
                del self._recent_commits[p]
        match_key = next(
            (p for p in self._recent_commits
             if levenshtein_distance(best.normalized, p) <= self._dedup_tol),
            None,
        )
        if match_key is not None:
            rec = self._recent_commits[match_key]
            rec["ts"] = now_ts
            rec["votes"][direction] = rec["votes"].get(direction, 0) + weight
            win = _dir_winner(rec["votes"])
            if win != rec["direction"]:
                rec["direction"] = win
                try:
                    self.db.update_passage(rec["id"], direction=win)
                except Exception:
                    logger.exception("Failed to update merged passage direction")
            passages_logger.info(
                "Duplicate passage merged: %s (1 vehicle = 1 passage, dir=%s)",
                best.normalized, rec["direction"],
            )
            return

        loop = asyncio.get_event_loop()

        # DB lookup — the fuzzy fallback can scan the vehicle table, so run it
        # off the event loop on the IO pool to avoid stalling websockets/health.
        vehicle = await loop.run_in_executor(
            get_io_pool(), self.db.find_vehicle, best.normalized, self.fuzzy_tolerance,
        )
        is_authorized = vehicle is not None
        owner_name = vehicle.owner_name if vehicle else ""

        # Screenshot for EVERY passage (authorized → neutral green capture,
        # unauthorized → red + "KACAK" watermark) so the live feed shows a photo
        # for each car. Uses the single retained sharpest frame + its bbox; heavy
        # PIL rendering runs on the IO pool to keep the detection loop hot.
        screenshot_url = ""
        if track.best_frame is not None:
            screenshot_url = await loop.run_in_executor(
                get_io_pool(), save_screenshot,
                track.best_frame, best.plate_text, track.best_bbox,
                self.screenshot_dir, is_authorized,
            )

        record = PassageRecord(
            plate=best.plate_text,
            plate_normalized=best.normalized,
            detected_at=best.timestamp,
            is_authorized=is_authorized,
            owner_name=owner_name,
            confidence=best.confidence,
            screenshot_path=screenshot_url,
            direction=direction,
        )
        record.id = self.db.add_passage(record)

        # Register as the canonical passage for this plate (later fragments of
        # the same vehicle merge into it instead of adding new records).
        self._recent_commits[best.normalized] = {
            "id": record.id, "ts": now_ts,
            "votes": {direction: weight}, "direction": direction,
        }

        status = "AUTHORIZED" if is_authorized else "UNAUTHORIZED"
        dir_label = {"entry": "ENTRY", "exit": "EXIT"}.get(direction, "???")
        valid_count = sum(1 for r in track.readings if r.valid)
        passages_logger.info(
            "Plate: %s | %s | %s | Conf: %.1f%% | Owner: %s | Frames: %d/%d valid",
            best.normalized, dir_label, status,
            best.confidence * 100, owner_name,
            valid_count, len(track.readings),
        )

        ws_data = {
            "id": record.id,
            "plate": best.plate_text,
            "detected_at": best.timestamp.isoformat(),
            "is_authorized": is_authorized,
            "owner_name": owner_name,
            "confidence": round(best.confidence * 100, 1),
            "screenshot_url": screenshot_url,
            "direction": direction,
        }

        if is_authorized:
            await self.ws_manager.broadcast({"type": "passage", "data": ws_data})
        else:
            if self.alarm.should_trigger(best.normalized):
                await self.alarm.trigger_alarm(best.plate_text, best.normalized)
            await self.ws_manager.broadcast({"type": "alarm_on", "data": ws_data})
