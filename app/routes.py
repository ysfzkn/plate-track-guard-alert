"""FastAPI route definitions."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.auth import (
    login as auth_login, logout as auth_logout,
    change_password as auth_change_password,
    admin_reset_password, hash_password,
    check_websocket_token, get_request_user, require_admin,
)
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
_detection_engine = None     # DetectionEngine — health surface
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
    detection_engine=None,
    intrusion_orchestrator=None,
    zone_manager=None,
    person_detector=None,
):
    global _db, _alarm, _ws_manager, _mdb_path, _camera, _detector, _detection_engine
    global _intrusion_orchestrator, _zone_manager, _person_detector
    _db = db
    _alarm = alarm
    _ws_manager = ws_manager
    _mdb_path = mdb_path
    _camera = camera
    _detector = detector
    if detection_engine is not None:
        _detection_engine = detection_engine
    _intrusion_orchestrator = intrusion_orchestrator
    _zone_manager = zone_manager
    _person_detector = person_detector


@router.post("/api/login")
async def login_endpoint(payload: dict, request: Request):
    """Kullanıcı adı + parola ile giriş. Başarılıysa session token döner."""
    username = str((payload or {}).get("username", "")).strip()
    password = str((payload or {}).get("password", ""))
    remember = bool((payload or {}).get("remember", False))
    if not username or not password:
        raise HTTPException(400, "Kullanıcı adı ve parola gerekli")
    ua = request.headers.get("user-agent", "")[:200]
    ip = request.client.host if request.client else ""
    result = auth_login(username, password, remember=remember,
                        user_agent=ua, ip=ip)
    return JSONResponse(result)


@router.post("/api/logout")
async def logout_endpoint(request: Request):
    """Mevcut session'ı sonlandır."""
    from app.auth import _extract_token
    token = _extract_token(request)
    if token:
        auth_logout(token)
    return JSONResponse({"ok": True})


@router.get("/api/auth/me")
async def auth_me(request: Request):
    """Mevcut kullanıcı bilgisi. Login değilse 401 değil — null döner ki UI'lar
    'login sayfasına yönlendir' kararını verebilsin."""
    user = get_request_user(request)
    if not user:
        return JSONResponse({"authenticated": False})
    return JSONResponse({
        "authenticated": True,
        "user": {
            "id": user["user_id"],
            "username": user["username"],
            "role": user["role"],
            "full_name": user.get("full_name") or "",
            "must_change_password": bool(user.get("must_change_password")),
        },
    })


@router.post("/api/auth/change-password")
async def change_password_endpoint(payload: dict, request: Request):
    """Kullanıcının kendi parolasını değiştirmesi."""
    user = getattr(request.state, "user", None) or get_request_user(request)
    if not user:
        raise HTTPException(401, "Oturum gerekli")
    current = str((payload or {}).get("current_password", ""))
    new = str((payload or {}).get("new_password", ""))
    auth_change_password(user["user_id"], current, new)
    return JSONResponse({"ok": True})


# ── Admin: user management ─────────────────────────────────

class UserCreateReq(BaseModel):
    username: str
    password: str
    role: str = "operator"
    full_name: str = ""


class UserUpdateReq(BaseModel):
    role: str | None = None
    full_name: str | None = None
    is_active: bool | None = None
    new_password: str | None = None


@router.get("/api/users")
async def list_users_endpoint(request: Request):
    """Kullanıcı listesi — admin only."""
    require_admin(request)
    if not _db:
        return JSONResponse({"users": []})
    return JSONResponse({"users": _db.list_users()})


@router.post("/api/users")
async def create_user_endpoint(req: UserCreateReq, request: Request):
    """Yeni kullanıcı oluştur — admin only."""
    require_admin(request)
    if not _db:
        raise HTTPException(503, "DB not ready")
    if len(req.username.strip()) < 3:
        raise HTTPException(400, "Kullanıcı adı en az 3 karakter olmalı")
    if len(req.password) < 6:
        raise HTTPException(400, "Parola en az 6 karakter olmalı")
    if req.role not in ("admin", "operator"):
        raise HTTPException(400, "Rol 'admin' veya 'operator' olmalı")
    # Username unique check
    if _db.get_user_by_username(req.username):
        raise HTTPException(409, "Bu kullanıcı adı zaten var")
    h, salt = hash_password(req.password)
    uid = _db.add_user(
        username=req.username.strip(), password_hash=h, salt=salt,
        role=req.role, full_name=req.full_name.strip(),
        must_change_password=1,    # yeni kullanıcı ilk girişte parola değiştirsin
    )
    return JSONResponse({"id": uid, "username": req.username})


@router.put("/api/users/{user_id}")
async def update_user_endpoint(user_id: int, req: UserUpdateReq, request: Request):
    """Kullanıcı güncelle (rol, ad, aktif, parola reset) — admin only."""
    admin = require_admin(request)
    if not _db:
        raise HTTPException(503, "DB not ready")
    target = _db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Kullanıcı bulunamadı")

    fields = req.model_dump(exclude_unset=True)
    if "new_password" in fields and fields["new_password"]:
        admin_reset_password(user_id, fields.pop("new_password"))
    # Admin kendi rolünü düşürmesini engelle
    if user_id == admin["user_id"] and fields.get("role") == "operator":
        raise HTTPException(400, "Kendi admin yetkinizi düşüremezsiniz")
    if user_id == admin["user_id"] and fields.get("is_active") is False:
        raise HTTPException(400, "Kendi hesabınızı pasifleştiremezsiniz")
    if fields:
        _db.update_user(user_id, **fields)
    return JSONResponse({"ok": True})


@router.delete("/api/users/{user_id}")
async def delete_user_endpoint(user_id: int, request: Request):
    """Kullanıcı sil — admin only. Admin kendini silemez."""
    admin = require_admin(request)
    if not _db:
        raise HTTPException(503, "DB not ready")
    if user_id == admin["user_id"]:
        raise HTTPException(400, "Kendi hesabınızı silemezsiniz")
    # Son aktif admin'i silmeyi engelle
    users = _db.list_users()
    admins_active = [u for u in users if u["role"] == "admin" and u["is_active"]]
    target = _db.get_user_by_id(user_id)
    if target and target["role"] == "admin" and len(admins_active) <= 1:
        raise HTTPException(400, "Son admin'i silemezsiniz — önce başka admin tanımlayın")
    _db.delete_user(user_id)
    return JSONResponse({"ok": True})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # WS auth — query param ?token=<session_token>
    user = check_websocket_token(websocket)
    if not user:
        await websocket.close(code=1008)   # policy violation
        return
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


@router.get("/api/plates/autocomplete")
async def plate_autocomplete(q: str = "", limit: int = 8):
    """Suggest plate matches across vehicles + recent passages.
    Returns: [{plate, owner, source, count}]  -- source: "vehicle"/"passage"
    """
    if not _db or not q or len(q) < 2:
        return JSONResponse({"suggestions": []})
    q_norm = q.upper().replace(" ", "").replace("-", "")[:12]
    suggestions: list[dict] = []
    try:
        # Authoritative plates from vehicles
        rows = _db.conn.execute(
            """SELECT plate, plate_normalized, owner_name, apartment
               FROM vehicles
               WHERE plate_normalized LIKE ?
               ORDER BY plate_normalized LIMIT ?""",
            (f"%{q_norm}%", limit),
        ).fetchall()
        for r in rows:
            suggestions.append({
                "plate": r["plate"],
                "owner": r["owner_name"] or "",
                "apartment": r["apartment"] or "",
                "source": "vehicle",
            })
        # Fill remaining slots from recent passages (unique plates only)
        if len(suggestions) < limit:
            seen = {s["plate"] for s in suggestions}
            extra = _db.conn.execute(
                """SELECT plate, plate_normalized, COUNT(*) as cnt
                   FROM passages
                   WHERE plate_normalized LIKE ?
                   GROUP BY plate_normalized
                   ORDER BY MAX(detected_at) DESC LIMIT ?""",
                (f"%{q_norm}%", limit - len(suggestions)),
            ).fetchall()
            for r in extra:
                if r["plate"] in seen:
                    continue
                suggestions.append({
                    "plate": r["plate"],
                    "owner": "",
                    "apartment": "",
                    "source": "passage",
                    "count": r["cnt"],
                })
    except Exception:
        logger.exception("Autocomplete failed")
    return JSONResponse({"suggestions": suggestions[:limit]})


# ── Tanımlı Araç / Plaka Yönetimi ───────────────────────────
# MDB kişileri (read-only) + elle eklenen ek/bağımsız plakalar (extra_plates).

class ExtraPlateCreateRequest(BaseModel):
    plate: str
    vehicle_moonwell_id: int | None = None   # bir MDB kişisine ek plaka; None = bağımsız
    owner_name: str = ""
    block_no: str = ""
    apartment: str = ""
    note: str = ""


class ExtraPlateUpdateRequest(BaseModel):
    plate: str | None = None
    vehicle_moonwell_id: int | None = None
    owner_name: str | None = None
    block_no: str | None = None
    apartment: str | None = None
    note: str | None = None


@router.get("/api/vehicles")
async def list_vehicles(search: str = "", limit: int = 50, offset: int = 0):
    """Kişi + tanımlı plakalar. Her MDB kişisine bağlı ek plakalar iliştirilir;
    bağımsız (kişisi MDB'de olmayan) plakalar ayrı listelenir."""
    if not _db:
        return JSONResponse({"vehicles": [], "total": 0, "standalone": [],
                             "counts": {"mdb": 0, "extra": 0}})
    try:
        rows, total = _db.get_vehicles_page(search.strip() or None, limit, offset)
        extras = _db.get_all_extra_plates()

        # moonwell_id -> [ek plakalar]
        by_person: dict[int, list] = {}
        standalone: list = []
        s = search.strip().upper()
        for e in extras:
            if e["vehicle_moonwell_id"] is not None:
                by_person.setdefault(e["vehicle_moonwell_id"], []).append(e)
            else:
                # Bağımsız plakaları da aramaya dahil et
                if not s or s in (e["plate_normalized"] or "") or s in (e["owner_name"] or "").upper():
                    standalone.append(e)

        vehicles = []
        for r in rows:
            v = dict(r)
            v["extra_plates"] = by_person.get(r["moonwell_id"], [])
            vehicles.append(v)

        return JSONResponse({
            "vehicles": vehicles,
            "total": total,
            "standalone": standalone,
            "counts": {
                "mdb": _db.get_vehicle_count(),
                "extra": _db.get_extra_plate_count(),
            },
        })
    except Exception:
        logger.exception("list_vehicles failed")
        return JSONResponse({"vehicles": [], "total": 0, "standalone": [],
                             "counts": {"mdb": 0, "extra": 0}}, status_code=500)


@router.post("/api/extra-plates")
async def add_extra_plate(req: ExtraPlateCreateRequest, request: Request):
    """Elle plaka ekle (mevcut kişiye ek veya bağımsız)."""
    if not _db:
        raise HTTPException(503, "Database not ready")
    user = getattr(request.state, "user", None) or {}
    try:
        pid = _db.add_extra_plate(
            plate=req.plate,
            vehicle_moonwell_id=req.vehicle_moonwell_id,
            owner_name=req.owner_name, block_no=req.block_no,
            apartment=req.apartment, note=req.note,
            created_by=user.get("username", ""),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"id": pid, "status": "created"})


@router.put("/api/extra-plates/{plate_id}")
async def edit_extra_plate(plate_id: int, req: ExtraPlateUpdateRequest):
    if not _db:
        raise HTTPException(503, "Database not ready")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        return JSONResponse({"status": "no_changes"})
    try:
        ok = _db.update_extra_plate(plate_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "Plaka bulunamadı")
    return JSONResponse({"status": "updated"})


@router.delete("/api/extra-plates/{plate_id}")
async def remove_extra_plate(plate_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    if not _db.delete_extra_plate(plate_id):
        raise HTTPException(404, "Plaka bulunamadı")
    return JSONResponse({"status": "deleted"})


@router.post("/api/passages/bulk-delete")
async def bulk_delete_passages(payload: dict):
    """Delete multiple passages by ID. Body: {ids: [int, ...]}."""
    if not _db:
        raise HTTPException(503, "Database not ready")
    ids = payload.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "ids list required")
    # Sanitize: only ints
    safe = [int(i) for i in ids if isinstance(i, (int, str)) and str(i).isdigit()]
    if not safe:
        raise HTTPException(400, "no valid ids")
    deleted = _db.bulk_delete_passages(safe)
    return JSONResponse({"deleted": deleted})


@router.get("/api/passages/export.csv")
async def export_passages_csv(
    start: str | None = None,
    end: str | None = None,
    direction: str | None = None,
    authorized: str | None = None,
    plate: str | None = None,
):
    """Export filtered passages as a UTF-8 CSV file (Excel-compatible BOM)."""
    if not _db:
        raise HTTPException(503, "Database not ready")
    auth_bool = True if authorized == "true" else False if authorized == "false" else None
    passages, _ = _db.get_passages_filtered(
        start_date=start, end_date=end, direction=direction,
        authorized=auth_bool, plate_search=plate,
        limit=100_000, offset=0,
    )
    import io as _io, csv as _csv
    buf = _io.StringIO()
    buf.write("﻿")   # BOM so Excel detects UTF-8
    w = _csv.writer(buf, delimiter=";")
    w.writerow(["ID", "Tarih/Saat", "Plaka", "Yön", "Yetkili",
                "Sahip", "Daire", "Güven", "Screenshot"])
    for r in passages:
        w.writerow([
            r.get("id", ""),
            r.get("detected_at", ""),
            r.get("plate", ""),
            {"entry": "Giriş", "exit": "Çıkış"}.get(r.get("direction", ""), "—"),
            "Evet" if r.get("is_authorized") else "Hayır",
            r.get("owner_name", ""),
            r.get("apartment", ""),
            f"{(r.get('confidence') or 0) * 100:.0f}%",
            r.get("screenshot_path", ""),
        ])
    fname = f"gateguard_kayitlar_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
    extra_count = 0
    last_sync = None
    if _db:
        try:
            vehicle_count = _db.get_vehicle_count()
            extra_count = _db.get_extra_plate_count()
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
        "extra_count": extra_count,
        "plate_count": vehicle_count + extra_count,
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

        # Run detection on the shared bounded inference pool so a manual test
        # serializes with live-camera inference instead of oversubscribing CPU.
        import asyncio
        from app.inference_pool import get_inference_pool
        loop = asyncio.get_event_loop()
        detections = await loop.run_in_executor(get_inference_pool(), _detector.detect, frame)

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

    Order (best-effort, never raises):
      1. Stop intrusion engines (release RTSP streams + writer threads).
      2. Stop the plate detection engine.
      3. Silence ESP32 alarm.
      4. Stop alarm heartbeat task.
      5. Close DB connection (flush WAL).
      6. Exit after a short delay so the HTTP response can flush.
    """
    import os
    import threading
    import time

    async def _graceful_shutdown():
        logger.info("Shutdown requested via API — graceful sequence starting")
        try:
            if _intrusion_orchestrator:
                await _intrusion_orchestrator.stop_all()
                logger.info("Shutdown: intrusion engines stopped")
        except Exception:
            logger.exception("Shutdown: intrusion stop error")
        try:
            if _detection_engine:
                await _detection_engine.stop()
                logger.info("Shutdown: detection engine stopped")
        except Exception:
            logger.exception("Shutdown: detection stop error")
        try:
            if _alarm:
                await _alarm.silence_alarm()
                await _alarm.close()
                logger.info("Shutdown: alarm silenced + closed")
        except Exception:
            logger.exception("Shutdown: alarm close error")
        try:
            if _camera and hasattr(_camera, "stop"):
                _camera.stop()
        except Exception:
            logger.exception("Shutdown: camera stop error")
        try:
            if _db and hasattr(_db, "conn"):
                _db.conn.commit()
                _db.conn.close()
                logger.info("Shutdown: DB closed")
        except Exception:
            logger.exception("Shutdown: DB close error")

    async def _drive_shutdown_then_exit():
        # Run the graceful sequence, then sleep briefly and exit cleanly.
        try:
            await _graceful_shutdown()
        finally:
            await asyncio.sleep(0.5)
            logger.info("Shutdown sequence complete — exiting")
            os._exit(0)

    import asyncio
    asyncio.create_task(_drive_shutdown_then_exit())
    return JSONResponse({"status": "shutting_down", "message": "Sunucu kapatılıyor..."})


# ── Camera Test Endpoints ──────────────────────────────────

@router.get("/api/health")
async def system_health():
    """Comprehensive system health snapshot.

    Returns FPS, lag, error state for every running camera + system metrics
    (disk free, memory, intrusion alarm state). Designed to be polled by the
    UI every 5 seconds for the health badge.
    """
    import shutil as _sh
    import os as _os
    from config import settings, BASE_DIR

    # ── Camera health (Module 1 + Module 2) ──
    cameras: list[dict] = []
    if _camera and hasattr(_camera, "get_health"):
        h = _camera.get_health()
        h["id"] = 0
        h["name"] = "Plaka Kamerası"
        h["role"] = "plate"
        cameras.append(h)

    if _intrusion_orchestrator:
        for cam_id in _intrusion_orchestrator.active_camera_ids():
            stream = _intrusion_orchestrator.get_stream(cam_id)
            if stream and hasattr(stream, "get_health"):
                h = stream.get_health()
                h["id"] = cam_id
                row = _db.get_camera(cam_id) if _db else None
                h["name"] = row["name"] if row else f"Kamera {cam_id}"
                h["role"] = row["role"] if row else "intrusion"
                cameras.append(h)

    # ── Disk usage ──
    try:
        disk = _sh.disk_usage(str(BASE_DIR))
        disk_free_gb = disk.free / (1024 ** 3)
        disk_total_gb = disk.total / (1024 ** 3)
        disk_used_pct = (disk.used / disk.total) * 100
    except Exception:
        disk_free_gb = disk_total_gb = disk_used_pct = 0

    # ── Memory ──
    mem_info = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        proc = psutil.Process(_os.getpid())
        mem_info = {
            "system_used_pct": round(vm.percent, 1),
            "process_mb": round(proc.memory_info().rss / (1024 * 1024), 1),
        }
    except Exception:
        pass

    # ── Detection engine health (Module 1 plaka + Modül 2 intrusion) ──
    detection_engines: list[dict] = []
    if _detection_engine and hasattr(_detection_engine, "get_health"):
        h = _detection_engine.get_health()
        h["name"] = "Plaka Tespit Motoru"
        h["module"] = "M1"
        detection_engines.append(h)
    if _intrusion_orchestrator:
        for cam_id, eng in getattr(_intrusion_orchestrator, "_engines", {}).items():
            if hasattr(eng, "get_health"):
                h = eng.get_health()
                h["name"] = f"Kişi Tespit Motoru (cam={cam_id})"
                h["module"] = "M2"
                detection_engines.append(h)

    # ── Overall status (worst-of) ──
    overall = "ok"
    issues: list[str] = []
    for de in detection_engines:
        if de.get("degraded"):
            overall = "error"
            issues.append(f"{de['name']}: DEGRADED (durmuş)")
    for c in cameras:
        if not c["connected"]:
            overall = "error"
            issues.append(f"{c['name']}: bağlantı yok")
        elif c["is_stale"]:
            overall = "error"
            issues.append(f"{c['name']}: stream durdu")
        elif c["fps"] < 0.5 and c["uptime_sec"] > 30:
            if overall != "error":
                overall = "warn"
            issues.append(f"{c['name']}: çok düşük FPS")
    if disk_free_gb < 2 and disk_total_gb > 0:
        overall = "error"
        issues.append(f"Disk: {disk_free_gb:.1f} GB boş kaldı")
    elif disk_free_gb < 5 and disk_total_gb > 0 and overall != "error":
        overall = "warn"
        issues.append(f"Disk: {disk_free_gb:.1f} GB boş")

    # ── ESP32 alarm hardware status ──
    alarm_hw = _alarm.get_hardware_status() if _alarm else {"ok": True, "mock_mode": True}
    if _alarm and not alarm_hw.get("ok"):
        overall = "error"
        issues.append(f"ESP32 siren bağlantısı yok ({alarm_hw.get('last_error') or 'timeout'})")

    # ── MDB sync status ──
    mdb_sync_state = {}
    try:
        if _db and not settings.MOCK_MODE:
            mdb_sync_state = _db.get_last_sync_status()
            if mdb_sync_state.get("stale"):
                if overall != "error":
                    overall = "warn"
                hrs = mdb_sync_state.get("hours_since")
                if hrs is None:
                    issues.append("MDB sync hiç çalışmamış")
                elif mdb_sync_state.get("status") != "success":
                    issues.append(f"Son MDB sync başarısız ({mdb_sync_state.get('error') or 'bilinmeyen'})")
                else:
                    issues.append(f"MDB sync {hrs:.0f} saatten beri çalışmamış")
    except Exception:
        logger.exception("MDB sync status check failed")

    # Startup validation snapshot
    try:
        from app.startup_validator import get_startup_results
        startup = get_startup_results()
    except Exception:
        startup = {"ok": True, "checks": []}

    return JSONResponse({
        "status": overall,           # "ok" | "warn" | "error"
        "issues": issues,
        "cameras": cameras,
        "detection_engines": detection_engines,
        "alarm_hardware": alarm_hw,
        "mdb_sync": mdb_sync_state,
        "startup": startup,
        "disk": {
            "free_gb": round(disk_free_gb, 1),
            "total_gb": round(disk_total_gb, 1),
            "used_pct": round(disk_used_pct, 1),
        },
        "memory": mem_info,
        "intrusion_alarm_enabled": (
            _intrusion_orchestrator.get_alarm_enabled()
            if _intrusion_orchestrator else None
        ),
        "shadow_mode": settings.INTRUSION_SHADOW_MODE,
    })


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


@router.get("/api/camera/{camera_id}/snapshot")
async def intrusion_camera_snapshot(camera_id: int):
    """Latest JPEG frame from a specific intrusion camera (Module 2)."""
    if not _intrusion_orchestrator:
        return JSONResponse({"error": "Intrusion module not running"}, status_code=503)
    stream = _intrusion_orchestrator.get_stream(camera_id)
    if stream is None:
        return JSONResponse({"error": "Camera not active"}, status_code=404)
    frame = stream.get_frame()
    if frame is None:
        return JSONResponse({"error": "No frame available"}, status_code=404)
    try:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return JSONResponse({"error": "Encode failed"}, status_code=500)
        return Response(content=buf.tobytes(), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except Exception:
        logger.exception("Intrusion snapshot failed")
        return JSONResponse({"error": "snapshot failed"}, status_code=500)


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
        # Wait up to 12s for a frame — a high-res main stream can take several
        # seconds to deliver the first decoded frame over RTSP.
        for _ in range(120):
            if test.is_connected and test.get_frame() is not None:
                break
            await _asyncio.sleep(0.1)
        result["connected"] = test.is_connected
        frame = test.get_frame()
        if frame is not None:
            result["frame_size"] = {"width": frame.shape[1], "height": frame.shape[0]}
        else:
            # Opened the connection but never decoded a frame — almost always
            # the main stream is H.265/HEVC (OpenCV can't decode) or UDP loss.
            result["error"] = (
                "Bağlandı ama kare alınamadı. Çoğunlukla: ana akım H.265/HEVC "
                "(OpenCV okuyamaz) → kameranın SUB-STREAM (alt akım, H.264) RTSP "
                "URL'sini dene; ya da URL yolu yanlış."
                if test.is_connected else
                "RTSP açılamadı: URL/parola/ağ yanlış olabilir."
            )
    finally:
        test.stop()
    return JSONResponse(result)


# ── Zone CRUD ───────────────────────────────────────────────

def _clamp_polygon(points) -> list[list[float]]:
    """Clamp polygon points into the normalized [0,1] range.

    The front-end editors already normalize clicks by canvas size, but an edge
    click, float rounding, or a momentarily zero-sized canvas can push a
    coordinate a hair outside [0,1] (or produce NaN). Rather than reject the
    whole zone — which the operator just sees as a mysterious
    "Kayıt başarısız" — we clamp each coordinate and drop any non-finite ones.
    """
    import math
    cleaned: list[list[float]] = []
    for pt in points:
        if len(pt) != 2:
            continue
        try:
            x, y = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        cleaned.append([min(1.0, max(0.0, x)), min(1.0, max(0.0, y))])
    return cleaned


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
    pts = _clamp_polygon(req.polygon_points)
    if len(pts) < 3:
        raise HTTPException(400, "Polygon must have at least 3 valid points")
    import json as _json
    poly_json = _json.dumps(pts)
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
        pts = _clamp_polygon(fields["polygon_points"])
        if len(pts) < 3:
            raise HTTPException(400, "Polygon must have at least 3 points")
        import json as _json
        fields["polygon_points"] = _json.dumps(pts)
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
async def acknowledge_event(event_id: int, payload: dict | None = None):
    """Mark event as acknowledged. Optional `note` body lets the operator
    record context ("kurye", "yanlış alarm", etc) — searchable later.
    """
    if not _db:
        raise HTTPException(503, "Database not ready")
    note = (payload or {}).get("note", "") if payload else ""
    note = str(note)[:500]   # cap length
    ok = _db.acknowledge_intrusion_event(event_id, note=note)
    if not ok:
        raise HTTPException(404, "Event not found")
    if _ws_manager:
        await _ws_manager.broadcast({
            "type": "intrusion_ack",
            "data": {"event_id": event_id, "note": note},
        })
    return JSONResponse({"status": "acknowledged", "note": note})


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
            out["module1"]["extra_count"] = _db.get_extra_plate_count()
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
        return JSONResponse({"enabled": True, "available": False, "cameras": []})
    st = _intrusion_orchestrator.get_alarm_states()
    return JSONResponse({
        "enabled": st["enabled"],
        "available": True,
        "cameras": st["cameras"],
    })


@router.post("/api/intrusion/alarm-toggle")
async def intrusion_alarm_toggle_set(payload: dict):
    """Toggle the intrusion siren. Omit camera_id for the GLOBAL master switch;
    pass a camera_id to temporarily mute/unmute a single camera."""
    if not _intrusion_orchestrator:
        raise HTTPException(503, "Intrusion module not running")
    enabled = bool(payload.get("enabled", True))
    raw_cam = payload.get("camera_id")
    camera_id = int(raw_cam) if raw_cam is not None else None
    await _intrusion_orchestrator.set_alarm_enabled(enabled, camera_id)
    return JSONResponse(_intrusion_orchestrator.get_alarm_states())


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
    from app.inference_pool import get_inference_pool
    loop = _asyncio.get_event_loop()
    # Use camera_id=0 for test (not a real camera)
    observations = await loop.run_in_executor(
        get_inference_pool(), _person_detector.detect_raw, frame, None,
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
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".asf"}:
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


# ════════════════════════════════════════════════════════════════
# P2.1 + P2.2 — End-to-End Test Endpoints
# ════════════════════════════════════════════════════════════════
# These run video frames through the SAME pipeline production uses:
# tracker + classifier + zone manager + MDB lookup. Alarm firing is
# simulated (logged but no ESP32 call).

@router.post("/api/intrusion/playground/extract-frame")
async def playground_extract_frame(file: UploadFile, pos: float = 0.1):
    """Stash an uploaded recording in a temp dir and return one frame
    (base64 JPEG) at relative position `pos` (0..1) for the zone-drawing
    playground. The returned `token` lets the E2E test reuse the stashed video
    without re-uploading it. Stale stashes (>2h) are reaped on each call."""
    import asyncio as _asyncio
    import base64
    import tempfile
    import time as _time
    import uuid
    from pathlib import Path
    from app.inference_pool import get_io_pool

    contents = await file.read()
    if not contents:
        raise HTTPException(400, "Empty upload")
    suffix = Path(file.filename or "v.mp4").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".asf"}:
        raise HTTPException(400, "Unsupported video format")

    pdir = Path(tempfile.gettempdir()) / "gateguard_playground"
    pdir.mkdir(parents=True, exist_ok=True)
    cutoff = _time.time() - 7200   # reap stashes older than 2h
    for old in pdir.glob("*"):
        try:
            if old.is_file() and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass

    token = uuid.uuid4().hex
    vid_path = pdir / f"{token}{suffix}"
    vid_path.write_bytes(contents)

    def _grab():
        cap = cv2.VideoCapture(str(vid_path))
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        fc = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        target = int(max(0.0, min(0.99, pos)) * fc) if fc > 0 else 0
        if target > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ret, frame = cap.read()
        if not ret:                       # fall back to first frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            return None
        h, w = frame.shape[:2]
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None
        return {"w": w, "h": h, "fps": float(fps), "frame_count": fc,
                "b64": base64.b64encode(buf.tobytes()).decode("ascii")}

    loop = _asyncio.get_event_loop()
    info = await loop.run_in_executor(get_io_pool(), _grab)
    if info is None:
        try: vid_path.unlink()
        except OSError: pass
        raise HTTPException(400, "Videodan kare alınamadı (codec?)")

    return JSONResponse({
        "token": token,
        "frame": "data:image/jpeg;base64," + info["b64"],
        "width": info["w"], "height": info["h"],
        "fps": round(info["fps"], 2), "frame_count": info["frame_count"],
    })


@router.post("/api/intrusion/test-video-e2e")
async def intrusion_test_video_e2e(
    file: UploadFile | None = File(None),
    sample_rate: int = 3,
    conf: float | None = None,
    camera_id: int | None = None,
    daynight: str = "night",
    save_to_archive: bool = True,
    video_token: str | None = Form(None),
    zones_json: str | None = Form(None),
):
    """Run a video through the full Module 2 pipeline (YOLO + tracker +
    5-rule classifier + zone filter + motion fallback) and report which
    events WOULD have fired in production. Does NOT trigger the ESP32.

    Video source (one of):
      - file: a multipart upload, OR
      - video_token: id of a video already stashed via the playground's
        extract-frame call (avoids re-uploading the same recording).

    Zones (one of):
      - zones_json: ad-hoc zones drawn in the test playground — a JSON list of
        {name, points:[[x,y],...], is_night_only, min_loiter_sec,
        enable_motion_fallback}. Evaluated in-memory; no camera/DB needed.
      - camera_id: apply the zones of a real saved camera.
      - neither: zones skipped (raw classifier only).

    daynight: simulated clock for zone night-mode evaluation ("night"=22:00
    default, "day"=12:00) — lets you validate the same zones against day AND
    night footage regardless of the real wall-clock.
    """
    import json as _json
    import re as _re
    import tempfile
    import uuid
    from pathlib import Path
    from config import settings, BASE_DIR as _BD
    from app.inference_pool import get_inference_pool
    from app.intrusion.models import Zone
    from app.intrusion.zone_manager import AdHocZoneManager

    if not _person_detector:
        raise HTTPException(503, "Intrusion module not running")

    # ── Resolve the input video: stashed token (playground) or fresh upload ──
    created_tmp: str | None = None
    src_name = "unknown"
    if video_token:
        safe = _re.sub(r"[^a-f0-9]", "", video_token)[:32]
        pdir = Path(tempfile.gettempdir()) / "gateguard_playground"
        matches = list(pdir.glob(f"{safe}.*")) if safe else []
        if not matches:
            raise HTTPException(400, "video_token süresi dolmuş veya geçersiz")
        input_path = str(matches[0])
        src_name = matches[0].name
    elif file is not None:
        contents = await file.read()
        if not contents:
            raise HTTPException(400, "Empty upload")
        suffix = Path(file.filename or "test.mp4").suffix.lower() or ".mp4"
        if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".asf"}:
            raise HTTPException(400, "Unsupported video format")
        tmp_input = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_input.write(contents)
        tmp_input.close()
        input_path = tmp_input.name
        created_tmp = tmp_input.name
        src_name = file.filename or "unknown"
    else:
        raise HTTPException(400, "file veya video_token gerekli")

    out_dir = Path(_BD) / "static" / "test_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"e2e_{uuid.uuid4().hex[:8]}.mp4"
    out_path = out_dir / out_name

    # ── Apply confidence override ──
    from app.intrusion.classifier import IntrusionClassifier
    orig_conf = _person_detector.confidence
    if conf is not None and 0.01 <= conf <= 0.99:
        _person_detector.confidence = float(conf)

    # ── Resolve zone source: ad-hoc (playground) > real camera > none ──
    ADHOC_CAM = 999999
    if zones_json:
        try:
            parsed = _json.loads(zones_json)
        except Exception:
            raise HTTPException(400, "zones_json geçersiz JSON")
        zlist: list[Zone] = []
        for i, z in enumerate(parsed if isinstance(parsed, list) else []):
            try:
                pts = [(float(p[0]), float(p[1])) for p in z.get("points", [])]
            except (TypeError, ValueError, IndexError):
                continue
            if len(pts) < 3:
                continue
            zlist.append(Zone(
                id=10000 + i, camera_id=ADHOC_CAM,
                name=str(z.get("name") or f"Bölge {i + 1}"),
                polygon_points=pts,
                is_night_only=bool(z.get("is_night_only", True)),
                min_loiter_sec=int(z.get("min_loiter_sec", 5) or 5),
                enabled=True,
                enable_motion_fallback=bool(z.get("enable_motion_fallback", False)),
            ))
        if not zlist:
            raise HTTPException(400, "Geçerli bölge yok (her bölge ≥3 nokta olmalı)")
        active_zm = AdHocZoneManager(
            {ADHOC_CAM: zlist}, settings.NIGHT_MODE_START, settings.NIGHT_MODE_END)
        use_camera_id = ADHOC_CAM
    else:
        if camera_id is not None and not _zone_manager:
            raise HTTPException(503, "Zone manager not running")
        active_zm = _zone_manager
        use_camera_id = camera_id if camera_id is not None else 0

    # ── Build a dedicated classifier instance (don't disturb production state) ──
    test_classifier = IntrusionClassifier(
        zone_manager=active_zm,
        min_confidence=_person_detector.confidence,
        cooldown_sec=settings.INTRUSION_COOLDOWN_SEC,
        warmup_sec=0,    # warmup off for tests
        min_consecutive_frames=settings.INTRUSION_MIN_CONSECUTIVE_FRAMES,
        frame_gap_reset_sec=settings.INTRUSION_FRAME_GAP_RESET_SEC,
    )
    test_classifier.mark_camera_started(use_camera_id)

    # Simulated clock hour for zone night-mode evaluation (see docstring).
    sim_hour = 12 if str(daynight).lower() == "day" else 22

    loop = asyncio.get_event_loop()
    try:
        summary = await loop.run_in_executor(
            get_inference_pool(), _run_e2e_video,
            input_path, str(out_path), sample_rate,
            test_classifier, use_camera_id, sim_hour,
        )
    except Exception as e:
        logger.exception("E2E video test failed")
        raise HTTPException(500, f"Processing failed: {e}")
    finally:
        _person_detector.confidence = orig_conf
        # Only delete a freshly-uploaded temp; keep token-stashed videos so the
        # playground can re-run them with different zones.
        if created_tmp:
            try: Path(created_tmp).unlink()
            except OSError: pass

    summary["annotated_video_url"] = f"/static/test_outputs/{out_name}"
    summary["filename"] = src_name
    summary["camera_id"] = camera_id
    summary["adhoc_zones"] = bool(zones_json)

    # Archive
    if save_to_archive and _db:
        try:
            _db.add_test_run(
                module="m2", test_type="video_e2e",
                source_filename=src_name,
                camera_id=camera_id,
                params_json=_json.dumps({
                    "sample_rate": sample_rate, "conf": _person_detector.confidence,
                    "camera_id": camera_id, "daynight": daynight,
                    "adhoc_zones": bool(zones_json),
                }),
                summary_json=_json.dumps({
                    "processed_frames": summary.get("processed_frames", 0),
                    "events": summary.get("events", []),
                    "rule_stats": summary.get("rule_stats", {}),
                }),
                event_count=summary.get("event_count", 0),
                duration_sec=summary.get("processing_time_sec", 0),
                output_video_url=summary["annotated_video_url"],
            )
        except Exception:
            logger.exception("Failed to archive E2E test run")

    return JSONResponse(summary)


def _run_e2e_video(
    input_path: str, output_path: str, sample_rate: int,
    classifier, camera_id: int, sim_hour: int = 22,
) -> dict:
    """Frame-by-frame: detect (stateless) + greedy-IoU IDs + classify (5-rule)."""
    import time as _time
    from datetime import datetime as _dt

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError("Cannot open video")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_rate = max(1, int(sample_rate))

    # CPU budget: each processed frame is a full YOLO pass (~0.3-0.5s on a
    # no-GPU box). A long clip at "every frame" = 1000+ passes = 10-15 min and
    # the UI looks frozen. Cap the TOTAL processed frames and spread them across
    # the WHOLE video by auto-raising the sample step — so a fence-jump late in
    # the clip is still caught, and the test finishes in ~1-2 min.
    import math as _math
    max_samples = 160
    auto_sampled = False
    if total_frames > 0:
        needed = _math.ceil(total_frames / max_samples)
        if needed > sample_rate:
            sample_rate = needed
            auto_sampled = True

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_fps = max(1.0, fps / sample_rate)
    writer = cv2.VideoWriter(output_path, fourcc, out_fps, (width, height))

    events: list[dict] = []
    timeline: list[dict] = []
    rule_stats = {"detections": 0, "in_zone": 0, "passed_classifier": 0}
    frame_idx = 0
    processed = 0
    t0 = _time.time()
    # Use a synthetic "now" that advances by 1/process_fps each sampled frame
    # so the classifier's loiter/cooldown logic sees realistic timing.
    sim_now = _dt(2026, 1, 1, int(sim_hour), 0, 0)
    sim_step_sec = sample_rate / max(1.0, fps)
    from datetime import timedelta as _td

    # Mirror production: stateless detect_raw() + a per-camera SimpleIouTracker
    # for stable cross-frame IDs (the consensus rule needs them). We do NOT use
    # detect()'s shared model.track(persist=True) here — its single ByteTrack
    # timeline is contended by live cameras and can suppress detections.
    from app.intrusion.simple_tracker import SimpleIouTracker
    tracker = SimpleIouTracker(camera_id=camera_id)

    # ── Motion fallback (perimeter / wall-top zones) ──
    # Mirror the LIVE engine so the playground can actually TEST a "Hareket
    # yakalama" zone. Without this the test ran YOLO only and silently
    # under-reported wall/climbing zones (where a climber's pose is often
    # missed by YOLO but shows up as a human-shaped motion blob).
    motion_detector = None
    try:
        zm = getattr(classifier, "zone_manager", None)
        zones = zm.get_zones(camera_id) if zm else []
        if any(getattr(z, "enable_motion_fallback", False) and z.enabled for z in zones):
            from app.intrusion.motion_detector import MotionDetector
            motion_detector = MotionDetector(camera_id=camera_id)
    except Exception:
        logger.exception("Playground motion fallback init failed (continuing YOLO-only)")
        motion_detector = None

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

            # Stateless detection + per-camera IoU track-id assignment
            raw = _person_detector.detect_raw(frame, sim_now)
            observations = tracker.update(raw, sim_now)

            # Motion-fallback observations for perimeter/wall zones — run the
            # SAME detector the live engine uses, suppressing blobs already
            # explained by YOLO person/animal boxes, then feed them through the
            # identical classifier path so the test reflects production.
            motion_obs = []
            if motion_detector is not None:
                try:
                    # Skip the extra YOLO animal pass here: a 2nd full inference
                    # per frame would ~double the test time and make it feel
                    # frozen. The motion detector already suppresses blobs
                    # explained by YOLO persons and filters by human-likeness, so
                    # animal suppression is a minor refinement we trade for speed.
                    motion_obs = motion_detector.detect(frame, observations, [], sim_now)
                except Exception:
                    motion_obs = []
            rule_stats["detections"] += len(observations) + len(motion_obs)

            annotated = frame.copy()
            frame_events_this_tick: list[dict] = []
            tagged = [(o, False) for o in observations] + [(o, True) for o in motion_obs]
            for obs, is_motion in tagged:
                x, y, w, h = obs.bbox
                # Pre-check: is the obs in any zone? (informational)
                matching = classifier.zone_manager.matching_zones_bbox(
                    camera_id, obs.bbox, width, height, sim_now
                )
                in_any_zone = bool(matching) or camera_id == 0  # cam=0 means no zones configured
                if in_any_zone:
                    rule_stats["in_zone"] += 1

                # Run full classifier
                ev = classifier.classify(obs, width, height, sim_now)

                # Colors (BGR): purple = motion-fallback, green = out-of-zone
                # YOLO, orange = in-zone YOLO, red = fired an event.
                if is_motion:
                    color = (190, 0, 190)
                else:
                    color = (0, 200, 0) if not in_any_zone else (0, 165, 255)
                if ev is not None:
                    rule_stats["passed_classifier"] += 1
                    color = (0, 0, 255)
                    frame_events_this_tick.append({
                        "frame": frame_idx,
                        "time_sec": round(frame_idx / fps, 2),
                        "zone_id": ev.zone_id,
                        "track_id": ev.track_id,
                        "duration_sec": round(ev.duration_sec, 2),
                        "confidence": round(ev.confidence, 3),
                        "source": "motion" if is_motion else "yolo",
                    })
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
                label = (f"H {int(obs.confidence*100)}%" if is_motion
                         else f"P {int(obs.confidence*100)}%")
                cv2.putText(annotated, label, (x, max(0, y - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            events.extend(frame_events_this_tick)
            timeline.append({
                "frame": frame_idx,
                "time_sec": round(frame_idx / fps, 2),
                "detections": len(observations),
                "events_fired": len(frame_events_this_tick),
            })

            header = (f"Frame {frame_idx} | det={len(observations)} | "
                      f"events={len(events)}")
            cv2.rectangle(annotated, (0, 0), (min(width, 420), 24), (0, 0, 0), -1)
            cv2.putText(annotated, header, (6, 17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            writer.write(annotated)

            processed += 1
            frame_idx += 1
            sim_now = sim_now + _td(seconds=sim_step_sec)
    finally:
        cap.release()
        writer.release()

    elapsed = _time.time() - t0

    # Best-effort transcode for browser compatibility (same as test-video)
    browser_compatible = False
    try:
        import shutil as _sh, subprocess as _sp, os as _os
        ffmpeg = _sh.which("ffmpeg")
        if ffmpeg and _os.path.exists(output_path):
            tmp_out = output_path + ".h264.mp4"
            proc = _sp.run(
                [ffmpeg, "-y", "-i", output_path,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", tmp_out],
                capture_output=True, timeout=180,
            )
            if proc.returncode == 0 and _os.path.exists(tmp_out):
                _os.replace(tmp_out, output_path)
                browser_compatible = True
    except Exception:
        pass

    return {
        "source_fps": round(float(fps), 2),
        "source_frames": total_frames,
        "processed_frames": processed,
        "sample_rate": sample_rate,
        "auto_sampled": auto_sampled,
        "event_count": len(events),
        "events": events,
        "timeline": timeline,
        "rule_stats": rule_stats,
        "processing_time_sec": round(elapsed, 1),
        "browser_compatible": browser_compatible,
    }


@router.post("/api/plate/test-video-e2e")
async def plate_test_video_e2e(
    file: UploadFile,
    sample_rate: int = 1,
    save_to_archive: bool = True,
):
    """Run a video through the FULL Module 1 pipeline:
    fast-ALPR → tracker (multi-frame consensus) → MDB lookup → direction.

    Reports per-track: best plate reading, confidence, valid TR check,
    MDB match (owner/apartment), direction classification, would-alarm-fire.
    """
    import json as _json
    import uuid
    from datetime import datetime as _dt
    from pathlib import Path
    from config import settings, BASE_DIR as _BD
    import tempfile, time as _time

    if not _detector or not _db:
        raise HTTPException(503, "Module 1 not running")

    contents = await file.read()
    if not contents:
        raise HTTPException(400, "Empty upload")
    suffix = Path(file.filename or "test.mp4").suffix.lower() or ".mp4"
    if suffix not in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".asf"}:
        raise HTTPException(400, "Unsupported video format")
    tmp_input = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_input.write(contents)
    tmp_input.close()

    out_dir = Path(_BD) / "static" / "test_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"plate_e2e_{uuid.uuid4().hex[:8]}.mp4"
    out_path = out_dir / out_name

    loop = asyncio.get_event_loop()
    try:
        summary = await loop.run_in_executor(
            None, _run_plate_e2e, tmp_input.name, str(out_path), sample_rate, settings,
        )
    except Exception as e:
        logger.exception("Plate E2E test failed")
        raise HTTPException(500, f"Processing failed: {e}")
    finally:
        try: Path(tmp_input.name).unlink()
        except OSError: pass

    summary["annotated_video_url"] = f"/static/test_outputs/{out_name}"
    summary["filename"] = file.filename or "unknown"

    if save_to_archive and _db:
        try:
            _db.add_test_run(
                module="m1", test_type="video_e2e",
                source_filename=file.filename or "unknown",
                params_json=_json.dumps({"sample_rate": sample_rate}),
                summary_json=_json.dumps({
                    "tracks": summary.get("tracks", []),
                    "stats": summary.get("stats", {}),
                }),
                event_count=len(summary.get("tracks", [])),
                duration_sec=summary.get("processing_time_sec", 0),
                output_video_url=summary["annotated_video_url"],
            )
        except Exception:
            logger.exception("Failed to archive plate E2E test run")

    return JSONResponse(summary)


def _run_plate_e2e(input_path: str, output_path: str, sample_rate: int, settings) -> dict:
    """Frame-by-frame: detect → tracker.update → reap → MDB lookup → direction."""
    from app.tracker import PlateTracker
    from app.database import normalize_plate, is_valid_turkish_plate, levenshtein_distance
    from datetime import datetime as _dt, timedelta as _td
    import time as _time

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError("Cannot open video")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
    sample_rate = max(1, int(sample_rate))

    tracker = PlateTracker(
        iou_threshold=settings.TRACK_IOU_THRESHOLD,
        fuzzy_tolerance=settings.TRACK_FUZZY_TOLERANCE,
        idle_frames=settings.TRACK_IDLE_FRAMES,
        max_duration_sec=settings.TRACK_MAX_DURATION_SEC,
        min_frames_for_commit=settings.MIN_FRAMES_FOR_COMMIT,
        direction_area_ratio=settings.DIRECTION_AREA_RATIO,
        entry_size_change=settings.CAMERA_ENTRY_SIZE_CHANGE,
        entry_y_direction=settings.CAMERA_ENTRY_Y_DIRECTION,
        single_commit_conf=getattr(settings, "COMMIT_SINGLE_CONF", 0.9),
    )

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_fps = max(1.0, fps / sample_rate)
    writer = cv2.VideoWriter(output_path, fourcc, out_fps, (width, height))

    committed_tracks: list[dict] = []
    # Canonical-dedup: collapse fragmented tracks of the SAME vehicle into one
    # passage (1 car = 1 record), keeping the most confident + definite-direction
    # reading. See PASSAGE_DEDUP_SEC. canon maps plate -> its COMMIT dict.
    _canon: dict = {}
    _dedup_win = settings.PASSAGE_DEDUP_SEC
    _dedup_tol = settings.TRACK_FUZZY_TOLERANCE

    def _dir_winner(votes: dict) -> str:
        # prefer the most-voted DEFINITE direction; fall back to unknown
        definite = {d: w for d, w in votes.items() if d not in ("unknown", "")}
        if definite:
            return max(definite, key=definite.get)
        return "unknown"

    def _emit_commit(track, best, eof=False):
        direction = tracker.classify_direction(track)
        weight = max(1, len(track.readings))   # longer track = more reliable direction
        vehicle = _db.find_vehicle(best.normalized, settings.FUZZY_TOLERANCE)
        is_authorized = vehicle is not None
        conf = round(best.confidence, 3)
        # find a recent canonical commit with a fuzzy-matching plate
        match = None
        for p, cd in _canon.items():
            if (levenshtein_distance(best.normalized, p) <= _dedup_tol and
                    (best.timestamp - cd["_ts"]).total_seconds() <= _dedup_win):
                match = (p, cd); break
        if match is not None:
            _, cd = match
            cd["_ts"] = best.timestamp
            cd["_merged"] = cd.get("_merged", 1) + 1
            # Frame-weighted direction VOTE across fragments (a single short
            # fragment can misclassify; the majority of the trajectory wins).
            cd["_votes"][direction] = cd["_votes"].get(direction, 0) + weight
            cd["direction"] = _dir_winner(cd["_votes"])
            if conf > cd["confidence"]:
                cd["confidence"] = conf
                cd["plate"] = best.plate_text
                cd["normalized"] = best.normalized
                cd["valid_tr"] = is_valid_turkish_plate(best.normalized)
            cd["reason"] = f"{cd.get('_merged',1)} parça birleştirildi (1 araç = 1 geçiş)"
            committed_tracks.append({
                "track_id": track.id, "outcome": "DUPLICATE",
                "reason": f"Aynı araç — track #{cd['track_id']} ile birleşti",
                "normalized": best.normalized,
            })
            return
        cd = {
            "track_id": track.id,
            "outcome": "COMMIT (eof)" if eof else "COMMIT",
            "plate": best.plate_text,
            "normalized": best.normalized,
            "confidence": conf,
            "valid_tr": is_valid_turkish_plate(best.normalized),
            "direction": direction,
            "authorized": is_authorized,
            "owner_name": vehicle.owner_name if vehicle else None,
            "apartment": vehicle.apartment if vehicle else None,
            "would_alarm": not is_authorized,
            "frame_count": len(track.readings),
            "readings_history": [
                {"plate": r.plate_text, "norm": r.normalized,
                 "conf": round(r.confidence, 3), "valid": r.valid}
                for r in track.readings[-5:]
            ],
            "_ts": best.timestamp,
            "_merged": 1,
            "_votes": {direction: weight},
        }
        _canon[best.normalized] = cd
        committed_tracks.append(cd)
    detection_count = 0
    valid_detection_count = 0
    frame_idx = 0
    processed = 0
    t0 = _time.time()
    sim_now = _dt(2026, 1, 1, 12, 0, 0)
    sim_step = _td(seconds=sample_rate / max(1.0, fps))

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % sample_rate != 0:
                frame_idx += 1
                continue

            detections = _detector.detect(frame)
            detection_count += len(detections)
            for d in detections:
                if d.frame is None:
                    d.frame = frame
                # Attach simulated time so tracker GC and direction logic
                # see realistic intervals between frames
                d.timestamp = sim_now
                if is_valid_turkish_plate(normalize_plate(d.plate_text or "")):
                    valid_detection_count += 1

            tracker.update(detections)
            finalized = tracker.reap(sim_now)

            for track in finalized:
                best = tracker.pick_best_reading(track)
                if best is None:
                    valid_count = sum(1 for r in track.readings if r.valid)
                    committed_tracks.append({
                        "track_id": track.id,
                        "outcome": "DROP",
                        "reason": f"Sadece {valid_count}/{len(track.readings)} geçerli okuma (min: {tracker.min_frames_for_commit})",
                        "readings": [r.normalized for r in track.readings if r.normalized],
                    })
                    continue
                _emit_commit(track, best)

            # Annotate
            annotated = frame.copy()
            for d in detections:
                if d.bbox is None:
                    continue
                x, y, w, h = d.bbox
                color = (0, 255, 0) if is_valid_turkish_plate(d.normalized_plate) else (0, 165, 255)
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
                cv2.putText(annotated, d.plate_text, (x, max(0, y - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            cv2.rectangle(annotated, (0, 0), (min(width, 380), 24), (0, 0, 0), -1)
            cv2.putText(annotated, f"frame={frame_idx} det={len(detections)} tracks={len(committed_tracks)}",
                        (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            writer.write(annotated)
            processed += 1
            frame_idx += 1
            sim_now += sim_step

        # Flush any still-active tracks at video end
        # (force reap by jumping the clock by the max duration)
        sim_now_end = sim_now + _td(seconds=tracker.max_duration_sec + 1)
        for track in tracker.reap(sim_now_end):
            best = tracker.pick_best_reading(track)
            if best is None:
                continue
            _emit_commit(track, best, eof=True)
    finally:
        cap.release()
        writer.release()

    # Strip internal bookkeeping keys (datetime isn't JSON-serializable)
    for t in committed_tracks:
        t.pop("_ts", None)
        t.pop("_votes", None)
        if "_merged" in t:
            t["merged_fragments"] = t.pop("_merged")

    # Browser transcode
    browser_compatible = False
    try:
        import shutil as _sh, subprocess as _sp, os as _os
        ffmpeg = _sh.which("ffmpeg")
        if ffmpeg and _os.path.exists(output_path):
            tmp_out = output_path + ".h264.mp4"
            proc = _sp.run(
                [ffmpeg, "-y", "-i", output_path,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", tmp_out],
                capture_output=True, timeout=180,
            )
            if proc.returncode == 0 and _os.path.exists(tmp_out):
                _os.replace(tmp_out, output_path)
                browser_compatible = True
    except Exception:
        pass

    return {
        "source_fps": round(float(fps), 2),
        "processed_frames": processed,
        "sample_rate": sample_rate,
        "stats": {
            "detections": detection_count,
            "valid_detections": valid_detection_count,
            "committed_tracks": sum(1 for t in committed_tracks if t["outcome"].startswith("COMMIT")),
            "dropped_tracks": sum(1 for t in committed_tracks if t["outcome"] == "DROP"),
        },
        "tracks": committed_tracks,
        "processing_time_sec": round(_time.time() - t0, 1),
        "browser_compatible": browser_compatible,
    }


# ════════════════════════════════════════════════════════════════
# P2.3 + P2.5 — Test run archive
# ════════════════════════════════════════════════════════════════

@router.get("/api/test-runs")
async def list_test_runs(limit: int = 50, module: str | None = None):
    if not _db:
        return JSONResponse({"runs": []})
    runs = _db.list_test_runs(limit=limit, module=module)
    return JSONResponse({"runs": runs})


@router.get("/api/test-runs/{run_id}")
async def get_test_run(run_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    run = _db.get_test_run(run_id)
    if not run:
        raise HTTPException(404, "Test run not found")
    import json as _json
    for key in ("params_json", "summary_json"):
        try:
            run[key.replace("_json", "")] = _json.loads(run[key]) if run.get(key) else {}
        except Exception:
            run[key.replace("_json", "")] = {}
    return JSONResponse(run)


@router.delete("/api/test-runs/{run_id}")
async def delete_test_run(run_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    ok = _db.delete_test_run(run_id)
    if not ok:
        raise HTTPException(404, "Not found")
    return JSONResponse({"deleted": True})


@router.get("/api/test-runs/export.xlsx")
async def export_test_runs_xlsx(module: str | None = None, limit: int = 500):
    """Export test runs to an XLSX file — each run is a row in a summary sheet."""
    if not _db:
        raise HTTPException(503, "Database not ready")
    try:
        from openpyxl import Workbook
    except ImportError:
        raise HTTPException(500, "openpyxl not installed (pip install openpyxl)")

    runs = _db.list_test_runs(limit=limit, module=module)
    wb = Workbook()
    ws = wb.active
    ws.title = "Test Runs"
    ws.append([
        "ID", "Tarih/Saat", "Modül", "Test Tipi", "Dosya",
        "Kamera ID", "Olay Sayısı", "Süre (sn)", "Çıktı Video",
    ])
    for r in runs:
        ws.append([
            r["id"], r["ran_at"], r["module"], r["test_type"],
            r["source_filename"], r["camera_id"],
            r["event_count"], r["duration_sec"], r["output_video_url"],
        ])
    import io as _io
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"gateguard_test_runs_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
