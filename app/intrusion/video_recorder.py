"""VideoClipRecorder — ring-buffer MP4 clip recording for intrusion events.

Each frame is pushed into a fixed-size deque (pre-buffer). When an event
fires, the pre-buffer is captured and we continue collecting post-frames
for the configured duration, then we asynchronously write an MP4 file.

One recorder instance per camera.

Design:
  - push_frame() is called by the detection loop every frame. O(1).
  - trigger_clip(event_id) snapshots the pre-buffer and registers an
    active recording that absorbs the next N post-frames.
  - Finalization (cv2.VideoWriter) is done on a worker thread to keep
    the detection loop non-blocking.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

logger = logging.getLogger("gateguard.app")


class VideoClipRecorder:
    def __init__(
        self,
        camera_id: int,
        fps: int = 2,
        pre_sec: int = 10,
        post_sec: int = 5,
        output_dir: str = "static/intrusion_clips",
    ):
        self.camera_id = camera_id
        self.fps = max(1, fps)
        self.pre_sec = pre_sec
        self.post_sec = post_sec
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._pre_len = self.pre_sec * self.fps
        self._post_len = self.post_sec * self.fps
        self._ring: deque = deque(maxlen=self._pre_len)
        # event_id -> list of collected frames (pre + accumulating post)
        self._active: dict[int, list] = {}
        # event_id -> frames_remaining
        self._remaining: dict[int, int] = {}
        # event_id -> callback fired after clip is written (async notification)
        self._callbacks: dict[int, Callable[[int, str], None]] = {}
        self._lock = threading.Lock()

    def push_frame(self, frame: np.ndarray, timestamp: Optional[datetime] = None) -> None:
        """Add a frame to the pre-buffer. Safe to call every frame."""
        if frame is None:
            return
        if timestamp is None:
            timestamp = datetime.now()
        frame_copy = frame.copy()
        with self._lock:
            self._ring.append((frame_copy, timestamp))
            # Feed active recordings
            finalize_ids: list[int] = []
            for eid, buffer in self._active.items():
                buffer.append((frame_copy, timestamp))
                self._remaining[eid] -= 1
                if self._remaining[eid] <= 0:
                    finalize_ids.append(eid)
            for eid in finalize_ids:
                frames = self._active.pop(eid)
                self._remaining.pop(eid, None)
                callback = self._callbacks.pop(eid, None)
                # Spawn writer thread (non-blocking)
                threading.Thread(
                    target=self._write_clip,
                    args=(eid, frames, callback),
                    daemon=True,
                ).start()

    def trigger_clip(
        self,
        event_id: int,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> str:
        """Start recording a clip for this event. Pre-buffer is captured now;
        post-frames are collected as push_frame() continues to be called.
        Returns the expected output path immediately (file appears ~post_sec later).
        """
        with self._lock:
            # Copy the current pre-buffer as the starting frames
            buffer = list(self._ring)
            self._active[event_id] = buffer
            self._remaining[event_id] = self._post_len
            if callback:
                self._callbacks[event_id] = callback
        path = self._path_for_event(event_id)
        logger.info(
            "Video clip queued: event=%d cam=%d pre=%d post=%d → %s",
            event_id, self.camera_id, len(buffer), self._post_len, path,
        )
        return str(path)

    # ── Internals ────────────────────────────────────────────────

    def _path_for_event(self, event_id: int) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.output_dir / f"cam{self.camera_id}_event{event_id}_{ts}.mp4"

    def _write_clip(
        self,
        event_id: int,
        frames: list,
        callback: Optional[Callable[[int, str], None]],
    ) -> None:
        if not frames:
            logger.warning("No frames to write for event %d", event_id)
            return
        path = self._path_for_event(event_id)
        try:
            h, w = frames[0][0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(path), fourcc, float(self.fps), (w, h))
            if not writer.isOpened():
                logger.error("cv2.VideoWriter failed to open for %s", path)
                return
            for frame, _ in frames:
                writer.write(frame)
            writer.release()
            logger.info("Clip written: event=%d frames=%d path=%s",
                        event_id, len(frames), path)
            if callback:
                try:
                    callback(event_id, str(path))
                except Exception:
                    logger.exception("Clip callback failed for event %d", event_id)
        except Exception:
            logger.exception("Clip write failed for event %d", event_id)
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    # Diagnostics
    def active_clip_count(self) -> int:
        with self._lock:
            return len(self._active)


def cleanup_old_clips(output_dir: str, retention_days: int = 60) -> int:
    """Delete clips older than retention_days. Returns count deleted."""
    import time
    cutoff = time.time() - retention_days * 86400
    base = Path(output_dir)
    if not base.exists():
        return 0
    deleted = 0
    for f in base.glob("*.mp4"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            continue
    if deleted:
        logger.info("Intrusion clip cleanup: removed %d old files", deleted)
    return deleted
