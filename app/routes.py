"""FastAPI route definitions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from app.alarm_manager import AlarmManager
    from app.database import Database
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger("gateguard.app")

router = APIRouter()

# These get set by main.py at startup
_db: Database | None = None
_alarm: AlarmManager | None = None
_ws_manager: ConnectionManager | None = None
_mdb_path: str = ""


def init_routes(db: Database, alarm: AlarmManager, ws_manager: ConnectionManager, mdb_path: str):
    global _db, _alarm, _ws_manager, _mdb_path
    _db = db
    _alarm = alarm
    _ws_manager = ws_manager
    _mdb_path = mdb_path


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await _ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, listen for client messages
            data = await websocket.receive_text()
            logger.debug("WS received: %s", data)
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)


@router.post("/alarm/off")
async def alarm_off():
    """Silence the active alarm."""
    await _alarm.silence_alarm()
    await _ws_manager.broadcast({"type": "alarm_off", "data": {}})
    return JSONResponse({"status": "ok", "alarm_active": False})


@router.post("/api/sync")
async def trigger_sync():
    """Manually trigger MDB → SQLite sync."""
    from app.mdb_sync import sync_mdb_to_sqlite
    result = await sync_mdb_to_sqlite(_mdb_path, _db)
    await _ws_manager.broadcast({"type": "sync_complete", "data": result})
    return JSONResponse(result)


@router.get("/api/passages")
async def get_passages(limit: int = 50):
    """Get recent passages."""
    passages = _db.get_recent_passages(limit)
    return JSONResponse(passages)


@router.get("/api/stats")
async def get_stats():
    """Get today's detection statistics."""
    stats = _db.get_stats()
    return JSONResponse(stats)


@router.get("/api/status")
async def get_status():
    """Get system status."""
    from config import settings
    return JSONResponse({
        "alarm_active": _alarm.alarm_active if _alarm else False,
        "last_alarm_plate": _alarm.last_alarm_plate if _alarm else "",
        "last_sync": _db.get_last_sync() if _db else None,
        "mock_mode": settings.MOCK_MODE,
    })
