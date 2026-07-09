"""ZoneManager — polygon CRUD cache + point-in-polygon testing.

Zones are stored in DB with normalized coordinates (0..1 range). At runtime,
we cache them in memory per camera for fast repeated point-in-polygon testing.
Cache is invalidated explicitly when the UI edits zones.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

from app.intrusion.models import Zone
from app.intrusion.night_mode import is_night_mode_active

if TYPE_CHECKING:
    from app.database import Database

logger = logging.getLogger("gateguard.app")


class ZoneManager:
    """In-memory cache of zones, keyed by camera_id.

    Thread-safe for reads (copy-on-refresh). Use `invalidate()` after
    any CRUD change in DB to force a reload.
    """

    def __init__(
        self,
        db: "Database",
        night_start: str = "22:00",
        night_end: str = "07:00",
    ):
        self._db = db
        self._night_start = night_start
        self._night_end = night_end
        self._cache: dict[int, list[Zone]] = {}    # camera_id -> zones
        self._lock = threading.Lock()
        self.refresh()

    def refresh(self) -> None:
        """Reload all enabled zones from DB into cache."""
        rows = self._db.list_all_zones(enabled_only=True)
        new_cache: dict[int, list[Zone]] = {}
        for row in rows:
            try:
                z = Zone.from_row(row)
            except Exception:
                logger.exception("Failed to parse zone id=%s", row.get("id"))
                continue
            new_cache.setdefault(z.camera_id, []).append(z)
        with self._lock:
            self._cache = new_cache
        logger.info(
            "ZoneManager refreshed: %d cameras, %d total zones",
            len(new_cache), sum(len(v) for v in new_cache.values()),
        )

    def invalidate(self) -> None:
        """Alias for refresh() — call after DB mutation from UI."""
        self.refresh()

    # --- Queries ---

    def get_zones(self, camera_id: int) -> list[Zone]:
        with self._lock:
            return list(self._cache.get(camera_id, []))

    def active_zones_at(
        self,
        camera_id: int,
        now: datetime | None = None,
    ) -> list[Zone]:
        """Zones that are currently armed (night-only filter applied)."""
        if now is None:
            now = datetime.now()
        is_night = is_night_mode_active(self._night_start, self._night_end, now)
        zones = self.get_zones(camera_id)
        if is_night:
            return zones  # everything is armed at night
        # During daytime, only 7/24 zones (is_night_only=False) are armed.
        return [z for z in zones if not z.is_night_only]

    def matching_zones(
        self,
        camera_id: int,
        point_norm: tuple[float, float],
        now: datetime | None = None,
    ) -> list[Zone]:
        """Return the active zones that contain this normalized point."""
        candidates = self.active_zones_at(camera_id, now)
        if not candidates:
            return []
        return [
            z for z in candidates
            if point_in_polygon_normalized(point_norm, z.polygon_points)
        ]

    def matching_zones_bbox(
        self,
        camera_id: int,
        bbox: tuple[int, int, int, int],
        frame_w: int,
        frame_h: int,
        now: datetime | None = None,
    ) -> list[Zone]:
        """Active zones a person bbox occupies, per the configured anchor
        (foot/center/foot_or_center — see anchor_points). A zone matches if ANY
        anchor point falls inside it."""
        candidates = self.active_zones_at(camera_id, now)
        if not candidates:
            return []
        pts = anchor_points(bbox, frame_w, frame_h)
        return [
            z for z in candidates
            if any(point_in_polygon_normalized(p, z.polygon_points) for p in pts)
        ]


class AdHocZoneManager(ZoneManager):
    """A ZoneManager not backed by a DB — holds an in-memory zone list.

    Used by the test playground so a user can draw zones on a frame from an
    uploaded recording and evaluate them WITHOUT creating a camera or writing
    to the DB. Reuses the parent's night-mode + point-in-polygon logic.
    """

    def __init__(
        self,
        zones_by_camera: dict[int, list[Zone]],
        night_start: str = "22:00",
        night_end: str = "07:00",
    ):
        self._db = None
        self._night_start = night_start
        self._night_end = night_end
        self._cache = dict(zones_by_camera)
        self._lock = threading.Lock()

    def refresh(self) -> None:   # no DB to reload from
        pass

    def invalidate(self) -> None:
        pass


# ────────────────────────────────────────────────────────────────
#  Point-in-polygon test (normalized coordinates, 0..1)
# ────────────────────────────────────────────────────────────────

def point_in_polygon_normalized(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> bool:
    """Test if `point` lies within `polygon`. Both in 0..1 normalized coords.

    Uses cv2.pointPolygonTest when OpenCV is available (fast), otherwise
    falls back to a ray-casting implementation.

    Returns True if the point is inside the polygon or on its edge.
    A polygon with fewer than 3 points returns False.
    """
    if not polygon or len(polygon) < 3:
        return False

    if _HAS_CV2:
        poly = np.asarray(polygon, dtype=np.float32)
        # measureDist=False → returns +1 inside, 0 on edge, -1 outside
        result = cv2.pointPolygonTest(poly, (float(point[0]), float(point[1])), False)
        return result >= 0

    return _ray_cast(point, polygon)


def _ray_cast(point, polygon) -> bool:
    """Plain-Python ray casting point-in-polygon. Works without OpenCV."""
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersect = ((yi > y) != (yj > y)) and \
                    (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def bbox_center_normalized(
    bbox: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
) -> tuple[float, float]:
    """Return the bbox center in normalized 0..1 coords."""
    if frame_w <= 0 or frame_h <= 0:
        return (0.0, 0.0)
    x, y, w, h = bbox
    return ((x + w / 2.0) / frame_w, (y + h / 2.0) / frame_h)


def bbox_foot_normalized(
    bbox: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
) -> tuple[float, float]:
    """Return the bbox FOOT point (bottom-center) in normalized 0..1 coords.

    For ground-level forbidden zones this is the correct anchor: a person is
    located by where their feet contact the ground, not their torso center.
    This keeps people beyond a perimeter fence (feet outside the zone) from
    matching even when a tall subject's bbox center floats over the zone, and
    catches intruders whose feet are clearly inside even if their head leans
    out of the polygon.
    """
    if frame_w <= 0 or frame_h <= 0:
        return (0.0, 0.0)
    x, y, w, h = bbox
    return ((x + w / 2.0) / frame_w, (y + h) / frame_h)


def anchor_points(
    bbox: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
) -> list[tuple[float, float]]:
    """The normalized point(s) tested for zone membership, per config ZONE_ANCHOR.

    Single source of truth so the live classifier and the E2E test harness
    always agree. A person is "in" a zone if ANY of these points is inside it.

      - "foot_or_center" (default): foot AND center. Catches both zones drawn on
        the GROUND where a person stands (foot matches) and zones drawn over a
        WALL/DOOR feature where the body overlaps (center matches). People beyond
        a perimeter fence stay out because BOTH points are outside an internal
        zone. Best recall for the "draw the forbidden feature" workflow.
      - "foot": ground contact only (strict; misses wall/door-drawn zones).
      - "center": bbox center only (legacy).
    """
    try:
        from config import settings
        anchor = getattr(settings, "ZONE_ANCHOR", "foot_or_center")
    except Exception:
        anchor = "foot_or_center"
    if anchor == "center":
        return [bbox_center_normalized(bbox, frame_w, frame_h)]
    if anchor == "foot":
        return [bbox_foot_normalized(bbox, frame_w, frame_h)]
    return [bbox_foot_normalized(bbox, frame_w, frame_h),
            bbox_center_normalized(bbox, frame_w, frame_h)]
