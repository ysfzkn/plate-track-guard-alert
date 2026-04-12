"""End-to-end plate OCR benchmark — tests multiple engines on YOLO-detected crops.

Engines tested:
  1. EasyOCR         — general OCR (baseline)
  2. PaddleOCR       — better structured text OCR
  3. fast-plate-ocr  — purpose-built plate OCR (trained on 220K plates globally)
  4. fast-alpr       — full ALPR pipeline (own detector + own OCR, no YOLO needed)

Install engines:
  pip install easyocr                              # Engine 1
  pip install paddleocr paddlepaddle               # Engine 2
  pip install fast-plate-ocr[onnx]                 # Engine 3
  pip install fast-alpr[onnx]                      # Engine 4

Usage:
  python test_ocr_pipeline.py --source data/test_images
  python test_ocr_pipeline.py --source data/test_images --show
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import cv2
import numpy as np

DEFAULT_WEIGHTS = "../runs/detect/runs/detect/plate_detector/weights/best.pt"
DEFAULT_CONF = 0.25
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
OUTPUT_DIR = Path("../runs/detect/test_output/ocr_results")

TURKISH_CHAR_MAP = str.maketrans("İŞÇĞÖÜışçğöü", "ISCGOUiscgou")
PLATE_REGEX = re.compile(r"^(0[1-9]|[1-7][0-9]|8[01])[A-Z]{1,3}\d{2,4}$")


def normalize_plate(raw: str) -> str:
    text = raw.translate(TURKISH_CHAR_MAP).upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def is_valid_plate(normalized: str) -> bool:
    return bool(PLATE_REGEX.match(normalized))


def upscale_crop(crop, target_width=400):
    """Upscale small crops so OCR has enough pixels to work with."""
    h, w = crop.shape[:2]
    if w < target_width:
        scale = target_width / w
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return crop


def enhance_crop(crop):
    """CLAHE contrast enhancement on grayscale."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


# ================================================================
#  OCR ENGINE WRAPPERS
# ================================================================

def load_easyocr():
    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False)
        return reader
    except ImportError:
        return None


def run_easyocr(reader, crop):
    """Run EasyOCR on a crop. Returns (plate_text, confidence)."""
    large = upscale_crop(crop)
    gray = enhance_crop(large)
    results = reader.readtext(gray, allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for _, text, conf in results:
        n = normalize_plate(text)
        if len(n) >= 6 and is_valid_plate(n):
            return n, conf, text
    for _, text, conf in results:
        n = normalize_plate(text)
        if len(n) >= 5:
            return n, conf, text
    return "", 0.0, ""


def load_paddleocr():
    try:
        from paddleocr import PaddleOCR
        import logging
        logging.getLogger("ppocr").setLevel(logging.WARNING)
        ocr = PaddleOCR(use_angle_cls=True, lang="en")
        return ocr
    except ImportError:
        return None


def run_paddleocr(ocr, crop):
    """Run PaddleOCR on a crop."""
    large = upscale_crop(crop)
    try:
        result = ocr.ocr(large, cls=True)
        if result and result[0]:
            for line in result[0]:
                text, conf = line[1][0], line[1][1]
                n = normalize_plate(text)
                if len(n) >= 5:
                    return n, conf, text
    except Exception:
        pass
    return "", 0.0, ""


def load_fast_plate_ocr():
    try:
        from fast_plate_ocr import LicensePlateRecognizer
        m = LicensePlateRecognizer("cct-s-v2-global-model")
        return m
    except (ImportError, Exception):
        return None


def run_fast_plate_ocr(model, crop):
    """Run fast-plate-ocr on a crop (expects cropped plate image)."""
    large = upscale_crop(crop)
    gray = cv2.cvtColor(large, cv2.COLOR_BGR2GRAY) if len(large.shape) == 3 else large
    try:
        # fast-plate-ocr accepts file path or numpy array
        preds = model.run(gray)
        if preds:
            text = preds[0] if isinstance(preds[0], str) else str(preds[0])
            n = normalize_plate(text)
            return n, 0.9, text  # Doesn't always return confidence
    except Exception:
        pass
    return "", 0.0, ""


def load_fast_alpr():
    try:
        from fast_alpr import ALPR
        alpr = ALPR(
            detector_model="yolo-v9-t-384-license-plate-end2end",
            ocr_model="cct-s-v2-global-model",
        )
        return alpr
    except (ImportError, Exception):
        return None


def run_fast_alpr(alpr, full_frame):
    """Run fast-alpr on the FULL frame (it does its own detection + OCR).
    Returns list of (plate_text, confidence, raw_text).
    """
    try:
        results = alpr.predict(full_frame)
        plates = []
        for r in results:
            # Extract text — handle different API response shapes
            if hasattr(r, "ocr"):
                text = r.ocr.text if hasattr(r.ocr, "text") else str(r.ocr)
                conf = r.ocr.confidence if hasattr(r.ocr, "confidence") else 0.0
            else:
                text = str(r)
                conf = 0.0

            # Confidence might be a list — take mean
            if isinstance(conf, (list, tuple)):
                conf = sum(conf) / len(conf) if conf else 0.0
            conf = float(conf)

            # Raw text might also need flattening
            if isinstance(text, (list, tuple)):
                text = "".join(str(t) for t in text)

            n = normalize_plate(text)
            plates.append((n, conf, text))
        return plates
    except Exception as e:
        return []


def main():
    parser = argparse.ArgumentParser(description="Multi-engine plate OCR benchmark")
    parser.add_argument("--source", required=True, help="Image file or folder")
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS, help="YOLO weights for crop-based engines")
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    if source.is_dir():
        images = sorted(f for f in source.rglob("*") if f.suffix.lower() in IMAGE_EXTS)
    elif source.is_file():
        images = [source]
    else:
        print(f"[ERROR] Source not found: {source}")
        sys.exit(1)

    if not images:
        print(f"[ERROR] No images in: {source}")
        sys.exit(1)

    # --- Load YOLO (for crop-based engines) ---
    yolo = None
    weights_path = Path(args.weights)
    if weights_path.exists():
        print("Loading YOLO plate detector...")
        from ultralytics import YOLO
        yolo = YOLO(str(weights_path))
    else:
        print(f"[WARNING] YOLO weights not found: {args.weights}")
        print("  Crop-based engines (EasyOCR, PaddleOCR, fast-plate-ocr) will be skipped.")
        print("  Only fast-alpr (has its own detector) will run.")

    # --- Load OCR engines ---
    engines = {}

    print("Loading engines...")
    easyocr_reader = load_easyocr()
    if easyocr_reader:
        engines["EasyOCR"] = True
        print("  EasyOCR:        ready")
    else:
        print("  EasyOCR:        not installed")

    paddle = load_paddleocr()
    if paddle:
        engines["PaddleOCR"] = True
        print("  PaddleOCR:      ready")
    else:
        print("  PaddleOCR:      not installed (pip install paddleocr paddlepaddle)")

    fpo = load_fast_plate_ocr()
    if fpo:
        engines["fast-plate-ocr"] = True
        print("  fast-plate-ocr: ready")
    else:
        print("  fast-plate-ocr: not installed (pip install fast-plate-ocr[onnx])")

    falpr = load_fast_alpr()
    if falpr:
        engines["fast-alpr"] = True
        print("  fast-alpr:      ready (has own detector — doesn't need YOLO)")
    else:
        print("  fast-alpr:      not installed (pip install fast-alpr[onnx])")

    if not engines:
        print("\n[ERROR] No OCR engines available! Install at least one:")
        print("  pip install easyocr")
        print("  pip install fast-plate-ocr[onnx]")
        print("  pip install fast-alpr[onnx]")
        sys.exit(1)

    # Prepare output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    crops_dir = OUTPUT_DIR / "crops"
    crops_dir.mkdir(exist_ok=True)

    print()
    print("=" * 80)
    print(f"  PLATE OCR BENCHMARK — {len(images)} images × {len(engines)} engines")
    print("=" * 80)

    scores = {name: {"valid": 0, "found": 0, "total": 0} for name in engines}
    all_results = []

    for idx, img_path in enumerate(images, 1):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue

        print(f"\n{'─'*80}")
        print(f"[{idx}/{len(images)}] {img_path.name}")

        # --- YOLO detection for crop-based engines ---
        crops = []
        if yolo:
            yolo_res = yolo.predict(source=frame, conf=args.conf, verbose=False)
            for box in yolo_res[0].boxes:
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                yc = float(box.conf[0])
                pad_x = int((x2 - x1) * 0.12)
                pad_y = int((y2 - y1) * 0.20)
                c = frame[max(0, y1-pad_y):min(frame.shape[0], y2+pad_y),
                          max(0, x1-pad_x):min(frame.shape[1], x2+pad_x)]
                if c.size > 0:
                    crops.append((c, yc, (x1, y1, x2, y2)))

            if not crops:
                print(f"  YOLO: no plates detected")

        # Results per image
        image_results = {}

        # --- Run crop-based engines ---
        for ci, (crop, yolo_c, bbox) in enumerate(crops):
            cw, ch = crop.shape[1], crop.shape[0]
            cv2.imwrite(str(crops_dir / f"{img_path.stem}_p{ci}.jpg"), crop)
            print(f"  Plate #{ci+1} — YOLO={yolo_c:.0%} crop={cw}x{ch}px")

            print(f"  {'ENGINE':<18} {'RAW':<22} {'NORMALIZED':<14} {'CONF':<7} {'VALID'}")
            print(f"  {'─'*18} {'─'*22} {'─'*14} {'─'*7} {'─'*5}")

            if easyocr_reader:
                n, c, raw = run_easyocr(easyocr_reader, crop)
                v = is_valid_plate(n) if n else False
                vm = "YES" if v else ("—" if not n else "NO")
                print(f"  {'EasyOCR':<18} {raw:<22} {n or '—':<14} {c:<7.0%} {vm}")
                scores["EasyOCR"]["total"] += 1
                if n: scores["EasyOCR"]["found"] += 1
                if v: scores["EasyOCR"]["valid"] += 1
                if "EasyOCR" not in image_results or v:
                    image_results["EasyOCR"] = (n, c, v)

            if paddle:
                n, c, raw = run_paddleocr(paddle, crop)
                v = is_valid_plate(n) if n else False
                vm = "YES" if v else ("—" if not n else "NO")
                print(f"  {'PaddleOCR':<18} {raw:<22} {n or '—':<14} {c:<7.0%} {vm}")
                scores["PaddleOCR"]["total"] += 1
                if n: scores["PaddleOCR"]["found"] += 1
                if v: scores["PaddleOCR"]["valid"] += 1
                if "PaddleOCR" not in image_results or v:
                    image_results["PaddleOCR"] = (n, c, v)

            if fpo:
                n, c, raw = run_fast_plate_ocr(fpo, crop)
                v = is_valid_plate(n) if n else False
                vm = "YES" if v else ("—" if not n else "NO")
                print(f"  {'fast-plate-ocr':<18} {raw:<22} {n or '—':<14} {c:<7.0%} {vm}")
                scores["fast-plate-ocr"]["total"] += 1
                if n: scores["fast-plate-ocr"]["found"] += 1
                if v: scores["fast-plate-ocr"]["valid"] += 1
                if "fast-plate-ocr" not in image_results or v:
                    image_results["fast-plate-ocr"] = (n, c, v)

        # --- Run fast-alpr (uses its own detector, not YOLO) ---
        if falpr:
            plates = run_fast_alpr(falpr, frame)
            if plates:
                for n, c, raw in plates:
                    v = is_valid_plate(n) if n else False
                    vm = "YES" if v else ("—" if not n else "NO")
                    print(f"  {'fast-alpr':<18} {raw:<22} {n or '—':<14} {c:<7.0%} {vm}")
                    scores["fast-alpr"]["total"] += 1
                    if n: scores["fast-alpr"]["found"] += 1
                    if v: scores["fast-alpr"]["valid"] += 1
                    if "fast-alpr" not in image_results or v:
                        image_results["fast-alpr"] = (n, c, v)
            else:
                print(f"  {'fast-alpr':<18} {'(no detection)':<22}")
                scores["fast-alpr"]["total"] += 1

        # Best result for this image
        best_name = ""
        best_plate = ""
        best_conf = 0.0
        for eng_name, (n, c, v) in image_results.items():
            if v and c >= best_conf:
                best_name, best_plate, best_conf = eng_name, n, c
            elif not best_plate and n:
                best_name, best_plate, best_conf = eng_name, n, c

        if best_plate:
            vld = is_valid_plate(best_plate)
            print(f"  >>> BEST: {best_plate} [{best_name}] ({best_conf:.0%}) {'VALID' if vld else ''}")
        else:
            print(f"  >>> NO ENGINE COULD READ THIS PLATE")

        all_results.append({
            "file": img_path.name,
            "plate": best_plate,
            "engine": best_name,
            "conf": round(best_conf, 3),
            "valid": is_valid_plate(best_plate) if best_plate else False,
        })

        if args.show and frame is not None:
            vis = frame.copy()
            for _, _, bbox in crops:
                x1, y1, x2, y2 = bbox
                col = (0, 255, 0) if best_plate and is_valid_plate(best_plate) else (0, 0, 255)
                cv2.rectangle(vis, (x1, y1), (x2, y2), col, 2)
                lbl = f"{best_plate} [{best_name}]" if best_plate else "FAILED"
                cv2.putText(vis, lbl, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
            cv2.imshow("OCR Benchmark", vis)
            if cv2.waitKey(0) & 0xFF == ord("q"):
                break

    if args.show:
        cv2.destroyAllWindows()

    # --- Leaderboard ---
    print()
    print()
    print("=" * 65)
    print("  ENGINE LEADERBOARD")
    print("=" * 65)
    print(f"  {'ENGINE':<18} {'TEXT FOUND':<12} {'VALID TR':<12} {'ACCURACY':<10}")
    print(f"  {'─'*18} {'─'*12} {'─'*12} {'─'*10}")

    ranked = sorted(scores.items(), key=lambda x: (x[1]["valid"], x[1]["found"]), reverse=True)
    for name, s in ranked:
        t = s["total"] or 1
        marker = " ◄◄ WINNER" if name == ranked[0][0] and s["valid"] > 0 else ""
        print(f"  {name:<18} {s['found']}/{t:<10} {s['valid']}/{t:<10} {s['valid']/t*100:>5.0f}%{marker}")

    total = len(all_results) or 1
    valid_count = sum(1 for r in all_results if r["valid"])

    csv_path = OUTPUT_DIR / "ocr_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file", "plate", "engine", "conf", "valid"])
        w.writeheader()
        w.writerows(all_results)

    print()
    print(f"  OVERALL: {valid_count}/{total} valid plates ({valid_count/total*100:.0f}%)")
    print(f"  Report:  {csv_path}")
    print("=" * 65)

    if valid_count == 0:
        print()
        print("  SUGGESTIONS:")
        print("  1. Install fast-plate-ocr:  pip install fast-plate-ocr[onnx]")
        print("     Purpose-built for plates, trained on 220K global plates")
        print("  2. Install fast-alpr:       pip install fast-alpr[onnx]")
        print("     Full pipeline with its own detector + OCR, best accuracy")
        print("  3. Train a 2nd YOLO model for character-level detection")
        print("     See: github.com/Semihocakli/turkish-plate-recognition-w-yolov8")
    elif ranked[0][1]["valid"] > 0:
        winner = ranked[0][0]
        print(f"\n  Use '{winner}' as the OCR engine in GateGuard production.")


if __name__ == "__main__":
    main()
