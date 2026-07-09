"""VideoClipRecorder — ring-buffer clip recording for intrusion events.

Each frame is pushed into a fixed-size deque (pre-buffer). When an event
fires, the pre-buffer is captured and we continue collecting post-frames
for the configured duration, then we asynchronously write a video file.

One recorder instance per camera.

Memory note (low-RAM boxes): the ring stores JPEG-ENCODED bytes, not raw
ndarrays. A raw 1080p frame is ~6 MB; a pre-buffer of pre_sec*fps such
frames per camera, times 3-4 cameras, was hundreds of MB of resident RAM.
Encoding (optionally after a downscale to INTRUSION_CLIP_MAX_WIDTH) shrinks
each stored frame ~10-20x. Frames are decoded again only at write time.

Design:
  - push_frame() is called by the detection loop every frame. O(1) + encode.
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

# Codec fallback chain: (fourcc, file-extension). The first writer that
# actually opens on this machine wins. mp4v is the most portable MP4 codec;
# if the platform lacks it we fall back to AVI containers.
_CODEC_CHAIN = [("mp4v", ".mp4"), ("XVID", ".avi"), ("MJPG", ".avi")]


def _clip_setting(name: str, default):
    """Read a clip knob from config.settings without a hard import dependency."""
    try:
        from config import settings
        return getattr(settings, name, default)
    except Exception:
        return default


class VideoClipRecorder:
    def __init__(
        self,
        camera_id: int,
        fps: int = 2,
        pre_sec: int = 10,
        post_sec: int = 5,
        output_dir: str = "static/intrusion_clips",
        jpeg_quality: Optional[int] = None,
        max_width: Optional[int] = None,
    ):
        self.camera_id = camera_id
        self.fps = max(1, fps)
        self.pre_sec = pre_sec
        self.post_sec = post_sec
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Memory knobs (fall back to config defaults, then hard defaults).
        self.jpeg_quality = int(
            jpeg_quality if jpeg_quality is not None
            else _clip_setting("INTRUSION_CLIP_JPEG_QUALITY", 70)
        )
        self.max_width = int(
            max_width if max_width is not None
            else _clip_setting("INTRUSION_CLIP_MAX_WIDTH", 960)
        )

        self._pre_len = self.pre_sec * self.fps
        self._post_len = self.post_sec * self.fps
        self._ring: deque = deque(maxlen=self._pre_len)
        # event_id -> list of collected (jpeg_bytes, timestamp) tuples
        self._active: dict[int, list] = {}
        # event_id -> frames_remaining
        self._remaining: dict[int, int] = {}
        # event_id -> callback fired after clip is written (async notification)
        self._callbacks: dict[int, Callable[[int, str], None]] = {}
        self._lock = threading.Lock()

    def _encode(self, frame: np.ndarray) -> Optional[bytes]:
        """Downscale (if wider than max_width) and JPEG-encode a frame.
        Returns bytes, or None if encoding fails."""
        try:
            h, w = frame.shape[:2]
            if self.max_width and w > self.max_width:
                scale = self.max_width / float(w)
                frame = cv2.resize(
                    frame, (self.max_width, max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
            )
            if not ok:
                return None
            return buf.tobytes()
        except Exception:
            logger.exception("Clip frame encode failed (cam=%d)", self.camera_id)
            return None

    def push_frame(self, frame: np.ndarray, timestamp: Optional[datetime] = None) -> None:
        """Add a frame to the pre-buffer. Safe to call every frame."""
        if frame is None:
            return
        if timestamp is None:
            timestamp = datetime.now()
        encoded = self._encode(frame)
        if encoded is None:
            return
        item = (encoded, timestamp)
        with self._lock:
            self._ring.append(item)
            # Feed active recordings
            finalize_ids: list[int] = []
            for eid, buffer in self._active.items():
                buffer.append(item)
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

    def _path_for_event(self, event_id: int, ext: str = ".mp4") -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.output_dir / f"cam{self.camera_id}_event{event_id}_{ts}{ext}"

    def _open_writer(self, tmp_base: Path, size: tuple[int, int]):
        """Try each codec in the fallback chain until one opens.
        Returns (writer, tmp_path, final_ext) or (None, None, None)."""
        w, h = size
        for fourcc_str, ext in _CODEC_CHAIN:
            tmp_path = tmp_base.with_suffix(ext + ".tmp")
            try:
                fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
                writer = cv2.VideoWriter(str(tmp_path), fourcc, float(self.fps), (w, h))
                if writer.isOpened():
                    return writer, tmp_path, ext
                writer.release()
            except Exception:
                logger.exception("VideoWriter init error for codec %s", fourcc_str)
            # Clean up a stillborn temp file before trying the next codec
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return None, None, None

    def _write_clip(
        self,
        event_id: int,
        frames: list,
        callback: Optional[Callable[[int, str], None]],
    ) -> None:
        """Decode the buffered JPEG frames and write a clip to a .tmp file,
        then atomically rename it. The .tmp scheme prevents the retention
        sweeper from deleting half-written clips.
        """
        if not frames:
            logger.warning("No frames to write for event %d", event_id)
            return
        # Decode the first frame to learn the dimensions.
        first = cv2.imdecode(np.frombuffer(frames[0][0], np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            logger.error("Clip first-frame decode failed for event %d", event_id)
            return
        h, w = first.shape[:2]

        tmp_base = self.output_dir / self._path_for_event(event_id).stem
        writer, tmp_path, ext = self._open_writer(tmp_base, (w, h))
        if writer is None:
            logger.error("No working video codec for event %d (cam=%d)",
                         event_id, self.camera_id)
            return
        final_path = tmp_path.with_suffix("")  # drop ".tmp" -> "...<ext>"

        try:
            writer.write(first)
            for buf, _ in frames[1:]:
                img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    writer.write(img)
            writer.release()
            # Atomic rename — retention sweeper now sees a complete file
            try:
                os.replace(tmp_path, final_path)
            except OSError:
                logger.exception("Clip rename failed: %s -> %s", tmp_path, final_path)
                # Don't leak the half-written temp file
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return
            logger.info("Clip written: event=%d frames=%d path=%s",
                        event_id, len(frames), final_path)
            if callback:
                try:
                    callback(event_id, str(final_path))
                except Exception:
                    logger.exception("Clip callback failed for event %d", event_id)
        except Exception:
            logger.exception("Clip write failed for event %d", event_id)
            try:
                writer.release()
            except Exception:
                pass
            for p in (tmp_path, final_path):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    # Diagnostics
    def active_clip_count(self) -> int:
        with self._lock:
            return len(self._active)


def cleanup_old_clips(output_dir: str, retention_days: int = 60) -> int:
    """Delete clips older than retention_days. Returns count deleted.

    Also reaps stale ``*.tmp`` files (left behind only by a crashed/killed
    write) regardless of the retention window once they're older than 1 hour.
    """
    import time
    now = time.time()
    cutoff = now - retention_days * 86400
    tmp_cutoff = now - 3600  # orphaned temp files older than 1h
    base = Path(output_dir)
    if not base.exists():
        return 0
    deleted = 0
    for f in base.iterdir():
        if not f.is_file():
            continue
        name = f.name.lower()
        try:
            mtime = f.stat().st_mtime
            if name.endswith(".tmp"):
                if mtime < tmp_cutoff:
                    f.unlink()
                    deleted += 1
            elif name.endswith((".mp4", ".avi")):
                if mtime < cutoff:
                    f.unlink()
                    deleted += 1
        except OSError:
            continue
    if deleted:
        logger.info("Intrusion clip cleanup: removed %d old files", deleted)
    return deleted
