"""Benchmark the person-detection model on a folder of test images.

Expected folder structure (default labels inferred from subdirectory name):

    tests/fixtures/intrusion/
    ├── positive/               → photos that SHOULD trigger (person present)
    │   ├── img1.jpg
    │   └── ...
    ├── negative/               → photos that should NOT trigger (no person)
    │   ├── empty_alley.jpg
    │   └── ...
    └── edge_cases/             → harder cases (pet, mannequin, shadow)
        └── cat_at_night.jpg

Alternatively, label per-file via filename suffix:
    person_<name>.jpg   → expected = person present
    empty_<name>.jpg    → expected = no person
    animal_<name>.jpg   → expected = no person (a person class filter test)

Usage:
    python scripts/benchmark_intrusion.py                           # default folder
    python scripts/benchmark_intrusion.py --folder path/to/fixtures
    python scripts/benchmark_intrusion.py --conf 0.6                # override confidence
    python scripts/benchmark_intrusion.py --report report.md        # save markdown report

The script prints a confusion matrix and writes a CSV report next to the folder.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# Make project root importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ── Labeling rules ──────────────────────────────────────────

def expected_from_path(path: Path, base: Path) -> bool | None:
    """Infer expected label from folder name or filename prefix.

    Returns:
        True  — photo should contain a person (positive class)
        False — photo should NOT contain a person
        None  — unknown, skip from metrics
    """
    # Folder-based
    rel = path.relative_to(base)
    parts = [p.lower() for p in rel.parts]

    if any(p in ("positive", "person", "intruder", "yes") for p in parts):
        return True
    if any(p in ("negative", "empty", "clean", "no", "animals", "cats", "dogs") for p in parts):
        return False

    # Filename prefix
    name = path.stem.lower()
    if name.startswith(("person_", "positive_", "intruder_", "yes_")):
        return True
    if name.startswith(("empty_", "negative_", "animal_", "cat_", "dog_", "no_")):
        return False

    return None


# ── Pretty print ────────────────────────────────────────────

def print_header(s: str):
    print()
    print("=" * 68)
    print("  " + s)
    print("=" * 68)


def print_row(label: str, value, width=38):
    print(f"  {label:<{width}} {value}")


# ── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark PersonDetector on test images")
    parser.add_argument("--folder", default="tests/fixtures/intrusion",
                        help="Test images root folder")
    parser.add_argument("--conf", type=float, default=None,
                        help="Override INTRUSION_CONFIDENCE (default from .env)")
    parser.add_argument("--model", default=None,
                        help="Override YOLO model path (default: .env)")
    parser.add_argument("--report", default=None,
                        help="Write markdown report to this path")
    parser.add_argument("--csv", default=None,
                        help="Write per-image CSV to this path (default: <folder>/results.csv)")
    parser.add_argument("--show-misses", action="store_true",
                        help="Print FP/FN filenames in detail")
    args = parser.parse_args()

    base = Path(args.folder).resolve()
    if not base.exists():
        print(f"[ERROR] Folder not found: {base}")
        print("  Put some test images there; see script header for structure.")
        sys.exit(1)

    # Collect image files
    images = sorted([p for p in base.rglob("*") if p.suffix.lower() in IMAGE_EXTS])
    if not images:
        print(f"[ERROR] No images found in {base}")
        sys.exit(1)

    print_header(f"Intrusion detector benchmark — {base}")
    print_row("Images found", len(images))

    # Lazy import of PersonDetector (pulls in ultralytics/YOLO)
    print("\n  Loading PersonDetector... (first run downloads yolov8n.pt ~6MB)")
    t_load = time.time()
    try:
        from config import settings
        from app.intrusion.person_detector import PersonDetector
    except ImportError as e:
        print(f"[ERROR] Cannot import detector: {e}")
        print("  Run: uv sync --extra intrusion")
        sys.exit(1)

    conf = args.conf if args.conf is not None else settings.INTRUSION_CONFIDENCE
    model_path = args.model or settings.YOLO_PERSON_MODEL
    detector = PersonDetector(
        model_path=model_path,
        tracker_config=settings.YOLO_PERSON_TRACKER,
        use_gpu=settings.USE_GPU_FOR_PERSON,
        confidence=conf,
    )
    # Warm up
    _ = detector.detect  # lazy-loaded on first call below
    print(f"  Model: {model_path} | conf threshold: {conf} | GPU: {settings.USE_GPU_FOR_PERSON}")

    # ── Run ────────────────────────────────────────────────
    print_header("Running detection...")

    tp = fp = tn = fn = 0
    skipped = 0
    per_image: list[dict] = []

    t_start = time.time()
    for i, img_path in enumerate(images, 1):
        expected = expected_from_path(img_path, base)
        frame = cv2.imread(str(img_path))
        if frame is None:
            print(f"  [SKIP] Cannot read: {img_path}")
            skipped += 1
            continue

        t0 = time.time()
        observations = detector.detect(frame, camera_id=0)
        dt_ms = (time.time() - t0) * 1000

        num = len(observations)
        max_conf = max((o.confidence for o in observations), default=0.0)
        predicted = num > 0

        result_str = "?"
        if expected is True:
            if predicted: tp += 1; result_str = "TP"
            else: fn += 1; result_str = "FN ❌"
        elif expected is False:
            if predicted: fp += 1; result_str = "FP ❌"
            else: tn += 1; result_str = "TN"
        else:
            skipped += 1
            result_str = "—"

        per_image.append({
            "path": str(img_path.relative_to(base)),
            "expected": {True: "person", False: "empty", None: "?"}[expected],
            "detected": num,
            "max_conf": round(max_conf, 3),
            "result": result_str,
            "time_ms": round(dt_ms, 1),
        })

        # Progress print every 10 or on change
        bar = "█" * int(20 * i / len(images)) + "░" * (20 - int(20 * i / len(images)))
        print(f"  [{bar}] {i}/{len(images)}  {img_path.name[:30]:<30} → {num} kişi | {result_str}", end="\r", flush=True)

    total_elapsed = time.time() - t_start
    print("\n")

    # ── Metrics ────────────────────────────────────────────
    print_header("Results")
    evaluated = tp + fp + tn + fn
    print_row("Evaluated", f"{evaluated}/{len(images)}  (skipped: {skipped})")
    print_row("Total elapsed", f"{total_elapsed:.1f}s")
    if evaluated > 0:
        print_row("Avg per image", f"{(total_elapsed / len(images) * 1000):.0f} ms")

    if evaluated == 0:
        print("\n  No images had an inferable label. Check folder structure.")
        print("  Expected subfolders: positive/ negative/ OR filename prefixes: person_*, empty_*")
        sys.exit(0)

    print()
    print("  Confusion Matrix:")
    print()
    print("                       ACTUAL person │ ACTUAL empty")
    print("                      ───────────────┼──────────────")
    print(f"    PREDICTED person  │    TP = {tp:>4}    │    FP = {fp:>4}")
    print(f"    PREDICTED empty   │    FN = {fn:>4}    │    TN = {tn:>4}")

    # Derived metrics
    accuracy  = (tp + tn) / evaluated if evaluated else 0
    precision = tp / (tp + fp) if (tp + fp) else 0
    recall    = tp / (tp + fn) if (tp + fn) else 0
    specificity = tn / (tn + fp) if (tn + fp) else 0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    print()
    print_row("Accuracy", f"{accuracy*100:.1f}%   (correctly classified)")
    print_row("Recall (TP rate)", f"{recall*100:.1f}%   (person found when person present)")
    print_row("Precision", f"{precision*100:.1f}%   (correct when system fires)")
    print_row("Specificity (TN rate)", f"{specificity*100:.1f}%   (clean photo = no alarm)")
    print_row("F1 score", f"{f1:.3f}")

    # Targets
    print()
    print("  Targets (from plan):")
    ok_recall = "✓" if recall >= 0.9 else "✗"
    ok_spec = "✓" if specificity >= 0.95 else "✗"
    print(f"    [{ok_recall}] Recall ≥ 90%       : {recall*100:.1f}%")
    print(f"    [{ok_spec}] Specificity ≥ 95%  : {specificity*100:.1f}%")

    # ── Show miscategorized ──────────────────────────
    if args.show_misses or fp > 0 or fn > 0:
        print_header("Misclassified images")
        misses = [r for r in per_image if "❌" in r["result"]]
        if not misses:
            print("  None!")
        for r in misses:
            print(f"  {r['result']:>4}  {r['path']}  (detected {r['detected']} persons, max conf {r['max_conf']:.2f})")

    # ── CSV output ───────────────────────────────────
    csv_path = Path(args.csv) if args.csv else (base / "benchmark_results.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["path", "expected", "detected", "max_conf", "result", "time_ms"])
        writer.writeheader()
        writer.writerows(per_image)
    print(f"\n  CSV → {csv_path}")

    # ── Markdown report ──────────────────────────────
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(f"# Intrusion benchmark — {base.name}\n\n")
            f.write(f"- Images: **{len(images)}**, evaluated: **{evaluated}**, skipped: **{skipped}**\n")
            f.write(f"- Confidence threshold: **{conf}**\n")
            f.write(f"- Model: `{model_path}`\n")
            f.write(f"- Total time: **{total_elapsed:.1f}s**\n\n")
            f.write("## Confusion Matrix\n\n")
            f.write("|                  | ACTUAL person | ACTUAL empty |\n")
            f.write("|------------------|--------------:|-------------:|\n")
            f.write(f"| PREDICTED person | TP = **{tp}**   | FP = **{fp}**  |\n")
            f.write(f"| PREDICTED empty  | FN = **{fn}**   | TN = **{tn}**  |\n\n")
            f.write("## Metrics\n\n")
            f.write(f"- Accuracy: **{accuracy*100:.1f}%**\n")
            f.write(f"- Recall (TP rate): **{recall*100:.1f}%**\n")
            f.write(f"- Precision: **{precision*100:.1f}%**\n")
            f.write(f"- Specificity: **{specificity*100:.1f}%**\n")
            f.write(f"- F1 score: **{f1:.3f}**\n\n")
            if any("❌" in r["result"] for r in per_image):
                f.write("## Misclassified\n\n")
                f.write("| Type | File | Detected | Max conf |\n")
                f.write("|------|------|---------:|---------:|\n")
                for r in per_image:
                    if "❌" in r["result"]:
                        f.write(f"| {r['result']} | `{r['path']}` | {r['detected']} | {r['max_conf']:.2f} |\n")
        print(f"  Markdown report → {args.report}")

    print()


if __name__ == "__main__":
    main()
