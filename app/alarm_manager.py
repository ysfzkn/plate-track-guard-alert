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
        self.last_alarm_source: str = ""           # "plate" | "intrusion"
        self._recent_plates: dict[str, datetime] = {}
        # Per-(camera_id, zone_id, track_id) cooldown for Module 2 intrusions
        self._recent_intrusions: dict[tuple[int, int, int], datetime] = {}
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

    # ── Module 2: intrusion-keyed alarms ────────────────────────

    def should_trigger_intrusion(
        self,
        camera_id: int,
        zone_id: int,
        track_id: int,
        cooldown_sec: int | None = None,
    ) -> bool:
        """Intrusion cooldown: per (camera, zone, track) triple.

        Separate from plate cooldown so a plate alarm doesn't suppress
        an intrusion alarm on the same device (and vice versa).
        """
        now = datetime.now()
        key = (camera_id, zone_id, track_id)
        cd = cooldown_sec if cooldown_sec is not None else self.cooldown_sec
        last = self._recent_intrusions.get(key)
        if last is None:
            return True
        return (now - last).total_seconds() >= cd

    async def trigger_intrusion_alarm(
        self,
        camera_id: int,
        zone_id: int,
        track_id: int,
        label: str = "",
    ) -> bool:
        """Fire siren for an intrusion event. Mirrors trigger_alarm() but
        keyed on (camera, zone, track) rather than plate string.
        """
        now = datetime.now()
        key = (camera_id, zone_id, track_id)
        self._recent_intrusions[key] = now
        self.alarm_active = True
        self.last_alarm_plate = label or f"CAM{camera_id}_Z{zone_id}"
        self.last_alarm_time = now
        self.last_alarm_source = "intrusion"

        # GC old entries
        self._recent_intrusions = {
            k: v for k, v in self._recent_intrusions.items()
            if (now - v).total_seconds() < self.cooldown_sec * 2
        }

        logger.warning(
            "INTRUSION ALARM: camera=%d zone=%d track=%d",
            camera_id, zone_id, track_id,
        )

        if self.mock_mode:
            logger.info("[MOCK] ESP32 intrusion alarm skipped")
            return True

        try:
            resp = await self._client.get(f"http://{self.esp32_ip}/alarm/on")
            if resp.status_code == 200:
                logger.info("ESP32 alarm ON (intrusion) sent")
                return True
            logger.error("ESP32 alarm ON (intrusion) failed: HTTP %d", resp.status_code)
            return False
        except httpx.HTTPError as e:
            logger.error("ESP32 connection error (intrusion): %s", e)
            return True  # keep software alarm active

    async def close(self):
        await self._client.aclose()
