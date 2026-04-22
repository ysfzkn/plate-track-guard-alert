# GateGuard — Production Kurulum Rehberi

Guvenlik bilgisayarina adim adim kurulum.

---

## Gereksinimler (Hedef PC)

- Windows 10 veya 11 (64-bit)
- Ag baglantisi (kamera ve ESP32 ile ayni ag)
- Microsoft Access Database Engine (Moonwell MDB okumak icin)
  Indirme: https://www.microsoft.com/en-us/download/details.aspx?id=54920
- Chrome veya Edge tarayici
- IP kamera (RTSP destekli — Dahua, Hikvision vb.)
- ESP32 alarm modulu (opsiyonel — yazilim olmadan da calisir)

---

## Yontem 1: EXE ile Kurulum (Python Gerektirmez)

### Adim 1 — Dosyalari Kopyala

`dist/GateGuard/` klasorunu USB bellek veya ag ile hedef PC'ye kopyala.
Ornegin: `C:\GateGuard\` olarak kopyala.

Klasor yapisi:
```
C:\GateGuard\
  ├── GateGuard.exe        ← Cift tikla calistir
  ├── .env                 ← Ayarlar (duzenlenecek)
  ├── config.py
  ├── static\              ← Web arayuzu dosyalari
  ├── models\              ← YOLO model (opsiyonel)
  ├── app\                 ← Uygulama kodu
  ├── data\                ← SQLite DB (otomatik olusur)
  ├── logs\                ← Loglar (otomatik olusur)
  ├── moonwel_db\          ← MDB dosyasini buraya koy
  └── _internal\           ← Sistem dosyalari (dokunma)
```

### Adim 2 — Moonwell MDB Dosyasini Koy

Moonwell MW-305 yaziliminin veritabani dosyasini bul:
- Genellikle: `C:\MoonWell MW-305 V2.11\MW305_DB001.mdb`
- Bu dosyayi `C:\GateGuard\moonwel_db\` altina kopyala
  VEYA .env dosyasinda yolunu goster (Adim 3)

### Adim 3 — .env Dosyasini Duzenle

`C:\GateGuard\.env` dosyasini Notepad ile ac ve asagidakileri guncelle:

```
# Kamera RTSP adresi (kendi kameranizin IP ve sifresi)
RTSP_URL=rtsp://admin:SIFRE@192.168.1.100:554/Streaming/Channels/101

# ESP32 alarm modulu IP adresi
ESP32_IP=192.168.1.50

# Moonwell MDB dosya yolu
MDB_PATH=moonwel_db/MW305_DB001.mdb

# CANLI mod (false = gercek kamera kullanir)
MOCK_MODE=false
```

NOT: Kamera RTSP URL'sini bilmiyorsan, kamera web arayuzunden
veya NVR ayarlarindan bulabilirsin. Dahua varsayilan format:
  rtsp://admin:sifre@IP:554/cam/realmonitor?channel=1&subtype=0

### Adim 4 — Calistir

`GateGuard.exe` dosyasina cift tikla.

- Konsol penceresi acilir (kapatma!)
- 2-3 saniye sonra tarayici otomatik acilir
- http://localhost:8000 adresinde arayuz gorunur

### Adim 5 — Masaustu Kisayolu (Opsiyonel)

GateGuard.exe'ye sag tikla > Kisayol olustur > Masaustune tasi.

### Adim 6 — Windows ile Otomatik Baslat (Opsiyonel)

PC her acildiginda GateGuard otomatik baslasin:

1. Win+R bas, `shell:startup` yaz, Enter
2. Startup klasoru acilir
3. GateGuard.exe'nin kisayolunu bu klasore kopyala
4. Artik PC her acildiginda otomatik baslar

---

## Yontem 2: Python ile Kurulum (Gelistirici)

### Adim 1 — Python Kur

https://www.python.org/downloads/ adresinden Python 3.10+ indir ve kur.
Kurulumda "Add to PATH" secenegini isaretleyin.

### Adim 2 — Proje Dosyalarini Kopyala

Tum proje klasorunu hedef PC'ye kopyala.

### Adim 3 — Bagimliliklari Kur

```
cd C:\GateGuard
pip install uv
uv sync
```

### Adim 4 — .env Duzenle

Yontem 1 Adim 3 ile ayni.

### Adim 5 — Calistir

```
python main.py
```

veya

```
start.bat
```

---

## Ilk Calistirmada Ne Olur?

1. SQLite veritabani otomatik olusturulur (data/gateguard.db)
2. Moonwell MDB'den plakalar senkronize edilir (~330 plaka)
3. Kamera baglantisi kurulur
4. ALPR modeli yuklenir (ilk seferde ~30 saniye surebilir)
5. Tarayici acilir — sistem hazir

---

## Sorun Giderme

### "RTSP baglantisi basarisiz"
- Kamera IP adresini kontrol et (ping 192.168.1.100)
- Kamera sifresi dogru mu?
- Kamera ve PC ayni agda mi?
- 554 portunun acik oldugundan emin ol

### "MDB senkronizasyon hatasi"
- Microsoft Access Database Engine kurulu mu?
- MDB dosya yolu dogru mu? (.env icindeki MDB_PATH)
- Moonwell yazilimi MDB'yi kilitliyorsa 5 saniye aralikla 3 kez dener

### "ESP32 baglanamiyor"
- ESP32'nin WiFi'a bagli oldugundan emin ol
- IP adresini kontrol et: ping 192.168.1.50
- Tarayicida test et: http://192.168.1.50/status
- ESP32 olmadan da calisir — sadece fiziksel alarm calismaz,
  ekrandaki alarm gorunmeye devam eder

### Loglar nerede?
- logs/app.log — genel uygulama loglar
- logs/passages.log — tum gecis kayitlari
- logs/alarms.log — alarm kayitlari

### Port 8000 kullanimda
- Baska bir uygulama 8000 portunu kullaniyor olabilir
- O uygulamayi kapat veya GateGuard'i farkli portta calistir

---

## Kamera Pozisyonu Onerileri

- Kamera plaka hizasinda veya hafif yukarda olmali
- Plaka en az 100 piksel genislikte gorunmeli
- Gece icin IR aydinlatma veya ek isik kaynagi
- Lens temiz tutulmali (yagmur/toz)
- Arka isik (gunes, far) kamerayi kor etmemeli
