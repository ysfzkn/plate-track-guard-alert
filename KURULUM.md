# GateGuard — Saha Kurulum Rehberi

> **Güvenlik PC'sine kurulum için adım adım Türkçe rehber.**
> Kurulum süresi: ~30-45 dakika

---

## İçindekiler

1. [Hazırlık — Kuruluma Götüreceklerin](#1-hazırlık--kuruluma-götüreceklerin)
2. [Hedef PC Gereksinimleri](#2-hedef-pc-gereksinimleri)
3. [Ağ Bilgilerini Topla](#3-ağ-bilgilerini-topla)
4. [Dosyaları Kopyala ve Yapılandır](#4-dosyaları-kopyala-ve-yapılandır)
5. [Kamera Bağlantısını Test Et](#5-kamera-bağlantısını-test-et)
6. [Sistemi İlk Kez Çalıştır](#6-sistemi-ilk-kez-çalıştır)
7. [Otomatik Başlatma Kurulumu](#7-otomatik-başlatma-kurulumu)
8. [Saha Kontrol Listesi](#8-saha-kontrol-listesi)
9. [Yaygın Sorunlar ve Çözümler](#9-yaygın-sorunlar-ve-çözümler)

---

## 1. Hazırlık — Kuruluma Götüreceklerin

### USB Bellekte Olsun

- `dist/GateGuard/` klasörünün tamamı (exe dağıtımı, ~5 GB)
- **VLC Media Player** kurulum dosyası ([videolan.org/vlc](https://www.videolan.org/vlc/)) — kamera testi için
- **Microsoft Access Database Engine 2016 Redistributable** ([indirme linki](https://www.microsoft.com/en-us/download/details.aspx?id=54920)) — MDB okumak için
- Bu rehberin bir kopyası (`KURULUM.md`)

### Fiziksel Donanım

- ESP32 modülü (alarm için, opsiyonel)
- USB-C veya Micro-USB kablo (ESP32 flashı için)
- Yedek ethernet kablosu (ağ sorunları için)

### Bilgi Olarak Yanında Olsun

- Kamera IP adresi, kullanıcı adı, şifresi
- ESP32 IP adresi (WiFi'a bağlandıktan sonra router'dan öğrenilir)
- Moonwell MW-305 yazılımının kurulu olduğu MDB dosyasının yolu
  (genellikle `C:\MoonWell MW-305 V2.11\MW305_DB001.mdb`)
- Router WiFi SSID + şifresi (ESP32 için)

---

## 2. Hedef PC Gereksinimleri

- Windows 10 veya 11 (64-bit)
- En az 4 GB RAM, 10 GB boş disk alanı
- Ethernet ile yerel ağa bağlı (kamera, ESP32, router ile aynı ağ)
- Moonwell MW-305 yazılımı **kurulu** (MDB dosyası burada)

---

## 3. Ağ Bilgilerini Topla

Kuruluma başlamadan önce aşağıdaki bilgileri **kağıda yaz**:

| Bilgi | Değer |
|---|---|
| Kameranın IP adresi | `192.168.___.___` |
| Kamera kullanıcı adı | |
| Kamera şifresi | |
| Kameranın RTSP portu | `554` (standart) |
| ESP32 IP adresi | `192.168.___.___` |
| Moonwell MDB dosya yolu | |

### IP Adreslerini Nasıl Bulursun?

**Kameranın IP adresi:**
- Dahua/Hikvision kameralar genellikle router'ın DHCP listesinde görünür
- Kamera yazılımı kurulu ise ayarlardan bulabilirsin
- Ağ tarayıcısı: `arp -a` komutu (CMD'de) → IP listesi verir

**ESP32 IP adresi:**
- ESP32 açıldığında USB Serial'den IP yazdırır (Arduino IDE Serial Monitor)
- Router'ın bağlı cihazlar listesinden bulunabilir

---

## 4. Dosyaları Kopyala ve Yapılandır

### Adım 4.1 — Dosyaları Kopyala

USB bellekteki `GateGuard` klasörünü hedef PC'de şu yola kopyala:

```
C:\GateGuard\
```

### Adım 4.2 — Microsoft Access Database Engine Kur

USB'deki `AccessDatabaseEngine.exe` dosyasına çift tıkla, kur.

> **ÖNEMLİ:** Python 64-bit kullanıyorsak, Access Engine da 64-bit olmalı.
> Eğer Office 32-bit kuruluysa, Python 32-bit gerekebilir.
> Çelişki çıkarsa: kurulumda `/passive` parametresiyle aç.

### Adım 4.3 — Moonwell MDB Dosyasını Kopyala

Moonwell yazılımının veritabanını bul ve kopyala:

```
Kaynak: C:\MoonWell MW-305 V2.11\MW305_DB001.mdb
Hedef: C:\GateGuard\moonwel_db\MW305_DB001.mdb
```

> MDB'yi Moonwell yazılımı çalışırken kopyalayabilirsin — GateGuard sadece okuyacak, yazmayacak.

### Adım 4.4 — .env Dosyasını Düzenle

`C:\GateGuard\.env` dosyasını **Notepad** ile aç (sağ tık → Birlikte aç → Notepad).

Şu satırları kendi bilgilerinle güncelle:

```ini
# ─── Kamera ─────────────────────────────────
# Dahua için format:
RTSP_URL=rtsp://admin:KAMERASIFRE@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0

# Hikvision için format:
# RTSP_URL=rtsp://admin:KAMERASIFRE@192.168.1.100:554/Streaming/Channels/101

# ─── ESP32 Alarm ────────────────────────────
ESP32_IP=192.168.1.50

# ─── Veritabanı ─────────────────────────────
MDB_PATH=moonwel_db/MW305_DB001.mdb

# ─── Çalışma Modu ───────────────────────────
# ÖNEMLİ: Kurulumda CANLI moda geç (false)
MOCK_MODE=false

# ─── Diğer ──────────────────────────────────
PROCESS_FPS=2
ALARM_COOLDOWN_SEC=60
CAMERA_ENTRY_DIRECTION=down
LPR_ENGINE=fast_alpr
```

**Dosyayı kaydet** ve kapat.

---

## 5. Kamera Bağlantısını Test Et

**SİSTEMİ BAŞLATMADAN ÖNCE** kamerayı mutlaka doğrula. Üç farklı yöntem var:

### Yöntem A: Ping ile Ağ Kontrolü (30 saniye)

CMD'yi aç (Win+R → `cmd` → Enter):

```cmd
ping 192.168.1.100
```

(IP'yi kendi kamera IP'nle değiştir)

**Beklenen sonuç:**
```
Reply from 192.168.1.100: bytes=32 time=1ms TTL=64
Reply from 192.168.1.100: bytes=32 time=1ms TTL=64
```

**"Request timed out" hatası alırsan:** Kamera ağa bağlı değil veya IP yanlış. Devam etme — önce ağ bağlantısını düzelt.

### Yöntem B: VLC ile RTSP Testi (2 dakika)

1. VLC Media Player'ı aç
2. **Ortam → Ağ Yayını Aç** (veya `Ctrl+N`)
3. Şu URL'yi yapıştır:
   ```
   rtsp://admin:KAMERASIFRE@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0
   ```
4. **Oynat**'a bas

**Beklenen sonuç:** Kameradan canlı görüntü gelir.

**Görüntü gelmiyorsa:**
- RTSP URL'si yanlış → kamera markasına göre formatı kontrol et (Dahua/Hikvision farklı)
- Şifre yanlış → kamera web arayüzüne girip şifreyi onayla
- Windows Güvenlik Duvarı 554 portunu engelliyor olabilir → geçici olarak kapat test için

### Yöntem C: Kamera Test UI (en güvenilir)

Sistem çalıştıktan sonra kullanılır. Detay için [Adım 6](#6-sistemi-ilk-kez-çalıştır)'ya bak.

---

## 6. Sistemi İlk Kez Çalıştır

### Adım 6.1 — Başlat

`C:\GateGuard\GateGuard.exe` dosyasına **çift tıkla**.

- Bir konsol penceresi açılır (**KAPATMA!**)
- 2-3 saniye sonra tarayıcı otomatik açılır
- `http://localhost:8000` adresinde arayüz görünür

### Adım 6.2 — İlk Yükleme

İlk çalıştırmada arka planda şunlar olur (ilk kez ~30-60 sn sürebilir):

1. SQLite veritabanı oluşturulur
2. Moonwell MDB'den plakalar senkronize edilir (~332 plaka)
3. ALPR AI modelleri indirilir ve yüklenir (**İLK SEFER İÇİN İNTERNET GEREKLİ**)
4. Kameraya bağlanılır
5. Sistem hazır → "Canlı" sekmesi açılır

### Adım 6.3 — Kamera Test Sayfasını Aç

Tarayıcıda şu butona bas:

```
Sağ üstteki 📹 ikonu    VEYA    doğrudan http://localhost:8000/camera-test
```

**Bu sayfada göreceklerin:**

| Gösterge | Anlamı |
|---|---|
| 🟢 Yeşil + "Kamera bağlı ve görüntü alınıyor" | ✅ Her şey yolunda |
| 🟡 Sarı + "Bağlantı var ama görüntü gelmiyor" | ⚠ Kamera bağlanıyor, 10-30 sn bekle |
| 🔴 Kırmızı + "Bağlantı kurulamadı" | ❌ .env veya ağ sorunu |
| 🔵 Mavi + "Mock modda" | ⚠ .env'de `MOCK_MODE=true` kalmış, `false` yap |

**Alt kısımda canlı görüntü:** Kamera doğru ayarlanmışsa saniyede 1 kez yenilenen kamera görüntüsü belirir.

### Adım 6.4 — ALPR Test Sayfası (isteğe bağlı)

Plaka okuma doğruluğunu test etmek için:

```
Sağ üstteki 🔬 ikonu    VEYA    http://localhost:8000/alpr-test
```

Buraya örnek plaka fotoğrafları yükleyip modelin doğru okuyup okumadığını görebilirsin.

---

## 7. Otomatik Başlatma Kurulumu

PC her açıldığında GateGuard otomatik başlasın:

### Yöntem 1 — Startup Klasörü (Basit)

1. `Win + R` tuşları → `shell:startup` yaz → **Enter**
2. Açılan klasörde boş yere sağ tık → **Yeni → Kısayol**
3. Hedef olarak şunu gir:
   ```
   C:\GateGuard\GateGuard.exe
   ```
4. İsim: `GateGuard`
5. **Son**

Artık PC her açıldığında sistem otomatik başlar.

### Yöntem 2 — Windows Görev Zamanlayıcı (Daha Sağlam)

CMD'yi **Yönetici olarak** aç:

```cmd
schtasks /create /tn "GateGuard" /tr "C:\GateGuard\GateGuard.exe" /sc ONSTART /ru SYSTEM /f
```

Bu yöntem:
- PC açılırken (kullanıcı giriş yapmadan bile) başlar
- SYSTEM hesabıyla çalışır, daha güvenlidir
- Crash olursa Windows otomatik restart'lar

### Masaüstü Kısayolu

Güvenlik görevlisi manuel başlatabilsin diye masaüstüne kısayol:

1. `C:\GateGuard\GateGuard.exe` → sağ tık → **Kısayol oluştur**
2. Kısayolu masaüstüne taşı
3. İsmini `GateGuard` yap

---

## 8. Saha Kontrol Listesi

Kurulum tamamlandıktan sonra şunları sırayla doğrula:

- [ ] Ping ile kamera IP'sine erişilebiliyor
- [ ] VLC ile RTSP URL'den görüntü alınabiliyor
- [ ] Microsoft Access Database Engine kurulu
- [ ] `.env` dosyasında `MOCK_MODE=false`
- [ ] `.env` dosyasında doğru RTSP URL ve ESP32 IP
- [ ] `moonwel_db/` klasöründe MDB dosyası mevcut
- [ ] `GateGuard.exe` çalıştırılabiliyor, konsol açılıyor
- [ ] Tarayıcı otomatik `localhost:8000` açıyor
- [ ] **Kamera test sayfasında yeşil ışık** 🟢
- [ ] Canlı kamera görüntüsü geliyor
- [ ] Ana panelde "Son Senkronizasyon" zamanı güncel (bugün)
- [ ] Sağ panelde "Plaka Sayısı" 300+ (Moonwell'den gelen)
- [ ] Bir aracın plakasını manuel test et: `/alpr-test` sayfasına fotoğrafını yükle
- [ ] ESP32 kuruluysa: `http://192.168.1.50/status` tarayıcıda açılıyor
- [ ] Windows Startup'ta kayıtlı (Yöntem 1 veya 2)

---

## 9. Yaygın Sorunlar ve Çözümler

### A) Kamera görüntüsü gelmiyor

**Kontrol et:**

```cmd
ping KAMERA_IP
```

Ping başarılı ama görüntü yoksa RTSP URL'si yanlış.

**Dahua kameralar** için olası URL formatları:
```
rtsp://admin:SIFRE@IP:554/cam/realmonitor?channel=1&subtype=0   ← Ana stream
rtsp://admin:SIFRE@IP:554/cam/realmonitor?channel=1&subtype=1   ← Düşük kalite (önerilir, daha az CPU)
```

**Hikvision kameralar:**
```
rtsp://admin:SIFRE@IP:554/Streaming/Channels/101   ← Ana stream
rtsp://admin:SIFRE@IP:554/Streaming/Channels/102   ← Düşük kalite
```

### B) MDB senkronizasyonu başarısız

**Hata mesajı:** `MDB file not found` → `.env`'deki `MDB_PATH` yanlış.

**Hata mesajı:** `Driver not found` → Microsoft Access Database Engine kurulu değil VEYA mimari uyumsuz (32-bit/64-bit).

Düzeltme:
```cmd
REM Python mimarisini kontrol et
C:\GateGuard\_internal\python.exe -c "import struct; print(struct.calcsize('P')*8)"
```

Çıktı `64` ise: 64-bit Access Database Engine kur.
Çıktı `32` ise: 32-bit kur.

### C) Port 8000 başka uygulamada

**Hata:** `[Errno 10048] Only one usage of each socket address is normally permitted`

Çözüm:
```cmd
netstat -ano | findstr :8000
```

Çıkan PID'yi Task Manager'dan kapat VEYA `.env`'e farklı port ekle.

### D) ESP32 yanıt vermiyor

1. ESP32'nin WiFi'a bağlandığını doğrula (ESP32 LED'i)
2. Tarayıcıda test: `http://192.168.1.50/status` → JSON dönmeli
3. Ping: `ping 192.168.1.50`
4. **Yazılımsal çalışır**: ESP32 olmadan da sistem devam eder — sadece fiziksel siren çalmaz, ekranda alarm yine görünür.

### E) Sistem yavaş, yüksek CPU

- `.env`'de `PROCESS_FPS=1` yap (saniyede 1 kare yeterli)
- Kameradan düşük çözünürlüklü stream al (subtype=1)
- Antivirus GateGuard klasörünü tarıyor olabilir → istisna ekle

### F) Loglar nerede?

```
C:\GateGuard\logs\app.log        — Genel uygulama logu
C:\GateGuard\logs\passages.log   — Tüm geçiş kayıtları
C:\GateGuard\logs\alarms.log     — Alarm olayları
C:\GateGuard\logs\sync.log       — MDB senkronizasyon logu
```

Sorun olursa **önce `app.log`'un son 50 satırına bak**.

### G) Exe hızlıca kapanıyor

Exe'yi CMD'den çalıştır ki hatayı görebilesin:

```cmd
cd C:\GateGuard
GateGuard.exe
```

Hata mesajını oku, çözüm bölümüne bak.

---

## Hızlı Komut Referansı (CMD)

```cmd
REM Ağda kamera tara
arp -a | findstr "192.168"

REM Kamera IP'sine ping at
ping 192.168.1.100

REM 554 portu açık mı? (test için)
powershell "Test-NetConnection -ComputerName 192.168.1.100 -Port 554"

REM ESP32 testi (PowerShell)
powershell "Invoke-WebRequest http://192.168.1.50/status"

REM GateGuard servisi başlat / durdur
schtasks /run /tn "GateGuard"
schtasks /end /tn "GateGuard"

REM GateGuard loglarını izle
cd C:\GateGuard\logs
type app.log
```

---

## Tarayıcı Hızlı Referans

Kurulumdan sonra sıkça kullanılacak sayfalar:

| URL | Ne İşe Yarar |
|---|---|
| `http://localhost:8000` | Ana panel (canlı izleme, geçmiş, galeri) |
| `http://localhost:8000/camera-test` | Kamera bağlantı testi + canlı görüntü |
| `http://localhost:8000/alpr-test` | Fotoğrafla plaka okuma testi |
| `http://localhost:8000/api/camera/test` | Kamera durumu (JSON) |
| `http://localhost:8000/api/stats` | Günlük istatistikler (JSON) |
| `http://localhost:8000/api/status` | Sistem durumu (JSON) |

---

## Acil Durum Numaraları

Sahada sorun çıkarsa:

1. **Logları oku:** `C:\GateGuard\logs\app.log` (son 50 satır)
2. **Kamera test sayfası:** `http://localhost:8000/camera-test`
3. **Mock moda geç:** `.env`'de `MOCK_MODE=true` → kamera olmadan sistemin çalıştığını doğrula, sonra tekrar `false`
4. **Yeniden başlat:** Konsol penceresini kapat → `GateGuard.exe`'ye tekrar çift tıkla

---

**İyi kurulumlar! 🚀**
