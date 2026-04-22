"""FastAPI route definitions."""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

import cv2
import numpy as np
from fastapi import APIRouter, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from app.database import is_valid_turkish_plate

if TYPE_CHECKING:
    from app.alarm_manager import AlarmManager
    from app.camera import CameraStream, MockCamera
    from app.database import Database
    from app.plate_detector import BasePlateDetector
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger("gateguard.app")

router = APIRouter()

# These get set by main.py at startup
_db: Database | None = None
_alarm: AlarmManager | None = None
_ws_manager: ConnectionManager | None = None
_camera: CameraStream | MockCamera | None = None
_detector: BasePlateDetector | None = None
_mdb_path: str = ""


def init_routes(
    db: Database,
    alarm: AlarmManager,
    ws_manager: ConnectionManager,
    mdb_path: str,
    camera: CameraStream | MockCamera | None = None,
    detector: BasePlateDetector | None = None,
):
    global _db, _alarm, _ws_manager, _mdb_path, _camera, _detector
    _db = db
    _alarm = alarm
    _ws_manager = ws_manager
    _mdb_path = mdb_path
    _camera = camera
    _detector = detector


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await _ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            logger.debug("WS received: %s", data)
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception:
        _ws_manager.disconnect(websocket)


@router.post("/alarm/off")
async def alarm_off():
    """Silence the active alarm."""
    if _alarm:
        await _alarm.silence_alarm()
    if _ws_manager:
        await _ws_manager.broadcast({"type": "alarm_off", "data": {}})
    return JSONResponse({"status": "ok", "alarm_active": False})


@router.post("/api/sync")
async def trigger_sync():
    """Trigger manual MDB-to-SQLite sync."""
    if not _db:
        return JSONResponse({"errors": ["Database not ready"]}, status_code=503)
    try:
        from app.mdb_sync import sync_mdb_to_sqlite
        result = await sync_mdb_to_sqlite(_mdb_path, _db)
        if _ws_manager:
            await _ws_manager.broadcast({"type": "sync_complete", "data": result})
        return JSONResponse(result)
    except Exception as e:
        logger.exception("MDB sync error")
        return JSONResponse(
            {"total": 0, "new": 0, "updated": 0, "errors": [str(e)], "timestamp": ""},
            status_code=500,
        )


@router.get("/api/passages")
async def get_passages(
    limit: int = 50,
    offset: int = 0,
    start: str | None = None,
    end: str | None = None,
    direction: str | None = None,
    authorized: str | None = None,
    plate: str | None = None,
):
    """Get passage records with optional filters and pagination."""
    if not _db:
        return JSONResponse({"passages": [], "total": 0})
    try:
        auth_bool = None
        if authorized == "true":
            auth_bool = True
        elif authorized == "false":
            auth_bool = False

        passages, total = _db.get_passages_filtered(
            start_date=start,
            end_date=end,
            direction=direction,
            authorized=auth_bool,
            plate_search=plate,
            limit=limit,
            offset=offset,
        )
        return JSONResponse({"passages": passages, "total": total})
    except Exception:
        logger.exception("Passage query failed")
        return JSONResponse({"passages": [], "total": 0}, status_code=500)


@router.get("/api/stats")
async def get_stats(start: str | None = None, end: str | None = None):
    """Get detection statistics, optionally filtered by date range."""
    empty = {"today_total": 0, "today_authorized": 0, "today_unauthorized": 0,
             "today_entries": 0, "today_exits": 0, "auth_rate": 0}
    if not _db:
        return JSONResponse(empty)
    try:
        stats = _db.get_stats(start_date=start, end_date=end)
        return JSONResponse(stats)
    except Exception:
        logger.exception("Stats query failed")
        return JSONResponse(empty)


@router.get("/api/status")
async def get_status():
    """Get system status (camera, alarm, sync, vehicle count)."""
    from config import settings

    camera_connected = False
    if _camera:
        camera_connected = _camera.is_connected

    vehicle_count = 0
    last_sync = None
    if _db:
        try:
            vehicle_count = _db.get_vehicle_count()
            last_sync = _db.get_last_sync()
        except Exception:
            pass

    return JSONResponse({
        "alarm_active": _alarm.alarm_active if _alarm else False,
        "last_alarm_plate": _alarm.last_alarm_plate if _alarm else "",
        "last_sync": last_sync,
        "mock_mode": settings.MOCK_MODE,
        "camera_connected": camera_connected,
        "vehicle_count": vehicle_count,
    })


# ── ALPR Test Endpoint ─────────────────────────────────────

@router.post("/api/test-detect")
async def test_detect(file: UploadFile):
    """Run ALPR detection on an uploaded image. Returns plates + annotated image."""
    if not _detector:
        return JSONResponse({"error": "Detector not initialized"}, status_code=503)

    try:
        # Read uploaded image bytes into numpy array
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return JSONResponse({"error": "Invalid image file"}, status_code=400)

        # Run detection (in thread executor to avoid blocking)
        import asyncio
        loop = asyncio.get_event_loop()
        detections = await loop.run_in_executor(None, _detector.detect, frame)

        # Draw bounding boxes on a copy
        annotated = frame.copy()
        plates = []

        for det in detections:
            valid = is_valid_turkish_plate(det.normalized_plate)
            color = (0, 200, 0) if valid else (0, 140, 255)  # Green if valid, orange if not

            if det.bbox:
                x, y, w, h = det.bbox
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
                # Label above the box
                label = f"{det.normalized_plate} {det.confidence:.0%}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                cv2.rectangle(annotated, (x, y - th - 10), (x + tw + 4, y), color, -1)
                cv2.putText(annotated, label, (x + 2, y - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            plates.append({
                "text": det.plate_text,
                "normalized": det.normalized_plate,
                "confidence": round(det.confidence, 4),
                "valid": valid,
                "bbox": list(det.bbox) if det.bbox else None,
            })

        # Encode annotated image as base64 JPEG
        _, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
        b64_img = base64.b64encode(buffer).decode("utf-8")

        return JSONResponse({
            "plates": plates,
            "annotated_image": f"data:image/jpeg;base64,{b64_img}",
            "filename": file.filename or "unknown",
            "image_size": {"width": frame.shape[1], "height": frame.shape[0]},
        })

    except Exception:
        logger.exception("Test detection failed")
        return JSONResponse({"error": "Detection failed"}, status_code=500)


# ── Shutdown Endpoint ──────────────────────────────────────

@router.post("/api/shutdown")
async def shutdown():
    """Gracefully shut down the server process.

    Called from the UI when the operator wants to close the app.
    Returns immediately, then exits the process after a short delay
    so the HTTP response reaches the browser.
    """
    import os
    import sys
    import threading
    import time

    def _delayed_exit():
        time.sleep(0.8)  # Let the HTTP response complete
        logger.info("Shutdown requested via API — exiting process")
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    return JSONResponse({"status": "shutting_down", "message": "Sunucu kapatılıyor..."})


# ── Camera Test Endpoints ──────────────────────────────────

@router.get("/api/camera/snapshot")
async def camera_snapshot():
    """Return the latest camera frame as JPEG."""
    if not _camera:
        return JSONResponse({"error": "Camera not initialized"}, status_code=503)

    frame = _camera.get_frame()
    if frame is None:
        return JSONResponse({"error": "No frame available"}, status_code=404)

    try:
        success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not success:
            return JSONResponse({"error": "Encoding failed"}, status_code=500)
        return Response(content=buffer.tobytes(), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    except Exception:
        logger.exception("Snapshot failed")
        return JSONResponse({"error": "Snapshot failed"}, status_code=500)


@router.get("/api/camera/test")
async def camera_test():
    """Test RTSP connection and return status + frame info."""
    from config import settings

    result = {
        "rtsp_url": settings.RTSP_URL if not settings.MOCK_MODE else "mock://camera",
        "mock_mode": settings.MOCK_MODE,
        "connected": False,
        "frame_available": False,
        "frame_size": None,
        "error": None,
    }

    if not _camera:
        result["error"] = "Kamera başlatılmamış"
        return JSONResponse(result)

    result["connected"] = _camera.is_connected

    frame = _camera.get_frame()
    if frame is not None:
        result["frame_available"] = True
        result["frame_size"] = {"width": frame.shape[1], "height": frame.shape[0]}
    else:
        result["error"] = "Kameradan henüz görüntü alınamadı (bağlantı kurulmamış olabilir)"

    return JSONResponse(result)
