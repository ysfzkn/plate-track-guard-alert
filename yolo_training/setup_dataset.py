"""Dataset preparation — organizes labeled frames into YOLO train/val structure.

Usage:
  1. Label your frames (with Roboflow, CVAT, or LabelImg)
  2. Place YOLO-format .txt labels in data/dataset/labels/
  3. Run: python setup_dataset.py
  4. The script auto-splits into train/val and generates dataset.yaml

NOTE: If you exported from Roboflow in YOLOv8 format, it already
provides train/val split + YAML file. You can skip this script.
"""

import random
import shutil
from pathlib import Path

import yaml

# --- Configuration ---
FRAMES_DIR = Path("data/extracted_frames")
LABELS_DIR = Path("data/dataset/labels")
DATASET_DIR = Path("data/dataset")
TRAIN_RATIO = 0.85  # 85% train, 15% validation
YAML_PATH = DATASET_DIR / "dataset.yaml"


def find_labeled_pairs():
    """Find matching image + label file pairs."""
    pairs = []

    # Scan for label files
    label_files = list(LABELS_DIR.glob("*.txt"))
    if not label_files:
        # Maybe placed under train/val subdirs
        label_files = list((LABELS_DIR / "train").glob("*.txt"))
        label_files += list((LABELS_DIR / "val").glob("*.txt"))

    for label_path in label_files:
        stem = label_path.stem
        # Find matching image
        for ext in [".jpg", ".jpeg", ".png"]:
            img_path = FRAMES_DIR / (stem + ext)
            if img_path.exists():
                pairs.append((img_path, label_path))
                break

    return pairs


def setup_dataset():
    """Build the YOLO dataset directory structure."""
    for split in ["train", "val"]:
        (DATASET_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
        (DATASET_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Find labeled image+label pairs
    pairs = find_labeled_pairs()
    if not pairs:
        print("[WARNING] No matched image+label pairs found!")
        print()
        print("Steps to fix:")
        print("  1. Label the images in data/extracted_frames/")
        print("  2. Export YOLO-format .txt files to data/dataset/labels/")
        print("  3. Each .txt must match an image by filename (e.g. frame_001.txt <-> frame_001.jpg)")
        print()
        print("YOLO label format (class 0 = plate):")
        print("  0 0.45 0.72 0.15 0.04")
        print("  ^  ^    ^    ^    ^")
        print("  |  |    |    |    +-- height (normalized)")
        print("  |  |    |    +------- width  (normalized)")
        print("  |  |    +------------ center y (normalized)")
        print("  |  +----------------- center x (normalized)")
        print("  +-------------------- class ID (0 = plate)")
        return

    print(f"Found {len(pairs)} labeled images.")

    # Shuffle and split
    random.shuffle(pairs)
    split_idx = int(len(pairs) * TRAIN_RATIO)
    train_pairs = pairs[:split_idx]
    val_pairs = pairs[split_idx:]

    print(f"  Train: {len(train_pairs)} images")
    print(f"  Val:   {len(val_pairs)} images")

    # Copy files to dataset structure
    for split_name, split_pairs in [("train", train_pairs), ("val", val_pairs)]:
        for img_path, lbl_path in split_pairs:
            shutil.copy2(img_path, DATASET_DIR / "images" / split_name / img_path.name)
            shutil.copy2(lbl_path, DATASET_DIR / "labels" / split_name / lbl_path.name)

    # Generate dataset.yaml
    yaml_content = {
        "path": str(DATASET_DIR.absolute()),
        "train": "images/train",
        "val": "images/val",
        "nc": 1,
        "names": ["plate"],
    }

    with open(YAML_PATH, "w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f, default_flow_style=False, allow_unicode=True)

    print(f"\nCreated {YAML_PATH}")
    print()
    print("NEXT STEP:")
    print("  python train_yolo.py")


if __name__ == "__main__":
    setup_dataset()
