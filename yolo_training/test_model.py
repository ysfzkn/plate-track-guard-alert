"""
YOLOv8 Test / Cikarim (Inference) Scripti

Egitilmis modeli bir test videosu uzerinde calistirir
ve sonuclari (bounding box cizilmis) yeni bir video olarak kaydeder.

Kullanim:
  python test_model.py                              # Varsayilan video
  python test_model.py --source test_video.mp4      # Belirli video
  python test_model.py --source 0                   # Kamera (webcam)
  python test_model.py --source image.jpg           # Tek gorsel

Gereksinimler:
  pip install ultralytics opencv-python
"""

import argparse
import sys
from pathlib import Path

import cv2
from ultralytics import YOLO

# --- Varsayilan Degerler ---
DEFAULT_WEIGHTS = "runs/detect/plate_detector/weights/best.pt"
DEFAULT_SOURCE = "test_video.mp4"
DEFAULT_CONF = 0.25         # Minimum guven esigi
DEFAULT_IOU = 0.45          # NMS IoU esigi
OUTPUT_DIR = Path("runs/detect/test_output")


def run_inference(weights: str, source: str, conf: float, iou: float, show: bool):
    """Modeli yukle ve cikarim yap."""

    weights_path = Path(weights)
    if not weights_path.exists():
        print(f"[HATA] Agirlik dosyasi bulunamadi: {weights}")
        print("  Lutfen once train_yolo.py ile egitimi tamamlayin.")
        print(f"  Beklenen konum: {DEFAULT_WEIGHTS}")
        sys.exit(1)

    print("=" * 60)
    print("  YOLOv8 PLAKA TESPIT — TEST")
    print("=" * 60)
    print(f"  Agirliklar: {weights}")
    print(f"  Kaynak: {source}")
    print(f"  Guven Esigi: {conf}")
    print("=" * 60)

    # Modeli yukle
    model = YOLO(weights)

    # Kaynak bir dosya mi?
    source_path = Path(source)
    is_video = source_path.suffix.lower() in {".mp4", ".avi", ".mkv", ".mov"}
    is_image = source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
    is_camera = source.isdigit()

    if is_video or is_camera:
        _process_video(model, source, conf, iou, show)
    elif is_image:
        _process_image(model, source, conf, iou, show)
    else:
        # Ultralytics kendi cikarsama yapar
        results = model.predict(
            source=source,
            conf=conf,
            iou=iou,
            save=True,
            project=str(OUTPUT_DIR),
            name="predictions",
            exist_ok=True,
        )
        print(f"\nSonuclar kaydedildi: {OUTPUT_DIR}/predictions/")


def _process_video(model, source, conf, iou, show):
    """Video uzerinde frame frame cikarim yap."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        print(f"[HATA] Video/kamera acilamadi: {source}")
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Cikti video yazici
    output_path = OUTPUT_DIR / "output_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    frame_count = 0
    detection_count = 0

    print(f"Video isleniyor... (Cikmak icin 'q' tusuna basin)")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        # Her frame'de cikarim yap
        results = model.predict(
            source=frame,
            conf=conf,
            iou=iou,
            verbose=False,
        )

        # Sonuclari frame uzerine ciz
        annotated = results[0].plot()

        # Tespit sayisini say
        detections = len(results[0].boxes)
        if detections > 0:
            detection_count += detections

        # Bilgi metni ekle
        info_text = f"Frame: {frame_count} | Tespitler: {detections}"
        cv2.putText(
            annotated, info_text, (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2,
        )

        writer.write(annotated)

        if show:
            cv2.imshow("YOLOv8 Plaka Tespit", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        # Ilerleme gostergesi
        if frame_count % 100 == 0:
            print(f"  {frame_count} frame islendi, {detection_count} tespit...")

    cap.release()
    writer.release()
    if show:
        cv2.destroyAllWindows()

    print(f"\nTamamlandi!")
    print(f"  Islenen frame: {frame_count}")
    print(f"  Toplam tespit: {detection_count}")
    print(f"  Cikti video: {output_path}")


def _process_image(model, source, conf, iou, show):
    """Tek gorsel uzerinde cikarim yap."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = model.predict(
        source=source,
        conf=conf,
        iou=iou,
    )

    annotated = results[0].plot()
    detections = len(results[0].boxes)

    output_path = OUTPUT_DIR / f"predicted_{Path(source).name}"
    cv2.imwrite(str(output_path), annotated)

    print(f"Tespit sayisi: {detections}")
    print(f"Sonuc kaydedildi: {output_path}")

    if show:
        cv2.imshow("YOLOv8 Plaka Tespit", annotated)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    # Tespit detaylari
    for box in results[0].boxes:
        cls = int(box.cls[0])
        conf_val = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        print(f"  Plaka bulundu: conf={conf_val:.2f} bbox=({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YOLOv8 Plaka Tespit — Test")
    parser.add_argument("--weights", default=DEFAULT_WEIGHTS, help="Model agirlik dosyasi (.pt)")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Video/gorsel/kamera (0=webcam)")
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF, help="Minimum guven esigi")
    parser.add_argument("--iou", type=float, default=DEFAULT_IOU, help="NMS IoU esigi")
    parser.add_argument("--show", action="store_true", help="Sonuclari ekranda goster")
    args = parser.parse_args()

    run_inference(args.weights, args.source, args.conf, args.iou, args.show)
