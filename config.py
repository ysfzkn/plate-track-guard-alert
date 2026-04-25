"""Application configuration loaded from .env file."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# When running as a frozen exe (PyInstaller), use the exe's directory.
# Otherwise use the source file's directory.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


class Settings:
    # Camera
    RTSP_URL: str = os.getenv("RTSP_URL", "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101")

    # ESP32 Alarm
    ESP32_IP: str = os.getenv("ESP32_IP", "192.168.1.50")

    # Database
    MDB_PATH: str = os.getenv("MDB_PATH", str(BASE_DIR / "moonwel_db" / "MW305_DB200.mdb"))
    SQLITE_PATH: str = os.getenv("SQLITE_PATH", str(BASE_DIR / "data" / "gateguard.db"))

    # Screenshots
    SCREENSHOT_DIR: str = os.getenv("SCREENSHOT_DIR", str(BASE_DIR / "static" / "screenshots"))

    # Detection
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "false").lower() == "true"
    PROCESS_FPS: int = int(os.getenv("PROCESS_FPS", "2"))
    CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.45"))
    ALARM_COOLDOWN_SEC: int = int(os.getenv("ALARM_COOLDOWN_SEC", "60"))
    FUZZY_TOLERANCE: int = int(os.getenv("FUZZY_TOLERANCE", "1"))  # for DB lookup

    # Track-based multi-frame consensus (see app/tracker.py)
    MIN_FRAMES_FOR_COMMIT: int = int(os.getenv("MIN_FRAMES_FOR_COMMIT", "2"))
    TRACK_IDLE_FRAMES: int = int(os.getenv("TRACK_IDLE_FRAMES", "2"))
    TRACK_MAX_DURATION_SEC: float = float(os.getenv("TRACK_MAX_DURATION_SEC", "15"))
    TRACK_IOU_THRESHOLD: float = float(os.getenv("TRACK_IOU_THRESHOLD", "0.2"))
    TRACK_FUZZY_TOLERANCE: int = int(os.getenv("TRACK_FUZZY_TOLERANCE", "2"))
    DIRECTION_AREA_RATIO: float = float(os.getenv("DIRECTION_AREA_RATIO", "1.2"))

    # Direction detection (primary = bbox area change, secondary = Y-delta)
    #   entry_size_change:
    #     "approach" = plate growing (vehicle approaching camera) means ENTRY (typical gate setup)
    #     "recede"   = plate shrinking (vehicle moving away) means ENTRY (camera inside lot facing entrance)
    #   entry_y_direction (fallback for ambiguous area changes):
    #     "down" = plate moving downward in frame = ENTRY
    #     "up"   = plate moving upward in frame = ENTRY
    CAMERA_ENTRY_SIZE_CHANGE: str = os.getenv("CAMERA_ENTRY_SIZE_CHANGE", "approach")
    CAMERA_ENTRY_Y_DIRECTION: str = os.getenv("CAMERA_ENTRY_Y_DIRECTION", "down")

    # Legacy config: map old CAMERA_ENTRY_DIRECTION to new CAMERA_ENTRY_Y_DIRECTION
    _legacy_entry_dir = os.getenv("CAMERA_ENTRY_DIRECTION")
    if _legacy_entry_dir and not os.getenv("CAMERA_ENTRY_Y_DIRECTION"):
        CAMERA_ENTRY_Y_DIRECTION = _legacy_entry_dir

    # ── Module 2: Intrusion Detection ─────────────────────────────
    ENABLE_INTRUSION_MODULE: bool = os.getenv("ENABLE_INTRUSION_MODULE", "false").lower() == "true"

    # Night mode (used by zones with is_night_only=1)
    NIGHT_MODE_START: str = os.getenv("NIGHT_MODE_START", "22:00")
    NIGHT_MODE_END: str = os.getenv("NIGHT_MODE_END", "07:00")

    # Detection rules — tuned for high recall + low false-positive
    # Strategy: lower per-frame conf threshold (catches distant/partial subjects)
    # is paired with a frame-count consensus rule that demands the same track
    # appear in N consecutive frames before any alarm fires.
    INTRUSION_CONFIDENCE: float = float(os.getenv("INTRUSION_CONFIDENCE", "0.30"))
    INTRUSION_MIN_LOITER_SEC: int = int(os.getenv("INTRUSION_MIN_LOITER_SEC", "1"))
    INTRUSION_COOLDOWN_SEC: int = int(os.getenv("INTRUSION_COOLDOWN_SEC", "30"))
    INTRUSION_PROCESS_FPS: int = int(os.getenv("INTRUSION_PROCESS_FPS", "2"))
    INTRUSION_WARMUP_SEC: int = int(os.getenv("INTRUSION_WARMUP_SEC", "30"))
    INTRUSION_SHADOW_MODE: bool = os.getenv("INTRUSION_SHADOW_MODE", "true").lower() == "true"
    # Frame-count consensus: alarm only after N detections in the zone.
    # Pairs with PROCESS_FPS — 3 frames @ 2fps ≈ 1.5s of confirmed presence.
    INTRUSION_MIN_CONSECUTIVE_FRAMES: int = int(os.getenv("INTRUSION_MIN_CONSECUTIVE_FRAMES", "3"))
    INTRUSION_FRAME_GAP_RESET_SEC: float = float(os.getenv("INTRUSION_FRAME_GAP_RESET_SEC", "3.0"))

    # Model
    YOLO_PERSON_MODEL: str = os.getenv("YOLO_PERSON_MODEL", "yolov8n.pt")
    YOLO_PERSON_TRACKER: str = os.getenv("YOLO_PERSON_TRACKER", "bytetrack.yaml")
    USE_GPU_FOR_PERSON: bool = os.getenv("USE_GPU_FOR_PERSON", "false").lower() == "true"

    # Video clip + burst screenshots
    INTRUSION_CLIP_ENABLED: bool = os.getenv("INTRUSION_CLIP_ENABLED", "true").lower() == "true"
    INTRUSION_CLIP_PRE_SEC: int = int(os.getenv("INTRUSION_CLIP_PRE_SEC", "10"))
    INTRUSION_CLIP_POST_SEC: int = int(os.getenv("INTRUSION_CLIP_POST_SEC", "5"))
    INTRUSION_CLIP_DIR: str = os.getenv("INTRUSION_CLIP_DIR", str(BASE_DIR / "static" / "intrusion_clips"))
    INTRUSION_BURST_SCREENSHOTS: int = int(os.getenv("INTRUSION_BURST_SCREENSHOTS", "5"))
    INTRUSION_RETENTION_DAYS: int = int(os.getenv("INTRUSION_RETENTION_DAYS", "60"))

    # LPR Engine selection: "fast_alpr" (best), "yolo_easyocr", "easyocr", "mock"
    LPR_ENGINE: str = os.getenv("LPR_ENGINE", "fast_alpr")
    YOLO_WEIGHTS: str = os.getenv("YOLO_WEIGHTS", str(BASE_DIR / "models" / "plate_detector.pt"))

    # Backward compat: USE_YOLO=true maps to yolo_easyocr engine
    USE_YOLO: bool = os.getenv("USE_YOLO", "false").lower() == "true"

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = str(BASE_DIR / "logs")


settings = Settings()
