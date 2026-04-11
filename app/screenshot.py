"""Screenshot capture with overlay for unauthorized passages."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("gateguard.app")

# Sanitize filenames — remove anything except alphanumeric, dash, underscore
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_\-]")


def _safe_filename(plate_text: str) -> str:
    """Create a filesystem-safe filename from plate text."""
    return _SAFE_CHARS.sub("", plate_text) or "unknown"


def _get_font(size: int):
    """Try to load a TrueType font, fall back to default."""
    font_candidates = [
        "arial.ttf", "Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in font_candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def save_screenshot(
    frame: np.ndarray,
    plate_text: str,
    bbox: tuple[int, int, int, int] | None,
    screenshot_dir: str,
) -> str:
    """Save a frame with alarm overlay. Returns the relative URL path.

    Never raises — returns empty string on failure.
    """
    try:
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        safe_plate = _safe_filename(plate_text)
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{safe_plate}.jpg"
        filepath = Path(screenshot_dir) / filename

        # Work on a copy
        overlay = frame.copy()

        # Draw bounding box if available
        if bbox:
            x, y, w, h = bbox
            cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 255), 3)

        # Convert to PIL for better text rendering (Turkish chars)
        img = Image.fromarray(cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img)

        font_large = _get_font(28)
        font_small = _get_font(16)

        w_img, h_img = img.size

        # Timestamp (top-left, with background)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        ts_bbox = draw.textbbox((0, 0), ts, font=font_small)
        ts_w = ts_bbox[2] - ts_bbox[0]
        draw.rectangle([(5, 5), (ts_w + 15, 28)], fill=(0, 0, 0, 180))
        draw.text((10, 7), ts, fill=(255, 255, 255), font=font_small)

        # Plate text (bottom center, large with background)
        text_bbox = draw.textbbox((0, 0), plate_text, font=font_large)
        tw = text_bbox[2] - text_bbox[0]
        tx = (w_img - tw) // 2
        draw.rectangle(
            [(tx - 12, h_img - 52), (tx + tw + 12, h_img - 8)],
            fill=(0, 0, 0),
        )
        draw.text((tx, h_img - 50), plate_text, fill=(255, 50, 50), font=font_large)

        # "KACAK GECIS" watermark (diagonal, semi-transparent)
        watermark = "KACAK GECIS"
        try:
            wm_font = _get_font(36)
            wm_bbox = draw.textbbox((0, 0), watermark, font=wm_font)
            wm_w = wm_bbox[2] - wm_bbox[0]
            wm_h = wm_bbox[3] - wm_bbox[1]

            wm_img = Image.new("RGBA", (wm_w + 30, wm_h + 30), (0, 0, 0, 0))
            wm_draw = ImageDraw.Draw(wm_img)
            wm_draw.text((15, 15), watermark, fill=(255, 0, 0, 100), font=wm_font)
            wm_img = wm_img.rotate(25, expand=True)

            paste_x = (w_img - wm_img.width) // 2
            paste_y = (h_img - wm_img.height) // 2
            img.paste(wm_img, (paste_x, paste_y), wm_img)
        except Exception:
            pass  # Watermark is optional — don't fail the screenshot

        # Save
        img.save(str(filepath), "JPEG", quality=85)
        logger.info("Screenshot saved: %s", filepath)

        return f"/static/screenshots/{filename}"

    except Exception:
        logger.exception("Failed to save screenshot")
        return ""


def cleanup_old_screenshots(screenshot_dir: str, retention_days: int = 90) -> int:
    """Delete screenshots older than retention_days. Returns count of deleted files."""
    deleted = 0
    try:
        cutoff = datetime.now() - timedelta(days=retention_days)
        cutoff_ts = cutoff.timestamp()
        base = Path(screenshot_dir)
        if not base.exists():
            return 0

        for f in base.glob("*.jpg"):
            try:
                if f.stat().st_mtime < cutoff_ts:
                    f.unlink()
                    deleted += 1
            except OSError:
                continue

        if deleted > 0:
            logger.info("Screenshot cleanup: %d old files deleted", deleted)
    except Exception:
        logger.exception("Screenshot cleanup failed")

    return deleted
