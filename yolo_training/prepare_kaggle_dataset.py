"""Kaggle dataset adapter — converts downloaded Kaggle YOLO datasets to our format.

This script handles the Turkish License Plate Dataset from Kaggle:
https://www.kaggle.com/datasets/smaildurcan/turkish-license-plate-dataset

Usage:
  1. Download the dataset ZIP from Kaggle
  2. Extract to yolo_training/data/kaggle/  (or any folder)
  3. Run: python prepare_kaggle_dataset.py --source data/kaggle
  4. Then: python train_yolo.py

The script will:
  - Auto-detect the dataset structure (images + labels)
  - Validate YOLO label format
  - Split into train/val if not already split
  - Generate dataset.yaml for YOLOv8 training
"""

import argparse
import os
import random
import shutil
from pathlib import Path

import yaml

# --- Defaults ---
OUTPUT_DIR = Path("data/dataset")
TRAIN_RATIO = 0.85
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
LABEL_EXT = ".txt"


def find_images(root: Path) -> list[Path]:
    """Recursively find all image files under root."""
    images = []
    for ext in IMAGE_EXTENSIONS:
        images.extend(root.rglob(f"*{ext}"))
    return sorted(images)


def find_label_for_image(img_path: Path, search_dirs: list[Path]) -> Path | None:
    """Find the matching .txt label file for a given image."""
    stem = img_path.stem
    # Check same directory first
    same_dir = img_path.parent / f"{stem}{LABEL_EXT}"
    if same_dir.exists():
        return same_dir
    # Check known label directories
    for d in search_dirs:
        candidate = d / f"{stem}{LABEL_EXT}"
        if candidate.exists():
            return candidate
    return None


def detect_structure(source: Path) -> dict:
    """Auto-detect the dataset layout.

    Common Kaggle structures:
      A) images/ + labels/  (flat or with train/val subdirs)
      B) train/images/ + train/labels/ + valid/images/ + valid/labels/
      C) Everything in one folder (images + .txt side by side)
      D) data.yaml already present (Roboflow-style export)
    """
    info = {
        "type": "unknown",
        "images": [],
        "label_dirs": [],
        "has_yaml": False,
        "has_split": False,
        "yaml_path": None,
        "class_names": ["plate"],
        "num_classes": 1,
    }

    # Check for existing YAML
    for yaml_name in ["data.yaml", "dataset.yaml", "config.yaml"]:
        yaml_path = source / yaml_name
        if yaml_path.exists():
            info["has_yaml"] = True
            info["yaml_path"] = yaml_path
            # Parse it to get class info
            try:
                with open(yaml_path) as f:
                    cfg = yaml.safe_load(f)
                if "names" in cfg:
                    info["class_names"] = cfg["names"] if isinstance(cfg["names"], list) else list(cfg["names"].values())
                    info["num_classes"] = len(info["class_names"])
            except Exception:
                pass

    # Collect all label directories
    label_dirs = []
    for d in source.rglob("*"):
        if d.is_dir() and d.name.lower() in ("labels", "label", "annotations"):
            label_dirs.append(d)
    info["label_dirs"] = label_dirs

    # Check for pre-split structure
    train_img = source / "train" / "images"
    val_img = source / "valid" / "images"
    if not val_img.exists():
        val_img = source / "val" / "images"
    if not val_img.exists():
        val_img = source / "test" / "images"

    if train_img.exists() and val_img.exists():
        info["type"] = "pre_split"
        info["has_split"] = True
        info["train_images_dir"] = train_img
        info["val_images_dir"] = val_img
        info["train_labels_dir"] = train_img.parent / "labels"
        info["val_labels_dir"] = val_img.parent / "labels"
    else:
        # Flat structure — find all images
        all_images = find_images(source)
        info["images"] = all_images
        info["type"] = "flat" if all_images else "unknown"

    return info


def prepare_pre_split(info: dict, output: Path):
    """Handle datasets that already have train/val split."""
    print("  Dataset has pre-existing train/val split")

    for split, img_dir, lbl_dir in [
        ("train", info["train_images_dir"], info["train_labels_dir"]),
        ("val", info["val_images_dir"], info["val_labels_dir"]),
    ]:
        out_img = output / "images" / split
        out_lbl = output / "labels" / split
        out_img.mkdir(parents=True, exist_ok=True)
        out_lbl.mkdir(parents=True, exist_ok=True)

        if not img_dir.exists():
            print(f"  [WARNING] {img_dir} not found, skipping {split}")
            continue

        images = find_images(img_dir)
        copied = 0
        for img in images:
            lbl = find_label_for_image(img, [lbl_dir] + info["label_dirs"])
            if lbl:
                shutil.copy2(img, out_img / img.name)
                shutil.copy2(lbl, out_lbl / lbl.name)
                copied += 1

        print(f"  {split}: {copied} image+label pairs")


def prepare_flat(info: dict, output: Path):
    """Handle flat datasets — split into train/val ourselves."""
    print("  Flat dataset — auto-splitting into train/val")

    # Find all image+label pairs
    pairs = []
    for img in info["images"]:
        lbl = find_label_for_image(img, info["label_dirs"])
        if lbl:
            pairs.append((img, lbl))

    if not pairs:
        print("  [ERROR] No matching image+label pairs found!")
        print("  Make sure .txt label files exist alongside or in a labels/ subfolder")
        return

    print(f"  Found {len(pairs)} labeled images")

    # Shuffle and split
    random.shuffle(pairs)
    split_idx = int(len(pairs) * TRAIN_RATIO)
    splits = {
        "train": pairs[:split_idx],
        "val": pairs[split_idx:],
    }

    for split_name, split_pairs in splits.items():
        out_img = output / "images" / split_name
        out_lbl = output / "labels" / split_name
        out_img.mkdir(parents=True, exist_ok=True)
        out_lbl.mkdir(parents=True, exist_ok=True)

        for img, lbl in split_pairs:
            shutil.copy2(img, out_img / img.name)
            shutil.copy2(lbl, out_lbl / lbl.name)

        print(f"  {split_name}: {len(split_pairs)} images")


def validate_labels(output: Path) -> int:
    """Spot-check a few label files for valid YOLO format."""
    issues = 0
    label_files = list((output / "labels" / "train").glob("*.txt"))[:20]

    for lbl in label_files:
        try:
            with open(lbl) as f:
                for line_num, line in enumerate(f, 1):
                    parts = line.strip().split()
                    if len(parts) == 0:
                        continue
                    if len(parts) != 5:
                        print(f"  [WARNING] {lbl.name}:{line_num} — expected 5 values, got {len(parts)}")
                        issues += 1
                        continue
                    cls_id = int(parts[0])
                    coords = [float(x) for x in parts[1:]]
                    if any(c < 0 or c > 1 for c in coords):
                        print(f"  [WARNING] {lbl.name}:{line_num} — coordinates out of [0,1] range")
                        issues += 1
        except Exception as e:
            print(f"  [WARNING] Cannot parse {lbl.name}: {e}")
            issues += 1

    return issues


def generate_yaml(output: Path, class_names: list[str]):
    """Generate dataset.yaml for YOLOv8."""
    yaml_content = {
        "path": str(output.absolute()),
        "train": "images/train",
        "val": "images/val",
        "nc": len(class_names),
        "names": class_names,
    }

    yaml_path = output / "dataset.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f, default_flow_style=False, allow_unicode=True)

    return yaml_path


def count_dataset(output: Path) -> dict:
    """Count images and labels in the prepared dataset."""
    counts = {}
    for split in ["train", "val"]:
        img_dir = output / "images" / split
        lbl_dir = output / "labels" / split
        img_count = len(list(img_dir.glob("*"))) if img_dir.exists() else 0
        lbl_count = len(list(lbl_dir.glob("*.txt"))) if lbl_dir.exists() else 0
        counts[split] = {"images": img_count, "labels": lbl_count}
    return counts


def main():
    parser = argparse.ArgumentParser(description="Prepare Kaggle dataset for YOLOv8 training")
    parser.add_argument("--source", required=True, help="Path to extracted Kaggle dataset folder")
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="Output dataset directory")
    parser.add_argument("--split", type=float, default=TRAIN_RATIO, help="Train ratio (default 0.85)")
    args = parser.parse_args()

    source = Path(args.source)
    output = Path(args.output)

    if not source.exists():
        print(f"[ERROR] Source directory not found: {source}")
        print(f"  Download the dataset from Kaggle and extract it to: {source}")
        return

    print("=" * 60)
    print("  KAGGLE DATASET PREPARATION")
    print("=" * 60)
    print(f"  Source: {source.absolute()}")
    print(f"  Output: {output.absolute()}")
    print()

    # Step 1: Detect dataset structure
    print("[1/4] Detecting dataset structure...")
    info = detect_structure(source)
    print(f"  Type:    {info['type']}")
    print(f"  Classes: {info['class_names']} (nc={info['num_classes']})")
    print(f"  YAML:    {'found' if info['has_yaml'] else 'not found (will generate)'}")
    print(f"  Split:   {'pre-split' if info['has_split'] else 'needs splitting'}")

    if info["type"] == "unknown":
        print()
        print("[ERROR] Could not detect dataset structure!")
        print("  Expected: a folder with images (.jpg/.png) and labels (.txt)")
        print(f"  Found {len(find_images(source))} images in {source}")
        return

    # Step 2: Organize files
    print()
    print("[2/4] Organizing files...")
    if info["type"] == "pre_split":
        prepare_pre_split(info, output)
    else:
        prepare_flat(info, output)

    # Step 3: Validate labels
    print()
    print("[3/4] Validating label format...")
    issues = validate_labels(output)
    if issues == 0:
        print("  All labels valid")
    else:
        print(f"  {issues} issues found (training may still work)")

    # Step 4: Generate YAML
    print()
    print("[4/4] Generating dataset.yaml...")
    yaml_path = generate_yaml(output, info["class_names"])
    print(f"  Created: {yaml_path}")

    # Summary
    counts = count_dataset(output)
    print()
    print("=" * 60)
    print("  DATASET READY!")
    print("=" * 60)
    print(f"  Train: {counts['train']['images']} images, {counts['train']['labels']} labels")
    print(f"  Val:   {counts['val']['images']} images, {counts['val']['labels']} labels")
    print(f"  YAML:  {yaml_path}")
    print()
    print("NEXT STEP:")
    print("  python train_yolo.py")


if __name__ == "__main__":
    main()
