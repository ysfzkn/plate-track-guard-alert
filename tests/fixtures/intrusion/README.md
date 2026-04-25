# Hırsızlık Tespit Benchmark Veri Seti

Bu klasör `scripts/benchmark_intrusion.py`'ın çalıştırılacağı test görselleri içindir.

## Klasör Yapısı

```
tests/fixtures/intrusion/
├── positive/         ← Kişi VAR olan fotoğraflar (alarm beklenir)
├── negative/         ← Kişi YOK olan fotoğraflar (alarm beklenmez)
└── edge_cases/       ← Kedi/köpek, heykel, gölge gibi zor durumlar
```

## Nereden Test Verisi Bulabilirsin?

### 1. Ücretsiz CCTV / Gözetim Fotoğrafları
- **Unsplash** — `unsplash.com/s/photos/cctv-camera`
- **Pexels** — `pexels.com/search/surveillance/`
- **Pixabay** — `pixabay.com/images/search/security-camera/`

### 2. Kamera Test Veri Setleri
- **UCF Crime Dataset** — `webpages.uncc.edu/cchen62/dataset.html` (gerçek hırsızlık CCTV)
- **VIRAT Ground Dataset** — `viratdata.org` (açık kaynak gözetleme)
- **Roboflow "security camera" arama** — `universe.roboflow.com`

### 3. Kendi Fotoğrafların
En gerçekçi testler: kendi evinde/işinde benzer ışık koşullarında çekilmiş fotoğraflar.
- Gece karanlık odada telefonunla çekilmiş — kişi olan + olmayan kareler
- Sokak ışığı altında dış çekim
- Farklı açılardan (kamera yükseklığı simüle etmek için yüksekten çek)

### 4. Video Frame'i Çıkarma
Eğer elinizde video varsa, frame'lere çıkarmak için:

```powershell
# 1 saniyede bir frame çıkar
ffmpeg -i input.mp4 -vf "fps=1" tests/fixtures/intrusion/positive/frame_%04d.jpg
```

## Etiketleme

İki yöntemden birini kullanabilirsiniz:

### Yöntem 1 — Klasöre Göre
Fotoğrafı uygun klasöre koy, script otomatik etiketler:
- `positive/` klasöründeki tüm fotoğraflar "kişi var" kabul edilir
- `negative/` klasöründekiler "kişi yok" kabul edilir

### Yöntem 2 — Dosya Adı Öneki
Klasörden bağımsız, dosya adı başına önek koy:
- `person_*.jpg` — kişi var beklenir
- `empty_*.jpg` — kişi yok beklenir
- `animal_*.jpg`, `cat_*.jpg`, `dog_*.jpg` — kişi yok beklenir (sadece hayvan)

## Hedef Veri Seti (Minimum)

İyi bir benchmark için:
- **Positive**: 20-30 fotoğraf (farklı açı, ışık, poz)
- **Negative**: 20-30 fotoğraf (boş alanlar, gece/gündüz)
- **Edge cases**: 10-15 (kedi, köpek, insan heykeli, güçlü gölge, ayna yansıması)

Toplam 50-75 fotoğrafla güvenilir bir confusion matrix çıkar.

## Çalıştırma

```powershell
# Varsayılan: bu klasörü işler
python scripts/benchmark_intrusion.py

# Özel klasör
python scripts/benchmark_intrusion.py --folder C:\path\to\photos

# Confidence threshold'u değiştir
python scripts/benchmark_intrusion.py --conf 0.6

# Markdown rapor üret
python scripts/benchmark_intrusion.py --report rapor.md

# Hatalı sınıflandırılan dosyaların hepsini göster
python scripts/benchmark_intrusion.py --show-misses
```

## Örnek Çıktı

```
====================================================================
  Confusion Matrix:

                       ACTUAL person │ ACTUAL empty
                      ───────────────┼──────────────
    PREDICTED person  │    TP =   18   │    FP =    1
    PREDICTED empty   │    FN =    2   │    TN =   19

  Accuracy                                95.0%   (correctly classified)
  Recall (TP rate)                        90.0%   (person found when person present)
  Precision                               94.7%   (correct when system fires)
  Specificity (TN rate)                   95.0%   (clean photo = no alarm)
  F1 score                                0.923

  Targets (from plan):
    [✓] Recall ≥ 90%       : 90.0%
    [✓] Specificity ≥ 95%  : 95.0%
```
