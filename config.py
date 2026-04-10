"""Application configuration loaded from .env file."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


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
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() == "true"
    PROCESS_FPS: int = int(os.getenv("PROCESS_FPS", "2"))
    CONFIDENCE_THRESHOLD: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.4"))
    ALARM_COOLDOWN_SEC: int = int(os.getenv("ALARM_COOLDOWN_SEC", "60"))
    FUZZY_TOLERANCE: int = int(os.getenv("FUZZY_TOLERANCE", "1"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = str(BASE_DIR / "logs")


settings = Settings()
