"""ESP32 HTTP alarm manager with cooldown, connectivity tracking, and fail-safe alerts."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import httpx

logger = logging.getLogger("gateguard.alarms")


class AlarmManager:
    # If we get this many consecutive failed pings/commands, the ESP32 is
    # considered DOWN — UI shows a persistent red banner. One success reverts.
    MAX_CONSECUTIVE_FAILURES = 3
    HEARTBEAT_INTERVAL_SEC = 60

    def __init__(self, esp32_ip: str, cooldown_sec: int = 60, mock_mode: bool = False,
                 enabled: bool = True):
        self.esp32_ip = esp32_ip
        self.cooldown_sec = cooldown_sec
        self.mock_mode = mock_mode
        # When the physical siren is disabled (optional hardware), we never call
        # the ESP32 and never report it as "down" — there is nothing to monitor.
        self.enabled = enabled
        self.alarm_active: bool = False
        self.last_alarm_plate: str = ""
        self.last_alarm_time: datetime | None = None
        self.last_alarm_source: str = ""
        self._recent_plates: dict[str, datetime] = {}
        self._recent_intrusions: dict[tuple[int, int, int], datetime] = {}
        self._client = httpx.AsyncClient(timeout=5.0)

        # ── Connectivity tracking ──
        self._consecutive_failures: int = 0
        self._last_ok_at: float = 0.0
        self._last_attempt_at: float = 0.0
        self._last_error: str = ""
        self._was_down_announced: bool = False
        self._heartbeat_task: asyncio.Task | None = None
        # Set by main.py after ws_manager exists
        self._ws_manager = None

    # ── Connectivity helpers ──────────────────────────────────────

    def attach_ws_manager(self, ws_manager) -> None:
        """Inject ws_manager so we can broadcast hardware-down events."""
        self._ws_manager = ws_manager

    def is_hardware_ok(self) -> bool:
        """True if ESP32 has responded recently. Mock/disabled is always 'ok'."""
        if self.mock_mode or not self.enabled:
            return True
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            return False
        return True

    def get_hardware_status(self) -> dict:
        now = time.time()
        return {
            "ok": self.is_hardware_ok(),
            "enabled": self.enabled,
            "esp32_ip": self.esp32_ip,
            "mock_mode": self.mock_mode,
            "consecutive_failures": self._consecutive_failures,
            "last_ok_ago_sec": round(now - self._last_ok_at, 1) if self._last_ok_at else None,
            "last_attempt_ago_sec": round(now - self._last_attempt_at, 1) if self._last_attempt_at else None,
            "last_error": self._last_error,
        }

    async def start_heartbeat(self) -> None:
        """Spawn background task that pings ESP32 every minute.
        Surfaces hardware-down state to UI even when no alarms are firing.
        """
        if self.mock_mode:
            logger.info("ESP32 heartbeat skipped (mock mode)")
            return
        if not self.enabled:
            logger.info("ESP32 heartbeat skipped (siren disabled)")
            return
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        # Wait a few seconds before first ping (let everything settle)
        await asyncio.sleep(5)
        while True:
            try:
                await self._ping_esp32()
            except Exception:
                logger.exception("ESP32 heartbeat error")
            await asyncio.sleep(self.HEARTBEAT_INTERVAL_SEC)

    async def _ping_esp32(self) -> bool:
        """Background-friendly health check. Records success/failure state."""
        self._last_attempt_at = time.time()
        try:
            resp = await self._client.get(
                f"http://{self.esp32_ip}/status", timeout=3.0,
            )
            if resp.status_code == 200:
                await self._record_success("heartbeat")
                return True
            self._last_error = f"HTTP {resp.status_code}"
            await self._record_failure()
            return False
        except httpx.HTTPError as e:
            self._last_error = str(e)[:200]
            await self._record_failure()
            return False

    async def _record_success(self, source: str) -> None:
        self._last_ok_at = time.time()
        self._last_error = ""
        # If we were previously down, announce recovery
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            logger.warning("ESP32 RECOVERED after %d failures (%s)",
                           self._consecutive_failures, source)
            self._was_down_announced = False
            await self._broadcast_status(ok=True)
        self._consecutive_failures = 0

    async def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures == self.MAX_CONSECUTIVE_FAILURES:
            logger.error(
                "ESP32 hardware DOWN (%d consecutive failures, last error: %s)",
                self._consecutive_failures, self._last_error,
            )
            if not self._was_down_announced:
                self._was_down_announced = True
                await self._broadcast_status(ok=False)

    async def _broadcast_status(self, ok: bool) -> None:
        if not self._ws_manager:
            return
        try:
            await self._ws_manager.broadcast({
                "type": "alarm_hardware_status",
                "data": {
                    "ok": ok,
                    "esp32_ip": self.esp32_ip,
                    "last_error": self._last_error,
                },
            })
        except Exception:
            logger.exception("Failed to broadcast hardware status")

    # ── Plate alarms (Module 1) ────────────────────────────────────

    def should_trigger(self, plate_normalized: str) -> bool:
        """Check if this plate should trigger an alarm (respects cooldown)."""
        now = datetime.now()
        if plate_normalized in self._recent_plates:
            elapsed = (now - self._recent_plates[plate_normalized]).total_seconds()
            if elapsed < self.cooldown_sec:
                return False
        return True

    async def trigger_alarm(self, plate: str, plate_normalized: str) -> bool:
        """Send alarm ON to ESP32. Returns True if successful."""
        now = datetime.now()
        self._recent_plates[plate_normalized] = now
        self.alarm_active = True
        self.last_alarm_plate = plate
        self.last_alarm_time = now
        self.last_alarm_source = "plate"

        # Cleanup old entries from cooldown dict
        self._recent_plates = {
            k: v for k, v in self._recent_plates.items()
            if (now - v).total_seconds() < self.cooldown_sec * 2
        }

        logger.warning("ALARM TRIGGERED: plate=%s", plate)

        if self.mock_mode:
            logger.info("[MOCK] ESP32 alarm ON skipped (mock mode)")
            return True

        return await self._send_to_esp32("/alarm/on", "plate alarm ON")

    async def silence_alarm(self) -> bool:
        """Send alarm OFF to ESP32. Returns True if successful."""
        self.alarm_active = False
        logger.info("Alarm silenced")

        if self.mock_mode:
            logger.info("[MOCK] ESP32 alarm OFF skipped (mock mode)")
            return True

        return await self._send_to_esp32("/alarm/off", "alarm OFF")

    # ── Module 2: intrusion-keyed alarms ────────────────────────────

    def should_trigger_intrusion(
        self, camera_id: int, zone_id: int, track_id: int,
        cooldown_sec: int | None = None,
    ) -> bool:
        now = datetime.now()
        key = (camera_id, zone_id, track_id)
        cd = cooldown_sec if cooldown_sec is not None else self.cooldown_sec
        last = self._recent_intrusions.get(key)
        if last is None:
            return True
        return (now - last).total_seconds() >= cd

    async def trigger_intrusion_alarm(
        self, camera_id: int, zone_id: int, track_id: int, label: str = "",
    ) -> bool:
        now = datetime.now()
        key = (camera_id, zone_id, track_id)
        self._recent_intrusions[key] = now
        self.alarm_active = True
        self.last_alarm_plate = label or f"CAM{camera_id}_Z{zone_id}"
        self.last_alarm_time = now
        self.last_alarm_source = "intrusion"

        self._recent_intrusions = {
            k: v for k, v in self._recent_intrusions.items()
            if (now - v).total_seconds() < self.cooldown_sec * 2
        }

        logger.warning("INTRUSION ALARM: camera=%d zone=%d track=%d",
                       camera_id, zone_id, track_id)

        if self.mock_mode:
            logger.info("[MOCK] ESP32 intrusion alarm skipped")
            return True

        return await self._send_to_esp32("/alarm/on", "intrusion alarm ON")

    # ── Shared ESP32 HTTP wrapper ───────────────────────────────────

    async def _send_to_esp32(self, path: str, label: str) -> bool:
        """Single-point HTTP wrapper — records success/failure in connectivity tracking."""
        if not self.enabled:
            logger.info("ESP32 %s skipped (siren disabled)", label)
            return True
        try:
            resp = await self._client.get(f"http://{self.esp32_ip}{path}")
            if resp.status_code == 200:
                await self._record_success(label)
                logger.info("ESP32 %s sent successfully", label)
                return True
            self._last_error = f"HTTP {resp.status_code}"
            await self._record_failure()
            logger.error("ESP32 %s failed: HTTP %d", label, resp.status_code)
            return False
        except httpx.HTTPError as e:
            self._last_error = str(e)[:200]
            await self._record_failure()
            logger.error("ESP32 %s connection error: %s", label, e)
            return False

    async def close(self):
        await self.stop_heartbeat()
        await self._client.aclose()
