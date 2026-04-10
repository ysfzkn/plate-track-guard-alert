"""SQLite database layer with plate normalization and fuzzy matching."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from app.models import Vehicle, PassageRecord

# --- Turkish plate normalization ---

TURKISH_CHAR_MAP = str.maketrans(
    "İŞÇĞÖÜışçğöü",
    "ISCGOUiscgou",
)

PLATE_REGEX = re.compile(r"^(0[1-9]|[1-7][0-9]|8[01])[A-Z]{1,3}\d{2,4}$")


def normalize_plate(raw: str) -> str:
    """Normalize a Turkish plate: uppercase, strip non-alphanumeric, translate Turkish chars."""
    text = raw.translate(TURKISH_CHAR_MAP).upper()
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text


def is_valid_turkish_plate(normalized: str) -> bool:
    return bool(PLATE_REGEX.match(normalized))


def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


# --- Database ---

class Database:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                moonwell_id INTEGER UNIQUE NOT NULL,
                plate TEXT NOT NULL,
                plate_normalized TEXT NOT NULL,
                owner_name TEXT DEFAULT '',
                block_no TEXT DEFAULT '',
                apartment TEXT DEFAULT '',
                user_type INTEGER DEFAULT 0,
                kart_id TEXT DEFAULT '',
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_vehicles_plate
                ON vehicles(plate_normalized);

            CREATE TABLE IF NOT EXISTS passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                plate_normalized TEXT NOT NULL,
                detected_at TIMESTAMP NOT NULL,
                is_authorized INTEGER NOT NULL DEFAULT 0,
                owner_name TEXT DEFAULT '',
                confidence REAL DEFAULT 0.0,
                screenshot_path TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_passages_date
                ON passages(detected_at);

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total INTEGER DEFAULT 0,
                new_count INTEGER DEFAULT 0,
                updated_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                error_message TEXT DEFAULT ''
            );
        """)
        self.conn.commit()

    # --- Vehicle operations ---

    def upsert_vehicles(self, vehicles: list[Vehicle]) -> tuple[int, int]:
        """Bulk upsert vehicles. Returns (new_count, updated_count)."""
        new_count = 0
        updated_count = 0
        for v in vehicles:
            cursor = self.conn.execute(
                "SELECT id FROM vehicles WHERE moonwell_id = ?", (v.moonwell_id,)
            )
            existing = cursor.fetchone()
            if existing:
                self.conn.execute(
                    """UPDATE vehicles
                       SET plate=?, plate_normalized=?, owner_name=?,
                           block_no=?, apartment=?, user_type=?, kart_id=?,
                           synced_at=CURRENT_TIMESTAMP
                       WHERE moonwell_id=?""",
                    (v.plate, v.plate_normalized, v.owner_name,
                     v.block_no, v.apartment, v.user_type, v.kart_id,
                     v.moonwell_id),
                )
                updated_count += 1
            else:
                self.conn.execute(
                    """INSERT INTO vehicles
                       (moonwell_id, plate, plate_normalized, owner_name,
                        block_no, apartment, user_type, kart_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (v.moonwell_id, v.plate, v.plate_normalized, v.owner_name,
                     v.block_no, v.apartment, v.user_type, v.kart_id),
                )
                new_count += 1
        self.conn.commit()
        return new_count, updated_count

    def lookup_plate(self, normalized: str) -> Optional[Vehicle]:
        """Exact plate lookup."""
        row = self.conn.execute(
            "SELECT * FROM vehicles WHERE plate_normalized = ?", (normalized,)
        ).fetchone()
        if row:
            return self._row_to_vehicle(row)
        return None

    def lookup_plate_fuzzy(self, normalized: str, tolerance: int = 1) -> Optional[Vehicle]:
        """Fuzzy plate lookup with Levenshtein distance tolerance."""
        rows = self.conn.execute("SELECT * FROM vehicles").fetchall()
        best_match = None
        best_dist = tolerance + 1
        for row in rows:
            dist = levenshtein_distance(normalized, row["plate_normalized"])
            if dist <= tolerance and dist < best_dist:
                best_dist = dist
                best_match = row
        if best_match:
            return self._row_to_vehicle(best_match)
        return None

    def find_vehicle(self, normalized: str, fuzzy_tolerance: int = 1) -> Optional[Vehicle]:
        """Exact lookup first, then fuzzy fallback."""
        vehicle = self.lookup_plate(normalized)
        if vehicle:
            return vehicle
        return self.lookup_plate_fuzzy(normalized, fuzzy_tolerance)

    def get_all_vehicles(self) -> list[Vehicle]:
        rows = self.conn.execute("SELECT * FROM vehicles ORDER BY owner_name").fetchall()
        return [self._row_to_vehicle(r) for r in rows]

    # --- Passage operations ---

    def add_passage(self, record: PassageRecord) -> int:
        cursor = self.conn.execute(
            """INSERT INTO passages
               (plate, plate_normalized, detected_at, is_authorized,
                owner_name, confidence, screenshot_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record.plate, record.plate_normalized, record.detected_at.isoformat(),
             int(record.is_authorized), record.owner_name,
             record.confidence, record.screenshot_path),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_recent_passages(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT id, plate, plate_normalized, detected_at, is_authorized,
                      owner_name, confidence, screenshot_path
               FROM passages ORDER BY detected_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        today = date.today().isoformat()
        row = self.conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN is_authorized = 1 THEN 1 ELSE 0 END) as authorized,
                 SUM(CASE WHEN is_authorized = 0 THEN 1 ELSE 0 END) as unauthorized
               FROM passages
               WHERE detected_at >= ?""",
            (today,),
        ).fetchone()
        total = row["total"] or 0
        authorized = row["authorized"] or 0
        unauthorized = row["unauthorized"] or 0
        auth_rate = (authorized / total * 100) if total > 0 else 0.0
        return {
            "today_total": total,
            "today_authorized": authorized,
            "today_unauthorized": unauthorized,
            "auth_rate": round(auth_rate, 1),
        }

    # --- Sync log ---

    def log_sync(self, total: int, new: int, updated: int,
                 status: str = "success", error: str = ""):
        self.conn.execute(
            """INSERT INTO sync_log (total, new_count, updated_count, status, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (total, new, updated, status, error),
        )
        self.conn.commit()

    def get_last_sync(self) -> Optional[str]:
        row = self.conn.execute(
            "SELECT synced_at FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["synced_at"] if row else None

    # --- Helpers ---

    @staticmethod
    def _row_to_vehicle(row: sqlite3.Row) -> Vehicle:
        return Vehicle(
            moonwell_id=row["moonwell_id"],
            plate=row["plate"],
            plate_normalized=row["plate_normalized"],
            owner_name=row["owner_name"],
            block_no=row["block_no"],
            apartment=row["apartment"],
            user_type=row["user_type"],
            kart_id=row["kart_id"],
        )

    def close(self):
        self.conn.close()
