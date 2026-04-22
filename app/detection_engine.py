"""Detection engine — orchestrates camera → ALPR → DB lookup → direction → alarm."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from app.database import Database, normalize_plate
from app.models import PassageRecord, PlateTrack
from app.screenshot import save_screenshot

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
        camera: CameraStream | MockCamera,
        detector: BasePlateDetector,
        db: Database,
        alarm: AlarmManager,
        ws_manager: ConnectionManager,
        process_fps: int = 2,
        fuzzy_tolerance: int = 1,
        screenshot_dir: str = "static/screenshots",
        entry_direction: str = "down",
    ):
        self.camera = camera
        self.detector = detector
        self.db = db
        self.alarm = alarm
        self.ws_manager = ws_manager
        self.process_fps = process_fps
        self.fuzzy_tolerance = fuzzy_tolerance
        self.screenshot_dir = screenshot_dir
        self.entry_direction = entry_direction  # "down" or "up"
        self._running = False
        self._task: asyncio.Task | None = None

        # Plate tracking: normalized_plate → PlateTrack
        self._plate_tracks: dict[str, PlateTrack] = {}
        self._dedup_window_sec = 60
        # Minimum Y-movement (pixels) to classify direction
        self._min_direction_delta = 15

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Detection engine started (fps=%d, entry_dir=%s)", self.process_fps, self.entry_direction)

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
        frame = self.camera.get_frame()
        if frame is None:
            return

        loop = asyncio.get_event_loop()
        detections = await loop.run_in_executor(None, self.detector.detect, frame)

        for det in detections:
            normalized = det.normalized_plate
            bbox_y = self._get_bbox_center_y(det.bbox)

            # Update plate tracking (for direction detection)
            track = self._update_track(normalized, bbox_y, det)

            # Dedup: only process once per dedup window, but keep tracking position
            if track.frame_count > 1 and not self._is_first_process(track):
                continue

            # Determine direction from accumulated tracking data
            direction = self._classify_direction(track)

            # DB lookup
            vehicle = self.db.find_vehicle(normalized, self.fuzzy_tolerance)
            is_authorized = vehicle is not None
            owner_name = vehicle.owner_name if vehicle else ""

            # Screenshot (only for unauthorized)
            screenshot_url = ""
            if not is_authorized:
                screenshot_url = save_screenshot(
                    frame, det.plate_text, det.bbox, self.screenshot_dir
                )

            record = PassageRecord(
                plate=det.plate_text,
                plate_normalized=normalized,
                detected_at=det.timestamp,
                is_authorized=is_authorized,
                owner_name=owner_name,
                confidence=det.confidence,
                screenshot_path=screenshot_url,
                direction=direction,
            )
            record.id = self.db.add_passage(record)

            # Log
            status = "AUTHORIZED" if is_authorized else "UNAUTHORIZED"
            dir_label = {"entry": "ENTRY", "exit": "EXIT"}.get(direction, "???")
            passages_logger.info(
                "Plate: %s | %s | %s | Conf: %.1f%% | Owner: %s",
                normalized, dir_label, status, det.confidence * 100, owner_name,
            )

            # Build WebSocket data payload
            ws_data = {
                "id": record.id,
                "plate": det.plate_text,
                "detected_at": det.timestamp.isoformat(),
                "is_authorized": is_authorized,
                "owner_name": owner_name,
                "confidence": round(det.confidence * 100, 1),
                "screenshot_url": screenshot_url,
                "direction": direction,
            }

            if is_authorized:
                await self.ws_manager.broadcast({"type": "passage", "data": ws_data})
            else:
                if self.alarm.should_trigger(normalized):
                    await self.alarm.trigger_alarm(det.plate_text, normalized)
                await self.ws_manager.broadcast({"type": "alarm_on", "data": ws_data})

    # --- Direction detection helpers ---

    @staticmethod
    def _get_bbox_center_y(bbox) -> float:
        """Get vertical center of a bounding box."""
        if bbox is None:
            return -1.0
        _, y, _, h = bbox
        return float(y + h / 2)

    def _update_track(self, normalized: str, bbox_y: float, det) -> PlateTrack:
        """Update or create a plate track entry."""
        now = datetime.now()

        if normalized in self._plate_tracks:
            track = self._plate_tracks[normalized]
            elapsed = (now - track.first_seen).total_seconds()
            if elapsed < self._dedup_window_sec:
                # Same plate still in dedup window — update last position
                if bbox_y >= 0:
                    track.last_y = bbox_y
                track.frame_count += 1
                return track
            # Expired — treat as new

        # New plate track
        track = PlateTrack(
            first_seen=now,
            first_y=bbox_y,
            last_y=bbox_y,
            frame_count=1,
            plate_text=det.plate_text,
            normalized=normalized,
        )
        self._plate_tracks[normalized] = track

        # Cleanup old tracks
        self._plate_tracks = {
            k: v for k, v in self._plate_tracks.items()
            if (now - v.first_seen).total_seconds() < self._dedup_window_sec * 2
        }

        return track

    def _is_first_process(self, track: PlateTrack) -> bool:
        """Return True only on the second frame (when we have direction data)."""
        return track.frame_count == 2

    def _classify_direction(self, track: PlateTrack) -> str:
        """Classify entry/exit based on Y-movement across frames."""
        if track.first_y < 0 or track.last_y < 0:
            return "unknown"

        delta = track.last_y - track.first_y

        if abs(delta) < self._min_direction_delta:
            # Not enough movement — try to guess from single-frame position
            # If plate is in lower half of typical 480p frame, likely entry
            return "unknown"

        # delta > 0 means plate moved DOWN in frame
        moving_down = delta > 0

        if self.entry_direction == "down":
            return "entry" if moving_down else "exit"
        else:  # entry_direction == "up"
            return "entry" if not moving_down else "exit"
