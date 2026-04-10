"""ESP32 HTTP alarm manager with cooldown and state tracking."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

logger = logging.getLogger("gateguard.alarms")


class AlarmManager:
    def __init__(self, esp32_ip: str, cooldown_sec: int = 60, mock_mode: bool = False):
        self.esp32_ip = esp32_ip
        self.cooldown_sec = cooldown_sec
        self.mock_mode = mock_mode
        self.alarm_active: bool = False
        self.last_alarm_plate: str = ""
        self.last_alarm_time: datetime | None = None
        self._recent_plates: dict[str, datetime] = {}
        self._client = httpx.AsyncClient(timeout=5.0)

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

        # Cleanup old entries from cooldown dict
        self._recent_plates = {
            k: v for k, v in self._recent_plates.items()
            if (now - v).total_seconds() < self.cooldown_sec * 2
        }

        logger.warning("ALARM TRIGGERED: plate=%s", plate)

        if self.mock_mode:
            logger.info("[MOCK] ESP32 alarm ON skipped (mock mode)")
            return True

        try:
            resp = await self._client.get(f"http://{self.esp32_ip}/alarm/on")
            if resp.status_code == 200:
                logger.info("ESP32 alarm ON sent successfully")
                return True
            else:
                logger.error("ESP32 alarm ON failed: HTTP %d", resp.status_code)
                return False
        except httpx.HTTPError as e:
            logger.error("ESP32 connection error: %s", e)
            return True  # Alarm state is still active in software

    async def silence_alarm(self) -> bool:
        """Send alarm OFF to ESP32. Returns True if successful."""
        self.alarm_active = False
        logger.info("Alarm silenced")

        if self.mock_mode:
            logger.info("[MOCK] ESP32 alarm OFF skipped (mock mode)")
            return True

        try:
            resp = await self._client.get(f"http://{self.esp32_ip}/alarm/off")
            if resp.status_code == 200:
                logger.info("ESP32 alarm OFF sent successfully")
                return True
            else:
                logger.error("ESP32 alarm OFF failed: HTTP %d", resp.status_code)
                return False
        except httpx.HTTPError as e:
            logger.error("ESP32 connection error: %s", e)
            return False

    async def close(self):
        await self._client.aclose()
