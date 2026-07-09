"""Startup validation — runs once at boot.

Confirms every external dependency (RTSP, ESP32, MDB, YOLO weights, disk space)
is reachable BEFORE the detection loops start spinning. Surfaces a single
JSON summary via /api/health.startup so the UI can display a checklist.

Failures are NEVER fatal — the server still starts so the operator can fix
issues from the UI. Each check has a 3-5 second timeout to avoid blocking
startup more than ~30 seconds in the worst case.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import time
from pathlib import Path
from typing import Any

import cv2
import httpx

logger = logging.getLogger("gateguard.app")

# Module-level cache so /api/health can read the results
_STARTUP_RESULTS: dict[str, Any] = {
    "ran_at": None,
    "duration_sec": 0,
    "checks": [],
    "ok": False,
}


def get_startup_results() -> dict:
    return dict(_STARTUP_RESULTS)


async def validate_startup(
    db,
    rtsp_url: str | None,
    mdb_path: str,
    esp32_ip: str,
    yolo_model_path: str,
    base_dir: str,
    mock_mode: bool,
    intrusion_enabled: bool,
    siren_enabled: bool = True,
) -> dict:
    """Run all startup checks in parallel where possible. Returns aggregated dict."""
    t0 = time.time()
    checks: list[dict] = []

    # ── 1. Disk space ──
    try:
        d = shutil.disk_usage(base_dir)
        free_gb = d.free / (1024 ** 3)
        if free_gb >= 5:
            checks.append({"name": "Disk alanı", "ok": True,
                           "detail": f"{free_gb:.1f} GB boş"})
        elif free_gb >= 2:
            checks.append({"name": "Disk alanı", "ok": True, "warn": True,
                           "detail": f"Az kaldı: {free_gb:.1f} GB"})
        else:
            checks.append({"name": "Disk alanı", "ok": False,
                           "detail": f"Kritik: {free_gb:.1f} GB"})
    except Exception as e:
        checks.append({"name": "Disk alanı", "ok": False, "detail": str(e)[:200]})

    # ── 2. DB writable ──
    try:
        if db:
            db.conn.execute("SELECT 1").fetchone()
            checks.append({"name": "Veritabanı", "ok": True, "detail": "Bağlantı tamam"})
        else:
            checks.append({"name": "Veritabanı", "ok": False, "detail": "DB henüz açılmadı"})
    except Exception as e:
        checks.append({"name": "Veritabanı", "ok": False, "detail": str(e)[:200]})

    # ── 3. YOLO model file ──
    try:
        ypath = Path(yolo_model_path)
        if ypath.exists():
            size_mb = ypath.stat().st_size / (1024 * 1024)
            checks.append({"name": "YOLO modeli", "ok": True,
                           "detail": f"{ypath.name} ({size_mb:.1f} MB)"})
        else:
            # YOLO modelleri ultralytics tarafından otomatik indirilir, info olarak işle
            checks.append({"name": "YOLO modeli", "ok": True, "warn": True,
                           "detail": f"{ypath.name} bulunamadı — ilk çalıştırmada otomatik indirilecek"})
    except Exception as e:
        checks.append({"name": "YOLO modeli", "ok": False, "detail": str(e)[:200]})

    # ── 4. MDB file (Moonwell) ──
    try:
        if not mdb_path:
            checks.append({"name": "Moonwell MDB", "ok": True, "warn": True,
                           "detail": "MDB_PATH boş — yetkili plaka listesi YOK"})
        elif Path(mdb_path).exists():
            checks.append({"name": "Moonwell MDB", "ok": True,
                           "detail": f"{Path(mdb_path).name} bulundu"})
        else:
            checks.append({"name": "Moonwell MDB", "ok": False,
                           "detail": f"Dosya bulunamadı: {mdb_path}"})
    except Exception as e:
        checks.append({"name": "Moonwell MDB", "ok": False, "detail": str(e)[:200]})

    # ── 5. RTSP camera reachability ──
    # Skip when mock mode (no real RTSP) and when no URL given.
    if not mock_mode and rtsp_url:
        rtsp_ok, rtsp_detail = await _check_rtsp(rtsp_url, timeout=6.0)
        checks.append({"name": "Plaka kamerası (RTSP)", "ok": rtsp_ok, "detail": rtsp_detail})
    elif mock_mode:
        checks.append({"name": "Plaka kamerası", "ok": True,
                       "detail": "Mock mod aktif — fiziksel kamera atlandı"})

    # ── 6. ESP32 reachability ──
    if not siren_enabled:
        checks.append({"name": "ESP32 siren", "ok": True,
                       "detail": "Siren devre dışı (opsiyonel) — atlandı"})
    elif not mock_mode and esp32_ip:
        esp32_ok, esp32_detail = await _check_esp32(esp32_ip, timeout=3.0)
        checks.append({"name": "ESP32 siren", "ok": esp32_ok, "detail": esp32_detail})
    elif mock_mode:
        checks.append({"name": "ESP32 siren", "ok": True,
                       "detail": "Mock mod aktif — fiziksel siren atlandı"})

    # ── 7. Multi-camera RTSP'leri (Module 2 için DB'deki kameralar) ──
    if intrusion_enabled and db:
        try:
            cams = db.list_cameras(enabled_only=True)
            for cam in cams:
                if cam.get("role") in ("intrusion", "both"):
                    name = cam.get("name") or f"Cam {cam.get('id')}"
                    url = cam.get("rtsp_url") or ""
                    if not url:
                        continue
                    cam_ok, cam_detail = await _check_rtsp(url, timeout=4.0)
                    checks.append({"name": f"Kamera: {name}", "ok": cam_ok, "detail": cam_detail})
        except Exception as e:
            logger.exception("Multi-camera RTSP validation failed")
            checks.append({"name": "Modül 2 kameraları", "ok": False, "detail": str(e)[:200]})

    duration = time.time() - t0
    blockers = sum(1 for c in checks if not c.get("ok"))
    warnings = sum(1 for c in checks if c.get("warn"))
    overall_ok = blockers == 0

    _STARTUP_RESULTS.update({
        "ran_at": time.time(),
        "duration_sec": round(duration, 2),
        "checks": checks,
        "ok": overall_ok,
        "blockers": blockers,
        "warnings": warnings,
    })

    logger.info(
        "Startup validation: %d check, %d hata, %d uyarı (%.1fs)",
        len(checks), blockers, warnings, duration,
    )
    for c in checks:
        prefix = "[✓]" if c.get("ok") and not c.get("warn") else "[!]" if c.get("warn") else "[✗]"
        logger.info("%s %s — %s", prefix, c["name"], c.get("detail", ""))

    return _STARTUP_RESULTS


# ── Helpers ──────────────────────────────────────────────────────

async def _check_rtsp(url: str, timeout: float = 6.0) -> tuple[bool, str]:
    """OpenCV ile RTSP bağlantısını dener; ilk frame'i alırsa OK.
    Bloke I/O içerdiğinden run_in_executor ile çağrılır.
    """
    loop = asyncio.get_event_loop()
    try:
        ok, msg = await asyncio.wait_for(
            loop.run_in_executor(None, _try_open_rtsp, url),
            timeout=timeout,
        )
        return ok, msg
    except asyncio.TimeoutError:
        return False, f"Zaman aşımı (>{timeout:.0f}s)"
    except Exception as e:
        return False, str(e)[:200]


def _try_open_rtsp(url: str) -> tuple[bool, str]:
    cap = cv2.VideoCapture(url)
    try:
        if not cap.isOpened():
            return False, "Bağlantı açılamadı (RTSP URL/credentials kontrol et)"
        ret, _ = cap.read()
        if not ret:
            return False, "Bağlandı ama frame okunamadı"
        return True, "Stream OK"
    finally:
        cap.release()


async def _check_esp32(ip: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"http://{ip}/status")
            if resp.status_code == 200:
                return True, f"{ip} cevap verdi"
            return False, f"HTTP {resp.status_code}"
    except httpx.ConnectTimeout:
        return False, f"{ip} ulaşılamıyor (timeout)"
    except httpx.HTTPError as e:
        return False, f"{ip}: {e}"
    except Exception as e:
        return False, str(e)[:200]
