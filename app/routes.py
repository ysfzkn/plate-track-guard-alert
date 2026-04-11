"""FastAPI route definitions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from app.alarm_manager import AlarmManager
    from app.camera import CameraStream, MockCamera
    from app.database import Database
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger("gateguard.app")

router = APIRouter()

# These get set by main.py at startup
_db: Database | None = None
_alarm: AlarmManager | None = None
_ws_manager: ConnectionManager | None = None
_camera: CameraStream | MockCamera | None = None
_mdb_path: str = ""


def init_routes(
    db: Database,
    alarm: AlarmManager,
    ws_manager: ConnectionManager,
    mdb_path: str,
    camera: CameraStream | MockCamera | None = None,
):
    global _db, _alarm, _ws_manager, _mdb_path, _camera
    _db = db
    _alarm = alarm
    _ws_manager = ws_manager
    _mdb_path = mdb_path
    _camera = camera


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
async def get_passages(limit: int = 50):
    """Get recent passage records."""
    if not _db:
        return JSONResponse([])
    try:
        passages = _db.get_recent_passages(limit)
        return JSONResponse(passages)
    except Exception as e:
        logger.exception("Passage query failed")
        return JSONResponse([], status_code=500)


@router.get("/api/stats")
async def get_stats():
    """Get today's detection statistics."""
    if not _db:
        return JSONResponse({"today_total": 0, "today_authorized": 0, "today_unauthorized": 0, "auth_rate": 0})
    try:
        stats = _db.get_stats()
        return JSONResponse(stats)
    except Exception as e:
        logger.exception("Stats query failed")
        return JSONResponse({"today_total": 0, "today_authorized": 0, "today_unauthorized": 0, "auth_rate": 0})


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
