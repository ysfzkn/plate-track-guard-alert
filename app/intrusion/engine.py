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
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from app.inference_pool import get_inference_pool, get_io_pool
from app.intrusion.models import IntrusionEvent
from app.intrusion.motion_detector import MotionDetector, MOTION_TRACK_ID_BASE
from app.intrusion.simple_tracker import SimpleIouTracker
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
        start_delay: float = 0.0,
    ):
        self.camera = camera
        # Phase offset so N cameras don't all fire inference on the same tick
        # (they share one inference worker — staggering keeps the queue smooth).
        self._start_delay = max(0.0, float(start_delay))
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
        # Camera-wide default from env. NOTE: motion fallback also activates
        # whenever ANY zone on this camera has enable_motion_fallback=1 — the
        # per-zone UI toggle is sufficient on its own, this env just force-on's
        # it for every zone. See the zone re-check in _loop().
        self._motion_fallback_enabled = motion_fallback_enabled
        # Latched True only if OpenCV is missing — stops us retrying MOG2 setup
        # every re-check when the module can never load.
        self._motion_unavailable = False

        # Per-camera tracker. We detect with the STATELESS detect_raw() and
        # assign IDs here, instead of the shared model.track(persist=True) whose
        # single ByteTrack timeline gets cross-contaminated across cameras.
        self._tracker = SimpleIouTracker(camera_id=camera.id)
        self._motion_check_interval = 30   # re-check zone settings every N frames
        self._motion_check_counter = 0

        # ── Motion-aware frame skip (perf optimization) ──
        # YOLO inference is the dominant cost. If the scene hasn't changed
        # meaningfully since the last frame we processed, skip YOLO entirely
        # and reuse the previous result. We compare consecutive frames with a
        # cheap absdiff; if the changed-pixel ratio < threshold, the frame is
        # "static" and we save ~50ms of YOLO work.
        self._prev_gray = None
        self._static_frame_streak = 0
        self._motion_skip_threshold = 0.005   # 0.5% of pixels must change
        # Always run YOLO every Nth frame even on static scenes — guards against
        # gradual lighting changes the absdiff misses.
        self._mandatory_yolo_every_n = 10

        self._video_recorder = VideoClipRecorder(
            camera_id=camera.id,
            fps=self.process_fps,
            pre_sec=clip_pre_sec,
            post_sec=clip_post_sec,
            output_dir=clip_dir,
        ) if clip_enabled else None

        # ── Health metrics (per camera) ──
        self._last_loop_at: float = 0.0
        self._last_success_at: float = 0.0
        self._loop_count: int = 0
        self._error_count: int = 0
        self._consecutive_errors: int = 0
        self.MAX_CONSECUTIVE_ERRORS = 5
        self.STALL_TIMEOUT_SEC = 30.0

        # ── Runtime state ──
        self._running = False
        self._task: asyncio.Task | None = None
        # Per-event burst queue: event_id -> frames still to capture
        self._burst_remaining: dict[int, int] = {}
        self._burst_paths: dict[int, list[str]] = {}
        # event_id -> in-flight save futures not yet completed. Lets us persist
        # the burst paths to the DB and free the dicts only once every save has
        # landed (otherwise the dicts leaked and paths were never stored).
        self._burst_pending: dict[int, int] = {}

    def get_health(self) -> dict:
        now = time.time()
        success_ago = (now - self._last_success_at) if self._last_success_at else None
        loop_ago = (now - self._last_loop_at) if self._last_loop_at else None
        is_stalled = (
            success_ago is not None
            and success_ago > self.STALL_TIMEOUT_SEC
            and self._loop_count > 0
        )
        return {
            "camera_id": self.camera.id,
            "running": self._running,
            "loop_count": self._loop_count,
            "error_count": self._error_count,
            "consecutive_errors": self._consecutive_errors,
            "last_success_ago_sec": round(success_ago, 1) if success_ago is not None else None,
            "last_loop_ago_sec": round(loop_ago, 1) if loop_ago is not None else None,
            "stalled": is_stalled,
            "degraded": is_stalled or self._consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS,
        }

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
        # Stagger startup so cameras interleave on the shared inference worker.
        if self._start_delay:
            try:
                await asyncio.sleep(self._start_delay)
            except asyncio.CancelledError:
                return
        while self._running:
            self._last_loop_at = time.time()
            self._loop_count += 1
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

                    # ── Motion-aware skip ──
                    # Skip YOLO if scene hasn't changed; keep tracker state
                    # warm by feeding it nothing this iteration.
                    skip_yolo = self._should_skip_yolo(frame)

                    if skip_yolo:
                        observations = []
                    else:
                        # Stateless detection on the shared bounded inference
                        # pool, then per-camera ID assignment (no shared
                        # ByteTrack contention across cameras).
                        raw = await loop.run_in_executor(
                            get_inference_pool(), self.detector.detect_raw, frame, now,
                        )
                        observations = self._tracker.update(raw, now)

                    # ── Motion fallback (perimeter zones) ───────────
                    # Only runs if at least one zone on this camera has
                    # enable_motion_fallback=1. Re-checked every N frames so
                    # operator toggles take effect within ~15s.
                    motion_obs: list = []
                    if self._zone_manager is not None and not self._motion_unavailable:
                        self._motion_check_counter += 1
                        if self._motion_check_counter >= self._motion_check_interval or self._motion_detector is None:
                            self._motion_check_counter = 0
                            zones = self._zone_manager.get_zones(self.camera.id)
                            # Activate if the env master-switch is on OR any zone
                            # explicitly requests it (per-zone "hareket yakalama").
                            wants_fallback = self._motion_fallback_enabled or any(
                                getattr(z, "enable_motion_fallback", False) and z.enabled
                                for z in zones
                            )
                            if wants_fallback and self._motion_detector is None:
                                try:
                                    self._motion_detector = MotionDetector(camera_id=self.camera.id)
                                    logger.info("Motion fallback ENABLED for camera %d", self.camera.id)
                                except ImportError:
                                    logger.warning("OpenCV unavailable; motion fallback disabled")
                                    self._motion_unavailable = True
                            elif not wants_fallback and self._motion_detector is not None:
                                self._motion_detector = None
                                logger.info("Motion fallback DISABLED for camera %d", self.camera.id)

                        if self._motion_detector is not None:
                            animals = await loop.run_in_executor(
                                get_inference_pool(), self.detector.detect_animals, frame,
                            )
                            motion_obs = await loop.run_in_executor(
                                get_inference_pool(), self._motion_detector.detect,
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

                # Successful tick (with or without observations)
                self._last_success_at = time.time()
                if self._consecutive_errors > 0:
                    logger.info("Intrusion cam=%d recovered after %d errors",
                                self.camera.id, self._consecutive_errors)
                    self._consecutive_errors = 0

            except Exception:
                self._error_count += 1
                self._consecutive_errors += 1
                logger.exception(
                    "IntrusionEngine loop error (cam=%d, %d consecutive)",
                    self.camera.id, self._consecutive_errors,
                )
                if self._consecutive_errors == self.MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "Intrusion engine cam=%d flagged DEGRADED (%d errors)",
                        self.camera.id, self._consecutive_errors,
                    )

            await asyncio.sleep(interval)

    async def _commit_event(self, event: IntrusionEvent, frame, source: str = "yolo") -> None:
        """Persist + alarm + broadcast a finalized intrusion event.

        source: "yolo" for class-based detection, "motion" for motion fallback.
                Stored in `notes` so the UI can render a different badge.
        """
        event.shadow_mode = self.shadow_mode
        if source == "motion":
            event.notes = "motion_fallback"

        # 1. Screenshot (first frame of event) — heavy PIL work runs in
        # the event-loop's default executor so the detection loop never
        # stalls on disk I/O or font rendering.
        label = f"CAM{event.camera_id}_Z{event.zone_id}_T{event.track_id}"
        loop = asyncio.get_event_loop()
        event.screenshot_path = await loop.run_in_executor(
            get_io_pool(), save_screenshot, frame, label, None, self.screenshot_dir,
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

    def _should_skip_yolo(self, frame) -> bool:
        """Return True if the frame is essentially identical to the previous
        one — in which case running YOLO again is wasted CPU.

        Uses a downsampled grayscale absdiff so the cost is O(N) per frame at
        a fraction of YOLO's cost (~1ms vs ~50ms).
        """
        try:
            import cv2 as _cv2
            small = _cv2.resize(frame, (160, 90))
            gray = _cv2.cvtColor(small, _cv2.COLOR_BGR2GRAY)
        except Exception:
            return False   # any error → don't skip, run YOLO

        if self._prev_gray is None:
            self._prev_gray = gray
            return False

        try:
            import cv2 as _cv2
            import numpy as _np
            diff = _cv2.absdiff(gray, self._prev_gray)
            # Pixels that differ by more than 25 grey levels = real motion
            changed = float((diff > 25).sum()) / float(diff.size)
        except Exception:
            self._prev_gray = gray
            return False

        self._prev_gray = gray
        if changed < self._motion_skip_threshold:
            self._static_frame_streak += 1
            # Force a YOLO call every N static frames to catch slow drifts
            if self._static_frame_streak >= self._mandatory_yolo_every_n:
                self._static_frame_streak = 0
                return False
            return True
        self._static_frame_streak = 0
        return False

    def _capture_burst_frames(self, frame, timestamp) -> None:
        """Queue extra screenshots for recently-fired events.
        Saves run on the IO pool — never blocks the detection loop.
        """
        if not self._burst_remaining:
            return
        loop = asyncio.get_event_loop()
        finalized_ids = []
        for event_id, remaining in list(self._burst_remaining.items()):
            if remaining <= 0:
                finalized_ids.append(event_id)
                continue
            label = f"BURST_{event_id}_{self.burst_count - remaining}"
            # Fire-and-forget: result goes into self._burst_paths via callback
            future = loop.run_in_executor(
                get_io_pool(), save_screenshot, frame.copy(), label, None, self.screenshot_dir,
            )
            self._burst_pending[event_id] = self._burst_pending.get(event_id, 0) + 1
            future.add_done_callback(
                lambda f, eid=event_id: self._on_burst_saved(eid, f)
            )
            self._burst_remaining[event_id] = remaining - 1
        for eid in finalized_ids:
            self._burst_remaining.pop(eid, None)
            # Nothing left to queue — if all saves already landed, finalize now.
            self._maybe_finalize_burst(eid)

    def _on_burst_saved(self, event_id: int, future) -> None:
        try:
            path = future.result()
            if path:
                self._burst_paths.setdefault(event_id, []).append(path)
        except Exception:
            logger.exception("Burst save failed for event %d", event_id)
        finally:
            self._burst_pending[event_id] = max(0, self._burst_pending.get(event_id, 1) - 1)
            self._maybe_finalize_burst(event_id)

    def _maybe_finalize_burst(self, event_id: int) -> None:
        """When an event has nothing left to queue AND no saves in flight,
        persist the collected burst paths to the DB and free the per-event
        state (prevents the dicts from growing without bound)."""
        if self._burst_remaining.get(event_id, 0) > 0:
            return   # still queuing more frames
        if self._burst_pending.get(event_id, 0) > 0:
            return   # saves still in flight
        paths = self._burst_paths.pop(event_id, None)
        self._burst_pending.pop(event_id, None)
        if paths:
            try:
                self.db.update_intrusion_event(
                    event_id, burst_paths=json.dumps(paths),
                )
            except Exception:
                logger.exception("Failed to persist burst paths for event %d", event_id)
