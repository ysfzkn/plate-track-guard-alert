"""IntrusionEngine — per-camera async detection loop for Module 2.

Each camera gets one IntrusionEngine. It:
  1. pulls frames from its CameraStream
  2. feeds them through the shared PersonDetector
  3. records each frame into its VideoClipRecorder (ring buffer)
  4. runs each observation through the shared IntrusionClassifier
  5. on any returned IntrusionEvent:
     - saves burst screenshots (reuses save_screenshot)
     - triggers video clip capture
     - commits the event to the DB (with shadow_mode flag)
     - either triggers the alarm (live mode) or just logs (shadow mode)
     - broadcasts an `intrusion_on` WebSocket event
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from app.intrusion.models import IntrusionEvent
from app.intrusion.motion_detector import MotionDetector, MOTION_TRACK_ID_BASE
from app.intrusion.video_recorder import VideoClipRecorder
from app.screenshot import save_screenshot

if TYPE_CHECKING:
    from app.alarm_manager import AlarmManager
    from app.camera import CameraStream
    from app.database import Database
    from app.intrusion.classifier import IntrusionClassifier
    from app.intrusion.models import Camera
    from app.intrusion.person_detector import PersonDetector
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger("gateguard.app")
intrusion_logger = logging.getLogger("gateguard.intrusions")


class IntrusionEngine:
    """Per-camera detection loop. Owns a VideoClipRecorder but shares
    PersonDetector and IntrusionClassifier across the orchestrator."""

    def __init__(
        self,
        camera: "Camera",
        stream: "CameraStream",
        detector: "PersonDetector",
        classifier: "IntrusionClassifier",
        db: "Database",
        alarm: "AlarmManager",
        ws_manager: "ConnectionManager",
        process_fps: int = 2,
        shadow_mode: bool = True,
        screenshot_dir: str = "static/intrusion_clips",
        clip_enabled: bool = True,
        clip_pre_sec: int = 10,
        clip_post_sec: int = 5,
        clip_dir: str = "static/intrusion_clips",
        burst_screenshots: int = 5,
        motion_fallback_enabled: bool = True,
        alarm_enabled_getter=None,
        zone_manager=None,
    ):
        self.camera = camera
        self.stream = stream
        self.detector = detector
        self.classifier = classifier
        self.db = db
        self.alarm = alarm
        self.ws_manager = ws_manager
        self.process_fps = max(1, process_fps)
        self.shadow_mode = shadow_mode
        self.screenshot_dir = screenshot_dir
        self.clip_enabled = clip_enabled
        self.burst_count = max(1, burst_screenshots)
        # Runtime "user can mute siren" toggle (separate from shadow_mode).
        # Returns bool. When False: events still log + broadcast, but no siren.
        self._alarm_enabled_getter = alarm_enabled_getter or (lambda: True)
        self._zone_manager = zone_manager
        # Motion fallback for perimeter zones — only spun up if any zone on
        # this camera has enable_motion_fallback=1 (checked lazily in loop).
        self._motion_detector: MotionDetector | None = None
        self._motion_fallback_enabled = motion_fallback_enabled
        self._motion_check_interval = 30   # re-check zone settings every N frames
        self._motion_check_counter = 0

        self._video_recorder = VideoClipRecorder(
            camera_id=camera.id,
            fps=self.process_fps,
            pre_sec=clip_pre_sec,
            post_sec=clip_post_sec,
            output_dir=clip_dir,
        ) if clip_enabled else None

        self._running = False
        self._task: asyncio.Task | None = None
        # Per-event burst queue: event_id -> frames still to capture
        self._burst_remaining: dict[int, int] = {}
        self._burst_paths: dict[int, list[str]] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.classifier.mark_camera_started(self.camera.id)
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "IntrusionEngine started: cam=%d name=%r shadow=%s fps=%d",
            self.camera.id, self.camera.name, self.shadow_mode, self.process_fps,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("IntrusionEngine stopped: cam=%d", self.camera.id)

    async def _loop(self) -> None:
        interval = 1.0 / self.process_fps
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                frame = self.stream.get_frame()
                now = datetime.now()

                if frame is not None:
                    h, w = frame.shape[:2]
                    # Record camera resolution once (used by UI)
                    if self.camera.resolution_w == 0 or self.camera.resolution_h == 0:
                        self.camera.resolution_w = w
                        self.camera.resolution_h = h
                        self.db.update_camera(self.camera.id,
                                              resolution_w=w, resolution_h=h)

                    # Ring buffer
                    if self._video_recorder:
                        self._video_recorder.push_frame(frame, now)

                    # Burst screenshot accumulation
                    self._capture_burst_frames(frame, now)

                    # Detect (in executor — YOLO is CPU-bound)
                    observations = await loop.run_in_executor(
                        None, self.detector.detect, frame, self.camera.id, now,
                    )

                    # ── Motion fallback (perimeter zones) ───────────
                    # Only runs if at least one zone on this camera has
                    # enable_motion_fallback=1. Re-checked every N frames so
                    # operator toggles take effect within ~15s.
                    motion_obs: list = []
                    if self._motion_fallback_enabled and self._zone_manager is not None:
                        self._motion_check_counter += 1
                        if self._motion_check_counter >= self._motion_check_interval or self._motion_detector is None:
                            self._motion_check_counter = 0
                            zones = self._zone_manager.get_zones(self.camera.id)
                            wants_fallback = any(
                                getattr(z, "enable_motion_fallback", False) and z.enabled
                                for z in zones
                            )
                            if wants_fallback and self._motion_detector is None:
                                try:
                                    self._motion_detector = MotionDetector(camera_id=self.camera.id)
                                    logger.info("Motion fallback ENABLED for camera %d", self.camera.id)
                                except ImportError:
                                    logger.warning("OpenCV unavailable; motion fallback disabled")
                                    self._motion_fallback_enabled = False
                            elif not wants_fallback and self._motion_detector is not None:
                                self._motion_detector = None
                                logger.info("Motion fallback DISABLED for camera %d", self.camera.id)

                        if self._motion_detector is not None:
                            animals = await loop.run_in_executor(
                                None, self.detector.detect_animals, frame,
                            )
                            motion_obs = await loop.run_in_executor(
                                None, self._motion_detector.detect,
                                frame, observations, animals, now,
                            )

                    # Run all observations (YOLO + motion) through the same classifier
                    for obs in observations:
                        event = self.classifier.classify(obs, w, h, now)
                        if event is not None:
                            await self._commit_event(event, frame, source="yolo")
                    for obs in motion_obs:
                        event = self.classifier.classify(obs, w, h, now)
                        if event is not None:
                            await self._commit_event(event, frame, source="motion")

            except Exception:
                logger.exception("IntrusionEngine loop error (cam=%d)", self.camera.id)

            await asyncio.sleep(interval)

    async def _commit_event(self, event: IntrusionEvent, frame, source: str = "yolo") -> None:
        """Persist + alarm + broadcast a finalized intrusion event.

        source: "yolo" for class-based detection, "motion" for motion fallback.
                Stored in `notes` so the UI can render a different badge.
        """
        event.shadow_mode = self.shadow_mode
        if source == "motion":
            event.notes = "motion_fallback"

        # 1. Screenshot (first frame of event)
        label = f"CAM{event.camera_id}_Z{event.zone_id}_T{event.track_id}"
        event.screenshot_path = save_screenshot(
            frame, label, None, self.screenshot_dir,
        )

        # 2. Persist to DB to get an ID
        event.id = self.db.add_intrusion_event(
            camera_id=event.camera_id,
            zone_id=event.zone_id,
            track_id=event.track_id,
            detected_at=event.detected_at,
            duration_sec=event.duration_sec,
            confidence=event.confidence,
            person_count=event.person_count,
            screenshot_path=event.screenshot_path,
            video_clip_path="",
            shadow_mode=event.shadow_mode,
        )

        intrusion_logger.info(
            "Event cam=%d zone=%d track=%d duration=%.1fs conf=%.2f shadow=%s id=%d",
            event.camera_id, event.zone_id, event.track_id,
            event.duration_sec, event.confidence, event.shadow_mode, event.id,
        )

        # 3. Trigger video clip (asynchronous; DB row is updated when clip lands)
        if self._video_recorder:
            self._video_recorder.trigger_clip(
                event.id, callback=self._on_clip_ready,
            )

        # 4. Queue a burst of N screenshots
        self._burst_remaining[event.id] = self.burst_count - 1   # screenshot already counted
        self._burst_paths[event.id] = [event.screenshot_path] if event.screenshot_path else []

        # 5. Alarm (only in live mode AND when operator has not muted)
        alarm_user_enabled = bool(self._alarm_enabled_getter())
        will_alarm = (not self.shadow_mode) and alarm_user_enabled
        if will_alarm:
            if self.alarm.should_trigger_intrusion(
                event.camera_id, event.zone_id, event.track_id,
            ):
                await self.alarm.trigger_intrusion_alarm(
                    event.camera_id, event.zone_id, event.track_id,
                    label=self.camera.name,
                )

        # 6. WebSocket broadcast (always — UI reflects shadow + muted events too)
        await self.ws_manager.broadcast({
            "type": "intrusion_on",
            "data": {
                "id": event.id,
                "camera_id": event.camera_id,
                "camera_name": self.camera.name,
                "zone_id": event.zone_id,
                "track_id": event.track_id,
                "detected_at": event.detected_at.isoformat(),
                "duration_sec": round(event.duration_sec, 1),
                "confidence": round(event.confidence * 100, 1),
                "screenshot_url": event.screenshot_path,
                "shadow_mode": event.shadow_mode,
                "alarm_muted": (not will_alarm) and (not event.shadow_mode),
                "source": source,   # "yolo" | "motion"
            },
        })

    def _on_clip_ready(self, event_id: int, clip_path: str) -> None:
        """VideoClipRecorder callback — runs on the recorder's worker thread."""
        try:
            # Store a web-servable path if under /static/
            public_path = clip_path
            if "static" in clip_path.replace("\\", "/"):
                idx = clip_path.replace("\\", "/").find("static/")
                public_path = "/" + clip_path.replace("\\", "/")[idx:]
            self.db.update_intrusion_event(event_id, video_clip_path=public_path)
            logger.info("Clip attached to event %d: %s", event_id, public_path)
        except Exception:
            logger.exception("Failed to attach clip to event %d", event_id)

    def _capture_burst_frames(self, frame, timestamp) -> None:
        """Accumulate extra screenshots for recently-fired events."""
        if not self._burst_remaining:
            return
        finalized_ids = []
        for event_id, remaining in list(self._burst_remaining.items()):
            if remaining <= 0:
                finalized_ids.append(event_id)
                continue
            label = f"BURST_{event_id}_{self.burst_count - remaining}"
            path = save_screenshot(frame, label, None, self.screenshot_dir)
            if path:
                self._burst_paths[event_id].append(path)
            self._burst_remaining[event_id] = remaining - 1
        for eid in finalized_ids:
            self._burst_remaining.pop(eid, None)
            # Note: burst_paths remain in memory for now — could be attached to DB
            # via a 'burst_screenshots' JSON field later.
