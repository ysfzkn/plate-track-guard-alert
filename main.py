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
from app.inference_pool import init_pools, get_inference_pool, shutdown_pools
from app.plate_detector import (
    BasePlateDetector, EasyOCRDetector, FastALPRDetector,
    MockPlateDetector, YOLOv8Detector,
)
from app.routes import init_routes, router
from app.websocket_manager import ConnectionManager

# --- Logging setup ---

LOG_DIR = Path(settings.LOG_DIR)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# RotatingFileHandler: max 50 MB per file, keep 5 backups (so total cap ~300 MB).
# Prevents unbounded log growth on long-running deployments.
import time as _time_mod
from logging.handlers import RotatingFileHandler


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that survives Windows multi-process file locks.

    With `uvicorn --reload` (dev) two processes hold the same log file, so the
    rename in doRollover fails with WinError 32 and the stdlib retries it on
    EVERY log line → console flood. Here a failed rollover is swallowed: we
    reopen the stream and back off rollover attempts for a while (the file may
    grow a little past maxBytes — harmless). In single-process production
    (frozen exe, no reload) rollover works normally and this path never runs.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._suppress_until = 0.0

    def shouldRollover(self, record):
        if _time_mod.time() < self._suppress_until:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            self._suppress_until = _time_mod.time() + 300  # back off 5 min
            if self.stream is None:
                try:
                    self.stream = self._open()
                except OSError:
                    pass


_app_handler = SafeRotatingFileHandler(
    LOG_DIR / "app.log", maxBytes=50 * 1024 * 1024, backupCount=5,
    encoding="utf-8",
)
_app_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
))

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        _app_handler,
    ],
)

# Separate (also rotating) loggers for passages, alarms, sync
for log_name, log_file in [
    ("gateguard.passages", "passages.log"),
    ("gateguard.alarms", "alarms.log"),
    ("gateguard.sync", "sync.log"),
    ("gateguard.intrusions", "intrusions.log"),
]:
    _logger = logging.getLogger(log_name)
    _handler = SafeRotatingFileHandler(
        LOG_DIR / log_file, maxBytes=20 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    _logger.addHandler(_handler)

logger = logging.getLogger("gateguard.app")

# Silence watchfiles' per-change INFO spam. It propagates to the root file
# handler, so every "N changes detected" line is itself written to logs/app.log
# — which watchfiles then sees as a new change, logging again: a self-feeding
# loop that floods the console and pins CPU. WARNING level breaks the loop.
logging.getLogger("watchfiles").setLevel(logging.WARNING)

# --- Shared instances ---

db: Database | None = None
camera: CameraStream | MockCamera | None = None
detector: EasyOCRDetector | MockPlateDetector | None = None
alarm: AlarmManager | None = None
engine: DetectionEngine | None = None
ws_manager = ConnectionManager()

# Module 2 (Intrusion) — populated only when ENABLE_INTRUSION_MODULE=true
intrusion_orchestrator = None  # type: ignore[assignment]
zone_manager = None            # type: ignore[assignment]
person_detector = None         # type: ignore[assignment]
intrusion_classifier = None    # type: ignore[assignment]

# Background housekeeper — always started, independent of the intrusion module
retention_sweeper = None       # type: ignore[assignment]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, camera, detector, alarm, engine

    logger.info("=" * 60)
    logger.info("GateGuard starting (mock_mode=%s)", settings.MOCK_MODE)
    logger.info("=" * 60)

    # Shared bounded thread pools for all model inference + disk IO. Created
    # once here so every camera/engine funnels through them instead of the
    # default unbounded executor (critical on a weak CPU — see inference_pool).
    init_pools(
        inference_workers=settings.INFERENCE_WORKERS,
        io_workers=settings.IO_WORKERS,
    )

    # Ensure directories exist
    Path(settings.SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)

    # Initialize database
    db = Database(settings.SQLITE_PATH)
    logger.info("Database initialized: %s", settings.SQLITE_PATH)

    # ── Auth bootstrap: yarat varsayılan admin (yoksa) ──
    from app.auth import init_auth
    init_auth(db, BASE_DIR)

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

    # Initialize plate detector based on LPR_ENGINE config
    engine = settings.LPR_ENGINE if not settings.MOCK_MODE else "mock"
    # Backward compat: USE_YOLO=true overrides to yolo_easyocr
    if settings.USE_YOLO and engine not in ("mock",):
        engine = "yolo_easyocr"

    if engine == "mock":
        detector = MockPlateDetector()
    elif engine == "fast_alpr":
        detector = FastALPRDetector(confidence_threshold=settings.CONFIDENCE_THRESHOLD)
        logger.info("LPR engine: fast-alpr (built-in detector + OCR)")
    elif engine == "yolo_easyocr":
        detector = YOLOv8Detector(
            weights_path=settings.YOLO_WEIGHTS,
            confidence_threshold=settings.CONFIDENCE_THRESHOLD,
        )
        logger.info("LPR engine: YOLOv8 + EasyOCR (%s)", settings.YOLO_WEIGHTS)
    elif engine == "easyocr":
        detector = EasyOCRDetector(confidence_threshold=settings.CONFIDENCE_THRESHOLD)
        logger.info("LPR engine: EasyOCR (contour-based)")
    else:
        logger.warning("Unknown LPR_ENGINE '%s', falling back to fast_alpr", engine)
        detector = FastALPRDetector(confidence_threshold=settings.CONFIDENCE_THRESHOLD)

    # Preload the ALPR model now (fail fast on a low-RAM box) rather than
    # lazily on the first frame. Runs on the inference pool so the model is
    # initialized on the same worker thread that will serve detections.
    if settings.PRELOAD_MODELS and hasattr(detector, "preload"):
        try:
            import asyncio as _aio
            await _aio.get_event_loop().run_in_executor(
                get_inference_pool(), detector.preload,
            )
            logger.info("ALPR model preloaded")
        except Exception:
            logger.exception("ALPR preload failed (will lazy-load on first frame)")

    # Initialize alarm manager
    alarm = AlarmManager(
        esp32_ip=settings.ESP32_IP,
        cooldown_sec=settings.ALARM_COOLDOWN_SEC,
        mock_mode=settings.MOCK_MODE,
        enabled=settings.ALARM_SIREN_ENABLED,
    )

    # Hook alarm manager to ws_manager for hardware-down broadcasts
    alarm.attach_ws_manager(ws_manager)
    await alarm.start_heartbeat()

    # Initialize routes with shared instances (Module 2 globals wired later).
    # Detection engine reference is patched in once it's constructed below.
    init_routes(db, alarm, ws_manager, settings.MDB_PATH, camera, detector)

    # ── Startup validation (run async, never fail boot) ──
    try:
        from app.startup_validator import validate_startup
        await validate_startup(
            db=db,
            rtsp_url=settings.RTSP_URL if not settings.MOCK_MODE else None,
            mdb_path=settings.MDB_PATH,
            esp32_ip=settings.ESP32_IP,
            yolo_model_path=settings.YOLO_PERSON_MODEL,
            base_dir=str(BASE_DIR),
            mock_mode=settings.MOCK_MODE,
            intrusion_enabled=settings.ENABLE_INTRUSION_MODULE,
            siren_enabled=settings.ALARM_SIREN_ENABLED,
        )
    except Exception:
        logger.exception("Startup validator crashed (continuing boot)")

    # Route singletons for Module 2 are patched in after the orchestrator starts
    # (see below, after intrusion module bootstrap).

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
        # Tracker config
        min_frames_for_commit=settings.MIN_FRAMES_FOR_COMMIT,
        track_idle_frames=settings.TRACK_IDLE_FRAMES,
        track_max_duration_sec=settings.TRACK_MAX_DURATION_SEC,
        track_iou_threshold=settings.TRACK_IOU_THRESHOLD,
        track_fuzzy_tolerance=settings.TRACK_FUZZY_TOLERANCE,
        direction_area_ratio=settings.DIRECTION_AREA_RATIO,
        entry_size_change=settings.CAMERA_ENTRY_SIZE_CHANGE,
        entry_y_direction=settings.CAMERA_ENTRY_Y_DIRECTION,
        # Motion gate keys off real frame-to-frame change; the synthetic
        # MockCamera doesn't move enough to satisfy it, so disable in mock mode.
        motion_gate_enabled=settings.PLATE_MOTION_GATE_ENABLED and not settings.MOCK_MODE,
        motion_skip_threshold=settings.PLATE_MOTION_SKIP_THRESHOLD,
        mandatory_detect_every_n=settings.PLATE_MANDATORY_DETECT_EVERY_N,
        passage_dedup_sec=settings.PASSAGE_DEDUP_SEC,
        single_commit_conf=settings.COMMIT_SINGLE_CONF,
    )
    await engine.start()
    # Patch detection engine reference for health endpoint
    init_routes(db, alarm, ws_manager, settings.MDB_PATH, camera, detector,
                detection_engine=engine)

    # Cleanup old screenshots on startup
    try:
        from app.screenshot import cleanup_old_screenshots
        cleanup_old_screenshots(
            settings.SCREENSHOT_DIR,
            retention_days=settings.INTRUSION_RETENTION_DAYS,
        )
    except Exception:
        logger.exception("Screenshot cleanup failed")

    # Background retention sweeper — hourly housekeeping for screenshots, clips
    # and test_outputs. Started unconditionally so photos are purged even when
    # the intrusion module is disabled.
    global retention_sweeper
    try:
        from app.retention import RetentionSweeper
        from config import BASE_DIR as _BD
        retention_sweeper = RetentionSweeper(
            db=db, ws_manager=ws_manager,
            retention_days=settings.INTRUSION_RETENTION_DAYS,
            directories=[
                settings.INTRUSION_CLIP_DIR,
                settings.SCREENSHOT_DIR,
                str(_BD / "static" / "test_outputs"),
            ],
            base_dir=str(_BD),
        )
        await retention_sweeper.start()
    except Exception:
        logger.exception("Retention sweeper failed to start")

    # ── Module 2: Intrusion Detection (optional) ──────────────────
    global intrusion_orchestrator, zone_manager, person_detector, intrusion_classifier
    if settings.ENABLE_INTRUSION_MODULE:
        try:
            from app.intrusion.multi_camera import MultiCameraOrchestrator
            from app.intrusion.zone_manager import ZoneManager
            from app.intrusion.person_detector import PersonDetector
            from app.intrusion.classifier import IntrusionClassifier
            from app.intrusion.video_recorder import cleanup_old_clips

            Path(settings.INTRUSION_CLIP_DIR).mkdir(parents=True, exist_ok=True)

            zone_manager = ZoneManager(
                db,
                night_start=settings.NIGHT_MODE_START,
                night_end=settings.NIGHT_MODE_END,
            )
            person_detector = PersonDetector(
                model_path=settings.YOLO_PERSON_MODEL,
                tracker_config=settings.YOLO_PERSON_TRACKER,
                use_gpu=settings.USE_GPU_FOR_PERSON,   # "true"/"false"/"auto"
                confidence=settings.INTRUSION_CONFIDENCE,
                inference_size=settings.YOLO_INFERENCE_SIZE,
            )
            # Preload the person model too (fail fast; surfaces RSS at boot).
            if settings.PRELOAD_MODELS and hasattr(person_detector, "preload"):
                try:
                    import asyncio as _aio
                    await _aio.get_event_loop().run_in_executor(
                        get_inference_pool(), person_detector.preload,
                    )
                    logger.info("Person model preloaded")
                except Exception:
                    logger.exception("Person preload failed (will lazy-load)")
            intrusion_classifier = IntrusionClassifier(
                zone_manager=zone_manager,
                min_confidence=settings.INTRUSION_CONFIDENCE,
                cooldown_sec=settings.INTRUSION_COOLDOWN_SEC,
                warmup_sec=settings.INTRUSION_WARMUP_SEC,
                min_consecutive_frames=settings.INTRUSION_MIN_CONSECUTIVE_FRAMES,
                frame_gap_reset_sec=settings.INTRUSION_FRAME_GAP_RESET_SEC,
            )
            intrusion_orchestrator = MultiCameraOrchestrator(
                db=db,
                alarm=alarm,
                ws_manager=ws_manager,
                detector=person_detector,
                classifier=intrusion_classifier,
                zone_manager=zone_manager,
                process_fps=settings.INTRUSION_PROCESS_FPS,
                shadow_mode=settings.INTRUSION_SHADOW_MODE,
                screenshot_dir=settings.SCREENSHOT_DIR,
                clip_enabled=settings.INTRUSION_CLIP_ENABLED,
                clip_pre_sec=settings.INTRUSION_CLIP_PRE_SEC,
                clip_post_sec=settings.INTRUSION_CLIP_POST_SEC,
                clip_dir=settings.INTRUSION_CLIP_DIR,
                burst_screenshots=settings.INTRUSION_BURST_SCREENSHOTS,
                motion_fallback_enabled=settings.INTRUSION_MOTION_FALLBACK_ENABLED,
                fps_by_camera=getattr(settings, "INTRUSION_FPS_MAP", {}),
            )
            await intrusion_orchestrator.start()

            # Patch route globals with Module 2 singletons
            init_routes(
                db, alarm, ws_manager, settings.MDB_PATH, camera, detector,
                intrusion_orchestrator=intrusion_orchestrator,
                zone_manager=zone_manager,
                person_detector=person_detector,
            )

            cleanup_old_clips(settings.INTRUSION_CLIP_DIR,
                              retention_days=settings.INTRUSION_RETENTION_DAYS)

            logger.info(
                "Module 2 ready: intrusion module enabled "
                "(shadow=%s, cameras=%d)",
                settings.INTRUSION_SHADOW_MODE,
                len(intrusion_orchestrator.active_camera_ids()),
            )
        except Exception:
            logger.exception("Module 2 (intrusion) failed to start")
            intrusion_orchestrator = None
    else:
        logger.info("Module 2 (intrusion) disabled via ENABLE_INTRUSION_MODULE")

    logger.info("All systems online. Detection active.")

    # Auto-open browser (works in both script and frozen exe)
    import threading
    import webbrowser
    def _open():
        import time
        time.sleep(2)
        webbrowser.open("http://localhost:8000")
    threading.Thread(target=_open, daemon=True).start()

    yield

    # Shutdown
    logger.info("GateGuard shutting down...")
    if retention_sweeper:
        try:
            await retention_sweeper.stop()
        except Exception:
            logger.exception("Error stopping retention sweeper")
    if intrusion_orchestrator:
        try:
            await intrusion_orchestrator.stop_all()
        except Exception:
            logger.exception("Error stopping intrusion orchestrator")
    if engine:
        await engine.stop()
    if camera:
        camera.stop()
    if alarm:
        await alarm.close()
    if db:
        db.close()
    shutdown_pools()
    logger.info("Shutdown complete.")


# --- Resolve paths (works both as script and frozen exe) ---

from config import BASE_DIR

STATIC_DIR = str(BASE_DIR / "static")

# --- FastAPI app ---

app = FastAPI(
    title="GateGuard",
    description="Camera-based unauthorized vehicle detection and physical alarm system",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Auth bootstrap ─────────────────────────────────────────────
# init_auth needs the DB which is created in lifespan, so we wire the
# middleware here but the actual init runs inside lifespan before the
# first request can hit a protected route.
from app.auth import auth_middleware
app.middleware("http")(auth_middleware)

# Mount static files (public — login page needs its own assets)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Include routes
app.include_router(router)


@app.get("/login")
async def login_page():
    return FileResponse(str(BASE_DIR / "static" / "login.html"))


@app.get("/")
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/alpr-test")
async def alpr_test():
    return FileResponse(str(BASE_DIR / "static" / "alpr-test.html"))


@app.get("/camera-test")
async def camera_test_page():
    return FileResponse(str(BASE_DIR / "static" / "camera-test.html"))


# ── Module 2 UI pages ──
@app.get("/night-watch")
async def night_watch_page():
    return FileResponse(str(BASE_DIR / "static" / "night-watch.html"))


@app.get("/intrusion-history")
async def intrusion_history_page():
    return FileResponse(str(BASE_DIR / "static" / "intrusion-history.html"))


@app.get("/zone-editor")
async def zone_editor_page():
    return FileResponse(str(BASE_DIR / "static" / "zone-editor.html"))


@app.get("/admin")
async def admin_page():
    return FileResponse(str(BASE_DIR / "static" / "admin.html"))


@app.get("/cameras")
async def cameras_page():
    # Camera management lives in admin.html; /cameras is the link used in the UI.
    return FileResponse(str(BASE_DIR / "static" / "admin.html"))


@app.get("/intrusion-test")
async def intrusion_test_page():
    return FileResponse(str(BASE_DIR / "static" / "intrusion-test.html"))


@app.get("/test-history")
async def test_history_page():
    return FileResponse(str(BASE_DIR / "static" / "test-history.html"))


@app.get("/zone-playground")
async def zone_playground_page():
    return FileResponse(str(BASE_DIR / "static" / "zone-playground.html"))


if __name__ == "__main__":
    import sys
    import uvicorn
    try:
        # NOTE: --reload is OFF by default. uvicorn's reload supervisor spawns
        # the worker via multiprocessing-spawn on Windows, and that worker can't
        # open OpenCV/FFmpeg RTSP streams (VideoCapture.isOpened() returns False
        # instantly) — so with reload on, the cameras never connect. Verified:
        # reload off → both cameras connect; reload on → both fail immediately.
        # Production (frozen exe) already runs without reload, so it's unaffected.
        # Set DEV_RELOAD=1 to opt back in for code-only work (cameras won't run).
        want_reload = (not getattr(sys, "frozen", False)
                       and os.environ.get("DEV_RELOAD") == "1")
        if want_reload:
            # Code-editing mode (no working cameras). Exclude runtime output dirs
            # so the app's own writes don't trigger a reload storm.
            uvicorn.run(
                "main:app", host="0.0.0.0", port=8000, reload=True,
                reload_includes=["*.py", "*.html"],
                reload_excludes=[
                    "static/test_outputs/*", "static/screenshots/*",
                    "static/intrusion_clips/*", "static/test_kamera_kayitlari/*",
                    "data/*", "logs/*", "*.db", "*.db-*", "*.log", "*.mp4",
                ],
            )
        elif getattr(sys, "frozen", False):
            # Frozen exe: pass the app object directly (string import won't work)
            uvicorn.run(app, host="0.0.0.0", port=8000)
        else:
            # Dev mode WITH working cameras: single process, no reload.
            uvicorn.run(app, host="0.0.0.0", port=8000)
    except Exception as e:
        print(f"\n\n[HATA] {e}\n")
        import traceback
        traceback.print_exc()
        if getattr(sys, "frozen", False):
            input("\nDevam etmek icin Enter'a basin...")
