"""Seed sample cameras for Module 2 intrusion testing.

Run once to populate the `cameras` table with example entries.
Web UI can edit/delete/add afterwards. This is only for initial testing.

Usage (from project root):
    python scripts/seed_cameras.py
    python scripts/seed_cameras.py --clear   # wipe cameras before seeding
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings
from app.database import Database


SAMPLE_CAMERAS = [
    {
        "name": "Bariyer Kamerası (Modül 1)",
        "rtsp_url": settings.RTSP_URL,      # mevcut plaka kamerası
        "location": "Ana giriş / bariyer",
        "role": "plate",
        "enabled": 1,
    },
    {
        "name": "Bahçe Arka",
        "rtsp_url": "rtsp://admin:SIFRE@192.168.1.57:554/cam/realmonitor?channel=2&subtype=0",
        "location": "D blok arka bahçe",
        "role": "intrusion",
        "enabled": 0,      # örnek — RTSP URL gerçek olmadığı için pasif başlatıldı
    },
    {
        "name": "Yangın Merdiveni",
        "rtsp_url": "rtsp://admin:SIFRE@192.168.1.58:554/cam/realmonitor?channel=1&subtype=0",
        "location": "C blok yan yangın çıkışı",
        "role": "intrusion",
        "enabled": 0,
    },
]


def main():
    parser = argparse.ArgumentParser(description="Seed test cameras")
    parser.add_argument("--clear", action="store_true",
                        help="Delete all existing cameras first")
    args = parser.parse_args()

    db = Database(settings.SQLITE_PATH)
    try:
        if args.clear:
            existing = db.list_cameras()
            for c in existing:
                db.delete_camera(c["id"])
            print(f"Cleared {len(existing)} existing camera(s)")

        existing_names = {c["name"] for c in db.list_cameras()}
        added = 0
        skipped = 0
        for cam in SAMPLE_CAMERAS:
            if cam["name"] in existing_names:
                skipped += 1
                continue
            cid = db.add_camera(
                name=cam["name"],
                rtsp_url=cam["rtsp_url"],
                location=cam["location"],
                role=cam["role"],
                enabled=bool(cam["enabled"]),
            )
            added += 1
            status = "enabled" if cam["enabled"] else "DISABLED (edit via UI)"
            print(f"  + [{cid}] {cam['name']}  ({cam['role']}, {status})")

        print()
        print(f"Seeded: {added} new, skipped {skipped} existing")
        print("Use web UI at /admin to edit credentials and enable cameras.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
