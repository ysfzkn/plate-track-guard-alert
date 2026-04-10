"""Moonwell MDB → SQLite sync worker.

Reads the Users table from the Moonwell MW-305 Access database
and upserts authorized vehicle plates into the local SQLite DB.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pyodbc

from app.database import Database, normalize_plate
from app.models import Vehicle

logger = logging.getLogger("gateguard.sync")
_executor = ThreadPoolExecutor(max_workers=1)


def _sync_blocking(mdb_path: str, db: Database) -> dict:
    """Run the MDB sync in a blocking fashion (called from thread executor)."""
    mdb = Path(mdb_path)
    if not mdb.exists():
        msg = f"MDB file not found: {mdb_path}"
        logger.error(msg)
        db.log_sync(0, 0, 0, status="error", error=msg)
        return {"total": 0, "new": 0, "updated": 0, "errors": [msg], "timestamp": datetime.now().isoformat()}

    conn_str = (
        r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
        f"DBQ={mdb_path};"
        r"ReadOnly=1;"
    )

    max_retries = 3
    last_error = ""

    for attempt in range(max_retries):
        try:
            conn = pyodbc.connect(conn_str)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ID, [Kart ID], Adi, Soyadi, Plaka, [Blok No], Daire, [Kullanici Tipi] "
                "FROM Users WHERE Plaka IS NOT NULL AND Plaka <> ''"
            )

            vehicles: list[Vehicle] = []
            for row in cursor.fetchall():
                raw_plate = str(row[4]).strip() if row[4] else ""
                if not raw_plate:
                    continue

                normalized = normalize_plate(raw_plate)
                name_parts = []
                if row[2]:
                    name_parts.append(str(row[2]).strip())
                if row[3]:
                    name_parts.append(str(row[3]).strip())

                vehicles.append(Vehicle(
                    moonwell_id=int(row[0]),
                    plate=raw_plate,
                    plate_normalized=normalized,
                    owner_name=" ".join(name_parts),
                    block_no=str(row[5]) if row[5] is not None else "",
                    apartment=str(row[6]) if row[6] is not None else "",
                    user_type=int(row[7]) if row[7] is not None else 0,
                    kart_id=str(row[1]) if row[1] else "",
                ))

            conn.close()

            new_count, updated_count = db.upsert_vehicles(vehicles)
            total = len(vehicles)

            logger.info(
                "MDB sync complete: %d total, %d new, %d updated",
                total, new_count, updated_count,
            )
            db.log_sync(total, new_count, updated_count)

            return {
                "total": total,
                "new": new_count,
                "updated": updated_count,
                "errors": [],
                "timestamp": datetime.now().isoformat(),
            }

        except pyodbc.Error as e:
            last_error = str(e)
            logger.warning(
                "MDB sync attempt %d/%d failed: %s",
                attempt + 1, max_retries, last_error,
            )
            if attempt < max_retries - 1:
                time.sleep(5)

    logger.error("MDB sync failed after %d retries: %s", max_retries, last_error)
    db.log_sync(0, 0, 0, status="error", error=last_error)
    return {
        "total": 0,
        "new": 0,
        "updated": 0,
        "errors": [last_error],
        "timestamp": datetime.now().isoformat(),
    }


async def sync_mdb_to_sqlite(mdb_path: str, db: Database) -> dict:
    """Async wrapper — runs MDB sync in thread executor to avoid blocking the event loop."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _sync_blocking, mdb_path, db)
