"""MultiCameraOrchestrator — manage many cameras as one logical system.

Responsibilities:
  - On startup: load enabled cameras from DB, spin up CameraStream + IntrusionEngine
    for each one with role in ('intrusion', 'both').
  - On shutdown: stop all engines + streams gracefully.
  - Runtime add/remove/reload of cameras (from admin UI, without server restart).
  - Provide snapshot accessor for each camera (used by /api/camera/snapshot).

Shares a single PersonDetector and IntrusionClassifier across all cameras
to keep GPU/memory footprint low.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.camera import CameraStream
from app.intrusion.classifier import IntrusionClassifier
from app.intrusion.engine import IntrusionEngine
from app.intrusion.models import Camera
from app.intrusion.person_detector import PersonDetector
from app.intrusion.zone_manager import ZoneManager

if TYPE_CHECKING:
    from app.alarm_manager import AlarmManager
    from app.database import Database
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger("gateguard.app")


class MultiCameraOrchestrator:
    """Wires cameras to streams + engines. Call start() after DB is ready."""

    def __init__(
        self,
        db: "Database",
        alarm: "AlarmManager",
        ws_manager: "ConnectionManager",
        detector: PersonDetector,
        classifier: IntrusionClassifier,
        zone_manager: ZoneManager,
        process_fps: int = 2,
        shadow_mode: bool = True,
        screenshot_dir: str = "static/intrusion_clips",
        clip_enabled: bool = True,
        clip_pre_sec: int = 10,
        clip_post_sec: int = 5,
        clip_dir: str = "static/intrusion_clips",
        burst_screenshots: int = 5,
        motion_fallback_enabled: bool = False,
        fps_by_camera: Optional[dict] = None,
        state_file: str = "data/intrusion_state.json",
    ):
        self.db = db
        self.alarm = alarm
        self.ws_manager = ws_manager
        self.detector = detector
        self.classifier = classifier
        self.zone_manager = zone_manager
        self.process_fps = process_fps
        self.shadow_mode = shadow_mode
        self.screenshot_dir = screenshot_dir
        self.clip_enabled = clip_enabled
        self.clip_pre_sec = clip_pre_sec
        self.clip_post_sec = clip_post_sec
        self.clip_dir = clip_dir
        self.burst_screenshots = burst_screenshots
        self.motion_fallback_enabled = motion_fallback_enabled
        self.fps_by_camera = fps_by_camera or {}

        self._streams: dict[int, CameraStream] = {}
        self._engines: dict[int, IntrusionEngine] = {}
        self._lock = asyncio.Lock()

        # Operator-controllable mute toggle, persisted across restarts.
        # There is a GLOBAL master switch plus optional PER-CAMERA overrides, so
        # a guard can temporarily silence ONE nuisance camera (e.g. wind in
        # foliage) without muting the whole site. Engines read the effective
        # value via get_alarm_enabled(camera_id) before triggering a siren.
        self._state_file = Path(state_file)
        self._alarm_enabled: bool = True                    # global master
        self._alarm_enabled_cameras: dict[int, bool] = {}   # per-camera overrides
        self._load_state()

    # ── Alarm enable/disable (runtime, persisted) ────────────────

    def _load_state(self) -> None:
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._alarm_enabled = bool(data.get("alarm_enabled", True))
                cams = data.get("cameras", {}) or {}
                self._alarm_enabled_cameras = {
                    int(k): bool(v) for k, v in cams.items()
                }
        except Exception:
            logger.exception("Failed to read intrusion state file")

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps({
                    "alarm_enabled": self._alarm_enabled,
                    "cameras": {
                        str(k): v for k, v in self._alarm_enabled_cameras.items()
                    },
                }),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to persist intrusion state")

    def get_alarm_enabled(self, camera_id: int | None = None) -> bool:
        """Effective alarm state. The global master gates everything; a
        per-camera override can additionally silence a single camera."""
        if not self._alarm_enabled:
            return False
        if camera_id is not None:
            return self._alarm_enabled_cameras.get(camera_id, True)
        return True

    def get_alarm_states(self) -> dict:
        """Snapshot for the management UI: global master + per-camera state."""
        rows = {r["id"]: r for r in self.db.list_cameras()}
        cam_ids = sorted(set(rows) | set(self._engines))
        cameras = []
        for cam_id in cam_ids:
            row = rows.get(cam_id)
            if row is not None and row.get("role") not in ("intrusion", "both"):
                continue
            cameras.append({
                "id": cam_id,
                "name": (row or {}).get("name") or f"Kamera {cam_id}",
                "running": cam_id in self._engines,
                "alarm_enabled": self._alarm_enabled_cameras.get(cam_id, True),
            })
        return {"enabled": self._alarm_enabled, "cameras": cameras}

    async def set_alarm_enabled(
        self, enabled: bool, camera_id: int | None = None
    ) -> None:
        enabled = bool(enabled)
        if camera_id is None:
            if self._alarm_enabled == enabled:
                return
            self._alarm_enabled = enabled
            scope = "ALL cameras"
        else:
            if self._alarm_enabled_cameras.get(camera_id, True) == enabled:
                return
            if enabled:
                # Back to default — drop the override so state stays minimal.
                self._alarm_enabled_cameras.pop(camera_id, None)
            else:
                self._alarm_enabled_cameras[camera_id] = False
            scope = f"camera {camera_id}"
        self._save_state()
        logger.info("Intrusion alarm %s for %s by operator",
                    "ENABLED" if enabled else "MUTED", scope)
        # Stop any currently-blaring siren when muting.
        if not enabled:
            try:
                await self.alarm.silence_alarm()
            except Exception:
                logger.exception("Failed to silence siren on mute")
        await self.ws_manager.broadcast({
            "type": "intrusion_alarm_toggle",
            "data": self.get_alarm_states(),
        })

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Load enabled cameras from DB and spin up engines."""
        rows = self.db.list_cameras(enabled_only=True)
        intrusion_cams = [r for r in rows if r.get("role") in ("intrusion", "both")]
        if not intrusion_cams:
            logger.info("MultiCameraOrchestrator: no intrusion cameras enabled")
            return

        interval = 1.0 / max(1, self.process_fps)
        count = len(intrusion_cams)
        for i, row in enumerate(intrusion_cams):
            cam = Camera.from_row(row)
            # Spread the cameras evenly across one frame interval.
            await self._spawn_engine(cam, start_delay=(i / count) * interval)

        logger.info(
            "MultiCameraOrchestrator started with %d cameras",
            len(self._engines),
        )

    async def stop_all(self) -> None:
        async with self._lock:
            # Stop engines first, then streams
            await asyncio.gather(
                *[e.stop() for e in self._engines.values()],
                return_exceptions=True,
            )
            for stream in self._streams.values():
                try:
                    stream.stop()
                except Exception:
                    logger.exception("Error stopping stream")
            self._engines.clear()
            self._streams.clear()
        logger.info("MultiCameraOrchestrator: all engines stopped")

    # ── Runtime CRUD ─────────────────────────────────────────────

    async def add_camera(self, cam: Camera) -> bool:
        """Start tracking a newly added camera (called from UI)."""
        async with self._lock:
            if cam.id in self._engines:
                logger.warning("Camera %d already has an engine", cam.id)
                return False
            if not cam.enabled or cam.role not in ("intrusion", "both"):
                return False
            # Offset a runtime-added camera by a fraction of the interval so it
            # doesn't land exactly on the existing cameras' tick.
            interval = 1.0 / max(1, self.process_fps)
            delay = (len(self._engines) % 4) * (interval / 4.0)
            await self._spawn_engine(cam, start_delay=delay)
        return True

    async def remove_camera(self, camera_id: int) -> bool:
        """Stop tracking a removed or disabled camera."""
        async with self._lock:
            engine = self._engines.pop(camera_id, None)
            stream = self._streams.pop(camera_id, None)
            if engine is None:
                return False
            try:
                await engine.stop()
            except Exception:
                logger.exception("Error stopping engine for camera %d", camera_id)
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    logger.exception("Error stopping stream for camera %d", camera_id)
            logger.info("Removed camera %d from orchestrator", camera_id)
        return True

    async def reload_camera(self, camera_id: int) -> bool:
        """Stop and re-spawn the camera — used when RTSP URL or role changes."""
        row = self.db.get_camera(camera_id)
        if row is None:
            return await self.remove_camera(camera_id)
        await self.remove_camera(camera_id)
        cam = Camera.from_row(row)
        return await self.add_camera(cam)

    # ── Queries ──────────────────────────────────────────────────

    def get_stream(self, camera_id: int) -> Optional[CameraStream]:
        return self._streams.get(camera_id)

    def active_camera_ids(self) -> list[int]:
        return list(self._engines.keys())

    def is_running(self, camera_id: int) -> bool:
        return camera_id in self._engines

    # ── Internals ────────────────────────────────────────────────

    async def _spawn_engine(self, cam: Camera, start_delay: float = 0.0) -> None:
        """Start a CameraStream + IntrusionEngine for one camera.

        ``start_delay`` phase-shifts this camera's loop so multiple cameras
        don't all submit inference on the same tick (they share one worker).
        """
        try:
            cam_fps = self.fps_by_camera.get(cam.id, self.process_fps)
            stream = CameraStream(cam.rtsp_url)
            stream.start()
            engine = IntrusionEngine(
                camera=cam,
                stream=stream,
                detector=self.detector,
                classifier=self.classifier,
                db=self.db,
                alarm=self.alarm,
                ws_manager=self.ws_manager,
                process_fps=cam_fps,
                shadow_mode=self.shadow_mode,
                screenshot_dir=self.screenshot_dir,
                clip_enabled=self.clip_enabled,
                clip_pre_sec=self.clip_pre_sec,
                clip_post_sec=self.clip_post_sec,
                clip_dir=self.clip_dir,
                burst_screenshots=self.burst_screenshots,
                motion_fallback_enabled=self.motion_fallback_enabled,
                # Bind this camera's id so the engine reads its per-camera
                # alarm override (not just the global master).
                alarm_enabled_getter=(lambda cid=cam.id: self.get_alarm_enabled(cid)),
                zone_manager=self.zone_manager,
                start_delay=start_delay,
            )
            await engine.start()
            self._streams[cam.id] = stream
            self._engines[cam.id] = engine
            logger.info("Spawned engine for camera %d (%s)", cam.id, cam.name)
        except Exception:
            logger.exception("Failed to spawn engine for camera %d", cam.id)
