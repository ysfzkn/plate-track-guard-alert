"""Data models for GateGuard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
from pydantic import BaseModel


# --- Dataclasses (internal) ---

@dataclass
class Vehicle:
    moonwell_id: int
    plate: str
    plate_normalized: str
    owner_name: str = ""
    block_no: str = ""
    apartment: str = ""
    user_type: int = 0  # 0=Resident, 1=Visitor
    kart_id: str = ""


@dataclass
class DetectionResult:
    plate_text: str
    normalized_plate: str
    confidence: float
    bbox: tuple[int, int, int, int] | None = None  # x, y, w, h
    timestamp: datetime = field(default_factory=datetime.now)
    frame: Optional[np.ndarray] = field(default=None, repr=False)


@dataclass
class PassageRecord:
    plate: str
    plate_normalized: str
    detected_at: datetime
    is_authorized: bool
    owner_name: str = ""
    confidence: float = 0.0
    screenshot_path: str = ""
    id: int | None = None


# --- Pydantic models (API responses) ---

class VehicleOut(BaseModel):
    moonwell_id: int
    plate: str
    owner_name: str
    block_no: str
    apartment: str

    class Config:
        from_attributes = True


class PassageOut(BaseModel):
    id: int
    plate: str
    detected_at: str
    is_authorized: bool
    owner_name: str
    confidence: float
    screenshot_url: str


class StatsOut(BaseModel):
    today_total: int
    today_authorized: int
    today_unauthorized: int
    auth_rate: float


class SyncResultOut(BaseModel):
    total: int
    new: int
    updated: int
    errors: list[str]
    timestamp: str


class StatusOut(BaseModel):
    camera_connected: bool
    alarm_active: bool
    last_sync: str | None
    mock_mode: bool


class WSMessage(BaseModel):
    type: str  # "passage", "alarm_on", "alarm_off", "sync_complete", "status"
    data: dict
