"""YOLOv8 training script — optimized for GTX 1650 (4 GB VRAM).

Usage:
  python train_yolo.py

Requirements:
  pip install ultralytics

Output:
  Best weights: runs/detect/plate_detector/weights/best.pt
"""

from pathlib import Path

from ultralytics import YOLO

# --- Configuration (tuned for GTX 1650 4 GB VRAM) ---
# If you get OOM errors, reduce BATCH_SIZE to 4

MODEL_NAME = "yolov8n.pt"       # Nano model — lightest, fastest on 1650
DATASET_YAML = "data/dataset/dataset.yaml"
EPOCHS = 100                    # Max epochs (early stopping will cut short if needed)
BATCH_SIZE = 8                  # Safe for 4 GB VRAM; use 4 if OOM
IMAGE_SIZE = 640                # Standard YOLO input resolution
DEVICE = 0                      # GPU index (0 = first GPU)
WORKERS = 2                     # Dataloader threads (adjust per your CPU/RAM)
PATIENCE = 20                   # Stop if no improvement for 20 epochs
PROJECT = "runs/detect"
NAME = "plate_detector"


def main():
    yaml_path = Path(DATASET_YAML)
    if not yaml_path.exists():
        print(f"[ERROR] dataset.yaml not found: {yaml_path}")
        print("  Run setup_dataset.py first to create the dataset config.")
        return

    print("=" * 60)
    print("  YOLOv8 LICENSE PLATE DETECTOR — TRAINING")
    print("  Optimized for GTX 1650 (4 GB VRAM)")
    print("=" * 60)
    print(f"  Model:      {MODEL_NAME}")
    print(f"  Dataset:    {DATASET_YAML}")
    print(f"  Epochs:     {EPOCHS}")
    print(f"  Batch size: {BATCH_SIZE}")
    print(f"  Image size: {IMAGE_SIZE}x{IMAGE_SIZE}")
    print(f"  Device:     cuda:{DEVICE}")
    print("=" * 60)

    # Load pretrained model (auto-downloads ~6 MB on first run)
    model = YOLO(MODEL_NAME)

    # Train
    results = model.train(
        data=str(yaml_path.absolute()),
        epochs=EPOCHS,
        batch=BATCH_SIZE,
        imgsz=IMAGE_SIZE,
        device=DEVICE,
        workers=WORKERS,
        patience=PATIENCE,
        project=PROJECT,
        name=NAME,
        exist_ok=True,

        # --- GPU memory optimizations ---
        amp=True,              # Mixed precision (FP16) — halves VRAM usage

        # --- Data augmentation (critical for small datasets) ---
        hsv_h=0.015,           # Hue shift
        hsv_s=0.7,             # Saturation variation
        hsv_v=0.4,             # Brightness variation (important for day/night)
        degrees=5.0,           # Slight rotation
        translate=0.1,         # Translation shift
        scale=0.3,             # Scale jitter
        flipud=0.0,            # Vertical flip — disabled for plates
        fliplr=0.0,            # Horizontal flip — disabled for plates
        mosaic=1.0,            # Mosaic augmentation
        mixup=0.0,             # MixUp — generally off for small objects

        # --- Performance ---
        cache=True,            # Cache images in RAM (faster epochs)
        verbose=True,
    )

    print("\n" + "=" * 60)
    print("  TRAINING COMPLETE!")
    print("=" * 60)
    print(f"  Best weights: {PROJECT}/{NAME}/weights/best.pt")
    print(f"  Last weights: {PROJECT}/{NAME}/weights/last.pt")
    print(f"  Metrics:      {PROJECT}/{NAME}/")
    print()
    print("NEXT STEP:")
    print("  python test_model.py")
    print()
    print("DEPLOY TO GATEGUARD:")
    print(f"  1. copy {PROJECT}\\{NAME}\\weights\\best.pt models\\plate_detector.pt")
    print("  2. Set USE_YOLO=true in .env")
    print("  3. Restart the server")


if __name__ == "__main__":
    main()
