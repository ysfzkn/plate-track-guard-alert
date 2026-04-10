"""RTSP camera stream reader with mock mode support."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("gateguard.app")


class CameraStream:
    """Threaded RTSP camera capture with auto-reconnect."""

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._thread: Optional[threading.Thread] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Camera stream started: %s", self.rtsp_url)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Camera stream stopped")

    def _capture_loop(self):
        backoff = 1
        while self._running:
            cap = cv2.VideoCapture(self.rtsp_url)
            if not cap.isOpened():
                logger.warning("Camera connection failed, retrying in %ds...", backoff)
                self._connected = False
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue

            self._connected = True
            backoff = 1
            logger.info("Camera connected")

            while self._running:
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Frame read failed, reconnecting...")
                    self._connected = False
                    break
                with self._lock:
                    self._frame = frame

            cap.release()


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

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def current_mock_plate(self) -> str:
        return self._current_plate if self._plate_visible else ""

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._generate_loop, daemon=True)
        self._thread.start()
        self._connected = True
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
            # Generate a gate-like background
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            # Road surface (dark gray)
            frame[300:480, :] = (60, 60, 60)
            # Sky (dark blue)
            frame[0:200, :] = (80, 50, 30)
            # Gate barrier (yellow)
            cv2.rectangle(frame, (50, 180), (590, 200), (0, 200, 255), -1)
            # Gate posts
            cv2.rectangle(frame, (40, 150), (60, 320), (100, 100, 100), -1)
            cv2.rectangle(frame, (580, 150), (600, 320), (100, 100, 100), -1)

            now = time.time()

            # Show plate periodically
            if now - last_plate_time > plate_interval:
                all_plates = AUTHORIZED_PLATES + UNAUTHORIZED_PLATES
                self._current_plate = random.choice(all_plates)
                self._plate_visible = True
                last_plate_time = now
                plate_interval = random.uniform(4, 8)

            if self._plate_visible and now - last_plate_time < plate_show_duration:
                # Draw a car shape
                cv2.rectangle(frame, (200, 250), (440, 380), (40, 40, 120), -1)
                # Windshield
                cv2.rectangle(frame, (220, 255), (420, 310), (80, 80, 80), -1)
                # License plate background (white)
                cv2.rectangle(frame, (260, 340), (380, 370), (255, 255, 255), -1)
                # License plate text
                cv2.putText(
                    frame, self._current_plate,
                    (265, 363), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2,
                )
            elif now - last_plate_time >= plate_show_duration:
                self._plate_visible = False

            # Timestamp overlay
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, ts, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.putText(frame, "MOCK CAMERA", (480, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

            with self._lock:
                self._frame = frame

            time.sleep(1 / 15)  # ~15 fps generation
