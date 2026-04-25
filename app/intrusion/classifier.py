"""IntrusionClassifier — 5-rule filter that decides if an observation becomes an event.

Rules (ALL must pass for an alarm to fire):
  1. Confidence >= min_confidence (per-frame filter).
  2. Bbox center lies within at least one ACTIVE zone (ZoneManager already
     applies night_only vs always-on filtering via active_zones_at()).
  3. Track has been seen in the zone for >= min_consecutive_frames recent frames.
     If the gap between observations exceeds frame_gap_reset_sec, the counter
     resets — this kills single-frame false positives (a flicker shadow,
     mannequin glint, brief occlusion artifact) without delaying real intruders.
  4. Track has been observed in the zone for >= zone.min_loiter_sec (loitering).
  5. No recent alarm for this (camera_id, zone_id, track_id) triple — cooldown.

State held per (camera_id, zone_id, track_id):
  - first_seen: datetime — first frame this track was observed in this zone
  - last_obs:   datetime — most recent observation (for frame-count reset)
  - frame_count: int    — consecutive observation count
  - last_alarm: datetime — last alarm time for cooldown suppression

Stale tracks (not seen for > 60s) are garbage collected.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from app.intrusion.models import IntrusionEvent, PersonObservation, Zone
from app.intrusion.zone_manager import ZoneManager, bbox_center_normalized

logger = logging.getLogger("gateguard.app")


TrackKey = tuple[int, int, int]   # (camera_id, zone_id, track_id)


class IntrusionClassifier:
    """Stateful 4-rule classifier. Thread-safe across cameras."""

    def __init__(
        self,
        zone_manager: ZoneManager,
        min_confidence: float = 0.30,
        cooldown_sec: int = 30,
        warmup_sec: int = 30,
        stale_track_sec: int = 60,
        min_consecutive_frames: int = 3,
        frame_gap_reset_sec: float = 3.0,
    ):
        self.zone_manager = zone_manager
        self.min_confidence = min_confidence
        self.cooldown_sec = cooldown_sec
        self.warmup_sec = warmup_sec
        self.stale_track_sec = stale_track_sec
        self.min_consecutive_frames = max(1, int(min_consecutive_frames))
        self.frame_gap_reset_sec = float(frame_gap_reset_sec)

        # Per-camera warmup tracking (system start time per camera)
        self._camera_started: dict[int, datetime] = {}

        self._first_seen: dict[TrackKey, datetime] = {}
        self._last_obs: dict[TrackKey, datetime] = {}
        self._frame_count: dict[TrackKey, int] = {}
        self._last_alarm: dict[TrackKey, datetime] = {}
        self._last_cleanup = datetime.now()

        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────

    def mark_camera_started(self, camera_id: int, now: Optional[datetime] = None) -> None:
        """Call when a camera engine starts so we know when warmup began."""
        if now is None:
            now = datetime.now()
        with self._lock:
            self._camera_started[camera_id] = now

    def classify(
        self,
        observation: PersonObservation,
        frame_w: int,
        frame_h: int,
        now: Optional[datetime] = None,
    ) -> Optional[IntrusionEvent]:
        """Return an IntrusionEvent if all 4 rules pass, else None."""
        if now is None:
            now = observation.timestamp or datetime.now()

        # Rule 0: warmup period
        started = self._camera_started.get(observation.camera_id)
        if started and (now - started).total_seconds() < self.warmup_sec:
            return None

        # Rule 1: confidence
        if observation.confidence < self.min_confidence:
            return None

        # Rule 2: bbox center in any active zone
        point = bbox_center_normalized(observation.bbox, frame_w, frame_h)
        matching = self.zone_manager.matching_zones(
            observation.camera_id, point, now
        )
        if not matching:
            self._maybe_gc(now)
            return None

        # Check each matching zone in turn — pick the first that fires
        for zone in matching:
            event = self._evaluate_zone(observation, zone, now)
            if event is not None:
                return event

        self._maybe_gc(now)
        return None

    def forget_track(self, camera_id: int, track_id: int) -> None:
        """Clear state for a track that is no longer observed (optional)."""
        with self._lock:
            for store in (self._first_seen, self._last_obs, self._frame_count, self._last_alarm):
                for key in list(store.keys()):
                    if key[0] == camera_id and key[2] == track_id:
                        del store[key]

    # ── Internals ────────────────────────────────────────────────

    def _evaluate_zone(
        self,
        observation: PersonObservation,
        zone: Zone,
        now: datetime,
    ) -> Optional[IntrusionEvent]:
        key: TrackKey = (observation.camera_id, zone.id, observation.track_id)

        with self._lock:
            # ── Frame-count bookkeeping ──────────────────────────
            prev_obs = self._last_obs.get(key)
            if prev_obs is None or (now - prev_obs).total_seconds() > self.frame_gap_reset_sec:
                # First time in zone, OR returned after a long gap → restart counter
                self._first_seen[key] = now
                self._frame_count[key] = 1
            else:
                self._frame_count[key] = self._frame_count.get(key, 0) + 1
            self._last_obs[key] = now

            # Rule 3: minimum consecutive frames (kills single-frame false positives)
            if self._frame_count[key] < self.min_consecutive_frames:
                return None

            # Rule 4: loitering duration
            duration = (now - self._first_seen[key]).total_seconds()
            if duration < zone.min_loiter_sec:
                return None

            # Rule 5: cooldown
            last = self._last_alarm.get(key)
            if last is not None:
                elapsed = (now - last).total_seconds()
                if elapsed < self.cooldown_sec:
                    return None

            # All rules pass → commit alarm
            self._last_alarm[key] = now
            frame_count_at_alarm = self._frame_count[key]

        logger.info(
            "Intrusion event: camera=%d zone=%d track=%d frames=%d duration=%.1fs conf=%.2f",
            observation.camera_id, zone.id, observation.track_id,
            frame_count_at_alarm, duration, observation.confidence,
        )

        return IntrusionEvent(
            camera_id=observation.camera_id,
            zone_id=zone.id,
            track_id=observation.track_id,
            detected_at=now,
            duration_sec=duration,
            confidence=observation.confidence,
            person_count=1,
        )

    def _maybe_gc(self, now: datetime) -> None:
        """Garbage-collect stale track state every ~30 seconds."""
        if (now - self._last_cleanup).total_seconds() < 30:
            return
        with self._lock:
            cutoff = now - timedelta(seconds=self.stale_track_sec)
            stale_first = [k for k, v in self._first_seen.items() if v < cutoff]
            for k in stale_first:
                self._first_seen.pop(k, None)
                self._last_obs.pop(k, None)
                self._frame_count.pop(k, None)
            # Keep last_alarm a bit longer (cooldown must survive brief reappear)
            cooldown_cutoff = now - timedelta(seconds=self.cooldown_sec * 2)
            stale_alarm = [k for k, v in self._last_alarm.items() if v < cooldown_cutoff]
            for k in stale_alarm:
                del self._last_alarm[k]
            self._last_cleanup = now
            if stale_first or stale_alarm:
                logger.debug(
                    "Classifier GC: removed %d first_seen, %d last_alarm",
                    len(stale_first), len(stale_alarm),
                )

    # Diagnostics
    def active_track_count(self) -> int:
        return len(self._first_seen)

    def active_cooldown_count(self) -> int:
        return len(self._last_alarm)
