"""SQLite database layer with plate normalization and fuzzy matching."""

from __future__ import annotations

import re
import sqlite3
import threading
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


def levenshtein_bounded(s1: str, s2: str, max_dist: int) -> int:
    """Edit distance, but returns ``max_dist + 1`` as soon as the distance is
    provably greater than ``max_dist``. Much cheaper than the full distance for
    the common case (most plates are nowhere near a match): a length-difference
    check rejects instantly, and the per-row minimum aborts long mismatches
    after a couple of rows. Used by the hot fuzzy-lookup path.
    """
    if abs(len(s1) - len(s2)) > max_dist:
        return max_dist + 1
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        row_min = curr[0]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            val = min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost)
            curr.append(val)
            if val < row_min:
                row_min = val
        if row_min > max_dist:
            return max_dist + 1   # cannot recover below threshold
        prev = curr
    return prev[-1]


# --- Database ---

class Database:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # ── Performance + concurrency PRAGMAs (P1.4) ──
        # journal_mode=WAL: reader/writer concurrency (already had it)
        # synchronous=NORMAL: 5-10x faster writes, durable enough for our use case
        #                    (a crash may lose the very last transaction)
        # busy_timeout=5000: if a writer waits on a lock, retry up to 5s before
        #                    raising — eliminates "database is locked" errors
        #                    under detection-loop + UI-query contention
        # cache_size=-64000: 64 MB page cache (sign-negative means KB)
        # temp_store=MEMORY: temp tables in RAM, not on disk
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA cache_size=-64000")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA foreign_keys=ON")
        # mmap_size: 256 MB memory-mapped I/O for read-heavy queries
        try:
            self.conn.execute("PRAGMA mmap_size=268435456")
        except sqlite3.OperationalError:
            pass   # not all SQLite builds support this
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

            -- ── Manuel / ek plakalar (MDB dışı) ─────────────────────
            -- MDB'de tanımlı olmayan ama sistemde elle eklenen plakalar.
            -- MDB sync bu tabloya ASLA dokunmaz (moonwell_id ile eşleşmez),
            -- böylece yeniden senkronizasyonda silinmezler.
            --   vehicle_moonwell_id NOT NULL  → mevcut bir kişiye EK plaka
            --   vehicle_moonwell_id NULL      → bağımsız (kişisi MDB'de yok) plaka
            CREATE TABLE IF NOT EXISTS extra_plates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                plate_normalized TEXT NOT NULL UNIQUE,
                vehicle_moonwell_id INTEGER,
                owner_name TEXT DEFAULT '',
                block_no TEXT DEFAULT '',
                apartment TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_extra_plates_norm
                ON extra_plates(plate_normalized);
            CREATE INDEX IF NOT EXISTS idx_extra_plates_vehicle
                ON extra_plates(vehicle_moonwell_id);

            CREATE TABLE IF NOT EXISTS passages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                plate_normalized TEXT NOT NULL,
                detected_at TIMESTAMP NOT NULL,
                is_authorized INTEGER NOT NULL DEFAULT 0,
                owner_name TEXT DEFAULT '',
                confidence REAL DEFAULT 0.0,
                screenshot_path TEXT DEFAULT '',
                direction TEXT DEFAULT 'unknown'
            );

            CREATE INDEX IF NOT EXISTS idx_passages_date
                ON passages(detected_at);

            -- Hot query: filter by plate within a date window (history search,
            -- duplicate detection). Composite index dramatically speeds the
            -- WHERE plate_normalized LIKE ? AND detected_at BETWEEN ? AND ?
            -- pattern used on the history tab.
            CREATE INDEX IF NOT EXISTS idx_passages_plate_date
                ON passages(plate_normalized, detected_at);

            -- Hot query: filter by authorized flag for daily stats
            CREATE INDEX IF NOT EXISTS idx_passages_auth_date
                ON passages(is_authorized, detected_at);

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total INTEGER DEFAULT 0,
                new_count INTEGER DEFAULT 0,
                updated_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                error_message TEXT DEFAULT ''
            );

            -- ── Module 2: Intrusion Detection ─────────────────
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                rtsp_url TEXT NOT NULL,
                location TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                role TEXT DEFAULT 'intrusion',
                resolution_w INTEGER DEFAULT 0,
                resolution_h INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                polygon_points TEXT NOT NULL,
                is_night_only INTEGER DEFAULT 1,
                min_loiter_sec INTEGER DEFAULT 5,
                enabled INTEGER DEFAULT 1,
                enable_motion_fallback INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_zones_camera ON zones(camera_id);

            CREATE TABLE IF NOT EXISTS intrusion_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER NOT NULL,
                zone_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                detected_at TIMESTAMP NOT NULL,
                duration_sec REAL NOT NULL,
                person_count INTEGER DEFAULT 1,
                confidence REAL,
                screenshot_path TEXT DEFAULT '',
                video_clip_path TEXT DEFAULT '',
                burst_paths TEXT DEFAULT '',
                acknowledged INTEGER DEFAULT 0,
                shadow_mode INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_intrusion_date
                ON intrusion_events(detected_at);
            CREATE INDEX IF NOT EXISTS idx_intrusion_camera
                ON intrusion_events(camera_id, detected_at);
            CREATE INDEX IF NOT EXISTS idx_intrusion_ack
                ON intrusion_events(acknowledged, detected_at);
            CREATE INDEX IF NOT EXISTS idx_intrusion_camera_ack
                ON intrusion_events(camera_id, acknowledged, detected_at);

            -- Vehicles: speed up plate_normalized lookups during sync + alert
            CREATE INDEX IF NOT EXISTS idx_vehicles_plate_norm
                ON vehicles(plate_normalized);
            CREATE INDEX IF NOT EXISTS idx_vehicles_user_type
                ON vehicles(user_type);

            -- ── Authentication: users + sessions (user/password model) ──
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator',   -- 'admin' | 'operator'
                full_name TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                must_change_password INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                user_agent TEXT DEFAULT '',
                ip TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

            -- Saha testi sonuçları arşivi (P2.3)
            CREATE TABLE IF NOT EXISTS test_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                module TEXT NOT NULL,        -- 'm1' | 'm2'
                test_type TEXT NOT NULL,     -- 'photo' | 'video' | 'video_e2e'
                source_filename TEXT,
                camera_id INTEGER,
                params_json TEXT,
                summary_json TEXT,
                event_count INTEGER DEFAULT 0,
                duration_sec REAL DEFAULT 0,
                output_video_url TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_test_runs_ran
                ON test_runs(ran_at);
            CREATE INDEX IF NOT EXISTS idx_test_runs_module
                ON test_runs(module, ran_at);
        """)
        self.conn.commit()
        self._migrate()

    def _migrate(self):
        """Add columns that may not exist in older databases."""
        try:
            self.conn.execute("SELECT direction FROM passages LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE passages ADD COLUMN direction TEXT DEFAULT 'unknown'")
            self.conn.commit()
        try:
            self.conn.execute("SELECT enable_motion_fallback FROM zones LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute(
                "ALTER TABLE zones ADD COLUMN enable_motion_fallback INTEGER DEFAULT 0"
            )
            self.conn.commit()
        try:
            self.conn.execute("SELECT burst_paths FROM intrusion_events LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute(
                "ALTER TABLE intrusion_events ADD COLUMN burst_paths TEXT DEFAULT ''"
            )
            self.conn.commit()

        # Idempotent index creation for older DBs
        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_passages_plate_date ON passages(plate_normalized, detected_at)",
            "CREATE INDEX IF NOT EXISTS idx_passages_auth_date  ON passages(is_authorized, detected_at)",
            "CREATE INDEX IF NOT EXISTS idx_intrusion_camera_ack ON intrusion_events(camera_id, acknowledged, detected_at)",
            "CREATE INDEX IF NOT EXISTS idx_vehicles_plate_norm ON vehicles(plate_normalized)",
            "CREATE INDEX IF NOT EXISTS idx_vehicles_user_type  ON vehicles(user_type)",
        ]:
            try:
                self.conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    # --- Vehicle operations ---

    def upsert_vehicles(self, vehicles: list[Vehicle]) -> tuple[int, int]:
        """Bulk upsert vehicles. Returns (new_count, updated_count). Thread-safe."""
        new_count = 0
        updated_count = 0
        with self._lock:
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
        """Fuzzy plate lookup with Levenshtein distance tolerance.

        Optimized for the detection hot path: a candidate must be within
        ``tolerance`` edits, which means its length is within ``tolerance`` of
        the query (|len(a)-len(b)| <= edit distance). We push that length window
        into SQL so the expensive Python Levenshtein only runs on the handful of
        plausible rows, then use the early-exit bounded variant on each. This is
        an exact optimization — no real match is ever excluded.
        """
        if not normalized:
            return None
        n = len(normalized)
        rows = self.conn.execute(
            "SELECT * FROM vehicles "
            "WHERE length(plate_normalized) BETWEEN ? AND ?",
            (n - tolerance, n + tolerance),
        ).fetchall()
        best_match = None
        best_dist = tolerance + 1
        for row in rows:
            dist = levenshtein_bounded(normalized, row["plate_normalized"], tolerance)
            if dist < best_dist:
                best_dist = dist
                best_match = row
                if dist == 0:
                    break   # can't do better than an exact match
        if best_match:
            return self._row_to_vehicle(best_match)
        return None

    def find_vehicle(self, normalized: str, fuzzy_tolerance: int = 1) -> Optional[Vehicle]:
        """Authorize a plate. Checks MDB vehicles first, then manual extra_plates.

        Order: exact MDB → exact extra → fuzzy MDB → fuzzy extra. MDB (the
        authoritative source) wins ties; manually-added plates are recognized
        exactly like MDB ones so a resident's second car / a guest plate that
        was never entered in Moonwell still opens the gate.
        """
        vehicle = self.lookup_plate(normalized)
        if vehicle:
            return vehicle
        extra = self.lookup_extra_plate(normalized)
        if extra:
            return extra
        vehicle = self.lookup_plate_fuzzy(normalized, fuzzy_tolerance)
        if vehicle:
            return vehicle
        return self.lookup_extra_plate_fuzzy(normalized, fuzzy_tolerance)

    def get_all_vehicles(self) -> list[Vehicle]:
        rows = self.conn.execute("SELECT * FROM vehicles ORDER BY owner_name").fetchall()
        return [self._row_to_vehicle(r) for r in rows]

    # --- Extra (manual / non-MDB) plate operations ---

    def _extra_row_to_vehicle(self, row: sqlite3.Row) -> Vehicle:
        """Turn an extra_plates row into a Vehicle for the authorization path.

        A linked extra plate (vehicle_moonwell_id set) inherits the owner /
        block / apartment of its MDB person; a standalone one uses its own
        stored fields.
        """
        owner_name = row["owner_name"] or ""
        block_no = row["block_no"] or ""
        apartment = row["apartment"] or ""
        link_id = row["vehicle_moonwell_id"]
        if link_id is not None:
            person = self.conn.execute(
                "SELECT owner_name, block_no, apartment FROM vehicles WHERE moonwell_id = ?",
                (link_id,),
            ).fetchone()
            if person:
                owner_name = person["owner_name"] or owner_name
                block_no = person["block_no"] or block_no
                apartment = person["apartment"] or apartment
        return Vehicle(
            # Negative synthetic id keeps it distinct from real moonwell ids.
            moonwell_id=link_id if link_id is not None else -int(row["id"]),
            plate=row["plate"],
            plate_normalized=row["plate_normalized"],
            owner_name=owner_name,
            block_no=block_no,
            apartment=apartment,
            user_type=0,
            kart_id="",
        )

    def lookup_extra_plate(self, normalized: str) -> Optional[Vehicle]:
        """Exact lookup in the manual extra_plates table."""
        if not normalized:
            return None
        row = self.conn.execute(
            "SELECT * FROM extra_plates WHERE plate_normalized = ?", (normalized,)
        ).fetchone()
        return self._extra_row_to_vehicle(row) if row else None

    def lookup_extra_plate_fuzzy(self, normalized: str, tolerance: int = 1) -> Optional[Vehicle]:
        """Fuzzy lookup in extra_plates (same length-window + bounded-Levenshtein
        optimization as the MDB path)."""
        if not normalized:
            return None
        n = len(normalized)
        rows = self.conn.execute(
            "SELECT * FROM extra_plates "
            "WHERE length(plate_normalized) BETWEEN ? AND ?",
            (n - tolerance, n + tolerance),
        ).fetchall()
        best_match = None
        best_dist = tolerance + 1
        for row in rows:
            dist = levenshtein_bounded(normalized, row["plate_normalized"], tolerance)
            if dist < best_dist:
                best_dist = dist
                best_match = row
                if dist == 0:
                    break
        return self._extra_row_to_vehicle(best_match) if best_match else None

    def plate_exists(self, normalized: str) -> Optional[str]:
        """Return where a normalized plate is already registered: 'mdb',
        'extra', or None. Used to reject duplicate manual entries."""
        if not normalized:
            return None
        if self.conn.execute(
            "SELECT 1 FROM vehicles WHERE plate_normalized = ? LIMIT 1", (normalized,)
        ).fetchone():
            return "mdb"
        if self.conn.execute(
            "SELECT 1 FROM extra_plates WHERE plate_normalized = ? LIMIT 1", (normalized,)
        ).fetchone():
            return "extra"
        return None

    def add_extra_plate(self, plate: str, vehicle_moonwell_id: int | None = None,
                        owner_name: str = "", block_no: str = "", apartment: str = "",
                        note: str = "", created_by: str = "") -> int:
        """Insert a manual plate. Returns the new row id.
        Raises ValueError if the plate is invalid or already registered."""
        raw = (plate or "").strip()
        normalized = normalize_plate(raw)
        if not normalized:
            raise ValueError("Geçersiz plaka")
        dup = self.plate_exists(normalized)
        if dup == "mdb":
            raise ValueError("Bu plaka zaten MDB'de tanımlı")
        if dup == "extra":
            raise ValueError("Bu plaka zaten eklenmiş")
        # If linked, verify the person exists; inherit nothing here (resolved at read time)
        if vehicle_moonwell_id is not None:
            person = self.conn.execute(
                "SELECT 1 FROM vehicles WHERE moonwell_id = ? LIMIT 1",
                (vehicle_moonwell_id,),
            ).fetchone()
            if not person:
                raise ValueError("Bağlanacak araç/kişi bulunamadı")
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO extra_plates
                   (plate, plate_normalized, vehicle_moonwell_id,
                    owner_name, block_no, apartment, note, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (raw, normalized, vehicle_moonwell_id,
                 owner_name.strip(), block_no.strip(), apartment.strip(),
                 note.strip(), created_by),
            )
            self.conn.commit()
            return cur.lastrowid

    def update_extra_plate(self, plate_id: int, **fields) -> bool:
        """Update a manual plate (owner info / note / link). If the plate text
        changes it is re-normalized and re-checked for duplicates."""
        allowed = {"plate", "vehicle_moonwell_id", "owner_name",
                   "block_no", "apartment", "note"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return False
        if "plate" in filtered:
            raw = (filtered["plate"] or "").strip()
            normalized = normalize_plate(raw)
            if not normalized:
                raise ValueError("Geçersiz plaka")
            clash = self.conn.execute(
                "SELECT id FROM extra_plates WHERE plate_normalized = ? AND id <> ?",
                (normalized, plate_id),
            ).fetchone()
            if clash:
                raise ValueError("Bu plaka zaten eklenmiş")
            if self.conn.execute(
                "SELECT 1 FROM vehicles WHERE plate_normalized = ? LIMIT 1", (normalized,)
            ).fetchone():
                raise ValueError("Bu plaka zaten MDB'de tanımlı")
            filtered["plate"] = raw
            filtered["plate_normalized"] = normalized
        cols = ", ".join(f"{k}=?" for k in filtered)
        vals = list(filtered.values()) + [plate_id]
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE extra_plates SET {cols} WHERE id=?", vals,
            )
            self.conn.commit()
            return cur.rowcount > 0

    def delete_extra_plate(self, plate_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM extra_plates WHERE id=?", (plate_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def get_extra_plate_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM extra_plates").fetchone()
        return row["c"] if row else 0

    def get_all_extra_plates(self) -> list[dict]:
        """All manual plates with resolved owner info (linked person's when set)."""
        rows = self.conn.execute(
            """SELECT e.*, v.owner_name AS v_owner, v.block_no AS v_block,
                      v.apartment AS v_apt, v.plate AS v_plate
               FROM extra_plates e
               LEFT JOIN vehicles v ON v.moonwell_id = e.vehicle_moonwell_id
               ORDER BY e.created_at DESC""",
        ).fetchall()
        out = []
        for r in rows:
            linked = r["vehicle_moonwell_id"] is not None
            out.append({
                "id": r["id"],
                "plate": r["plate"],
                "plate_normalized": r["plate_normalized"],
                "vehicle_moonwell_id": r["vehicle_moonwell_id"],
                "linked": linked,
                "owner_name": (r["v_owner"] if linked and r["v_owner"] else r["owner_name"]) or "",
                "block_no": (r["v_block"] if linked and r["v_block"] else r["block_no"]) or "",
                "apartment": (r["v_apt"] if linked and r["v_apt"] else r["apartment"]) or "",
                "linked_plate": r["v_plate"] if linked else None,
                "note": r["note"] or "",
                "created_at": r["created_at"],
            })
        return out

    def get_vehicles_page(self, search: str | None = None,
                          limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """Paginated MDB people list (search over plate / owner / block / apartment).
        Returns (rows, total)."""
        where = ""
        params: list = []
        if search:
            s = search.strip()
            where = ("WHERE plate_normalized LIKE ? OR owner_name LIKE ? "
                     "OR block_no LIKE ? OR apartment LIKE ?")
            like = f"%{s.upper()}%"
            like_raw = f"%{s}%"
            params = [like, like_raw, like_raw, like_raw]
        total = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM vehicles {where}", params,
        ).fetchone()["c"]
        rows = self.conn.execute(
            f"""SELECT moonwell_id, plate, plate_normalized, owner_name,
                       block_no, apartment, user_type, kart_id, synced_at
                FROM vehicles {where}
                ORDER BY owner_name, plate LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    # --- Passage operations ---

    def get_vehicle_count(self) -> int:
        """Return total number of registered vehicles."""
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM vehicles").fetchone()
        return row["cnt"] if row else 0

    def add_passage(self, record: PassageRecord) -> int:
        """Insert a passage record. Thread-safe."""
        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO passages
                   (plate, plate_normalized, detected_at, is_authorized,
                    owner_name, confidence, screenshot_path, direction)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.plate, record.plate_normalized, record.detected_at.isoformat(),
                 int(record.is_authorized), record.owner_name,
                 record.confidence, record.screenshot_path, record.direction),
            )
            self.conn.commit()
            return cursor.lastrowid

    def update_passage(self, passage_id: int, **fields) -> bool:
        """Update an existing passage (e.g. correct its direction once more
        fragments of the same vehicle vote a different way). Thread-safe."""
        allowed = {"direction", "confidence", "screenshot_path", "owner_name", "is_authorized"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return False
        cols = ", ".join(f"{k}=?" for k in filtered)
        vals = list(filtered.values()) + [passage_id]
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE passages SET {cols} WHERE id=?", vals,
            )
            self.conn.commit()
            return cur.rowcount > 0

    def get_recent_passages(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT id, plate, plate_normalized, detected_at, is_authorized,
                      owner_name, confidence, screenshot_path, direction
               FROM passages ORDER BY detected_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_passages_filtered(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        direction: str | None = None,
        authorized: bool | None = None,
        plate_search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Filtered passage query with pagination. Returns (rows, total_count).
        Joined with vehicles for apartment/block_no when authorised.
        """
        where_clauses = []
        params = []

        if start_date:
            where_clauses.append("p.detected_at >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("p.detected_at < date(?, '+1 day')")
            params.append(end_date)
        if direction and direction != "all":
            where_clauses.append("p.direction = ?")
            params.append(direction)
        if authorized is not None:
            where_clauses.append("p.is_authorized = ?")
            params.append(int(authorized))
        if plate_search:
            where_clauses.append("p.plate_normalized LIKE ?")
            params.append(f"%{plate_search.upper()}%")

        where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Total count (no JOIN needed)
        count_sql = where_sql.replace("p.", "")
        count_row = self.conn.execute(
            f"SELECT COUNT(*) as cnt FROM passages{count_sql}", params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        # Paginated data — JOIN vehicles for owner/apartment info
        rows = self.conn.execute(
            f"""SELECT p.id, p.plate, p.plate_normalized, p.detected_at, p.is_authorized,
                       p.owner_name, p.confidence, p.screenshot_path, p.direction,
                       v.apartment, v.block_no
                FROM passages p
                LEFT JOIN vehicles v ON v.plate_normalized = p.plate_normalized
                {where_sql}
                ORDER BY p.detected_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return [dict(r) for r in rows], total

    def get_stats(self, start_date: str | None = None, end_date: str | None = None) -> dict:
        """Get passage statistics. Defaults to today if no dates given."""
        if not start_date:
            start_date = date.today().isoformat()

        params = [start_date]
        date_filter = "WHERE detected_at >= ?"
        if end_date:
            date_filter += " AND detected_at < date(?, '+1 day')"
            params.append(end_date)

        row = self.conn.execute(
            f"""SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN is_authorized = 1 THEN 1 ELSE 0 END) as authorized,
                 SUM(CASE WHEN is_authorized = 0 THEN 1 ELSE 0 END) as unauthorized,
                 SUM(CASE WHEN direction = 'entry' THEN 1 ELSE 0 END) as entries,
                 SUM(CASE WHEN direction = 'exit' THEN 1 ELSE 0 END) as exits
               FROM passages {date_filter}""",
            params,
        ).fetchone()
        total = row["total"] or 0
        authorized = row["authorized"] or 0
        unauthorized = row["unauthorized"] or 0
        entries = row["entries"] or 0
        exits = row["exits"] or 0
        auth_rate = (authorized / total * 100) if total > 0 else 0.0
        return {
            "today_total": total,
            "today_authorized": authorized,
            "today_unauthorized": unauthorized,
            "today_entries": entries,
            "today_exits": exits,
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

    def get_last_sync_status(self) -> dict:
        """Detailed last-sync info — used by /api/health to surface stale syncs."""
        row = self.conn.execute(
            "SELECT synced_at, total, new_count, updated_count, status, error_message "
            "FROM sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"synced_at": None, "status": None, "stale": True,
                    "hours_since": None, "error": None}
        try:
            from datetime import datetime as _dt
            synced_at = _dt.fromisoformat(row["synced_at"])
            hours_since = (_dt.now() - synced_at).total_seconds() / 3600.0
        except Exception:
            hours_since = None
        return {
            "synced_at": row["synced_at"],
            "status": row["status"],
            "total": row["total"],
            "new_count": row["new_count"],
            "updated_count": row["updated_count"],
            "error": row["error_message"],
            "hours_since": round(hours_since, 1) if hours_since is not None else None,
            "stale": (hours_since is None) or (hours_since > 12) or (row["status"] != "success"),
        }

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

    # ══════════════════════════════════════════════════════════════
    #  Module 2 — Intrusion detection tables (cameras, zones, events)
    # ══════════════════════════════════════════════════════════════

    # --- Camera CRUD ---

    def add_camera(self, name: str, rtsp_url: str, location: str = "",
                   role: str = "intrusion", enabled: bool = True) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO cameras (name, rtsp_url, location, role, enabled)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, rtsp_url, location, role, int(enabled)),
            )
            self.conn.commit()
            return cur.lastrowid

    def update_camera(self, camera_id: int, **fields) -> bool:
        """Update camera fields (name, rtsp_url, location, enabled, role, resolution_*)."""
        allowed = {"name", "rtsp_url", "location", "enabled", "role",
                   "resolution_w", "resolution_h"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return False
        cols = ", ".join(f"{k}=?" for k in filtered)
        vals = list(filtered.values()) + [camera_id]
        with self._lock:
            cur = self.conn.execute(f"UPDATE cameras SET {cols} WHERE id=?", vals)
            self.conn.commit()
            return cur.rowcount > 0

    def delete_camera(self, camera_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM cameras WHERE id=?", (camera_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def list_cameras(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM cameras"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        rows = self.conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_camera(self, camera_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM cameras WHERE id=?", (camera_id,)
        ).fetchone()
        return dict(row) if row else None

    # --- Zone CRUD ---

    def add_zone(self, camera_id: int, name: str, polygon_points: str,
                 is_night_only: bool = True, min_loiter_sec: int = 5,
                 enable_motion_fallback: bool = False) -> int:
        """polygon_points: JSON string of normalized [[x,y],...] coordinates."""
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO zones (camera_id, name, polygon_points,
                                      is_night_only, min_loiter_sec,
                                      enable_motion_fallback)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (camera_id, name, polygon_points,
                 int(is_night_only), min_loiter_sec,
                 int(enable_motion_fallback)),
            )
            self.conn.commit()
            return cur.lastrowid

    def update_zone(self, zone_id: int, **fields) -> bool:
        allowed = {"name", "polygon_points", "is_night_only",
                   "min_loiter_sec", "enabled", "enable_motion_fallback"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return False
        cols = ", ".join(f"{k}=?" for k in filtered)
        vals = list(filtered.values()) + [zone_id]
        with self._lock:
            cur = self.conn.execute(f"UPDATE zones SET {cols} WHERE id=?", vals)
            self.conn.commit()
            return cur.rowcount > 0

    def delete_zone(self, zone_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def list_zones_for_camera(self, camera_id: int,
                              enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM zones WHERE camera_id=?"
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY id"
        rows = self.conn.execute(sql, (camera_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_all_zones(self, enabled_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM zones"
        if enabled_only:
            sql += " WHERE enabled=1"
        rows = self.conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    # --- Intrusion event CRUD ---

    def add_intrusion_event(self, camera_id: int, zone_id: int, track_id: int,
                            detected_at, duration_sec: float, confidence: float,
                            person_count: int = 1, screenshot_path: str = "",
                            video_clip_path: str = "", shadow_mode: bool = False,
                            notes: str = "") -> int:
        from datetime import datetime as _dt
        ts = detected_at.isoformat() if isinstance(detected_at, _dt) else str(detected_at)
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO intrusion_events
                   (camera_id, zone_id, track_id, detected_at, duration_sec,
                    person_count, confidence, screenshot_path, video_clip_path,
                    shadow_mode, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (camera_id, zone_id, track_id, ts, duration_sec,
                 person_count, confidence, screenshot_path, video_clip_path,
                 int(shadow_mode), notes),
            )
            self.conn.commit()
            return cur.lastrowid

    def update_intrusion_event(self, event_id: int, **fields) -> bool:
        """Used primarily to attach video_clip_path async after commit."""
        allowed = {"screenshot_path", "video_clip_path", "burst_paths", "acknowledged", "notes"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return False
        cols = ", ".join(f"{k}=?" for k in filtered)
        vals = list(filtered.values()) + [event_id]
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE intrusion_events SET {cols} WHERE id=?", vals,
            )
            self.conn.commit()
            return cur.rowcount > 0

    def get_intrusion_event(self, event_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM intrusion_events WHERE id=?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_intrusion_events(
        self,
        camera_id: int | None = None,
        zone_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        acknowledged: bool | None = None,
        shadow_mode: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Filtered event list with pagination. Returns (rows, total_count)."""
        where, params = [], []
        if camera_id is not None:
            where.append("camera_id=?")
            params.append(camera_id)
        if zone_id is not None:
            where.append("zone_id=?")
            params.append(zone_id)
        if start_date:
            where.append("detected_at >= ?")
            params.append(start_date)
        if end_date:
            where.append("detected_at < date(?, '+1 day')")
            params.append(end_date)
        if acknowledged is not None:
            where.append("acknowledged=?")
            params.append(int(acknowledged))
        if shadow_mode is not None:
            where.append("shadow_mode=?")
            params.append(int(shadow_mode))

        where_sql = " WHERE " + " AND ".join(where) if where else ""

        count_row = self.conn.execute(
            f"SELECT COUNT(*) as cnt FROM intrusion_events{where_sql}", params,
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        rows = self.conn.execute(
            f"""SELECT * FROM intrusion_events{where_sql}
                ORDER BY detected_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    def acknowledge_intrusion_event(self, event_id: int, note: str = "") -> bool:
        fields = {"acknowledged": 1}
        if note:
            fields["notes"] = note
        return self.update_intrusion_event(event_id, **fields)

    def get_passages_by_day(self, days: int = 7) -> list[dict]:
        """Daily aggregate of passages for the last N days (oldest first).
        Returns: [{day:'YYYY-MM-DD', total, authorized, unauthorized, entries, exits}]"""
        # Local-date window (detected_at is stored in LOCAL time). Using SQLite's
        # date('now') here would be UTC and could shift the window by a day.
        from datetime import timedelta as _td
        start_day = (date.today() - _td(days=days - 1)).isoformat()
        rows = self.conn.execute(
            """SELECT
                 DATE(detected_at) AS day,
                 COUNT(*) AS total,
                 SUM(CASE WHEN is_authorized=1 THEN 1 ELSE 0 END) AS authorized,
                 SUM(CASE WHEN is_authorized=0 THEN 1 ELSE 0 END) AS unauthorized,
                 SUM(CASE WHEN direction='entry' THEN 1 ELSE 0 END) AS entries,
                 SUM(CASE WHEN direction='exit' THEN 1 ELSE 0 END) AS exits
               FROM passages
               WHERE detected_at >= ?
               GROUP BY DATE(detected_at)
               ORDER BY day ASC""",
            (start_day,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_passages_by_hour(self, date_iso: str | None = None) -> list[dict]:
        """Hourly aggregate of passages for a single day.
        Returns 24 entries (hour=0..23, may be 0 for hours with no activity)."""
        from datetime import date as _date
        if not date_iso:
            date_iso = _date.today().isoformat()
        rows = self.conn.execute(
            """SELECT
                 CAST(strftime('%H', detected_at) AS INTEGER) AS hour,
                 COUNT(*) AS total,
                 SUM(CASE WHEN is_authorized=1 THEN 1 ELSE 0 END) AS authorized,
                 SUM(CASE WHEN is_authorized=0 THEN 1 ELSE 0 END) AS unauthorized
               FROM passages
               WHERE DATE(detected_at) = ?
               GROUP BY hour
               ORDER BY hour ASC""",
            (date_iso,),
        ).fetchall()
        # Ensure all 24 hours present
        by_hour = {int(r["hour"]): dict(r) for r in rows}
        result = []
        for h in range(24):
            r = by_hour.get(h, {"hour": h, "total": 0, "authorized": 0, "unauthorized": 0})
            result.append(r)
        return result

    def get_intrusions_by_day(self, days: int = 7) -> list[dict]:
        """Daily intrusion event counts for the last N days."""
        from datetime import timedelta as _td
        start_day = (date.today() - _td(days=days - 1)).isoformat()
        rows = self.conn.execute(
            """SELECT
                 DATE(detected_at) AS day,
                 COUNT(*) AS total,
                 SUM(CASE WHEN shadow_mode=1 THEN 1 ELSE 0 END) AS shadow,
                 SUM(CASE WHEN acknowledged=0 THEN 1 ELSE 0 END) AS unacknowledged
               FROM intrusion_events
               WHERE detected_at >= ?
               GROUP BY DATE(detected_at)
               ORDER BY day ASC""",
            (start_day,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_intrusions_by_hour(self, date_iso: str | None = None) -> list[dict]:
        from datetime import date as _date
        if not date_iso:
            date_iso = _date.today().isoformat()
        rows = self.conn.execute(
            """SELECT
                 CAST(strftime('%H', detected_at) AS INTEGER) AS hour,
                 COUNT(*) AS total
               FROM intrusion_events
               WHERE DATE(detected_at) = ?
               GROUP BY hour
               ORDER BY hour ASC""",
            (date_iso,),
        ).fetchall()
        by_hour = {int(r["hour"]): dict(r) for r in rows}
        return [by_hour.get(h, {"hour": h, "total": 0}) for h in range(24)]

    def get_last_passage(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM passages ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_last_intrusion(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM intrusion_events ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def delete_intrusion_events_before(self, cutoff: datetime) -> int:
        """Hard-delete intrusion events older than cutoff. Returns rows removed."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM intrusion_events WHERE detected_at < ?",
                (cutoff.isoformat(),),
            )
            self.conn.commit()
            return cur.rowcount

    # ── Users + sessions (auth) ─────────────────────────────────────

    def get_user_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE is_active=1"
        ).fetchone()
        return row["cnt"] if row else 0

    def get_user_by_username(self, username: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(?) AND is_active=1",
            (username.strip(),),
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE id=? AND is_active=1", (user_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT id, username, role, full_name, is_active,
                      must_change_password, created_at, last_login_at
               FROM users ORDER BY id ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def add_user(
        self, username: str, password_hash: str, salt: str,
        role: str = "operator", full_name: str = "",
        must_change_password: int = 0,
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO users (username, password_hash, salt, role,
                                      full_name, must_change_password)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (username.strip(), password_hash, salt, role,
                 full_name, must_change_password),
            )
            self.conn.commit()
            return cur.lastrowid

    def update_user_password(self, user_id: int, password_hash: str, salt: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                """UPDATE users SET password_hash=?, salt=?, must_change_password=0
                   WHERE id=?""",
                (password_hash, salt, user_id),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def update_user(self, user_id: int, **fields) -> bool:
        allowed = {"role", "full_name", "is_active"}
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return False
        cols = ", ".join(f"{k}=?" for k in filtered)
        vals = list(filtered.values()) + [user_id]
        with self._lock:
            cur = self.conn.execute(f"UPDATE users SET {cols} WHERE id=?", vals)
            self.conn.commit()
            return cur.rowcount > 0

    def delete_user(self, user_id: int) -> bool:
        with self._lock:
            # Sessions cascade-delete via FK
            cur = self.conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def touch_user_login(self, user_id: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?",
                (user_id,),
            )
            self.conn.commit()

    # Sessions

    def create_session(
        self, token: str, user_id: int, expires_at: str,
        user_agent: str = "", ip: str = "",
    ) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO sessions (token, user_id, expires_at, user_agent, ip)
                   VALUES (?, ?, ?, ?, ?)""",
                (token, user_id, expires_at, user_agent[:200], ip[:64]),
            )
            self.conn.commit()

    def get_session_user(self, token: str) -> dict | None:
        """Return joined user+session info if token valid + not expired."""
        row = self.conn.execute(
            """SELECT u.id AS user_id, u.username, u.role, u.full_name,
                      u.is_active, u.must_change_password,
                      s.token, s.expires_at, s.created_at AS session_created_at
               FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token=? AND s.expires_at > CURRENT_TIMESTAMP
                     AND u.is_active=1""",
            (token,),
        ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            self.conn.commit()
            return cur.rowcount > 0

    def delete_user_sessions(self, user_id: int) -> int:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE user_id=?", (user_id,),
            )
            self.conn.commit()
            return cur.rowcount

    def gc_expired_sessions(self) -> int:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE expires_at <= CURRENT_TIMESTAMP"
            )
            self.conn.commit()
            return cur.rowcount

    # ── Test runs archive (P2.3) ────────────────────────────────────

    def add_test_run(
        self, module: str, test_type: str, source_filename: str,
        params_json: str, summary_json: str, event_count: int,
        duration_sec: float, output_video_url: str = "", camera_id: int | None = None,
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO test_runs
                   (module, test_type, source_filename, camera_id,
                    params_json, summary_json, event_count, duration_sec, output_video_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (module, test_type, source_filename, camera_id,
                 params_json, summary_json, event_count, duration_sec, output_video_url),
            )
            self.conn.commit()
            return cur.lastrowid

    def list_test_runs(self, limit: int = 100, module: str | None = None) -> list[dict]:
        sql = "SELECT * FROM test_runs"
        params: list = []
        if module:
            sql += " WHERE module = ?"
            params.append(module)
        sql += " ORDER BY ran_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_test_run(self, run_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM test_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_test_run(self, run_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM test_runs WHERE id = ?", (run_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def bulk_delete_passages(self, ids: list[int]) -> int:
        """Delete multiple passages by ID. Returns rows removed."""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            cur = self.conn.execute(
                f"DELETE FROM passages WHERE id IN ({placeholders})",
                ids,
            )
            self.conn.commit()
            return cur.rowcount

    def delete_passages_before(self, cutoff: datetime) -> int:
        """Hard-delete passage records older than cutoff."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM passages WHERE detected_at < ?",
                (cutoff.isoformat(),),
            )
            self.conn.commit()
            return cur.rowcount

    def get_intrusion_stats(
        self, start_date: str | None = None, end_date: str | None = None,
    ) -> dict:
        """Summary: total, per-camera count, per-zone count, night vs day."""
        from datetime import date as _date
        if not start_date:
            start_date = _date.today().isoformat()
        params = [start_date]
        where = "WHERE detected_at >= ?"
        if end_date:
            where += " AND detected_at < date(?, '+1 day')"
            params.append(end_date)

        row = self.conn.execute(
            f"""SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN acknowledged=0 THEN 1 ELSE 0 END) as unack,
                 SUM(CASE WHEN shadow_mode=1 THEN 1 ELSE 0 END) as shadow
               FROM intrusion_events {where}""", params,
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "unacknowledged": row["unack"] or 0,
            "shadow_mode": row["shadow"] or 0,
        }

    def close(self):
        self.conn.close()
