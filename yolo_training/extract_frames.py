"""
Frame Extractor — Guvenlik kamerasi videolarindan eğitim icin frame cikarir.

Kullanim:
  1. Videolarinizi data/raw_videos/ klasorune koyun (.mp4, .avi)
  2. Bu scripti calistirin: python extract_frames.py
  3. Cikarilan kareler data/extracted_frames/ klasorunde olacak

Saniyede 1-2 frame alir (fazla benzer kareleri atlar).
"""

import os
import sys
from pathlib import Path

import cv2

# --- Konfigürasyon ---
RAW_VIDEOS_DIR = Path("data/raw_videos")
OUTPUT_DIR = Path("data/extracted_frames")
FRAMES_PER_SECOND = 2      # Saniyede kac kare alinacak
MIN_FRAME_DIFF = 30.0       # Birbirine cok benzer kareleri atlamak icin esik degeri
SUPPORTED_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}


def calculate_frame_diff(prev_frame, curr_frame):
    """Iki frame arasindaki farki hesaplar (benzerlik kontrolu icin)."""
    if prev_frame is None:
        return float("inf")
    diff = cv2.absdiff(
        cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY),
    )
    return diff.mean()


def extract_frames_from_video(video_path: Path, output_dir: Path, video_index: int):
    """Tek bir videodan frame cikarir."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  [HATA] Video acilamadi: {video_path}")
        return 0

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0

    print(f"  Video: {video_path.name}")
    print(f"  FPS: {fps:.0f} | Toplam: {total_frames} frame | Sure: {duration:.0f}sn")

    # Her kac frame'de bir isleyecegiz
    frame_interval = max(1, int(fps / FRAMES_PER_SECOND))

    frame_count = 0
    saved_count = 0
    prev_frame = None
    video_name = video_path.stem  # Dosya adi (uzantisiz)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        # Sadece belirli araliklarla isle
        if frame_count % frame_interval != 0:
            continue

        # Benzerlik kontrolu — cok benzer kareleri atla
        diff = calculate_frame_diff(prev_frame, frame)
        if diff < MIN_FRAME_DIFF:
            continue

        prev_frame = frame.copy()

        # Kaydet
        filename = f"{video_name}_frame_{saved_count:04d}.jpg"
        filepath = output_dir / filename
        cv2.imwrite(str(filepath), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        saved_count += 1

    cap.release()
    print(f"  Kaydedilen: {saved_count} frame")
    return saved_count


def main():
    # Klasorleri kontrol et
    if not RAW_VIDEOS_DIR.exists():
        RAW_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[UYARI] '{RAW_VIDEOS_DIR}' klasoru olusturuldu.")
        print("  Lutfen videolarinizi bu klasore koyup scripti tekrar calistirin.")
        sys.exit(0)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Videolari bul
    videos = [
        f for f in RAW_VIDEOS_DIR.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not videos:
        print(f"[UYARI] '{RAW_VIDEOS_DIR}' klasorunde video bulunamadi!")
        print(f"  Desteklenen formatlar: {', '.join(SUPPORTED_EXTENSIONS)}")
        sys.exit(0)

    print("=" * 60)
    print(f"  FRAME CIKARMA ARACI")
    print(f"  {len(videos)} video bulundu")
    print(f"  Saniyede {FRAMES_PER_SECOND} frame alinacak")
    print("=" * 60)

    total_saved = 0
    for i, video in enumerate(sorted(videos)):
        print(f"\n[{i+1}/{len(videos)}]")
        count = extract_frames_from_video(video, OUTPUT_DIR, i)
        total_saved += count

    print("\n" + "=" * 60)
    print(f"  TAMAMLANDI!")
    print(f"  Toplam: {total_saved} frame kaydedildi")
    print(f"  Konum: {OUTPUT_DIR.absolute()}")
    print("=" * 60)
    print()
    print("SONRAKI ADIM:")
    print("  Bu frame'leri bir etiketleme aracina yukleyin:")
    print("  - Roboflow (https://roboflow.com) — En kolay, web tabanli")
    print("  - CVAT (https://cvat.ai) — Acik kaynak, guclü")
    print("  - LabelImg — Masaustu uygulama")
    print()
    print("  Her frame'deki plakalarin etrafina kutu cizin (bounding box).")
    print("  Sinif adi: 'plate'")
    print("  Export formati: YOLO format (.txt dosyalari)")
    print("  Ciktilari data/dataset/labels/ klasorune koyun.")


if __name__ == "__main__":
    main()
