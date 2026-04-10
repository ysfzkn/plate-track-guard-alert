"""
Veri Seti Hazirlama — Etiketlenmis frame'leri YOLO egitim yapisina duzenler.

Kullanim:
  1. Frame'leri etiketleyin (Roboflow/CVAT/LabelImg ile)
  2. YOLO format .txt etiketlerini data/dataset/labels/ altina koyun
  3. Gorselleri data/extracted_frames/ icinde birakin
  4. Bu scripti calistirin: python setup_dataset.py
  5. Script otomatik olarak train/val ayirimi yapar ve dataset.yaml olusturur

NOT: Eger Roboflow'dan export ediyorsaniz, "YOLOv8" formatini secin.
     Roboflow zaten train/val ayirimi + YAML dosyasi olusturur.
     Bu durumda bu scripte gerek yoktur.
"""

import os
import random
import shutil
from pathlib import Path

import yaml

# --- Konfigürasyon ---
FRAMES_DIR = Path("data/extracted_frames")
LABELS_DIR = Path("data/dataset/labels")  # Kullanicinin elle koydugu etiketler
DATASET_DIR = Path("data/dataset")
TRAIN_RATIO = 0.85  # %85 egitim, %15 dogrulama
YAML_PATH = DATASET_DIR / "dataset.yaml"


def find_labeled_pairs():
    """Gorsel + etiket eslesmelerini bul."""
    pairs = []

    # Etiket dosyalarini tara
    label_files = list(LABELS_DIR.glob("*.txt"))
    if not label_files:
        # Belki train/val altina konmus
        label_files = list((LABELS_DIR / "train").glob("*.txt"))
        label_files += list((LABELS_DIR / "val").glob("*.txt"))

    for label_path in label_files:
        stem = label_path.stem
        # Gorseli bul
        for ext in [".jpg", ".jpeg", ".png"]:
            img_path = FRAMES_DIR / (stem + ext)
            if img_path.exists():
                pairs.append((img_path, label_path))
                break

    return pairs


def setup_dataset():
    """YOLO veri seti yapisini olustur."""
    # Klasorleri olustur
    for split in ["train", "val"]:
        (DATASET_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
        (DATASET_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Eslesmis veri ciflerini bul
    pairs = find_labeled_pairs()
    if not pairs:
        print("[UYARI] Eslestirilmis gorsel+etiket cifti bulunamadi!")
        print()
        print("Lutfen su adimlari takip edin:")
        print("  1. data/extracted_frames/ icindeki gorselleri etiketleyin")
        print("  2. YOLO formatinda .txt dosyalarini data/dataset/labels/ altina koyun")
        print("  3. Her .txt dosyasi, ayni isimdeki .jpg ile eslesmeli")
        print()
        print("Ornek YOLO etiket formati (plate sinifi = 0):")
        print("  0 0.45 0.72 0.15 0.04")
        print("  ^  ^    ^    ^    ^")
        print("  |  |    |    |    +-- yukseklik (normalize)")
        print("  |  |    |    +------- genislik (normalize)")
        print("  |  |    +------------ merkez y (normalize)")
        print("  |  +----------------- merkez x (normalize)")
        print("  +-------------------- sinif ID (0 = plate)")
        return

    print(f"Toplam {len(pairs)} etiketli gorsel bulundu.")

    # Karistir ve bol
    random.shuffle(pairs)
    split_idx = int(len(pairs) * TRAIN_RATIO)
    train_pairs = pairs[:split_idx]
    val_pairs = pairs[split_idx:]

    print(f"  Egitim: {len(train_pairs)} gorsel")
    print(f"  Dogrulama: {len(val_pairs)} gorsel")

    # Dosyalari kopyala
    for split_name, split_pairs in [("train", train_pairs), ("val", val_pairs)]:
        for img_path, lbl_path in split_pairs:
            shutil.copy2(img_path, DATASET_DIR / "images" / split_name / img_path.name)
            shutil.copy2(lbl_path, DATASET_DIR / "labels" / split_name / lbl_path.name)

    # dataset.yaml olustur
    yaml_content = {
        "path": str(DATASET_DIR.absolute()),
        "train": "images/train",
        "val": "images/val",
        "nc": 1,  # Sinif sayisi
        "names": ["plate"],  # Sinif isimleri
    }

    with open(YAML_PATH, "w", encoding="utf-8") as f:
        yaml.dump(yaml_content, f, default_flow_style=False, allow_unicode=True)

    print(f"\ndataset.yaml olusturuldu: {YAML_PATH}")
    print()
    print("SONRAKI ADIM:")
    print("  python train_yolo.py")


if __name__ == "__main__":
    setup_dataset()
