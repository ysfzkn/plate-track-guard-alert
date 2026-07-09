"""Export YOLOv8 person model to ONNX for faster CPU inference.

Why ONNX?
  - On CPU, ultralytics' default PyTorch backend is slow because each
    inference flows through the Python autograd machinery.
  - ONNX Runtime executes the graph through a heavily optimized C++ engine
    with operator fusion and SIMD vectorization. We typically see 2–3× speedup
    on Intel/AMD CPUs and similar gains on ARM.

Usage:
    python scripts/export_onnx.py                           # default model
    python scripts/export_onnx.py --model yolov8s.pt        # different size
    python scripts/export_onnx.py --imgsz 640               # input size

After export, set in .env:
    YOLO_PERSON_MODEL=models/yolov8n.onnx

The PersonDetector auto-detects .onnx and uses the ONNX backend.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Export YOLOv8 to ONNX")
    ap.add_argument("--model", default="yolov8n.pt",
                    help="Source model (.pt). Default: yolov8n.pt")
    ap.add_argument("--imgsz", type=int, default=640,
                    help="Input size (square). Default: 640")
    ap.add_argument("--opset", type=int, default=12,
                    help="ONNX opset version. Default: 12 (broad compatibility)")
    ap.add_argument("--simplify", action="store_true", default=True,
                    help="Run onnx-simplifier (faster inference). Default: on")
    ap.add_argument("--out-dir", default="models",
                    help="Where to save the .onnx file. Default: models/")
    ap.add_argument("--benchmark", action="store_true",
                    help="After export, run 50-frame timing comparison")
    args = ap.parse_args()

    print("=" * 60)
    print("YOLOv8 → ONNX exporter")
    print("=" * 60)
    print(f"  Source model: {args.model}")
    print(f"  Input size:   {args.imgsz}x{args.imgsz}")
    print(f"  ONNX opset:   {args.opset}")
    print(f"  Output dir:   {args.out_dir}")
    print()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: ultralytics not installed. Run:")
        print("       uv sync --extra intrusion")
        return 1

    # ── Export ─────────────────────────────────────────────
    print("Loading model...")
    t0 = time.time()
    model = YOLO(args.model)
    print(f"  Loaded in {time.time() - t0:.1f}s")

    print("Exporting to ONNX (this may take 30–60 seconds)...")
    t0 = time.time()
    onnx_path = model.export(
        format="onnx",
        imgsz=args.imgsz,
        opset=args.opset,
        simplify=args.simplify,
        dynamic=False,         # static shapes are faster
        half=False,            # FP32 — half-precision unsafe on CPU
    )
    onnx_path = Path(onnx_path)
    print(f"  Exported in {time.time() - t0:.1f}s")

    # Move to chosen output dir
    target = out_dir / onnx_path.name
    if onnx_path.resolve() != target.resolve():
        try:
            onnx_path.replace(target)
        except OSError:
            import shutil
            shutil.copy2(onnx_path, target)
            onnx_path.unlink(missing_ok=True)
        onnx_path = target

    size_mb = onnx_path.stat().st_size / (1024 * 1024)
    print()
    print(f"✓ Done: {onnx_path}  ({size_mb:.1f} MB)")
    print()
    print("To use this model, set in .env:")
    print(f"    YOLO_PERSON_MODEL={onnx_path.as_posix()}")
    print()

    # ── Optional benchmark ─────────────────────────────────
    if args.benchmark:
        print("=" * 60)
        print("Speed benchmark — PyTorch vs ONNX (50 frames @ 640x640)")
        print("=" * 60)
        try:
            import numpy as np
            dummy = np.zeros((args.imgsz, args.imgsz, 3), dtype=np.uint8)

            print("\nWarming up PyTorch backend...")
            for _ in range(3):
                model.predict(dummy, classes=[0], verbose=False)
            print("Timing PyTorch...")
            t0 = time.time()
            for _ in range(50):
                model.predict(dummy, classes=[0], verbose=False)
            pt_ms = (time.time() - t0) * 1000 / 50

            print("\nLoading ONNX model...")
            onnx_model = YOLO(str(onnx_path))
            print("Warming up ONNX backend...")
            for _ in range(3):
                onnx_model.predict(dummy, classes=[0], verbose=False)
            print("Timing ONNX...")
            t0 = time.time()
            for _ in range(50):
                onnx_model.predict(dummy, classes=[0], verbose=False)
            onnx_ms = (time.time() - t0) * 1000 / 50

            print()
            print(f"  PyTorch: {pt_ms:.1f} ms/frame  ({1000/pt_ms:.1f} FPS)")
            print(f"  ONNX:    {onnx_ms:.1f} ms/frame  ({1000/onnx_ms:.1f} FPS)")
            print(f"  Speedup: {pt_ms / onnx_ms:.2f}x faster")
        except Exception as e:
            print(f"  Benchmark failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
