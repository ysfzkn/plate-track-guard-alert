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
