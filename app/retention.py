"""Retention sweeper — disk space + file rotation + DB cleanup.

Runs as a background asyncio task. Every hour it:
  1. Walks intrusion_clips/, screenshots/, test_outputs/ → deletes files
     older than INTRUSION_RETENTION_DAYS (configurable).
  2. Issues DELETE on intrusion_events / passages older than retention window.
  3. Checks free disk space — if below 2 GB, escalates and deletes the oldest
     files first (regardless of age) until headroom is restored.
  4. VACUUMs the SQLite database weekly to reclaim freed pages.
  5. Broadcasts a `retention_swept` WS event with stats so the UI can show
     "Last cleanup: X minutes ago, freed Y MB".
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database import Database
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger("gateguard.app")


class RetentionSweeper:
    """Periodic disk + DB housekeeper. Safe to run once per process."""

    SWEEP_INTERVAL_SEC = 3600                  # hourly
    HEADROOM_LOW_GB = 2.0                      # trigger emergency purge
    HEADROOM_TARGET_GB = 5.0                   # purge until we hit this
    VACUUM_INTERVAL_DAYS = 7
    # Hardening (P1.6):
    MAX_FILES_PER_SWEEP = 1000                 # iter limit — protect from O(n) blowup
    SKIP_SUFFIXES = (".tmp", ".part", ".lock") # skip files being actively written

    def __init__(
        self,
        db: "Database",
        ws_manager: "ConnectionManager",
        retention_days: int = 60,
        directories: list[str] | None = None,
        base_dir: str = ".",
    ):
        self.db = db
        self.ws_manager = ws_manager
        self.retention_days = max(1, retention_days)
        self.directories = directories or []
        self.base_dir = Path(base_dir)
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_vacuum: float = 0.0
        self._stats: dict = {
            "last_run": None,
            "files_deleted": 0,
            "bytes_freed": 0,
            "rows_deleted": 0,
            "errors": [],
        }

    # ── Lifecycle ──────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "RetentionSweeper started: %d days retention, dirs=%s",
            self.retention_days, [str(d) for d in self.directories],
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_stats(self) -> dict:
        return dict(self._stats)

    async def run_now(self) -> dict:
        """Run a single sweep on demand (e.g. from an admin button). Returns stats."""
        return await self._sweep_once()

    # ── Internals ──────────────────────────────────────────

    async def _loop(self) -> None:
        # Run once shortly after start so admins see immediate effect
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("Retention sweep failed")
            await asyncio.sleep(self.SWEEP_INTERVAL_SEC)

    async def _sweep_once(self) -> dict:
        loop = asyncio.get_event_loop()
        # cv2/Path I/O is sync — push to executor
        result = await loop.run_in_executor(None, self._sweep_blocking)
        self._stats.update(result)
        try:
            await self.ws_manager.broadcast({
                "type": "retention_swept",
                "data": {
                    "files_deleted": result["files_deleted"],
                    "bytes_freed_mb": round(result["bytes_freed"] / (1024 * 1024), 1),
                    "rows_deleted": result["rows_deleted"],
                    "ran_at": datetime.now().isoformat(),
                },
            })
        except Exception:
            pass   # WS optional
        return result

    def _sweep_blocking(self) -> dict:
        cutoff_ts = time.time() - self.retention_days * 86400
        cutoff_dt = datetime.now() - timedelta(days=self.retention_days)
        deleted_files = 0
        bytes_freed = 0
        rows_deleted = 0
        errors: list[str] = []

        # ── 1. File cleanup by age (iter-limited to avoid O(n) blocking) ──
        scanned = 0
        for d in self.directories:
            p = Path(d)
            if not p.exists():
                continue
            for entry in p.rglob("*"):
                if scanned >= self.MAX_FILES_PER_SWEEP:
                    errors.append(f"iter limit ({self.MAX_FILES_PER_SWEEP}) — partial sweep")
                    break
                if not entry.is_file():
                    continue
                # Skip files currently being written by other components
                if entry.name.endswith(self.SKIP_SUFFIXES):
                    continue
                scanned += 1
                try:
                    mtime = entry.stat().st_mtime
                    if mtime < cutoff_ts:
                        size = entry.stat().st_size
                        entry.unlink()
                        deleted_files += 1
                        bytes_freed += size
                except (OSError, FileNotFoundError) as e:
                    errors.append(f"{entry.name}: {e}")
            if scanned >= self.MAX_FILES_PER_SWEEP:
                break

        # ── 2. DB row cleanup ──
        try:
            n = self.db.delete_intrusion_events_before(cutoff_dt)
            rows_deleted += n
        except AttributeError:
            pass
        except Exception as e:
            errors.append(f"intrusion DB cleanup: {e}")

        # ── 3. Disk headroom emergency purge (iter-limited, skip .tmp) ──
        try:
            disk = shutil.disk_usage(str(self.base_dir))
            free_gb = disk.free / (1024 ** 3)
            if free_gb < self.HEADROOM_LOW_GB:
                logger.warning(
                    "Disk LOW: %.1f GB free — emergency purge to %.1f GB",
                    free_gb, self.HEADROOM_TARGET_GB,
                )
                target_bytes = int(self.HEADROOM_TARGET_GB * (1024 ** 3))
                files: list[tuple[float, Path, int]] = []
                scanned2 = 0
                for d in self.directories:
                    pd = Path(d)
                    if not pd.exists():
                        continue
                    for f in pd.rglob("*"):
                        if scanned2 >= self.MAX_FILES_PER_SWEEP * 2:
                            errors.append("emergency: iter limit reached")
                            break
                        if not f.is_file():
                            continue
                        if f.name.endswith(self.SKIP_SUFFIXES):
                            continue
                        scanned2 += 1
                        try:
                            st = f.stat()
                            files.append((st.st_mtime, f, st.st_size))
                        except OSError:
                            pass
                files.sort()   # oldest first
                for mtime, f, size in files:
                    if shutil.disk_usage(str(self.base_dir)).free >= target_bytes:
                        break
                    try:
                        f.unlink()
                        deleted_files += 1
                        bytes_freed += size
                    except OSError as e:
                        errors.append(f"emergency purge {f.name}: {e}")
        except Exception as e:
            errors.append(f"disk check: {e}")

        # ── 4. Weekly VACUUM ──
        if time.time() - self._last_vacuum > self.VACUUM_INTERVAL_DAYS * 86400:
            try:
                # Database has a thread-safe lock; conn is sqlite3
                with self.db._lock:                              # type: ignore[attr-defined]
                    self.db.conn.execute("VACUUM")               # type: ignore[attr-defined]
                self._last_vacuum = time.time()
                logger.info("Database VACUUM completed")
            except Exception as e:
                errors.append(f"vacuum: {e}")

        logger.info(
            "Retention sweep: %d files (%.1f MB), %d DB rows, %d errors",
            deleted_files, bytes_freed / (1024 * 1024), rows_deleted, len(errors),
        )

        return {
            "last_run": datetime.now().isoformat(),
            "files_deleted": deleted_files,
            "bytes_freed": bytes_freed,
            "rows_deleted": rows_deleted,
            "errors": errors[-20:],   # cap log size
        }
