"""GateGuard — FastAPI entry point.

Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from app.alarm_manager import AlarmManager
from app.camera import CameraStream, MockCamera
from app.database import Database
from app.detection_engine import DetectionEngine
from app.plate_detector import EasyOCRDetector, MockPlateDetector
from app.routes import init_routes, router
from app.websocket_manager import ConnectionManager

# --- Logging setup ---

LOG_DIR = Path(settings.LOG_DIR)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"),
    ],
)

# Separate loggers for passages and alarms
for log_name, log_file in [("gateguard.passages", "passages.log"), ("gateguard.alarms", "alarms.log"), ("gateguard.sync", "sync.log")]:
    _logger = logging.getLogger(log_name)
    _handler = logging.FileHandler(LOG_DIR / log_file, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    _logger.addHandler(_handler)

logger = logging.getLogger("gateguard.app")

# --- Shared instances ---

db: Database | None = None
camera: CameraStream | MockCamera | None = None
detector: EasyOCRDetector | MockPlateDetector | None = None
alarm: AlarmManager | None = None
engine: DetectionEngine | None = None
ws_manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, camera, detector, alarm, engine

    logger.info("=" * 60)
    logger.info("GateGuard starting (mock_mode=%s)", settings.MOCK_MODE)
    logger.info("=" * 60)

    # Ensure directories exist
    Path(settings.SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)

    # Initialize database
    db = Database(settings.SQLITE_PATH)
    logger.info("Database initialized: %s", settings.SQLITE_PATH)

    # MDB sync on startup
    if not settings.MOCK_MODE:
        try:
            from app.mdb_sync import sync_mdb_to_sqlite
            result = await sync_mdb_to_sqlite(settings.MDB_PATH, db)
            logger.info("Startup sync: %s", result)
        except Exception:
            logger.exception("Startup MDB sync failed (continuing with existing data)")
    else:
        # In mock mode, load sample authorized plates into DB
        from app.camera import AUTHORIZED_PLATES
        from app.models import Vehicle
        from app.database import normalize_plate
        mock_vehicles = []
        for i, plate in enumerate(AUTHORIZED_PLATES):
            mock_vehicles.append(Vehicle(
                moonwell_id=1000 + i,
                plate=plate,
                plate_normalized=normalize_plate(plate),
                owner_name=f"Mock User {i+1}",
                block_no=str((i % 5) + 1),
                apartment=str((i * 3) + 1),
            ))
        db.upsert_vehicles(mock_vehicles)
        logger.info("Mock mode: loaded %d authorized plates", len(mock_vehicles))

    # Initialize camera
    if settings.MOCK_MODE:
        camera = MockCamera()
    else:
        camera = CameraStream(settings.RTSP_URL)
    camera.start()

    # Initialize plate detector
    if settings.MOCK_MODE:
        detector = MockPlateDetector()
    else:
        detector = EasyOCRDetector(confidence_threshold=settings.CONFIDENCE_THRESHOLD)

    # Initialize alarm manager
    alarm = AlarmManager(
        esp32_ip=settings.ESP32_IP,
        cooldown_sec=settings.ALARM_COOLDOWN_SEC,
        mock_mode=settings.MOCK_MODE,
    )

    # Initialize routes with shared instances
    init_routes(db, alarm, ws_manager, settings.MDB_PATH)

    # Start detection engine
    engine = DetectionEngine(
        camera=camera,
        detector=detector,
        db=db,
        alarm=alarm,
        ws_manager=ws_manager,
        process_fps=settings.PROCESS_FPS,
        fuzzy_tolerance=settings.FUZZY_TOLERANCE,
        screenshot_dir=settings.SCREENSHOT_DIR,
    )
    await engine.start()

    logger.info("All systems online. Monitoring active.")

    yield

    # Shutdown
    logger.info("GateGuard shutting down...")
    if engine:
        await engine.stop()
    if camera:
        camera.stop()
    if alarm:
        await alarm.close()
    if db:
        db.close()
    logger.info("Shutdown complete.")


# --- FastAPI app ---

app = FastAPI(
    title="GateGuard",
    description="Kacak Arac Tespit ve Fiziksel Alarm Sistemi",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routes
app.include_router(router)


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
