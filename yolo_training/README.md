# YOLOv8 Plaka Tespit Modeli Egitimi

GTX 1650 (4GB VRAM) icin optimize edilmis plaka tespit modeli egitim pipeline'i.

## Gereksinimler

```bash
pip install -r requirements.txt
```

CUDA ve cuDNN kurulu olmali (PyTorch GPU destegi icin).

## Adimlar

### 1. Video Kaydi Toplama
- Kameradan gunduz + gece video kayitlari alin
- Videolari `data/raw_videos/` klasorune koyun
- Minimum 10-15 dakika video onerilir

### 2. Frame Cikarma
```bash
python extract_frames.py
```
- Videolardan saniyede 2 frame cikarir
- Benzer frameleri otomatik atlar
- Cikti: `data/extracted_frames/`

### 3. Etiketleme (MANUEL)

**Bu adim elle yapilmalidir!** Her frame'deki plakalarin etrafina kutu cizmeniz gerekiyor.

#### Roboflow ile (Onerilen — En Kolay)
1. [roboflow.com](https://roboflow.com) adresine gidin, ucretsiz hesap acin
2. Yeni proje olusturun: "Object Detection" secin
3. `data/extracted_frames/` icindeki gorselleri yukleyin
4. Her gorselde plakalarin etrafina kutu cizin
5. Sinif adi: `plate`
6. Export: "YOLOv8" formatini secin
7. Indirilen dosyalari `data/dataset/` altina cikarin

#### LabelImg ile (Masaustu)
1. `pip install labelImg`
2. `labelImg data/extracted_frames/`
3. Format: YOLO secin
4. Her gorselde plakalarin etrafina kutu cizin
5. Kaydedilen `.txt` dosyalarini `data/dataset/labels/` altina tasyin

#### CVAT ile (Web Tabanli, Acik Kaynak)
1. [cvat.ai](https://cvat.ai) adresine gidin
2. Yeni gorev olusturun, gorselleri yukleyin
3. Etiketleyin, YOLO formatinda export edin

### 4. Veri Seti Hazirlama
Eger Roboflow kullanmadiysan (Roboflow bunu otomatik yapar):
```bash
python setup_dataset.py
```
Bu script train/val ayirimi yapar ve `dataset.yaml` olusturur.

### 5. Egitim
```bash
python train_yolo.py
```

Parametreler (GTX 1650 icin optimize):
| Parametre | Deger | Not |
|-----------|-------|-----|
| Model | yolov8n.pt | Nano — en hafif |
| Batch | 8 | OOM olursa 4 yap |
| Image Size | 640 | Standart |
| Epochs | 100 | Erken durdurma aktif |
| AMP | True | FP16 — VRAM tasarrufu |

**OOM hatasi alirsan:** `train_yolo.py` icindeki `BATCH_SIZE`'i 4'e dusur.

Egitim suresi: ~200-500 gorsel icin yaklasik 30-60 dakika.

### 6. Test
```bash
# Video ile test
python test_model.py --source test_video.mp4 --show

# Tek gorsel ile test
python test_model.py --source test_image.jpg --show

# Webcam ile canli test
python test_model.py --source 0 --show
```

## YOLO Etiket Formati

Her gorsel icin ayni isimde bir `.txt` dosyasi olmali.

Ornek: `frame_001.jpg` → `frame_001.txt`

```
0 0.4523 0.7234 0.1500 0.0400
```

Format: `<sinif_id> <merkez_x> <merkez_y> <genislik> <yukseklik>`
- Tum degerler 0-1 arasi normalize edilmis
- sinif_id = 0 (plate)

## Projeye Entegrasyon

Egitim tamamlandiktan sonra `best.pt` dosyasini ana projeye entegre edebilirsiniz.
`app/plate_detector.py` icinde `YOLOv8Detector` sinifini aktif edin.

## Dizin Yapisi

```
yolo_training/
├── data/
│   ├── raw_videos/          ← Videolarinizi buraya koyun
│   ├── extracted_frames/    ← Cikarilan frameler
│   └── dataset/
│       ├── images/
│       │   ├── train/
│       │   └── val/
│       ├── labels/
│       │   ├── train/
│       │   └── val/
│       └── dataset.yaml
├── runs/
│   └── detect/
│       └── plate_detector/
│           └── weights/
│               ├── best.pt  ← EN IYI MODEL
│               └── last.pt
├── extract_frames.py
├── setup_dataset.py
├── train_yolo.py
├── test_model.py
├── requirements.txt
└── README.md
```
