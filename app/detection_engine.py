"""Detection engine — orchestrates camera → ALPR → DB lookup → alarm."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from app.database import Database, normalize_plate
from app.models import PassageRecord
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
    ):
        self.camera = camera
        self.detector = detector
        self.db = db
        self.alarm = alarm
        self.ws_manager = ws_manager
        self.process_fps = process_fps
        self.fuzzy_tolerance = fuzzy_tolerance
        self.screenshot_dir = screenshot_dir
        self._running = False
        self._task: asyncio.Task | None = None

        # Deduplication: plate → last seen time
        self._seen_plates: dict[str, datetime] = {}
        self._dedup_window_sec = 60

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Detection engine started (process_fps=%d)", self.process_fps)

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

        # Run detection in thread executor to avoid blocking event loop
        loop = asyncio.get_event_loop()
        detections = await loop.run_in_executor(None, self.detector.detect, frame)

        for det in detections:
            normalized = det.normalized_plate

            # Dedup check
            if not self._should_process(normalized):
                continue

            self._seen_plates[normalized] = datetime.now()

            # DB lookup
            vehicle = self.db.find_vehicle(normalized, self.fuzzy_tolerance)
            is_authorized = vehicle is not None
            owner_name = vehicle.owner_name if vehicle else ""

            # Save passage record
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
            )
            record.id = self.db.add_passage(record)

            # Log passage
            status = "AUTHORIZED" if is_authorized else "UNAUTHORIZED"
            passages_logger.info(
                "Plate: %s | Normalized: %s | Conf: %.1f%% | Status: %s | Owner: %s",
                det.plate_text, normalized, det.confidence * 100, status, owner_name,
            )

            if is_authorized:
                # Broadcast normal passage via WebSocket
                await self.ws_manager.broadcast({
                    "type": "passage",
                    "data": {
                        "id": record.id,
                        "plate": det.plate_text,
                        "detected_at": det.timestamp.isoformat(),
                        "is_authorized": True,
                        "owner_name": owner_name,
                        "confidence": round(det.confidence * 100, 1),
                        "screenshot_url": "",
                    },
                })
            else:
                # Trigger alarm
                if self.alarm.should_trigger(normalized):
                    await self.alarm.trigger_alarm(det.plate_text, normalized)

                # Broadcast alarm event via WebSocket
                await self.ws_manager.broadcast({
                    "type": "alarm_on",
                    "data": {
                        "id": record.id,
                        "plate": det.plate_text,
                        "detected_at": det.timestamp.isoformat(),
                        "is_authorized": False,
                        "owner_name": "",
                        "confidence": round(det.confidence * 100, 1),
                        "screenshot_url": screenshot_url,
                    },
                })

    def _should_process(self, normalized: str) -> bool:
        """Check deduplication window."""
        now = datetime.now()
        if normalized in self._seen_plates:
            elapsed = (now - self._seen_plates[normalized]).total_seconds()
            if elapsed < self._dedup_window_sec:
                return False

        # Cleanup old entries
        self._seen_plates = {
            k: v for k, v in self._seen_plates.items()
            if (now - v).total_seconds() < self._dedup_window_sec * 2
        }
        return True
