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
    from app.intrusion.multi_camera import MultiCameraOrchestrator
    from app.intrusion.person_detector import PersonDetector
    from app.intrusion.zone_manager import ZoneManager
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

# Module 2 singletons (None when ENABLE_INTRUSION_MODULE=false)
_intrusion_orchestrator: MultiCameraOrchestrator | None = None
_zone_manager: ZoneManager | None = None
_person_detector: PersonDetector | None = None


def init_routes(
    db: Database,
    alarm: AlarmManager,
    ws_manager: ConnectionManager,
    mdb_path: str,
    camera: CameraStream | MockCamera | None = None,
    detector: BasePlateDetector | None = None,
    intrusion_orchestrator=None,
    zone_manager=None,
    person_detector=None,
):
    global _db, _alarm, _ws_manager, _mdb_path, _camera, _detector
    global _intrusion_orchestrator, _zone_manager, _person_detector
    _db = db
    _alarm = alarm
    _ws_manager = ws_manager
    _mdb_path = mdb_path
    _camera = camera
    _detector = detector
    _intrusion_orchestrator = intrusion_orchestrator
    _zone_manager = zone_manager
    _person_detector = person_detector


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


# ══════════════════════════════════════════════════════════════
#  Module 2 — Intrusion Detection Endpoints
# ══════════════════════════════════════════════════════════════

from fastapi import HTTPException
from pydantic import BaseModel


class CameraCreateRequest(BaseModel):
    name: str
    rtsp_url: str
    location: str = ""
    role: str = "intrusion"
    enabled: bool = True


class CameraUpdateRequest(BaseModel):
    name: str | None = None
    rtsp_url: str | None = None
    location: str | None = None
    role: str | None = None
    enabled: bool | None = None


class ZoneCreateRequest(BaseModel):
    name: str
    polygon_points: list[list[float]]    # [[x,y], ...] normalized 0..1
    is_night_only: bool = True
    min_loiter_sec: int = 5
    enable_motion_fallback: bool = False


class ZoneUpdateRequest(BaseModel):
    name: str | None = None
    polygon_points: list[list[float]] | None = None
    is_night_only: bool | None = None
    min_loiter_sec: int | None = None
    enabled: bool | None = None
    enable_motion_fallback: bool | None = None


# ── Camera CRUD ─────────────────────────────────────────────

@router.get("/api/cameras")
async def list_cameras():
    """List all registered cameras."""
    if not _db:
        return JSONResponse([])
    rows = _db.list_cameras()
    # Mask credentials in RTSP URL for response
    from app.intrusion.models import _mask_rtsp_credentials
    safe = []
    for r in rows:
        r = dict(r)
        r["rtsp_url_masked"] = _mask_rtsp_credentials(r.get("rtsp_url", ""))
        r["is_running"] = _intrusion_orchestrator.is_running(r["id"]) if _intrusion_orchestrator else False
        safe.append(r)
    return JSONResponse(safe)


@router.post("/api/cameras")
async def create_camera(req: CameraCreateRequest):
    """Add a new camera. If module 2 is running and camera is intrusion,
    a new engine is spawned at runtime (no restart needed)."""
    if not _db:
        raise HTTPException(503, "Database not ready")
    cid = _db.add_camera(
        name=req.name, rtsp_url=req.rtsp_url, location=req.location,
        role=req.role, enabled=req.enabled,
    )
    if _intrusion_orchestrator and req.enabled and req.role in ("intrusion", "both"):
        from app.intrusion.models import Camera
        row = _db.get_camera(cid)
        if row:
            await _intrusion_orchestrator.add_camera(Camera.from_row(row))
    return JSONResponse({"id": cid, "status": "created"})


@router.put("/api/cameras/{camera_id}")
async def update_camera(camera_id: int, req: CameraUpdateRequest):
    if not _db:
        raise HTTPException(503, "Database not ready")
    existing = _db.get_camera(camera_id)
    if not existing:
        raise HTTPException(404, "Camera not found")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        return JSONResponse({"status": "no_changes"})
    _db.update_camera(camera_id, **fields)
    # Reload the engine if it's a runtime-relevant change
    if _intrusion_orchestrator:
        if "rtsp_url" in fields or "enabled" in fields or "role" in fields:
            await _intrusion_orchestrator.reload_camera(camera_id)
    return JSONResponse({"status": "updated"})


@router.delete("/api/cameras/{camera_id}")
async def delete_camera(camera_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    if _intrusion_orchestrator:
        await _intrusion_orchestrator.remove_camera(camera_id)
    deleted = _db.delete_camera(camera_id)
    if not deleted:
        raise HTTPException(404, "Camera not found")
    return JSONResponse({"status": "deleted"})


@router.get("/api/cameras/{camera_id}/snapshot")
async def camera_snapshot_by_id(camera_id: int):
    """Live JPEG snapshot from a specific intrusion camera."""
    if not _intrusion_orchestrator:
        raise HTTPException(503, "Intrusion module not enabled")
    stream = _intrusion_orchestrator.get_stream(camera_id)
    if stream is None:
        raise HTTPException(404, "Camera not active")
    frame = stream.get_frame()
    if frame is None:
        raise HTTPException(404, "No frame available")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise HTTPException(500, "Encoding failed")
    return Response(content=buf.tobytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/cameras/{camera_id}/test")
async def test_camera_connection(camera_id: int):
    """Test whether a camera's RTSP URL is reachable. Returns snapshot + status."""
    if not _db:
        raise HTTPException(503, "Database not ready")
    row = _db.get_camera(camera_id)
    if not row:
        raise HTTPException(404, "Camera not found")

    result = {
        "camera_id": camera_id,
        "connected": False,
        "frame_size": None,
        "error": None,
    }

    # If orchestrator already has a stream, reuse it
    stream = _intrusion_orchestrator.get_stream(camera_id) if _intrusion_orchestrator else None
    if stream is not None:
        result["connected"] = stream.is_connected
        frame = stream.get_frame()
        if frame is not None:
            result["frame_size"] = {"width": frame.shape[1], "height": frame.shape[0]}
        else:
            result["error"] = "No frame yet (connecting...)"
        return JSONResponse(result)

    # Otherwise create a throwaway test stream
    from app.camera import CameraStream
    import asyncio as _asyncio
    test = CameraStream(row["rtsp_url"])
    test.start()
    try:
        # Wait up to 5 seconds for a frame
        for _ in range(50):
            if test.is_connected and test.get_frame() is not None:
                break
            await _asyncio.sleep(0.1)
        result["connected"] = test.is_connected
        frame = test.get_frame()
        if frame is not None:
            result["frame_size"] = {"width": frame.shape[1], "height": frame.shape[0]}
        else:
            result["error"] = "Could not get frame within 5s"
    finally:
        test.stop()
    return JSONResponse(result)


# ── Zone CRUD ───────────────────────────────────────────────

@router.get("/api/cameras/{camera_id}/zones")
async def list_zones(camera_id: int):
    if not _db:
        return JSONResponse([])
    rows = _db.list_zones_for_camera(camera_id)
    # Parse polygon_points JSON for each
    import json as _json
    for r in rows:
        try:
            r["polygon_points"] = _json.loads(r["polygon_points"])
        except (TypeError, ValueError):
            r["polygon_points"] = []
    return JSONResponse(rows)


@router.post("/api/cameras/{camera_id}/zones")
async def create_zone(camera_id: int, req: ZoneCreateRequest):
    if not _db:
        raise HTTPException(503, "Database not ready")
    if len(req.polygon_points) < 3:
        raise HTTPException(400, "Polygon must have at least 3 points")
    # Validate all points are in 0..1 range
    for pt in req.polygon_points:
        if len(pt) != 2 or not (0 <= pt[0] <= 1 and 0 <= pt[1] <= 1):
            raise HTTPException(400, "Polygon points must be normalized [0,1]")
    import json as _json
    poly_json = _json.dumps([[float(x), float(y)] for x, y in req.polygon_points])
    zid = _db.add_zone(
        camera_id=camera_id, name=req.name, polygon_points=poly_json,
        is_night_only=req.is_night_only, min_loiter_sec=req.min_loiter_sec,
        enable_motion_fallback=req.enable_motion_fallback,
    )
    if _zone_manager:
        _zone_manager.invalidate()
    return JSONResponse({"id": zid, "status": "created"})


@router.put("/api/zones/{zone_id}")
async def update_zone(zone_id: int, req: ZoneUpdateRequest):
    if not _db:
        raise HTTPException(503, "Database not ready")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "polygon_points" in fields:
        pts = fields["polygon_points"]
        if len(pts) < 3:
            raise HTTPException(400, "Polygon must have at least 3 points")
        import json as _json
        fields["polygon_points"] = _json.dumps([[float(x), float(y)] for x, y in pts])
    if not fields:
        return JSONResponse({"status": "no_changes"})
    updated = _db.update_zone(zone_id, **fields)
    if not updated:
        raise HTTPException(404, "Zone not found")
    if _zone_manager:
        _zone_manager.invalidate()
    return JSONResponse({"status": "updated"})


@router.delete("/api/zones/{zone_id}")
async def delete_zone(zone_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    deleted = _db.delete_zone(zone_id)
    if not deleted:
        raise HTTPException(404, "Zone not found")
    if _zone_manager:
        _zone_manager.invalidate()
    return JSONResponse({"status": "deleted"})


# ── Intrusion Events ────────────────────────────────────────

@router.get("/api/intrusion/events")
async def list_intrusion_events(
    camera_id: int | None = None,
    zone_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    acknowledged: str | None = None,
    shadow_mode: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    if not _db:
        return JSONResponse({"events": [], "total": 0})
    ack_val = None
    if acknowledged == "true": ack_val = True
    elif acknowledged == "false": ack_val = False
    shadow_val = None
    if shadow_mode == "true": shadow_val = True
    elif shadow_mode == "false": shadow_val = False
    rows, total = _db.list_intrusion_events(
        camera_id=camera_id, zone_id=zone_id,
        start_date=start, end_date=end,
        acknowledged=ack_val, shadow_mode=shadow_val,
        limit=limit, offset=offset,
    )
    return JSONResponse({"events": rows, "total": total})


@router.post("/api/intrusion/events/{event_id}/ack")
async def acknowledge_event(event_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    ok = _db.acknowledge_intrusion_event(event_id)
    if not ok:
        raise HTTPException(404, "Event not found")
    if _ws_manager:
        await _ws_manager.broadcast({"type": "intrusion_ack", "data": {"event_id": event_id}})
    return JSONResponse({"status": "acknowledged"})


# ── Dashboard (combined Module 1 + Module 2 summary) ───────

@router.get("/api/dashboard/summary")
async def dashboard_summary():
    """Unified summary card data for the home page."""
    from config import settings
    from app.intrusion.night_mode import is_night_mode_active

    out = {
        "module1": {
            "enabled": True,
            "today": {"total": 0, "authorized": 0, "unauthorized": 0,
                      "entries": 0, "exits": 0},
            "last_passage": None,
            "camera_connected": _camera.is_connected if _camera else False,
            "vehicle_count": 0,
            "last_sync": None,
            "alarm_active": _alarm.alarm_active if _alarm else False,
        },
        "module2": {
            "enabled": settings.ENABLE_INTRUSION_MODULE,
            "shadow_mode": settings.INTRUSION_SHADOW_MODE,
            "night_mode_active": is_night_mode_active(
                settings.NIGHT_MODE_START, settings.NIGHT_MODE_END,
            ),
            "night_window": f"{settings.NIGHT_MODE_START} - {settings.NIGHT_MODE_END}",
            "today": {"total": 0, "unacknowledged": 0, "shadow": 0},
            "last_intrusion": None,
            "cameras_running": [],
            "cameras_total": 0,
        },
        "system": {
            "mock_mode": settings.MOCK_MODE,
        },
    }

    if _db:
        try:
            stats = _db.get_stats()
            out["module1"]["today"] = {
                "total": stats.get("today_total", 0),
                "authorized": stats.get("today_authorized", 0),
                "unauthorized": stats.get("today_unauthorized", 0),
                "entries": stats.get("today_entries", 0),
                "exits": stats.get("today_exits", 0),
            }
            out["module1"]["last_passage"] = _db.get_last_passage()
            out["module1"]["vehicle_count"] = _db.get_vehicle_count()
            out["module1"]["last_sync"] = _db.get_last_sync()

            if settings.ENABLE_INTRUSION_MODULE:
                out["module2"]["today"] = _db.get_intrusion_stats()
                out["module2"]["last_intrusion"] = _db.get_last_intrusion()
                cams = _db.list_cameras(enabled_only=True)
                intrusion_cams = [c for c in cams if c.get("role") in ("intrusion", "both")]
                out["module2"]["cameras_total"] = len(intrusion_cams)
        except Exception:
            logger.exception("dashboard summary db error")

    if _intrusion_orchestrator:
        out["module2"]["cameras_running"] = _intrusion_orchestrator.active_camera_ids()

    return JSONResponse(out)


@router.get("/api/dashboard/trend")
async def dashboard_trend(days: int = 7):
    """Daily aggregates for stacked-bar chart (Module 1 + Module 2)."""
    if not _db:
        return JSONResponse({"days": [], "passages": [], "intrusions": []})
    days = max(1, min(30, days))
    passages = _db.get_passages_by_day(days)
    intrusions = _db.get_intrusions_by_day(days)

    # Build a unified day list (last N days, oldest first)
    from datetime import date, timedelta
    today = date.today()
    day_list = [(today - timedelta(days=i)).isoformat()
                for i in reversed(range(days))]

    by_p = {p["day"]: p for p in passages}
    by_i = {i["day"]: i for i in intrusions}

    return JSONResponse({
        "days": day_list,
        "passages": [
            {
                "day": d,
                "total": (by_p.get(d) or {}).get("total", 0),
                "authorized": (by_p.get(d) or {}).get("authorized", 0),
                "unauthorized": (by_p.get(d) or {}).get("unauthorized", 0),
                "entries": (by_p.get(d) or {}).get("entries", 0),
                "exits": (by_p.get(d) or {}).get("exits", 0),
            } for d in day_list
        ],
        "intrusions": [
            {
                "day": d,
                "total": (by_i.get(d) or {}).get("total", 0),
                "shadow": (by_i.get(d) or {}).get("shadow", 0),
                "unacknowledged": (by_i.get(d) or {}).get("unacknowledged", 0),
            } for d in day_list
        ],
    })


@router.get("/api/dashboard/hourly")
async def dashboard_hourly(date: str | None = None):
    """24-hour distribution of activity for a single date (today by default)."""
    if not _db:
        return JSONResponse({"date": date, "passages": [], "intrusions": []})
    return JSONResponse({
        "date": date,
        "passages": _db.get_passages_by_hour(date),
        "intrusions": _db.get_intrusions_by_hour(date),
    })


@router.get("/api/intrusion/status")
async def intrusion_status():
    from config import settings
    from app.intrusion.night_mode import is_night_mode_active
    from datetime import datetime as _dt

    is_night = is_night_mode_active(settings.NIGHT_MODE_START, settings.NIGHT_MODE_END)
    cameras_running = (
        _intrusion_orchestrator.active_camera_ids()
        if _intrusion_orchestrator else []
    )
    alarm_enabled = (
        _intrusion_orchestrator.get_alarm_enabled()
        if _intrusion_orchestrator else True
    )
    return JSONResponse({
        "enabled": settings.ENABLE_INTRUSION_MODULE,
        "shadow_mode": settings.INTRUSION_SHADOW_MODE,
        "alarm_enabled": alarm_enabled,
        "night_mode_active": is_night,
        "night_window": f"{settings.NIGHT_MODE_START} - {settings.NIGHT_MODE_END}",
        "cameras_running": cameras_running,
        "stats": _db.get_intrusion_stats() if _db else {},
    })


@router.get("/api/intrusion/alarm-toggle")
async def intrusion_alarm_toggle_get():
    if not _intrusion_orchestrator:
        return JSONResponse({"enabled": True, "available": False})
    return JSONResponse({
        "enabled": _intrusion_orchestrator.get_alarm_enabled(),
        "available": True,
    })


@router.post("/api/intrusion/alarm-toggle")
async def intrusion_alarm_toggle_set(payload: dict):
    if not _intrusion_orchestrator:
        raise HTTPException(503, "Intrusion module not running")
    enabled = bool(payload.get("enabled", True))
    await _intrusion_orchestrator.set_alarm_enabled(enabled)
    return JSONResponse({"enabled": enabled})


@router.get("/api/intrusion/events/{event_id}/clip")
async def get_event_clip(event_id: int):
    """Redirect to the static path of the video clip (if recorded)."""
    if not _db:
        raise HTTPException(503)
    event = _db.get_intrusion_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    clip = event.get("video_clip_path") or ""
    if not clip:
        raise HTTPException(404, "No clip yet (still processing or disabled)")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(clip)


@router.post("/api/intrusion/test-detect")
async def intrusion_test_detect(file: UploadFile):
    """Upload an image; run PersonDetector on it and return annotated result."""
    if not _person_detector:
        raise HTTPException(503, "Intrusion module not running")
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Invalid image")

    import asyncio as _asyncio
    loop = _asyncio.get_event_loop()
    # Use camera_id=0 for test (not a real camera)
    observations = await loop.run_in_executor(
        None, _person_detector.detect_raw, frame, None,
    )

    # Annotate
    annotated = frame.copy()
    persons = []
    for obs in observations:
        x, y, w, h = obs.bbox
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 200, 0), 2)
        label = f"PERSON {obs.confidence:.0%}"
        cv2.putText(annotated, label, (x, y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)
        persons.append({
            "track_id": obs.track_id,
            "confidence": round(obs.confidence, 3),
            "bbox": list(obs.bbox),
        })

    _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
    b64 = base64.b64encode(buf).decode("utf-8")
    return JSONResponse({
        "persons": persons,
        "annotated_image": f"data:image/jpeg;base64,{b64}",
        "filename": file.filename or "unknown",
        "image_size": {"width": frame.shape[1], "height": frame.shape[0]},
    })


@router.post("/api/intrusion/test-video")
async def intrusion_test_video(file: UploadFile, sample_rate: int = 5, conf: float | None = None):
    """Upload a video, process it frame-by-frame through PersonDetector,
    produce an annotated output MP4 and a timeline of detections.

    sample_rate: analyze every Nth frame (default 5 = every 5th frame, keeps it fast).
    """
    if not _person_detector:
        raise HTTPException(503, "Intrusion module not running")

    import asyncio as _asyncio
    import tempfile
    import uuid
    from pathlib import Path
    from config import settings

    # Save upload to temp file
    contents = await file.read()
    if not contents:
        raise HTTPException(400, "Empty upload")
    suffix = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        raise HTTPException(400, "Unsupported video format")

    tmp_input = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_input.write(contents)
    tmp_input.close()

    # Output path (under /static/ so we can serve it)
    from config import BASE_DIR as _BASE
    out_dir = Path(_BASE) / "static" / "test_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:8]
    out_name = f"intrusion_test_{job_id}.mp4"
    out_path = out_dir / out_name

    # Apply temporary confidence override if requested
    orig_conf = _person_detector.confidence
    if conf is not None and 0.01 <= conf <= 0.99:
        _person_detector.confidence = float(conf)

    # Process synchronously in a worker thread (blocking cv2 I/O)
    loop = _asyncio.get_event_loop()
    try:
        summary = await loop.run_in_executor(
            None,
            _process_test_video,
            tmp_input.name,
            str(out_path),
            sample_rate,
        )
    except Exception as e:
        logger.exception("Video processing failed")
        raise HTTPException(500, f"Processing failed: {e}")
    finally:
        _person_detector.confidence = orig_conf
        try:
            Path(tmp_input.name).unlink()
        except OSError:
            pass

    summary["annotated_video_url"] = f"/static/test_outputs/{out_name}"
    summary["filename"] = file.filename or "unknown"
    return JSONResponse(summary)


def _process_test_video(input_path: str, output_path: str, sample_rate: int = 5) -> dict:
    """Run PersonDetector on every Nth frame of input_path.
    Writes annotated MP4 to output_path. Returns statistics dict.
    This runs in a worker thread — don't call from the event loop directly.
    """
    import cv2
    from datetime import datetime

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError("Cannot open video")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    sample_rate = max(1, int(sample_rate))
    # Cap total processed frames to avoid long-running jobs (e.g. 600 samples max)
    max_samples = 600

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    # Output at the SAMPLE rate so playback matches processing granularity
    out_fps = max(1.0, fps / sample_rate)
    writer = cv2.VideoWriter(output_path, fourcc, out_fps, (width, height))

    timeline: list[dict] = []
    frame_idx = 0
    processed = 0
    total_detections = 0
    max_persons = 0
    conf_sum = 0.0
    conf_count = 0
    last_annotated = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % sample_rate != 0:
                frame_idx += 1
                continue
            if processed >= max_samples:
                break

            # Use frame_idx//1000 as a fake camera_id offset to avoid cross-test contamination
            observations = _person_detector.detect_raw(
                frame, timestamp=datetime.now(),
            )
            annotated = frame.copy()
            person_count = len(observations)
            max_persons = max(max_persons, person_count)
            total_detections += person_count

            for obs in observations:
                x, y, w, h = obs.bbox
                cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 200, 0), 2)
                conf_pct = int(obs.confidence * 100)
                label = f"PERSON {conf_pct}%"
                cv2.putText(annotated, label, (x, max(0, y - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 0), 2)
                conf_sum += obs.confidence
                conf_count += 1

            # Frame overlay: timestamp + counter
            header = f"Frame {frame_idx} ({frame_idx/fps:.1f}s) | Persons: {person_count}"
            cv2.rectangle(annotated, (0, 0), (min(width, 380), 22), (0, 0, 0), -1)
            cv2.putText(annotated, header, (6, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            writer.write(annotated)
            last_annotated = annotated

            timeline.append({
                "frame": frame_idx,
                "time_sec": round(frame_idx / fps, 2),
                "persons": person_count,
                "max_conf": round(
                    max((o.confidence for o in observations), default=0.0), 3,
                ),
            })

            processed += 1
            frame_idx += 1
    finally:
        cap.release()
        writer.release()

    # ── Browser-compatible transcode ─────────────────────────
    # OpenCV writes mp4v fourcc which Chrome/Edge often refuse to play inline.
    # If ffmpeg is on PATH, transcode to H.264 + AAC + faststart (universally playable).
    browser_compatible = False
    try:
        import shutil as _sh, subprocess as _sp, os as _os
        ffmpeg = _sh.which("ffmpeg")
        if ffmpeg and _os.path.exists(output_path):
            tmp_out = output_path + ".h264.mp4"
            proc = _sp.run(
                [ffmpeg, "-y", "-i", output_path,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                 "-an",  # no audio (we didn't write any)
                 tmp_out],
                capture_output=True, timeout=180,
            )
            if proc.returncode == 0 and _os.path.exists(tmp_out):
                _os.replace(tmp_out, output_path)
                browser_compatible = True
            else:
                logger.warning("ffmpeg transcode failed: %s", proc.stderr[:200])
    except Exception as _e:
        logger.warning("Browser transcode skipped: %s", _e)

    # Build first-frame thumbnail (JPEG base64) for quick display before playback
    thumb_b64 = ""
    if last_annotated is not None:
        ok, buf = cv2.imencode(".jpg", last_annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(buf).decode()

    avg_conf = (conf_sum / conf_count) if conf_count else 0.0
    frames_with_person = sum(1 for t in timeline if t["persons"] > 0)
    return {
        "source_fps": round(float(fps), 2),
        "source_frames": total_frames,
        "source_resolution": {"width": width, "height": height},
        "sample_rate": sample_rate,
        "processed_frames": processed,
        "frames_with_person": frames_with_person,
        "max_persons_in_any_frame": max_persons,
        "total_person_detections": total_detections,
        "average_confidence": round(avg_conf, 3),
        "timeline": timeline,
        "thumbnail": thumb_b64,
        "browser_compatible": browser_compatible,
    }
