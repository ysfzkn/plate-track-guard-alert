"""Screenshot capture with overlay for unauthorized passages."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("gateguard.app")


def save_screenshot(
    frame: np.ndarray,
    plate_text: str,
    bbox: tuple[int, int, int, int] | None,
    screenshot_dir: str,
) -> str:
    """Save a frame with alarm overlay. Returns the relative URL path."""
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{plate_text}.jpg"
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

    # Use a default font (fallback to basic if no font available)
    try:
        font_large = ImageFont.truetype("arial.ttf", 28)
        font_small = ImageFont.truetype("arial.ttf", 16)
    except (OSError, IOError):
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Timestamp (top-left)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    draw.text((10, 10), ts, fill=(255, 255, 255), font=font_small)

    # Plate text (bottom center, large)
    w_img, h_img = img.size
    text_bbox = draw.textbbox((0, 0), plate_text, font=font_large)
    tw = text_bbox[2] - text_bbox[0]
    tx = (w_img - tw) // 2
    # Background bar for plate text
    draw.rectangle(
        [(tx - 10, h_img - 50), (tx + tw + 10, h_img - 10)],
        fill=(0, 0, 0),
    )
    draw.text((tx, h_img - 48), plate_text, fill=(255, 50, 50), font=font_large)

    # "KACAK GECIS" watermark (diagonal)
    watermark = "KACAK GECIS"
    wm_bbox = draw.textbbox((0, 0), watermark, font=font_large)
    wm_w = wm_bbox[2] - wm_bbox[0]
    wm_h = wm_bbox[3] - wm_bbox[1]

    # Create a separate image for rotated watermark
    wm_img = Image.new("RGBA", (wm_w + 20, wm_h + 20), (0, 0, 0, 0))
    wm_draw = ImageDraw.Draw(wm_img)
    wm_draw.text((10, 10), watermark, fill=(255, 0, 0, 128), font=font_large)
    wm_img = wm_img.rotate(30, expand=True)

    # Paste watermark at center
    paste_x = (w_img - wm_img.width) // 2
    paste_y = (h_img - wm_img.height) // 2
    img.paste(wm_img, (paste_x, paste_y), wm_img)

    # Save
    img.save(str(filepath), "JPEG", quality=85)
    logger.info("Screenshot saved: %s", filepath)

    # Return URL-relative path (for serving via /static/)
    return f"/static/screenshots/{filename}"
