"""GateGuard — FastAPI entry point.

Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Frozen + console=False (pencereli exe) => sys.stdout/stderr None olur. isatty()
# cagiran kutuphaneler (uvicorn'un renkli log formatter'i gibi) burada patlar:
#   AttributeError: 'NoneType' object has no attribute 'isatty'
# None akislari zararsiz bir cop hedefe yonlendir ki her sey nazikce calissin.
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _devnull
    if sys.stderr is None:
        sys.stderr = _devnull

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

# console=False (pencereli exe) ile sys.stderr None olur → StreamHandler her
# log'da sessizce handleError'a duser. Konsol varsa ekle, yoksa sadece dosya.
_log_handlers = [_app_handler]
if sys.stderr is not None:
    _log_handlers.insert(0, logging.StreamHandler())

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    handlers=_log_handlers,
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
        detector = FastALPRDetector(
            confidence_threshold=settings.CONFIDENCE_THRESHOLD,
            detector_model=settings.ALPR_DETECTOR_MODEL,
            ocr_model=settings.ALPR_OCR_MODEL,
        )
        logger.info("LPR engine: fast-alpr (detector=%s, ocr=%s)",
                    settings.ALPR_DETECTOR_MODEL, settings.ALPR_OCR_MODEL)
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
            # Shadow modu UI'dan degistirilmis olabilir → DB'deki degeri kullan
            # (yoksa .env). Boylece UI'daki secim restart'ta korunur.
            _shadow = settings.INTRUSION_SHADOW_MODE
            try:
                _sv = db.get_setting("intrusion_shadow_mode")
                if _sv is not None:
                    _shadow = (_sv == "1")
            except Exception:
                pass
            intrusion_orchestrator = MultiCameraOrchestrator(
                db=db,
                alarm=alarm,
                ws_manager=ws_manager,
                detector=person_detector,
                classifier=intrusion_classifier,
                zone_manager=zone_manager,
                process_fps=settings.INTRUSION_PROCESS_FPS,
                shadow_mode=_shadow,
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

    # Auto-open browser (works in both script and frozen exe).
    # Native pencere modunda ATLA — pencere zaten localhost'u gösteriyor.
    import threading
    import webbrowser
    def _open():
        import time
        time.sleep(2)
        if os.environ.get("GG_WINDOW_MODE") == "1":
            return
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


# HTML sayfalarını ÖNBELLEĞE ALMA — arayüz güncellemeleri (tüm inline JS dahil)
# tarayıcıda anında görünsün. Aksi halde tarayıcı eski index.html'i cache'ler ve
# yeni özellikler (ses seçenekleri vb.) hard-refresh olmadan gelmez.
@app.middleware("http")
async def _no_cache_html(request, call_next):
    resp = await call_next(request)
    ct = resp.headers.get("content-type", "")
    if ct.startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


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


@app.get("/vehicles")
async def vehicles_page():
    # Tanımlı Araç / Plaka Yönetimi (MDB kişileri + elle eklenen ek plakalar)
    return FileResponse(str(BASE_DIR / "static" / "vehicles.html"))


@app.get("/kilavuz")
async def kilavuz_page():
    # Kullanım kılavuzu — nöbetçiler için sade, resimli rehber (public, statik)
    return FileResponse(str(BASE_DIR / "static" / "kilavuz.html"))


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


def _want_window() -> bool:
    """Native pencere modu mu? frozen exe'de VARSAYILAN AÇIK.
    Kapatmak icin: GG_WINDOW=0  ya da  --server  (servis / headless / oturumsuz
    otomatik baslatma). Zorla acmak icin: GG_WINDOW=1 / --window."""
    import sys as _sys
    if "--server" in _sys.argv or os.environ.get("GG_WINDOW") == "0":
        return False
    if "--window" in _sys.argv or os.environ.get("GG_WINDOW") == "1":
        return True
    return bool(getattr(_sys, "frozen", False))


def _splash_html() -> str:
    """Aninda gosterilen splash. Paketlenmis logo (static/logo.png) varsa base64
    gomulur (sunucu daha ayakta olmadigi icin /static'ten cekilemez)."""
    logo_tag = '<div class="t">GateGuard</div>'
    try:
        from config import BASE_DIR as _BD
        import base64
        _p = _BD / "static" / "logo.png"
        if _p.exists():
            b64 = base64.b64encode(_p.read_bytes()).decode("ascii")
            logo_tag = (f'<img src="data:image/png;base64,{b64}" '
                        f'style="max-width:260px;max-height:80px;object-fit:contain">')
    except Exception:
        pass
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
 html,body{{margin:0;height:100%;background:#0b1220;color:#e2e8f0;
   font-family:'Segoe UI',Arial,sans-serif;display:flex;align-items:center;
   justify-content:center;flex-direction:column;gap:22px;overflow:hidden}}
 .sp{{width:54px;height:54px;border:5px solid #1e293b;border-top-color:#22d3ee;
   border-radius:50%;animation:s .9s linear infinite}}
 @keyframes s{{to{{transform:rotate(360deg)}}}}
 .s{{font-size:13px;color:#94a3b8}}
</style></head><body>
 {logo_tag}
 <div class="sp"></div>
 <div class="s">Başlatılıyor, lütfen bekleyin…</div>
</body></html>"""


def _wait_server_up(timeout_steps: int = 120) -> None:
    import time as _time
    import urllib.request
    for _ in range(timeout_steps):
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/login", timeout=1)
            return
        except Exception:
            _time.sleep(0.5)


def _window_state_path():
    from config import BASE_DIR as _BD
    return _BD / "data" / "window_state.json"


def _load_window_state():
    """Kayitli pencere boyut/konumunu dondur (yoksa makul varsayilan)."""
    import json
    default = {"width": 1360, "height": 860, "x": None, "y": None, "maximized": False}
    try:
        p = _window_state_path()
        if p.exists():
            saved = json.loads(p.read_text(encoding="utf-8"))
            for k in default:
                if k in saved:
                    default[k] = saved[k]
            # Bozuk/absürt degerleri temizle
            if not (600 <= (default["width"] or 0) <= 8000):
                default["width"] = 1360
            if not (400 <= (default["height"] or 0) <= 8000):
                default["height"] = 860
    except Exception:
        pass
    return default


def _save_window_state(state: dict) -> None:
    import json
    try:
        p = _window_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _start_tray(window, on_quit):
    """Sistem tepsisi (tray) ikonu — kapatinca uygulama tepside kalir (7/24 guard
    PC icin ideal). pystray yoksa None doner ve X = tam cikis olur (regresyon yok).
    Dondugu icon nesnesi uzerinden .stop() ile kapatilir."""
    try:
        import pystray
        from PIL import Image
        from config import BASE_DIR as _BD
    except Exception as e:
        logger.info("Tray devre disi (pystray/PIL yok: %s) - X = tam cikis", e)
        return None

    try:
        ico = _BD / "static" / "favicon.ico"
        image = Image.open(str(ico)) if ico.exists() else Image.new("RGB", (64, 64), "#0b1220")
    except Exception:
        image = Image.new("RGB", (64, 64), "#0b1220")

    def _show(icon, item):
        try:
            window.show()
            window.restore()
        except Exception:
            pass

    def _quit(icon, item):
        try:
            icon.stop()
        except Exception:
            pass
        on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("GateGuard'ı Göster", _show, default=True),
        pystray.MenuItem("Çıkış", _quit),
    )
    icon = pystray.Icon("GateGuard", image, "GateGuard", menu)
    import threading
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


def _run_window_mode():
    """Sunucuyu arka plan thread'inde calistir; ana thread'de ANINDA bir splash
    penceresi ac, sunucu hazir olunca uygulamaya gec (Electron benzeri deneyim).
    Pencere boyutu hatirlanir; X ile kapatinca sistem tepsisine kucululur.
    Pencere acilamazsa (webview yok / GUI oturumu yok) sunucu moduna duser."""
    import threading
    import time as _time
    import uvicorn
    os.environ["GG_WINDOW_MODE"] = "1"   # lifespan tarayici acmasin

    # uvicorn.Server — sinyal isleyicileri KAPALI (non-main thread'de patlar).
    # log_config=None: uvicorn kendi renkli formatter'ini KURMASIN (console=False
    # exe'de stdout/stderr yok -> isatty patlar). Loglar bizim dosya handler'imiza
    # (logs/app.log) propagate olur.
    _config = uvicorn.Config(app, host="0.0.0.0", port=8000,
                             log_level="info", log_config=None)
    _server = uvicorn.Server(_config)
    _server.install_signal_handlers = lambda: None
    threading.Thread(target=_server.run, daemon=True).start()

    def _fallback_browser():
        os.environ.pop("GG_WINDOW_MODE", None)
        _wait_server_up()
        try:
            import webbrowser
            webbrowser.open("http://localhost:8000")
        except Exception:
            pass
        while True:
            _time.sleep(3600)

    try:
        import webview
    except Exception as e:
        logger.warning("pywebview yok (%s) - sunucu modu + tarayici", e)
        _fallback_browser()
        return

    st = _load_window_state()
    _geom = {"width": st["width"], "height": st["height"], "x": st["x"], "y": st["y"]}
    _quitting = {"v": False}

    try:
        window = webview.create_window(
            "GateGuard", html=_splash_html(),
            width=st["width"], height=st["height"],
            x=st["x"], y=st["y"], min_size=(1024, 640),
        )

        # Boyut/konum degisikliklerini izle (kapaninca kaydetmek icin)
        def _on_resized(w, h):
            _geom["width"], _geom["height"] = int(w), int(h)

        def _on_moved(x, y):
            _geom["x"], _geom["y"] = int(x), int(y)

        try:
            window.events.resized += _on_resized
            window.events.moved += _on_moved
        except Exception:
            pass  # bazi backend'lerde event yoksa sorun degil

        def _real_quit():
            _quitting["v"] = True
            _save_window_state(_geom)
            try:
                window.destroy()
            except Exception:
                pass

        tray = _start_tray(window, _real_quit)

        def _on_closing():
            # Tray varsa X = tepsiye kucult (kapatma). Yoksa normal cikis.
            if tray is not None and not _quitting["v"]:
                _save_window_state(_geom)
                try:
                    window.hide()
                except Exception:
                    pass
                return False   # kapatmayi iptal et
            _save_window_state(_geom)
            return True

        try:
            window.events.closing += _on_closing
        except Exception:
            pass

        def _loader():
            # Splash gorunurken sunucuyu bekle, hazir olunca uygulamaya gec.
            _wait_server_up()
            try:
                window.load_url("http://localhost:8000")
            except Exception:
                pass

        webview.start(_loader)   # bloklar; splash aninda gelir, sonra app yuklenir
        _save_window_state(_geom)
        os._exit(0)              # pencere tamamen kapandi -> uygulamayi kapat
    except Exception as e:
        logger.warning("Native pencere acilamadi (%s) - sunucu modu + tarayici", e)
        _fallback_browser()


if __name__ == "__main__":
    import sys
    import uvicorn
    try:
        if _want_window():
            _run_window_mode()
            sys.exit(0)
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
            # Frozen exe: pass the app object directly (string import won't work).
            # log_config=None: console=False'ta uvicorn'un renkli formatter'i
            # stdout/stderr'e (None) dokunmasin; loglar app.log'a propagate olur.
            uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
        else:
            # Dev mode WITH working cameras: single process, no reload.
            uvicorn.run(app, host="0.0.0.0", port=8000)
    except Exception as e:
        import traceback
        _err = f"{e}\n\n{traceback.format_exc()}"
        try:
            logger.exception("Baslatma hatasi")
        except Exception:
            pass
        # console=False (pencereli) exe'de print/input calismaz → native hata kutusu.
        if getattr(sys, "frozen", False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, _err[:1200], "GateGuard - Baslatma Hatasi", 0x10)
            except Exception:
                try:
                    print(f"\n\n[HATA] {_err}\n")
                    input("\nDevam etmek icin Enter'a basin...")
                except Exception:
                    pass
        else:
            print(f"\n\n[HATA] {_err}\n")
