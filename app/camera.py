"""RTSP camera stream reader with health metrics, watchdog, and auto-reconnect.

Health metrics tracked per stream:
  - fps (rolling 5 sec window)
  - last_frame_time
  - total_frames
  - reconnect_count
  - dropped_frames
  - is_connected
  - last_error

A watchdog inside the capture loop detects:
  - VideoCapture.read() blocking > 10 sec → force re-open
  - No frames for > 15 sec → mark stream as STALE and reconnect
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("gateguard.app")


class CameraStream:
    """Threaded RTSP camera capture with health + watchdog + auto-reconnect."""

    # Watchdog thresholds (seconds)
    STALE_FRAME_TIMEOUT = 15.0
    READ_TIMEOUT_HARD = 10.0
    FPS_WINDOW_SEC = 5.0

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._thread: Optional[threading.Thread] = None

        # ── Health metrics ──
        self._frame_times: deque[float] = deque(maxlen=300)   # last 300 timestamps
        self._last_frame_time: float = 0.0
        # Last time the frame CONTENT actually changed (vs a frozen decoder
        # repeating the same buffer). Drives the frozen-feed watchdog.
        self._last_changed_time: float = 0.0
        self._total_frames: int = 0
        self._reconnect_count: int = 0
        self._dropped_frames: int = 0
        self._connected_at: float = 0.0
        self._last_error: str = ""

    # ── Public API ───────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def get_health(self) -> dict:
        """Return health snapshot — safe to call from any thread."""
        now = time.time()
        # Rolling FPS over last FPS_WINDOW_SEC
        cutoff = now - self.FPS_WINDOW_SEC
        recent = [t for t in self._frame_times if t >= cutoff]
        fps = len(recent) / self.FPS_WINDOW_SEC if recent else 0.0
        lag = (now - self._last_frame_time) if self._last_frame_time else None
        content_age = (now - self._last_changed_time) if self._last_changed_time else None
        frozen = content_age is not None and content_age > self.STALE_FRAME_TIMEOUT
        return {
            "connected": self._connected,
            "fps": round(fps, 1),
            "last_frame_lag_sec": round(lag, 1) if lag is not None else None,
            "content_age_sec": round(content_age, 1) if content_age is not None else None,
            "frozen": frozen,
            "total_frames": self._total_frames,
            "reconnect_count": self._reconnect_count,
            "dropped_frames": self._dropped_frames,
            "uptime_sec": round(now - self._connected_at, 0) if self._connected_at else 0,
            "last_error": self._last_error,
            # Stale = no fresh frames OR a frozen (repeating) feed.
            "is_stale": (lag is not None and lag > self.STALE_FRAME_TIMEOUT) or frozen,
        }

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Camera stream started: %s", _mask_credentials(self.rtsp_url))

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Camera stream stopped: %s", _mask_credentials(self.rtsp_url))

    # ── Internals ────────────────────────────────────────────

    def _capture_loop(self):
        backoff = 1
        while self._running:
            try:
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                # OpenCV: keep latency low (drop old frames if we fall behind),
                # and bound open/read so a dead stream doesn't hang the thread.
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
                except Exception:
                    pass

                if not cap.isOpened():
                    self._connected = False
                    self._last_error = "Cannot open RTSP stream"
                    logger.warning("Camera connection failed (%s), retry in %ds",
                                   _mask_credentials(self.rtsp_url), backoff)
                    self._reconnect_count += 1
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue

                self._connected = True
                self._connected_at = time.time()
                self._last_frame_time = time.time()
                self._last_changed_time = time.time()
                self._last_error = ""
                backoff = 1
                logger.info("Camera connected: %s", _mask_credentials(self.rtsp_url))

                prev_sample: Optional[np.ndarray] = None
                while self._running:
                    read_start = time.time()
                    ret, frame = cap.read()
                    read_duration = time.time() - read_start

                    if read_duration > self.READ_TIMEOUT_HARD:
                        # The decoder blocked too long — likely TCP stall.
                        self._last_error = f"Read blocked {read_duration:.1f}s"
                        logger.warning("Camera read timeout: %.1fs, reconnecting...",
                                       read_duration)
                        self._reconnect_count += 1
                        break

                    if not ret:
                        self._dropped_frames += 1
                        self._last_error = "Frame read returned False"
                        logger.warning("Frame read failed, reconnecting...")
                        self._reconnect_count += 1
                        break

                    now = time.time()
                    with self._lock:
                        self._frame = frame
                    self._frame_times.append(now)
                    self._last_frame_time = now
                    self._total_frames += 1

                    # ── Frozen-feed watchdog ──
                    # Some cameras/decoders keep returning ret=True but repeat the
                    # SAME buffer when the link stalls. A live sensor never emits
                    # two bit-identical consecutive frames (sensor noise), so we
                    # compare a cheap strided sub-sample (noise survives sampling,
                    # but a static scene still varies pixel-to-pixel). If the
                    # content hasn't changed for STALE_FRAME_TIMEOUT, reconnect.
                    try:
                        sample = frame[::16, ::16]
                        if (prev_sample is not None
                                and sample.shape == prev_sample.shape
                                and np.array_equal(sample, prev_sample)):
                            if (now - self._last_changed_time) > self.STALE_FRAME_TIMEOUT:
                                self._last_error = "Stale (frozen frames)"
                                logger.warning("Camera frozen %.0fs — forcing reconnect",
                                               now - self._last_changed_time)
                                self._reconnect_count += 1
                                break
                        else:
                            self._last_changed_time = now
                        prev_sample = sample.copy()
                    except Exception:
                        self._last_changed_time = now

                self._connected = False
                cap.release()
            except Exception as e:
                self._connected = False
                self._last_error = f"Capture exception: {e}"
                logger.exception("Camera capture loop crashed; recovering")
                time.sleep(2)


def _mask_credentials(url: str) -> str:
    """Strip user:pass@ from RTSP URL for logging."""
    import re
    return re.sub(r"://([^/]+)@", "://***@", url)


# --- Mock plates for testing ---

AUTHORIZED_PLATES = [
    "34TV3409", "34PB5705", "34RJ1566", "34SBR34", "34GY4504",
    "34LB2317", "34SP9114", "34HN7644", "34KH7598", "34RK6329",
    "34BBB693", "34BDZ816", "34VP0991", "34DA1391", "34FC6340",
]

UNAUTHORIZED_PLATES = [
    "34ZZ9999", "06ABC123", "35XY4567", "41KK8888", "34TEST01",
    "34HAC1234", "16EF5678", "34NNN777", "07GH3333", "34QQ1111",
]


class MockCamera:
    """Generates fake frames with synthetic plate overlays for testing."""

    def __init__(self):
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._thread: Optional[threading.Thread] = None
        self._current_plate: str = ""
        self._plate_visible: bool = False
        self._frame_times: deque[float] = deque(maxlen=300)
        self._last_frame_time: float = 0.0
        self._total_frames: int = 0
        self._connected_at: float = 0.0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def current_mock_plate(self) -> str:
        return self._current_plate if self._plate_visible else ""

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def get_health(self) -> dict:
        now = time.time()
        cutoff = now - 5.0
        recent = [t for t in self._frame_times if t >= cutoff]
        return {
            "connected": self._connected,
            "fps": round(len(recent) / 5.0, 1) if recent else 0.0,
            "last_frame_lag_sec": round(now - self._last_frame_time, 1) if self._last_frame_time else None,
            "total_frames": self._total_frames,
            "reconnect_count": 0,
            "dropped_frames": 0,
            "uptime_sec": round(now - self._connected_at, 0) if self._connected_at else 0,
            "last_error": "",
            "is_stale": False,
        }

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._generate_loop, daemon=True)
        self._thread.start()
        self._connected = True
        self._connected_at = time.time()
        logger.info("Mock camera started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Mock camera stopped")

    def _generate_loop(self):
        plate_interval = random.uniform(4, 8)
        last_plate_time = time.time()
        plate_show_duration = 2.0

        while self._running:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            frame[300:480, :] = (60, 60, 60)
            frame[0:200, :] = (80, 50, 30)
            cv2.rectangle(frame, (50, 180), (590, 200), (0, 200, 255), -1)
            cv2.rectangle(frame, (40, 150), (60, 320), (100, 100, 100), -1)
            cv2.rectangle(frame, (580, 150), (600, 320), (100, 100, 100), -1)

            now = time.time()

            if now - last_plate_time > plate_interval:
                all_plates = AUTHORIZED_PLATES + UNAUTHORIZED_PLATES
                self._current_plate = random.choice(all_plates)
                self._plate_visible = True
                last_plate_time = now
                plate_interval = random.uniform(4, 8)

            if self._plate_visible and now - last_plate_time < plate_show_duration:
                cv2.rectangle(frame, (200, 250), (440, 380), (40, 40, 120), -1)
                cv2.rectangle(frame, (220, 255), (420, 310), (80, 80, 80), -1)
                cv2.rectangle(frame, (260, 340), (380, 370), (255, 255, 255), -1)
                cv2.putText(
                    frame, self._current_plate,
                    (265, 363), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2,
                )
            elif now - last_plate_time >= plate_show_duration:
                self._plate_visible = False

            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, ts, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(frame, "MOCK CAMERA", (480, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

            with self._lock:
                self._frame = frame
            self._frame_times.append(now)
            self._last_frame_time = now
            self._total_frames += 1

            time.sleep(1 / 15)
