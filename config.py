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
    # Physical siren is OPTIONAL. When disabled, the system runs full software
    # detection (passages, intrusion events, UI alerts) but makes NO ESP32 calls
    # and shows NO "siren down" warning — no hardware is expected.
    ALARM_SIREN_ENABLED: bool = os.getenv("ALARM_SIREN_ENABLED", "true").lower() == "true"

    # Database
    # MDB_PATH may be a LOCAL path or a UNC network path to the Moonwell DB on
    # another PC, e.g.  \\MOONWELL-PC\paylasim\moonwell\MW305_DB200.mdb
    MDB_PATH: str = os.getenv("MDB_PATH", str(BASE_DIR / "moonwel_db" / "MW305_DB200.mdb"))
    # When true (default) the .mdb is copied to a local temp file before the
    # Access driver opens it. This is the reliable way to read a Moonwell DB that
    # lives on another networked PC: it avoids the Access lock-file (.ldb) needing
    # write access on the share and survives brief network drops mid-read.
    MDB_COPY_LOCAL: bool = os.getenv("MDB_COPY_LOCAL", "true").lower() == "true"
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
    # Per-camera FPS override, e.g. "1:2,9:1,12:1" (camera_id:fps). Lets
    # vegetation/low-priority cameras run slower than the global default to
    # stay within the single-worker CPU budget. Malformed entries are ignored.
    INTRUSION_FPS_BY_CAMERA: str = os.getenv("INTRUSION_FPS_BY_CAMERA", "")
    INTRUSION_WARMUP_SEC: int = int(os.getenv("INTRUSION_WARMUP_SEC", "30"))
    INTRUSION_SHADOW_MODE: bool = os.getenv("INTRUSION_SHADOW_MODE", "true").lower() == "true"
    # Frame-count consensus: alarm only after N detections in the zone.
    # Pairs with PROCESS_FPS — 3 frames @ 2fps ≈ 1.5s of confirmed presence.
    INTRUSION_MIN_CONSECUTIVE_FRAMES: int = int(os.getenv("INTRUSION_MIN_CONSECUTIVE_FRAMES", "1"))
    # 10 sec — ağaç/araç arkasından geçen kişi sayaç sıfırlamadan devam etsin
    # (alarm cooldown zaten ayrı katmanda tekrar-tetiklemeyi engelliyor)
    INTRUSION_FRAME_GAP_RESET_SEC: float = float(os.getenv("INTRUSION_FRAME_GAP_RESET_SEC", "10.0"))

    # Model
    YOLO_PERSON_MODEL: str = os.getenv("YOLO_PERSON_MODEL", "yolov8n.pt")
    YOLO_PERSON_TRACKER: str = os.getenv("YOLO_PERSON_TRACKER", "bytetrack.yaml")
    # GPU usage: "true"/"false" or "auto" (auto-detect CUDA, fall back to CPU)
    USE_GPU_FOR_PERSON: str = os.getenv("USE_GPU_FOR_PERSON", "auto").lower()
    # Frame down-sampling for inference (long edge in pixels). Lower = faster
    # but may miss far-away subjects. 640 is YOLOv8's native size; on a CPU-only
    # box CPU cost scales ~quadratically, so 416 (~2.4x faster) is the low-spec
    # default. Drop to 320 only as a last resort (hurts recall on distant people).
    YOLO_INFERENCE_SIZE: int = int(os.getenv("YOLO_INFERENCE_SIZE", "416"))

    # Video clip + burst screenshots
    INTRUSION_CLIP_ENABLED: bool = os.getenv("INTRUSION_CLIP_ENABLED", "true").lower() == "true"
    # Pre/post buffer in seconds. Lower defaults on low-RAM boxes — the ring
    # buffer holds pre_sec*fps frames PER CAMERA (now JPEG-encoded, see below).
    INTRUSION_CLIP_PRE_SEC: int = int(os.getenv("INTRUSION_CLIP_PRE_SEC", "6"))
    INTRUSION_CLIP_POST_SEC: int = int(os.getenv("INTRUSION_CLIP_POST_SEC", "4"))
    INTRUSION_CLIP_DIR: str = os.getenv("INTRUSION_CLIP_DIR", str(BASE_DIR / "static" / "intrusion_clips"))
    INTRUSION_BURST_SCREENSHOTS: int = int(os.getenv("INTRUSION_BURST_SCREENSHOTS", "5"))
    # Retention window for saved evidence — screenshots, intrusion clips, and
    # test_outputs. Files older than this are auto-deleted by RetentionSweeper
    # (hourly) and on startup. Default 7 days to keep disk usage bounded on the
    # low-spec guardhouse box.
    INTRUSION_RETENTION_DAYS: int = int(os.getenv("INTRUSION_RETENTION_DAYS", "7"))
    # Ring-buffer frames are JPEG-encoded (and downscaled to max_width) to keep
    # RAM low. Quality 70 is visually fine for evidence; 960px is plenty.
    INTRUSION_CLIP_JPEG_QUALITY: int = int(os.getenv("INTRUSION_CLIP_JPEG_QUALITY", "70"))
    INTRUSION_CLIP_MAX_WIDTH: int = int(os.getenv("INTRUSION_CLIP_MAX_WIDTH", "960"))

    # LPR Engine selection: "fast_alpr" (best), "yolo_easyocr", "easyocr", "mock"
    LPR_ENGINE: str = os.getenv("LPR_ENGINE", "fast_alpr")
    YOLO_WEIGHTS: str = os.getenv("YOLO_WEIGHTS", str(BASE_DIR / "models" / "plate_detector.pt"))

    # fast-alpr models. The detector's INPUT RESOLUTION drives how tightly it
    # localizes the plate — a low-res detector can crop an edge character (e.g.
    # reading "34GPF89" instead of "34GPF891"). Options (low→high res / cost):
    #   yolo-v9-t-256/384/416/512/640-license-plate-end2end  (tiny)
    #   yolo-v9-s-608-license-plate-end2end                  (small, most accurate)
    # Default 512: clearly better boxes than 384 at modest CPU cost on the single
    # plate stream. On the GPU build bump to 640 or s-608.
    ALPR_DETECTOR_MODEL: str = os.getenv(
        "ALPR_DETECTOR_MODEL", "yolo-v9-t-512-license-plate-end2end")
    ALPR_OCR_MODEL: str = os.getenv("ALPR_OCR_MODEL", "cct-s-v2-global-model")

    # Backward compat: USE_YOLO=true maps to yolo_easyocr engine
    USE_YOLO: bool = os.getenv("USE_YOLO", "false").lower() == "true"

    # ── Performance / low-spec CPU tuning ────────────────────────
    # Shared bounded thread pools (see app/inference_pool.py).
    # INFERENCE_WORKERS=1: PersonDetector already serializes YOLO behind one
    # lock and ONNX intra-op threads give the real parallelism — extra workers
    # just thrash a weak CPU. Bump on 6+ physical cores.
    INFERENCE_WORKERS: int = int(os.getenv("INFERENCE_WORKERS", "1"))
    IO_WORKERS: int = int(os.getenv("IO_WORKERS", "2"))

    # ONNX Runtime / BLAS thread counts. intra-op = threads within one op;
    # 3 on a 4-core box leaves a core for the event loop + camera threads.
    ONNX_INTRA_OP_THREADS: int = int(os.getenv("ONNX_INTRA_OP_THREADS", "3"))
    ONNX_INTER_OP_THREADS: int = int(os.getenv("ONNX_INTER_OP_THREADS", "1"))

    # Preload heavy ML models at startup (fail fast on missing weights / OOM)
    # instead of lazily on the first frame.
    PRELOAD_MODELS: bool = os.getenv("PRELOAD_MODELS", "true").lower() == "true"

    # Motion-gate the PLATE pipeline (mirror of the intrusion engine's skip):
    # skip ALPR on essentially-static frames, but force a detection every Nth
    # frame so slow-approaching vehicles / lighting drift are never missed.
    PLATE_MOTION_GATE_ENABLED: bool = os.getenv("PLATE_MOTION_GATE_ENABLED", "true").lower() == "true"
    PLATE_MOTION_SKIP_THRESHOLD: float = float(os.getenv("PLATE_MOTION_SKIP_THRESHOLD", "0.005"))
    PLATE_MANDATORY_DETECT_EVERY_N: int = int(os.getenv("PLATE_MANDATORY_DETECT_EVERY_N", "10"))

    # Motion fallback human-likeness: score on NET displacement (start->end)
    # plus a directionality term, instead of accumulated path length (which
    # inflated jittery in-place blobs into false positives).
    MOTION_USE_NET_DISPLACEMENT: bool = os.getenv("MOTION_USE_NET_DISPLACEMENT", "true").lower() == "true"

    # Zone membership anchor (see zone_manager.anchor_points):
    #   "foot_or_center" (default): match if the foot OR the body-center is in
    #      the zone — catches zones drawn on the GROUND (foot) AND on a WALL/DOOR
    #      feature (center), while beyond-fence people (both points outside) stay
    #      out. Best for the "just draw the forbidden spot" workflow.
    #   "foot": ground contact only (strict). "center": legacy bbox center.
    ZONE_ANCHOR: str = os.getenv("ZONE_ANCHOR", "foot_or_center").lower()

    # Passage dedup window (sec): one vehicle that fragments into several short
    # tracks (ramp motion + OCR variance like 34GDZ11 vs 34GDZ114) is logged as
    # ONE passage. A fuzzy-matching plate committed within this window is merged
    # into the existing record (keeping the most confident, definite-direction
    # reading). A genuine re-entry after the window counts as a new passage.
    PASSAGE_DEDUP_SEC: float = float(os.getenv("PASSAGE_DEDUP_SEC", "30"))

    # Fast-pass recovery: a vehicle crossing quickly (esp. at higher speed) may
    # be readable in only ONE frame, below MIN_FRAMES_FOR_COMMIT. A single
    # reading at >= this confidence is trusted enough to commit alone. Set high
    # (0.90) so low-confidence night "guesses" (inconsistent ~0.5-0.6 reads)
    # are NOT committed as garbage plates. Lower only with caution.
    COMMIT_SINGLE_CONF: float = float(os.getenv("COMMIT_SINGLE_CONF", "0.90"))

    # Module 2 master switch for the MOG2 motion fallback. Default OFF: it is
    # the highest false-positive component (fires on swaying vegetation/shadows
    # that YOLO correctly ignores) and adds an extra inference per frame. Enable
    # only for specific low-vegetation wall/climb zones (per-zone flag).
    INTRUSION_MOTION_FALLBACK_ENABLED: bool = os.getenv("INTRUSION_MOTION_FALLBACK_ENABLED", "false").lower() == "true"

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = str(BASE_DIR / "logs")


settings = Settings()


def _parse_fps_map(s: str) -> dict:
    """Parse "1:2,9:1" -> {1:2, 9:1}. Malformed parts are skipped (fail-safe)."""
    out: dict = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        cid, fps = part.split(":", 1)
        try:
            out[int(cid)] = max(1, int(fps))
        except ValueError:
            continue
    return out


# Parsed per-camera FPS map (camera_id -> fps); empty means use the global FPS.
settings.INTRUSION_FPS_MAP = _parse_fps_map(settings.INTRUSION_FPS_BY_CAMERA)

# ── Thread-environment setup (MUST run before onnxruntime/torch import) ──
# These env vars are read once when the native libs initialize, so set them
# here in config — the first import in every module — before any detector
# pulls in onnxruntime or torch. Prevents spin-wait threads from pinning a
# whole core and caps BLAS/OpenMP fan-out on a small CPU.
os.environ.setdefault("OMP_NUM_THREADS", str(settings.ONNX_INTRA_OP_THREADS))
os.environ.setdefault("OPENBLAS_NUM_THREADS", str(settings.ONNX_INTRA_OP_THREADS))
os.environ.setdefault("MKL_NUM_THREADS", str(settings.ONNX_INTRA_OP_THREADS))
os.environ.setdefault("ORT_DISABLE_SPINNING", "1")

# ── RTSP reliability (MUST run before cv2/FFmpeg opens any stream) ──
# Many "camera connects but no image" cases are UDP packet loss or a slow first
# frame on a high-res main stream. Forcing TCP transport + a socket timeout
# fixes most of them. Override via OPENCV_FFMPEG_CAPTURE_OPTIONS in .env if a
# camera needs different options.
# NOTE: the RTSP socket-timeout option is "timeout" on FFmpeg 5.0+ (in µs); the
# old name "stimeout" is unrecognized on recent builds, which makes FFmpeg
# refuse the *whole* options string and the stream hangs forever at "connecting".
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|timeout;8000000|max_delay;500000",
)
