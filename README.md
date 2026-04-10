# GateGuard - Kamera Tabanli Kacak Arac Tespit ve Alarm Sistemi

Site/apartman girislerinde kameradan plaka okuyarak yetkisiz arac gecislerini tespit eden ve fiziksel alarm tetikleyen Python tabanli guvenlik sistemi.

## Nasil Calisir?

```
Dahua IP Kamera (RTSP)
        |
        v
  FastAPI Backend
  (OpenCV + EasyOCR)
        |
        v
  SQLite Plaka Sorgu
  (Moonwell MDB'den senkronize)
        |
   Yetkisiz mi?
   /         \
 Evet        Hayir
  |            |
  v            v
ESP32 HTTP   Log kaydi
/alarm/on
  |
  v
Siren + Cakar
  |
  v
WebSocket --> Tarayici UI
(Kirmizi alarm ekrani)
```

## Ozellikler

- **Canli plaka tanima** - Dahua IP kameradan RTSP ile goruntuyu alir, EasyOCR ile plaka okur
- **Moonwell MDB senkronizasyon** - MW-305 eris kontrol sisteminin veritabanindan yetkili plakalari otomatik ceker
- **Fiziksel alarm** - Yetkisiz arac gecisinde ESP32 uzerinden siren ve cakar lambasi tetikler
- **Canli web arayuzu** - WebSocket ile anlik bildirimler, kirmizi alarm ekrani, tek tusla alarm susturme
- **Ekran goruntusu** - Kacak gecislerin fotografini otomatik kaydeder (filigran + plaka bilgisi)
- **Bulanik eslesme** - OCR hatalarini tolere eder (0/O, 1/I, 8/B benzerlikleri)
- **Mock modu** - Kamera/ESP32 olmadan gelistirme ve test yapabilme

## Hizli Kurulum

### Gereksinimler

- Windows 10/11
- Python 3.10+
- Microsoft Access Database Engine (32-bit veya 64-bit, Python mimarisine uygun)
- Dahua IP Kamera (RTSP destekli)
- ESP32 + Role modulu + Siren (opsiyonel, mock modda calisir)

### Kurulum

```bash
# 1. Repoyu klonla
git clone https://github.com/ysfzkn/plate-track-guard-alert.git
cd plate-track-guard-alert

# 2. Bagimlirliklari kur
pip install -r requirements.txt

# 3. Yapilandirma dosyasini duzenle
# .env dosyasini kendi ortamina gore guncelle
```

### .env Dosyasi

```env
# Kamera
RTSP_URL=rtsp://admin:sifre@192.168.1.100:554/Streaming/Channels/101

# ESP32 Alarm Modulu
ESP32_IP=192.168.1.50

# Veritabanlari
MDB_PATH=moonwel_db/MW305_DB200.mdb
SQLITE_PATH=data/gateguard.db

# Ekran goruntuleri
SCREENSHOT_DIR=static/screenshots

# Genel
MOCK_MODE=true          # true: test modu (kamera/ESP32 gerekmez)
PROCESS_FPS=2           # Saniyede kac kare islenir
CONFIDENCE_THRESHOLD=0.4
ALARM_COOLDOWN_SEC=60   # Ayni plaka icin tekrar alarm bekleme suresi (sn)
LOG_LEVEL=INFO
```

### Calistirma

```bash
# Test modunda (kamera/ESP32 gerekmez)
python main.py

# Tarayicida ac
# http://localhost:8000
```

## Proje Yapisi

```
plate-track-guard-alert/
├── main.py                    # FastAPI giris noktasi
├── config.py                  # Yapilandirma (.env'den yukler)
├── requirements.txt           # Python bagimliliklari
├── .env                       # Ortam degiskenleri (git'e eklenmez)
│
├── app/
│   ├── database.py            # SQLite + plaka normalizasyonu + bulanik eslesme
│   ├── mdb_sync.py            # Moonwell MDB → SQLite senkronizasyon
│   ├── camera.py              # RTSP kamera okuma + MockCamera
│   ├── plate_detector.py      # EasyOCR ALPR + MockPlateDetector
│   ├── alarm_manager.py       # ESP32 HTTP alarm yonetimi
│   ├── detection_engine.py    # Ana orkestrator (kamera→tespit→sorgu→alarm)
│   ├── screenshot.py          # Ekran goruntusu kaydetme + filigran
│   ├── websocket_manager.py   # WebSocket baglanti yonetimi
│   ├── routes.py              # API endpointleri
│   └── models.py              # Veri modelleri
│
├── static/
│   ├── index.html             # Web arayuzu (Tailwind CSS)
│   └── screenshots/           # Kacak gecis fotograflari (otomatik olusur)
│
├── esp32/
│   └── esp32_relay.ino        # ESP32 Arduino firmware
│
├── moonwel_db/                # Moonwell MDB dosyasi (git'e eklenmez)
├── data/                      # SQLite veritabani (otomatik olusur)
├── logs/                      # Log dosyalari (otomatik olusur)
│
└── yolo_training/             # YOLOv8 egitim pipeline'i (opsiyonel)
    ├── extract_frames.py      # Videodan kare cikarma
    ├── setup_dataset.py       # Veri seti hazirlama
    ├── train_yolo.py          # Model egitimi (GTX 1650 optimize)
    ├── test_model.py          # Model testi
    └── README.md              # Egitim kilavuzu
```

## API Endpointleri

| Yontem | Endpoint | Aciklama |
|--------|----------|----------|
| GET | `/` | Web arayuzu (index.html) |
| WS | `/ws` | WebSocket baglantisi (canli bildirimler) |
| POST | `/alarm/off` | Alarmi sustur |
| POST | `/api/sync` | MDB senkronizasyonunu tetikle |
| GET | `/api/passages` | Son gecisleri getir |
| GET | `/api/stats` | Bugunun istatistiklerini getir |
| GET | `/api/status` | Sistem durumunu getir |

## WebSocket Mesaj Tipleri

| Tip | Aciklama |
|-----|----------|
| `passage` | Yetkili arac gecisi |
| `alarm_on` | Kacak arac tespit edildi — alarm aktif |
| `alarm_off` | Alarm susturuldu |
| `sync_complete` | MDB senkronizasyonu tamamlandi |

## ESP32 Kurulumu

1. `esp32/esp32_relay.ino` dosyasini Arduino IDE ile acin
2. WiFi SSID ve sifrenizi girin
3. GPIO 4 pinini role modulune baglayin
4. Role cikisina siren ve/veya cakar lamba baglayin
5. ESP32'ye yukleyin

ESP32 endpointleri:
- `GET /alarm/on` — Siren ac
- `GET /alarm/off` — Siren kapat
- `GET /status` — Durum sorgula

## Plaka Normalizasyonu

Turkiye plaka formatlari otomatik olarak normalize edilir:

| Girdi | Normalize |
|-------|-----------|
| `34 TV 3409` | `34TV3409` |
| `34-LB-2317` | `34LB2317` |
| `34PB5705` | `34PB5705` |

Turk karakterleri: I→I, S→S, C→C, G→G, O→O, U→U

Bulanik eslesme: OCR hatalarina karsi 1 karakter tolerans (0↔O, 1↔I, 8↔B)

## Sorun Giderme

### Kamera baglanmiyor
- RTSP URL'sini kontrol edin (`rtsp://kullanici:sifre@ip:554/...`)
- Kamera ve bilgisayarin ayni agda oldugundan emin olun
- Guvenlik duvarinda 554 portunu acin

### MDB senkronizasyonu basarisiz
- Microsoft Access Database Engine kurulu mu kontrol edin
- MDB dosya yolunun dogru oldugunu kontrol edin
- Moonwell yazilimi MDB'yi kilitlediginde 5 saniye aralikla 3 kez dener

### ESP32'ye baglanamiyor
- ESP32'nin WiFi'a bagli oldugundan emin olun
- IP adresini kontrol edin (`/status` endpointini deneyin)
- Mock modda (`MOCK_MODE=true`) ESP32 olmadan calisir

### OCR dogrulugu dusuk
- Kamera pozisyonunu ayarlayin (plaka net gorunmeli)
- Gece icin IR aydinlatma ekleyin
- YOLOv8 ile ozel model egitmeyi deneyin (`yolo_training/` klasorune bakin)

## Lisans

MIT License
