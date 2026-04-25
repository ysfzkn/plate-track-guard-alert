"""Detection engine — orchestrates camera -> ALPR -> tracker -> DB lookup -> alarm.

Uses the track-based multi-frame consensus pipeline from app/tracker.py.
A passage is committed ONCE per vehicle track, after the vehicle leaves
the detection window or the track's max duration is reached.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from app.database import Database
from app.models import PassageRecord
from app.screenshot import save_screenshot
from app.tracker import PlateTracker, Track

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
    ):
        self.camera = camera
        self.detector = detector
        self.db = db
        self.alarm = alarm
        self.ws_manager = ws_manager
        self.process_fps = process_fps
        self.fuzzy_tolerance = fuzzy_tolerance  # for DB lookup (different from tracker fuzzy)
        self.screenshot_dir = screenshot_dir

        self.tracker = PlateTracker(
            iou_threshold=track_iou_threshold,
            fuzzy_tolerance=track_fuzzy_tolerance,
            idle_frames=track_idle_frames,
            max_duration_sec=track_max_duration_sec,
            min_frames_for_commit=min_frames_for_commit,
            direction_area_ratio=direction_area_ratio,
            entry_size_change=entry_size_change,
            entry_y_direction=entry_y_direction,
        )

        self._running = False
        self._task: asyncio.Task | None = None

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
            try:
                await self._process_frame()
            except Exception:
                logger.exception("Detection loop error")
            await asyncio.sleep(interval)

    async def _process_frame(self):
        """One tick: detect -> update tracker -> reap finalized tracks -> commit."""
        now = datetime.now()
        frame = self.camera.get_frame()

        detections = []
        if frame is not None:
            loop = asyncio.get_event_loop()
            try:
                detections = await loop.run_in_executor(None, self.detector.detect, frame)
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

        # DB lookup
        vehicle = self.db.find_vehicle(best.normalized, self.fuzzy_tolerance)
        is_authorized = vehicle is not None
        owner_name = vehicle.owner_name if vehicle else ""

        # Screenshot (unauthorized only, using the best/sharpest frame)
        screenshot_url = ""
        if not is_authorized and best.frame is not None:
            screenshot_url = save_screenshot(
                best.frame, best.plate_text, best.bbox, self.screenshot_dir
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
