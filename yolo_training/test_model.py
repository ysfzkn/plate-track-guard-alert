"""YOLOv8 inference script — test a trained model on video, image, or webcam.

Usage:
  python test_model.py                              # Default test video
  python test_model.py --source test_video.mp4      # Specific video
  python test_model.py --source 0                   # Webcam (live)
  python test_model.py --source image.jpg           # Single image
  python test_model.py --source data/test_images    # Test Images Folder

Requirements:
  pip install ultralytics opencv-python
"""

import argparse
import sys
from pathlib import Path

import cv2
from ultralytics import YOLO

# --- Defaults ---
DEFAULT_WEIGHTS = "../runs/detect/runs/detect/plate_detector/weights/best.pt"
DEFAULT_SOURCE = "test_video.mp4"
DEFAULT_CONF = 0.25         # Minimum confidence threshold
DEFAULT_IOU = 0.45          # NMS IoU threshold
OUTPUT_DIR = Path("../runs/detect/test_output")


def run_inference(weights: str, source: str, conf: float, iou: float, show: bool):
    """Load model and run inference on the given source."""

    weights_path = Path(weights)
    if not weights_path.exists():
        print(f"[ERROR] Weights file not found: {weights}")
        print(f"  Run train_yolo.py first. Expected path: {DEFAULT_WEIGHTS}")
        sys.exit(1)

    print("=" * 60)
    print("  YOLOv8 PLATE DETECTION — INFERENCE")
    print("=" * 60)
    print(f"  Weights:    {weights}")
    print(f"  Source:     {source}")
    print(f"  Confidence: {conf}")
    print("=" * 60)

    model = YOLO(weights)

    source_path = Path(source)
    is_video = source_path.suffix.lower() in {".mp4", ".avi", ".mkv", ".mov"}
    is_image = source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
    is_camera = source.isdigit()
    is_folder = source_path.is_dir()

    if is_folder:
        _process_folder(model, source_path, conf, iou, show)
    elif is_video or is_camera:
        _process_video(model, source, conf, iou, show)
    elif is_image:
        _process_image(model, source, conf, iou, show)
    else:
        print(f"[ERROR] Unsupported source: {source}")
        print("  Pass an image (.jpg/.png), video (.mp4/.avi), folder path, or camera index (0)")
        sys.exit(1)


def _process_folder(model, folder: Path, conf, iou, show):
    """Run inference on every image in a folder and save annotated results."""
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    images = sorted(f for f in folder.rglob("*") if f.suffix.lower() in IMAGE_EXTS)

    if not images:
        print(f"[ERROR] No images found in: {folder}")
        print(f"  Supported formats: {', '.join(IMAGE_EXTS)}")
        sys.exit(1)

    out_dir = OUTPUT_DIR / "folder_predictions"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(images)} images from: {folder}")
    print(f"Output: {out_dir}")
    print()

    total_detections = 0
    images_with_plates = 0

    for i, img_path in enumerate(images, 1):
        results = model.predict(source=str(img_path), conf=conf, iou=iou, verbose=False)
        annotated = results[0].plot()
        detections = len(results[0].boxes)

        out_path = out_dir / f"pred_{img_path.name}"
        cv2.imwrite(str(out_path), annotated)

        if detections > 0:
            total_detections += detections
            images_with_plates += 1
            plates_info = []
            for box in results[0].boxes:
                c = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                plates_info.append(f"conf={c:.2f}")
            print(f"  [{i}/{len(images)}] {img_path.name} → {detections} plate(s) [{', '.join(plates_info)}]")
        else:
            print(f"  [{i}/{len(images)}] {img_path.name} → no plates")

        if show:
            cv2.imshow("YOLOv8 Plate Detection", annotated)
            key = cv2.waitKey(0) & 0xFF
            if key == ord("q"):
                print("  Stopped by user (q)")
                break

    if show:
        cv2.destroyAllWindows()

    print()
    print("=" * 60)
    print(f"  FOLDER INFERENCE COMPLETE")
    print("=" * 60)
    print(f"  Images processed:    {len(images)}")
    print(f"  Images with plates:  {images_with_plates}")
    print(f"  Total detections:    {total_detections}")
    print(f"  Detection rate:      {images_with_plates/len(images)*100:.1f}%")
    print(f"  Output:              {out_dir}")
    print("=" * 60)


def _process_video(model, source, conf, iou, show):
    """Run frame-by-frame inference on a video stream."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open video/camera: {source}")
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Output video writer
    output_path = OUTPUT_DIR / "output_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    frame_count = 0
    detection_count = 0

    print(f"Processing video... (press 'q' to stop)")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        results = model.predict(source=frame, conf=conf, iou=iou, verbose=False)
        annotated = results[0].plot()

        detections = len(results[0].boxes)
        if detections > 0:
            detection_count += detections

        # Overlay frame info
        info_text = f"Frame: {frame_count} | Detections: {detections}"
        cv2.putText(annotated, info_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        writer.write(annotated)

        if show:
            cv2.imshow("YOLOv8 Plate Detection", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        if frame_count % 100 == 0:
            print(f"  {frame_count} frames processed, {detection_count} detections...")

    cap.release()
    writer.release()
    if show:
        cv2.destroyAllWindows()

    print(f"\nDone!")
    print(f"  Frames processed: {frame_count}")
    print(f"  Total detections: {detection_count}")
    print(f"  Output video:     {output_path}")


def _process_image(model, source, conf, iou, show):
    """Run inference on a single image."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = model.predict(source=source, conf=conf, iou=iou)
    annotated = results[0].plot()
    detections = len(results[0].boxes)

    output_path = OUTPUT_DIR / f"predicted_{Path(source).name}"
    cv2.imwrite(str(output_path), annotated)

    print(f"Detections: {detections}")
    print(f"Saved to:   {output_path}")

    if show:
        cv2.imshow("YOLOv8 Plate Detection", annotated)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    for box in results[0].boxes:
        cls = int(box.cls[0])
        conf_val = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        print(f"  Plate found: conf={conf_val:.2f} bbox=({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YOLOv8 Plate Detection — Inference")
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS, help="Model weights (.pt)")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Video/image/camera (0=webcam)")
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF, help="Min confidence threshold")
    parser.add_argument("--iou", type=float, default=DEFAULT_IOU, help="NMS IoU threshold")
    parser.add_argument("--show", action="store_true", help="Display results in a window")
    args = parser.parse_args()

    run_inference(args.weights, args.source, args.conf, args.iou, args.show)
