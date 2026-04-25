"""Dataclasses for the intrusion module.

Separate from app/models.py to keep Module 1 (plate) concerns cleanly
separated from Module 2 (intrusion) concerns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np


@dataclass
class Camera:
    """A physical IP camera registered in the system."""
    id: int
    name: str
    rtsp_url: str
    location: str = ""
    enabled: bool = True
    role: str = "intrusion"      # 'plate' | 'intrusion' | 'both'
    resolution_w: int = 0
    resolution_h: int = 0

    @classmethod
    def from_row(cls, row: dict) -> "Camera":
        return cls(
            id=row["id"],
            name=row["name"],
            rtsp_url=row["rtsp_url"],
            location=row.get("location", "") or "",
            enabled=bool(row.get("enabled", 1)),
            role=row.get("role", "intrusion"),
            resolution_w=row.get("resolution_w", 0) or 0,
            resolution_h=row.get("resolution_h", 0) or 0,
        )

    def to_public_dict(self) -> dict:
        """Safe for API response — strips credentials from RTSP URL."""
        return {
            "id": self.id,
            "name": self.name,
            "rtsp_url": _mask_rtsp_credentials(self.rtsp_url),
            "location": self.location,
            "enabled": self.enabled,
            "role": self.role,
            "resolution_w": self.resolution_w,
            "resolution_h": self.resolution_h,
        }


@dataclass
class Zone:
    """A forbidden polygonal region within a camera's frame.

    `polygon_points` is a list of (x, y) tuples with normalized coords 0..1.
    Resolution-independent — works regardless of camera resolution changes.
    """
    id: int
    camera_id: int
    name: str
    polygon_points: list[tuple[float, float]]
    is_night_only: bool = True
    min_loiter_sec: int = 5
    enabled: bool = True
    enable_motion_fallback: bool = False   # perimeter / wall-top zones

    @classmethod
    def from_row(cls, row: dict) -> "Zone":
        pts_raw = row.get("polygon_points", "[]")
        try:
            pts = json.loads(pts_raw) if isinstance(pts_raw, str) else pts_raw
            pts = [(float(p[0]), float(p[1])) for p in pts]
        except (json.JSONDecodeError, ValueError, IndexError, TypeError):
            pts = []
        return cls(
            id=row["id"],
            camera_id=row["camera_id"],
            name=row["name"],
            polygon_points=pts,
            is_night_only=bool(row.get("is_night_only", 1)),
            min_loiter_sec=int(row.get("min_loiter_sec", 5) or 5),
            enabled=bool(row.get("enabled", 1)),
            enable_motion_fallback=bool(row.get("enable_motion_fallback", 0)),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "name": self.name,
            "polygon_points": self.polygon_points,
            "is_night_only": self.is_night_only,
            "min_loiter_sec": self.min_loiter_sec,
            "enabled": self.enabled,
            "enable_motion_fallback": self.enable_motion_fallback,
        }

    @staticmethod
    def polygon_to_json(points: list[tuple[float, float]]) -> str:
        return json.dumps([[float(x), float(y)] for x, y in points])


@dataclass
class PersonObservation:
    """A single person detection within a frame."""
    track_id: int
    bbox: tuple[int, int, int, int]    # (x, y, w, h) in pixel coords
    confidence: float
    timestamp: datetime
    camera_id: int
    frame: Optional[np.ndarray] = field(default=None, repr=False)

    @property
    def bbox_center_norm(self) -> tuple[float, float]:
        """Bbox center in normalized coords — requires frame or resolution."""
        if self.frame is not None:
            h, w = self.frame.shape[:2]
            x, y, bw, bh = self.bbox
            return ((x + bw / 2) / w, (y + bh / 2) / h)
        return (0.0, 0.0)


@dataclass
class IntrusionEvent:
    """A committed intrusion event (what gets stored in DB)."""
    camera_id: int
    zone_id: int
    track_id: int
    detected_at: datetime
    duration_sec: float
    confidence: float
    person_count: int = 1
    screenshot_path: str = ""
    video_clip_path: str = ""
    acknowledged: bool = False
    shadow_mode: bool = False
    notes: str = ""
    id: Optional[int] = None


# ── Helpers ─────────────────────────────────────────────────────

def _mask_rtsp_credentials(url: str) -> str:
    """Strip user:pass@ from RTSP URL for public display."""
    import re
    return re.sub(r"://([^/]+)@", "://***@", url)
