"""
YOLOv8 Egitim Scripti — GTX 1650 (4GB VRAM) Icin Optimize Edilmis

Kullanim:
  python train_yolo.py

Gereksinimler:
  pip install ultralytics

Bu script:
  - YOLOv8n (nano) modelini yukler (1650 icin ideal)
  - 4GB VRAM limitine uygun parametreler kullanir
  - Egitim sonuclarini runs/detect/train/ altina kaydeder
  - En iyi agirliklar: runs/detect/train/weights/best.pt
"""

from pathlib import Path

from ultralytics import YOLO

# --- Konfigürasyon ---
# GTX 1650 (4GB VRAM) icin optimize edilmis parametreler
# OOM (Out of Memory) hatasi alirsan BATCH_SIZE'i 4'e dusur

MODEL_NAME = "yolov8n.pt"       # Nano model — en hafif, 1650'de hizli calisir
DATASET_YAML = "data/dataset/dataset.yaml"
EPOCHS = 100                    # Epoch sayisi (erken durdurma aktif, 100 yeterli)
BATCH_SIZE = 8                  # 4GB VRAM icin 8 guvenli. OOM olursa 4 yap.
IMAGE_SIZE = 640                # Standart YOLO cozunurlugu
DEVICE = 0                      # GPU 0 (GTX 1650)
WORKERS = 2                     # Data loader thread sayisi (RAM'e gore ayarla)
PATIENCE = 20                   # Erken durdurma sabrı (20 epoch iyilesme olmazsa dur)
PROJECT = "runs/detect"         # Sonuclarin kaydedilecegi klasor
NAME = "plate_detector"         # Deneyin adi


def main():
    yaml_path = Path(DATASET_YAML)
    if not yaml_path.exists():
        print(f"[HATA] dataset.yaml bulunamadi: {yaml_path}")
        print("  Lutfen once setup_dataset.py scriptini calistirin.")
        return

    print("=" * 60)
    print("  YOLOv8 PLAKA TESPIT MODELI EGITIMI")
    print("  GTX 1650 (4GB VRAM) Optimizasyonu")
    print("=" * 60)
    print(f"  Model: {MODEL_NAME}")
    print(f"  Veri Seti: {DATASET_YAML}")
    print(f"  Epoch: {EPOCHS}")
    print(f"  Batch: {BATCH_SIZE}")
    print(f"  Goruntu: {IMAGE_SIZE}x{IMAGE_SIZE}")
    print(f"  GPU: cuda:{DEVICE}")
    print("=" * 60)

    # Modeli yukle
    # Ilk calistirmada yolov8n.pt otomatik indirilir (~6MB)
    model = YOLO(MODEL_NAME)

    # Egitimi baslat
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

        # --- GTX 1650 Optimizasyonlari ---
        # Mixed precision (FP16) — VRAM kullanimini yarisina indirir
        amp=True,

        # Veri artirma (augmentation) — kucuk veri setlerinde kritik
        hsv_h=0.015,           # Renk tonu varyasyonu
        hsv_s=0.7,             # Doygunluk varyasyonu
        hsv_v=0.4,             # Parlaklik varyasyonu (gece/gunduz icin onemli)
        degrees=5.0,           # Hafif rotasyon
        translate=0.1,         # Yatay/dikey kaydirma
        scale=0.3,             # Olcek degisimi
        flipud=0.0,            # Dikey cevirme — plakalar icin KAPALI
        fliplr=0.0,            # Yatay cevirme — plakalar icin KAPALI
        mosaic=1.0,            # Mosaic augmentation
        mixup=0.0,             # MixUp — kucuk nesneler icin genelde kapali

        # Performans
        cache=True,            # Gorselleri RAM'e onbellekle (hizlandirir)
        verbose=True,
    )

    print("\n" + "=" * 60)
    print("  EGITIM TAMAMLANDI!")
    print("=" * 60)
    print(f"  En iyi agirliklar: {PROJECT}/{NAME}/weights/best.pt")
    print(f"  Son agirliklar:    {PROJECT}/{NAME}/weights/last.pt")
    print(f"  Egitim grafikleri: {PROJECT}/{NAME}/")
    print()
    print("SONRAKI ADIM:")
    print("  python test_model.py")
    print()
    print("PROJEYE ENTEGRASYON:")
    print("  Egitilmis modeli ana projeye entegre etmek icin:")
    print(f"  best.pt dosyasini projenin kok dizinine kopyalayin")
    print("  ve plate_detector.py'deki EasyOCR yerine YOLOv8 kullanin.")


if __name__ == "__main__":
    main()
