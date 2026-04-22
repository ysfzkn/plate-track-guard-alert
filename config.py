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
    CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.4"))
    ALARM_COOLDOWN_SEC: int = int(os.getenv("ALARM_COOLDOWN_SEC", "60"))
    FUZZY_TOLERANCE: int = int(os.getenv("FUZZY_TOLERANCE", "1"))

    # Direction detection: which frame movement direction means "entry"
    # "down" = plate moving downward in frame = vehicle approaching = entry (default)
    # "up"   = plate moving upward in frame = vehicle approaching = entry
    CAMERA_ENTRY_DIRECTION: str = os.getenv("CAMERA_ENTRY_DIRECTION", "down")

    # LPR Engine selection: "fast_alpr" (best), "yolo_easyocr", "easyocr", "mock"
    LPR_ENGINE: str = os.getenv("LPR_ENGINE", "fast_alpr")
    YOLO_WEIGHTS: str = os.getenv("YOLO_WEIGHTS", str(BASE_DIR / "models" / "plate_detector.pt"))

    # Backward compat: USE_YOLO=true maps to yolo_easyocr engine
    USE_YOLO: bool = os.getenv("USE_YOLO", "false").lower() == "true"

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = str(BASE_DIR / "logs")


settings = Settings()
