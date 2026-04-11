"""Frame extractor — extracts training frames from gate camera recordings.

Usage:
  1. Place your video files in data/raw_videos/ (.mp4, .avi, .mkv, .mov)
  2. Run this script: python extract_frames.py
  3. Extracted frames will be saved to data/extracted_frames/

Captures ~2 frames per second, skipping near-duplicate frames automatically.
"""

import os
import sys
from pathlib import Path

import cv2

# --- Configuration ---
RAW_VIDEOS_DIR = Path("data/raw_videos")
OUTPUT_DIR = Path("data/extracted_frames")
FRAMES_PER_SECOND = 2       # How many frames to capture per second
MIN_FRAME_DIFF = 30.0        # Threshold for skipping near-identical frames
SUPPORTED_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}


def calculate_frame_diff(prev_frame, curr_frame):
    """Calculate mean pixel difference between two frames (for dedup)."""
    if prev_frame is None:
        return float("inf")
    diff = cv2.absdiff(
        cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY),
    )
    return diff.mean()


def extract_frames_from_video(video_path: Path, output_dir: Path, video_index: int):
    """Extract frames from a single video file."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  [ERROR] Cannot open video: {video_path}")
        return 0

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0

    print(f"  Video: {video_path.name}")
    print(f"  FPS: {fps:.0f} | Total: {total_frames} frames | Duration: {duration:.0f}s")

    # Process every N-th frame based on target FPS
    frame_interval = max(1, int(fps / FRAMES_PER_SECOND))

    frame_count = 0
    saved_count = 0
    prev_frame = None
    video_name = video_path.stem

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        # Only process at the configured interval
        if frame_count % frame_interval != 0:
            continue

        # Dedup check — skip near-identical frames
        diff = calculate_frame_diff(prev_frame, frame)
        if diff < MIN_FRAME_DIFF:
            continue

        prev_frame = frame.copy()

        # Save frame
        filename = f"{video_name}_frame_{saved_count:04d}.jpg"
        filepath = output_dir / filename
        cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        saved_count += 1

    cap.release()
    print(f"  Saved: {saved_count} frames")
    return saved_count


def main():
    if not RAW_VIDEOS_DIR.exists():
        RAW_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Created '{RAW_VIDEOS_DIR}' directory.")
        print("  Place your gate camera video files here and re-run this script.")
        sys.exit(0)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    videos = [
        f for f in RAW_VIDEOS_DIR.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not videos:
        print(f"[WARNING] No videos found in '{RAW_VIDEOS_DIR}'")
        print(f"  Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}")
        sys.exit(0)

    print("=" * 60)
    print(f"  FRAME EXTRACTION TOOL")
    print(f"  Found {len(videos)} video(s)")
    print(f"  Target: {FRAMES_PER_SECOND} frames per second")
    print("=" * 60)

    total_saved = 0
    for i, video in enumerate(sorted(videos)):
        print(f"\n[{i+1}/{len(videos)}]")
        count = extract_frames_from_video(video, OUTPUT_DIR, i)
        total_saved += count

    print("\n" + "=" * 60)
    print(f"  DONE!")
    print(f"  Total: {total_saved} frames saved")
    print(f"  Output: {OUTPUT_DIR.absolute()}")
    print("=" * 60)
    print()
    print("NEXT STEP:")
    print("  Upload these frames to a labeling tool:")
    print("  - Roboflow (https://roboflow.com) — easiest, web-based")
    print("  - CVAT (https://cvat.ai) — open-source, powerful")
    print("  - LabelImg — desktop app")
    print()
    print("  Draw bounding boxes around every license plate.")
    print("  Class name: 'plate'")
    print("  Export format: YOLO (.txt label files)")
    print("  Place exported labels in data/dataset/labels/")


if __name__ == "__main__":
    main()
