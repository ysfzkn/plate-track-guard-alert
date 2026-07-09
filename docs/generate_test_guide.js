// GateGuard — Saha Test Rehberi (Word doküman üreticisi)
// Çalıştırma: node generate_test_guide.js

const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
  BorderStyle, WidthType, ShadingType, PageNumber, TabStopType,
} = require('docx');

const ACCENT = "1F3864";
const ACCENT_LIGHT = "D9E2F3";
const GREY = "595959";
const GREEN = "548235";
const CODE_BG = "F2F2F2";

const border = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
const borders = { top: border, bottom: border, left: border, right: border };

const p = (text, opts = {}) => new Paragraph({
  spacing: { after: 100, line: 290 },
  alignment: opts.align ?? AlignmentType.JUSTIFIED,
  children: [new TextRun({ text, font: "Arial", size: 22, bold: opts.bold, color: opts.color, italics: opts.italics })],
});

const h1 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 280, after: 140 },
  pageBreakBefore: false,
  children: [new TextRun({ text, font: "Arial", size: 30, bold: true, color: ACCENT })],
});

const h2 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 220, after: 100 },
  children: [new TextRun({ text, font: "Arial", size: 24, bold: true, color: ACCENT })],
});

const h3 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_3,
  spacing: { before: 160, after: 80 },
  children: [new TextRun({ text, font: "Arial", size: 22, bold: true, color: GREY })],
});

const bullet = (text, level = 0) => new Paragraph({
  numbering: { reference: "bullets", level },
  spacing: { after: 60, line: 280 },
  children: [new TextRun({ text, font: "Arial", size: 22 })],
});

const bulletRich = (parts, level = 0) => new Paragraph({
  numbering: { reference: "bullets", level },
  spacing: { after: 60, line: 280 },
  children: parts.map(([text, opts = {}]) =>
    new TextRun({ text, font: opts.font || "Arial", size: 22, bold: opts.bold, color: opts.color, italics: opts.italics })),
});

const numbered = (text) => new Paragraph({
  numbering: { reference: "numbers", level: 0 },
  spacing: { after: 60, line: 280 },
  children: [new TextRun({ text, font: "Arial", size: 22 })],
});

const code = (text) => new Paragraph({
  spacing: { before: 80, after: 80 },
  shading: { fill: CODE_BG, type: ShadingType.CLEAR },
  border: {
    top:    { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
    left:   { style: BorderStyle.SINGLE, size: 12, color: ACCENT },
    right:  { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
  },
  children: [new TextRun({ text, font: "Consolas", size: 19 })],
});

const note = (label, body, color = ACCENT) => new Paragraph({
  spacing: { before: 120, after: 120 },
  shading: { fill: ACCENT_LIGHT, type: ShadingType.CLEAR },
  border: {
    left: { style: BorderStyle.SINGLE, size: 18, color },
    top: { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
    right: { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
  },
  children: [
    new TextRun({ text: label + " ", font: "Arial", size: 22, bold: true, color }),
    new TextRun({ text: body, font: "Arial", size: 22 }),
  ],
});

const cell = (text, opts = {}) => new TableCell({
  borders,
  width: { size: opts.width, type: WidthType.DXA },
  shading: opts.shade ? { fill: opts.shade, type: ShadingType.CLEAR } : undefined,
  margins: { top: 80, bottom: 80, left: 120, right: 120 },
  children: [new Paragraph({
    alignment: opts.align ?? AlignmentType.LEFT,
    children: [new TextRun({
      text, font: "Arial", size: 21,
      bold: opts.bold, color: opts.color,
    })],
  })],
});

const row = (cells) => new TableRow({ children: cells });

const children = [];

// ── KAPAK ────────────────────────────────────────────────────
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 60, before: 1200 },
  children: [new TextRun({ text: "GateGuard", font: "Arial", size: 56, bold: true, color: ACCENT })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 80 },
  children: [new TextRun({
    text: "Saha Test Rehberi",
    font: "Arial", size: 36, bold: true, color: GREY,
  })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 600 },
  children: [new TextRun({
    text: "Kamera Video Örnekleriyle Modül 1 ve Modül 2 Doğrulama",
    font: "Arial", size: 24, italics: true, color: GREY,
  })],
}));

const today = new Date();
const dateStr = `${String(today.getDate()).padStart(2, '0')}.${String(today.getMonth()+1).padStart(2, '0')}.${today.getFullYear()}`;
children.push(new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: [2800, 6226],
  rows: [
    ["Doküman", "Test Senaryoları ve Kabul Kriterleri"],
    ["Hedef Kitle", "Sistem yöneticisi / sahada test eden kullanıcı"],
    ["Test Yöntemi", "Gerçek IP kamera kayıtlarıyla offlıne doğrulama"],
    ["Modüller", "M1: Kaçak Araç Tespiti · M2: Kaçak İnsan Tespiti"],
    ["Tarih", dateStr],
  ].map(([k, v]) => row([
    cell(k, { width: 2800, bold: true, shade: ACCENT_LIGHT }),
    cell(v, { width: 6226 }),
  ])),
}));

// ── 1. GENEL BAKIŞ ───────────────────────────────────────────
children.push(h1("1. Genel Bakış ve Test Felsefesi"));
children.push(p("Bu rehber, GateGuard sisteminin sahada doğru çalıştığını kanıtlamak için yapılması gereken kontrollü testleri tarif eder. Stratejimiz \"laboratuvarda tahmin et, sahada doğrula\" değil; \"saha kayıtlarıyla yeniden oynat ve sayısal sonuç çıkar\"dır."));
children.push(p("Bu sayede:"));
children.push(bullet("Aynı video farklı parametrelerle defalarca işlenebilir — tek bir saha denemesinde mahsur kalmazsınız."));
children.push(bullet("Yapılan her ayar değişikliğinin etkisi sayısal olarak görülebilir (recall, precision, F1)."));
children.push(bullet("Hatalı tespitlerde tam olarak hangi karenin yanıltıcı olduğu işaretlenir, kalibrasyon kolaylaşır."));
children.push(bullet("Yönetim kuruluna sunulacak kanıt seti otomatik birikir."));

children.push(note("ÖNERİ:",
  "İlk hafta için en az 30 gerçek geçiş + 20 farklı yaya senaryosu kaydedin. " +
  "Bu set sisteme \"sahaya özgü doğru parametreleri\" öğretmenizin temelidir."));

// ── 2. EKIPMAN ───────────────────────────────────────────────
children.push(h1("2. Gerekli Ekipman ve Hazırlık"));

children.push(new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: [2800, 6226],
  rows: [
    ["Bileşen", "Açıklama"],
    ["Bilgisayar", "Windows + GateGuard kurulu (start.bat ile çalışıyor olmalı). 8 GB+ RAM önerilir."],
    ["ffmpeg", "Komut satırı video aracı — RTSP yakalama ve format çevirme için. Yol: C:\\ffmpeg\\bin\\ffmpeg.exe"],
    ["VLC (alternatif)", "ffmpeg yerine grafiksel arayüzle de RTSP kaydı alınabilir."],
    ["Kamera erişimi", "RTSP URL'si (kullanıcı adı + parola dahil). Örnek format: rtsp://admin:sifre@192.168.1.100:554/cam/realmonitor?channel=1"],
    ["Boş klasör", "C:\\GateGuard-Tests\\ — kayıtlar ve test sonuçları için"],
    ["Test bilgisayar", "Sunucu zaten çalışıyor olmalı. Tarayıcıdan http://localhost:8000 açılabilir olmalı."],
  ].map(([k, v], i) => row([
    cell(k, { width: 2800, bold: true, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
    cell(v, { width: 6226, bold: i === 0, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
  ])),
}));

children.push(p("ffmpeg kurulu olduğunu doğrulamak için PowerShell açıp şunu yazın:"));
children.push(code("ffmpeg -version"));
children.push(p("Çıktıda \"ffmpeg version 6.x ...\" görüyorsanız hazırsınız."));

// ── 3. KAMERADAN VIDEO KAYIT ─────────────────────────────────
children.push(h1("3. IP Kameradan Video Kaydetme"));
children.push(p("Sistemi gerçek koşullarda test etmek için sahaya benzeyen kayıtlar gerekir. İki yöntem tarif edilmiştir; ffmpeg yöntemi daha hızlı ve betikleştirilebilir, VLC yöntemi grafik arayüz ister."));

children.push(h2("3.1. Yöntem A — ffmpeg (önerilen)"));
children.push(p("PowerShell açın ve C:\\GateGuard-Tests\\ klasörüne girin:"));
children.push(code("cd C:\\GateGuard-Tests"));
children.push(p("Tek bir kameranın 60 saniyelik kaydını alma:"));
children.push(code(
  "ffmpeg -rtsp_transport tcp -i \"rtsp://admin:SIFRE@192.168.1.100:554/cam/realmonitor?channel=1\" ^\n" +
  "  -t 60 -c copy -y test_giris_01.mp4"
));
children.push(p("Açıklamalar:"));
children.push(bulletRich([["-rtsp_transport tcp ", { font: "Consolas", bold: true }], ["UDP yerine TCP kullan (paket kaybını engeller)"]]));
children.push(bulletRich([["-t 60 ", { font: "Consolas", bold: true }], ["60 saniye kaydet"]]));
children.push(bulletRich([["-c copy ", { font: "Consolas", bold: true }], ["kameradan gelen H.264 stream'i yeniden encode etmeden kopyala (CPU yormaz)"]]));
children.push(bulletRich([["-y ", { font: "Consolas", bold: true }], ["aynı isimde dosya varsa üstüne yaz"]]));

children.push(h3("Sürekli kayıt (timestamp ile)"));
children.push(p("Sahada bekleyip ilginç anlar yakalanacaksa segmentli sürekli kayıt:"));
children.push(code(
  "ffmpeg -rtsp_transport tcp -i \"rtsp://admin:SIFRE@192.168.1.100:554/cam/realmonitor?channel=1\" ^\n" +
  "  -c copy -f segment -segment_time 300 -reset_timestamps 1 ^\n" +
  "  -strftime 1 \"saha_%Y%m%d_%H%M%S.mp4\""
));
children.push(p("Bu komut her 5 dakikada bir yeni dosya yaratır (saha_20260425_143000.mp4 gibi). Ctrl+C ile durdurulur."));

children.push(h3("Kalite/boyut dengesi"));
children.push(p("Kayıtları paylaşmak veya küçültmek isterseniz bitrate düşürebilirsiniz:"));
children.push(code(
  "ffmpeg -i test_giris_01.mp4 -c:v libx264 -preset veryfast -crf 28 ^\n" +
  "  -an test_giris_01_small.mp4"
));
children.push(bulletRich([["crf 28 ", { font: "Consolas", bold: true }], ["yaklaşık 30-50% boyut, görüntü kalitesi yeterli"]]));
children.push(bulletRich([["-an ", { font: "Consolas", bold: true }], ["sesi at (zaten sessiz CCTV)"]]));

children.push(h2("3.2. Yöntem B — VLC ile Grafik Arayüzden"));
numberedSteps(children, [
  "VLC'yi açın → Medya menüsü → Ağ Akışı Aç…",
  "RTSP URL'sini yapıştırın: rtsp://admin:SIFRE@192.168.1.100:554/cam/realmonitor?channel=1",
  "\"Oynat\" butonunun yanındaki oka tıklayıp \"Dönüştür\"ü seçin.",
  "Profil: Video — H.264 + MP3 (MP4) — uygun.",
  "Hedef dosyaya bir isim verin (örn: test_giris_01.mp4) ve \"Başlat\"a basın.",
  "Yeterli süre kaydettikten sonra VLC'de oynatımı durdurun → kayıt tamamlanır.",
]);

children.push(h2("3.3. Hangi Kayıtları Almalısınız?"));
children.push(p("Test setiniz mümkün olduğunca çeşitli durumları kapsamalı. Aşağıdaki tabloyu boş bir Excel'e kopyalayıp her satır için kayıt aldığınızda işaretleyin:"));

children.push(h3("Modül 1 — Kaçak Araç Senaryoları"));
const m1Scenarios = [
  ["#", "Senaryo", "Beklenen", "Süre"],
  ["1", "Yetkili araç gündüz giriş", "Kayıt evet, alarm hayır, yön=giriş", "30s"],
  ["2", "Yetkili araç gündüz çıkış", "Kayıt evet, alarm hayır, yön=çıkış", "30s"],
  ["3", "Yetkili araç gece giriş (far ışığı var)", "Kayıt evet, alarm hayır", "30s"],
  ["4", "Yetkili araç plaka kirli/çamurlu", "Plaka okunmasa bile sistem çökmemeli", "30s"],
  ["5", "Yetkisiz araç (misafir/kurye) giriş", "Kayıt evet, ALARM evet, yön=giriş", "30s"],
  ["6", "Yetkisiz araç ardı sıra giren ikinci araç (tailgating)", "İkinci araç ALARM tetiklemeli", "60s"],
  ["7", "Aynı plaka kısa aralıkla 2 kez", "Tek geçiş kaydı (cooldown çalışmalı)", "60s"],
  ["8", "Plaka açısı eğik / 30°+", "Plaka okunmasa bile sistem çökmemeli", "30s"],
  ["9", "Motorsiklet (plaka küçük)", "Mümkünse okusun, okumazsa pas geçsin", "30s"],
  ["10", "Yağmur / sis", "Sistem stabil çalışmalı (FPS düşmesin)", "60s"],
];
children.push(makeTable(m1Scenarios, [600, 4500, 3326, 600]));

children.push(h3("Modül 2 — Kaçak İnsan Senaryoları"));
const m2Scenarios = [
  ["#", "Senaryo", "Beklenen", "Süre"],
  ["1", "Tek kişi tanımlı bölgeye girer (gece)", "ALARM, screenshot, klip kaydı", "60s"],
  ["2", "Tek kişi tanımlı bölgeden geçer (gündüz, gece-modu zone)", "Alarm yok (gündüz)", "60s"],
  ["3", "İki kişi aynı anda bölgeye girer", "ALARM (max 1 olay, cooldown)", "60s"],
  ["4", "Kedi / köpek bölgeye girer", "Alarm yok", "60s"],
  ["5", "Bir kişi bölgenin yanından geçer (girmeden)", "Alarm yok", "60s"],
  ["6", "Bir kişi bölgeye girer ve durmadan dolaşır (loitering)", "ALARM (~2 sn sonra)", "120s"],
  ["7", "Duvar üstünde tırmanan kişi (YOLO görmeyebilir)", "Hareket fallback ile ALARM", "60s"],
  ["8", "Rüzgarda sallanan dal / bayrak", "Alarm yok", "120s"],
  ["9", "Kuş / böcek kameraya yakın geçişi", "Alarm yok", "30s"],
  ["10", "Aynı kişi 30 sn arayla 2 kez girer", "İlk: ALARM. İkinci: alarm yok (cooldown)", "120s"],
];
children.push(makeTable(m2Scenarios, [600, 4500, 3326, 600]));

children.push(note("İPUCU:",
  "Senaryo 7 (duvar tırmanışı) en kritik. Mümkünse bir asistanla planlanmış bir tırmanış kaydedin. " +
  "Bu sahnenin kaydı, sistemin gerçek hayatta en çok değer ürettiği anı temsil eder."));

// ── 4. MODÜL 1 TESTİ ─────────────────────────────────────────
children.push(h1("4. Modül 1 — Kaçak Araç Tespit Testi"));

children.push(h2("4.1. Test Ekranını Açma"));
numberedSteps(children, [
  "GateGuard sunucunun çalıştığından emin olun (start.bat).",
  "Tarayıcıda http://localhost:8000/alpr-test adresini açın.",
  "\"Foto Testi\" sekmesi tek karelik test, \"Video Testi\" tüm video için kullanılır.",
]);

children.push(h2("4.2. Tek Bir Video İle Test"));
numberedSteps(children, [
  "Video Testi sekmesine geçin.",
  "Örnekleme hızını seçin: ilk testte \"Her 5. frame\" yeterli, hassas kalibrasyonda \"Her frame\".",
  "Güven eşiği sürgüsü %30'da bırakın (varsayılan).",
  "Kayıt aldığınız MP4'ü ekrana sürükleyip bırakın.",
  "İşlem tamamlandığında ekranda görünecek olanlar: işaretli video, kareye göre tespit istatistikleri, plaka listesi.",
  "Sonuçları gözden geçirin: her plaka tek satırda görünmeli; aynı araç birden fazla geçiş olarak görünüyorsa parametreler ayarlanmalı.",
]);

children.push(h2("4.3. Modül 1 Kabul Kriterleri"));
children.push(makeTable([
  ["Metrik", "Hedef", "Açıklama"],
  ["Plaka okuma doğruluğu", "≥ %85", "Doğru plakaların oranı (10/10 kayıt üzerinden)"],
  ["Yetkili / yetkisiz ayrımı", "≥ %95", "Beyaz listeyle eşleşme doğruluğu"],
  ["Yön tespiti (giriş/çıkış)", "≥ %85", "Kameranın açısına bağlı"],
  ["Aynı araç tek-geçiş", "%100", "Cooldown ile birden fazla kayıt olmamalı"],
  ["İşlem hızı (CPU)", "≥ 5 fps", "Mini PC'de tek kamera için"],
], [3000, 2000, 4026]));

children.push(h2("4.4. Hatalı Sonuçlarda Yapılacaklar"));
children.push(bulletRich([["Plaka okunmuyor: ", { bold: true }], ["Konfigürasyondan CONFIDENCE_THRESHOLD'u 0.45'ten 0.35'e düşürün ve tekrar deneyin. Hâlâ olmazsa kameranın çözünürlüğünü ve plaka açısını kontrol edin (60°+ açı zorlar)."]]));
children.push(bulletRich([["Aynı araç 2 kayıt: ", { bold: true }], ["FUZZY_TOLERANCE değerini 1'den 2'ye yükseltin (.env). MIN_FRAMES_FOR_COMMIT 2'de kalsın."]]));
children.push(bulletRich([["Yarım plaka kaydediliyor: ", { bold: true }], ["Validate kuralı zaten aktif. Yine olursa plaka regex'i kameranıza uygun mu kontrol edin (app/plate_detector.py)."]]));
children.push(bulletRich([["Yön ters tespit: ", { bold: true }], [".env'de CAMERA_ENTRY_SIZE_CHANGE değerini değiştirin (approach ↔ recede)."]]));

// ── 5. MODÜL 2 TESTİ ─────────────────────────────────────────
children.push(h1("5. Modül 2 — Kaçak İnsan Tespit Testi"));

children.push(h2("5.1. Tek Video Testi"));
numberedSteps(children, [
  "Tarayıcıda http://localhost:8000/intrusion-test açın.",
  "Video Testi sekmesine geçin.",
  "Örnekleme: \"Her 5. frame\" — tek kişi/iki kişi senaryolarında yeterli.",
  "Güven eşiği: %30 (varsayılan, sahada en iyi sonuç bu eşikte).",
  "MP4'ü sürükleyip bırakın.",
  "Sonuç ekranında: işaretli video, kişi sayısı zaman çizelgesi, ortalama güven oranı.",
]);

children.push(h2("5.2. Toplu Test (Klasör Bazlı Benchmark)"));
children.push(p("Birden fazla foto/video kaydını bir klasöre koyup tek seferde geçirebilirsiniz. Doğruluk, hassasiyet, F1 skoru otomatik hesaplanır."));
children.push(p("Klasör yapısını şu şekilde hazırlayın:"));
children.push(code(
  "C:\\GateGuard-Tests\\fotograflar\\\n" +
  "  positive\\          ← kişi VAR olan fotolar\n" +
  "  negative\\          ← kişi YOK olan fotolar\n" +
  "  edge_cases\\        ← kedi, köpek, manken vb."
));
children.push(p("Sonra PowerShell'de:"));
children.push(code(
  "cd C:\\Users\\asus\\Desktop\\code\\\"Out of work\"\\plate-track-guard-alert\n" +
  "python scripts/benchmark_intrusion.py --folder C:\\GateGuard-Tests\\fotograflar ^\n" +
  "  --report rapor.md --conf 0.30"
));
children.push(p("Çıktı: confusion matrix + accuracy/recall/precision/F1 değerleri. Markdown rapor (rapor.md) Word'e yapıştırılarak yönetime sunulabilir."));

children.push(h2("5.3. Modül 2 Kabul Kriterleri"));
children.push(makeTable([
  ["Metrik", "Hedef", "Önemi"],
  ["Recall (kişiyi yakalama)", "≥ %90", "Gerçek izinsiz girişin %90'ı yakalanmalı"],
  ["Specificity (boş alanda alarm yok)", "≥ %95", "Yanlış alarm en kritik şikayet kaynağı"],
  ["Hayvan elimine etme", "%100", "Hiçbir kedi/köpek alarm tetiklememeli"],
  ["F1 skoru", "≥ 0.90", "Genel başarı"],
  ["Hareket fallback (duvar üstü)", "≥ %70 recall", "YOLO'nun göremediği durumlarda"],
], [3500, 1800, 3726]));

children.push(h2("5.4. Hatalı Sonuçlarda Yapılacaklar"));
children.push(bulletRich([["Kişi tespit edilmiyor: ", { bold: true }], [".env'de INTRUSION_CONFIDENCE değerini 0.30'dan 0.20'ye düşürün. Hâlâ tespit olmazsa kameranın gece görüş kalitesi ve çözünürlüğünü kontrol edin."]]));
children.push(bulletRich([["Çok fazla yanlış alarm: ", { bold: true }], ["INTRUSION_MIN_CONSECUTIVE_FRAMES değerini 3'ten 4'e yükseltin. INTRUSION_MIN_LOITER_SEC=2 yapın."]]));
children.push(bulletRich([["Kedi/köpek alarm tetikliyor: ", { bold: true }], ["YOLO modeli detect_animals zaten çalışıyor. Beraber yapılacak şey: bölge sınırını duvar üstüne çekip yer seviyesinden uzak tutmak."]]));
children.push(bulletRich([["Hareket fallback yanlış alarm üretiyor: ", { bold: true }], ["Bu özelliği yalnızca duvar üstü gibi sınır bölgelerinde aktifleştirin. Avlu içi geniş alanlarda kapalı kalmalı."]]));

// ── 6. RAPORLAMA ─────────────────────────────────────────────
children.push(h1("6. Sonuçların Raporlanması"));
children.push(p("Test sonrasında üreteceğiniz çıktılar ve nereye kaydedileceği:"));
children.push(makeTable([
  ["Çıktı", "Otomatik Konum", "Ne İşe Yarar"],
  ["İşaretli video (M1/M2)", "static/test_outputs/", "Görsel kanıt — hangi karede neyin görüldüğü"],
  ["Confusion matrix CSV", "<klasör>/benchmark_results.csv", "Excel'e aktarıp grafiklendirme"],
  ["Markdown rapor", "Belirttiğiniz yol (örn. rapor.md)", "Yönetime sunum"],
  ["Olay screenshot'ları", "static/intrusion_clips/", "Resmi delil"],
  ["Olay video klipleri", "static/intrusion_clips/", "Resmi delil"],
  ["DB kayıtları", "data/gateguard.db", "Geçmişe dönük tüm olaylar"],
], [3000, 3500, 2526]));

children.push(p("Yönetim kuruluna sunulacak özet için en az şunları içeren bir tablo hazırlayın:"));
children.push(bullet("Toplam test edilen senaryo sayısı (M1 + M2)"));
children.push(bullet("Doğru tespit edilen senaryo sayısı"));
children.push(bullet("Yanlış alarm sayısı (en kritik metrik)"));
children.push(bullet("Kaçırılan olay sayısı"));
children.push(bullet("Belirlenen final parametre seti (kullanılacak .env değerleri)"));

// ── 7. HIZLI REFERANS ────────────────────────────────────────
children.push(h1("7. Hızlı Referans"));

children.push(h2("Kullanışlı Komutlar"));
children.push(code("# Kameradan 60 sn kayıt\nffmpeg -rtsp_transport tcp -i \"rtsp://...\" -t 60 -c copy kayit.mp4"));
children.push(code("# Foto klasörü benchmark\npython scripts\\benchmark_intrusion.py --folder C:\\test\\ --conf 0.30 --report rapor.md"));
children.push(code("# .env yeniden yükle (sunucuyu yeniden başlat)\nstart.bat"));
children.push(code("# Sunucu loglarını canlı izle\nGet-Content data\\logs\\app.log -Wait -Tail 50"));

children.push(h2("Önemli Dosya Yolları"));
children.push(makeTable([
  ["Amaç", "Yol"],
  [".env yapılandırması", ".env (proje kökü)"],
  ["Test ekranları (tarayıcı)", "/alpr-test , /intrusion-test , /night-watch"],
  ["Bölge editörü", "/zones (önce kamera kaydı yapın)"],
  ["Kamera yönetimi", "/cameras"],
  ["Test çıktıları", "static/test_outputs/"],
  ["Olay arşivi", "static/intrusion_clips/ ve screenshots/"],
  ["Veritabanı", "data/gateguard.db"],
  ["Log dosyaları", "data/logs/"],
], [3000, 6026]));

children.push(h2("İlk Hafta Önerilen İş Akışı"));
children.push(numbered("Gün 1: ffmpeg ile her saatten 5 dakikalık kayıt al (gündüz, akşam, gece)."));
children.push(numbered("Gün 2: Kayıtlardan plaka geçişlerini ayıkla, Modül 1 test ekranında geçir."));
children.push(numbered("Gün 3: .env'de plaka eşiklerini final ayarla; bir günlük canlı sınama."));
children.push(numbered("Gün 4: İnsan senaryolarını planla (asistanla 5–10 farklı durum), kaydet."));
children.push(numbered("Gün 5: Modül 2 benchmark çalıştır; bölge sınırlarını ayarla."));
children.push(numbered("Gün 6: Hareket fallback'i sadece duvar üstü bölgelerde aktifleştir, doğrulama testi yap."));
children.push(numbered("Gün 7: Final parametreleri .env'e yaz, raporu yönetim kuruluna sun."));

children.push(note("DESTEK:",
  "Sorun yaşadığınızda ekran görüntüsü + log dosyası ile birlikte iletişime geçin. " +
  "Logların son 100 satırı genelde yeterlidir."));

// ── helper for numbered steps with proper wrapping ──
function numberedSteps(arr, steps) {
  steps.forEach(s => arr.push(numbered(s)));
}

function makeTable(rows, widths) {
  return new Table({
    width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((r, i) => row(
      r.map((c, j) => cell(c, {
        width: widths[j],
        bold: i === 0,
        shade: i === 0 ? ACCENT_LIGHT : undefined,
        color: i === 0 ? ACCENT : undefined,
      }))
    )),
  });
}

// ── Belge ────────────────────────────────────────────────────
const doc = new Document({
  creator: "GateGuard Project",
  title: "GateGuard Saha Test Rehberi",
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 220, after: 100 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: "Arial", color: GREY },
        paragraph: { spacing: { before: 160, after: 80 }, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
      {
        reference: "numbers",
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({
            text: "GateGuard — Saha Test Rehberi",
            font: "Arial", size: 18, color: GREY, italics: true,
          })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          tabStops: [
            { type: TabStopType.CENTER, position: 4500 },
            { type: TabStopType.RIGHT, position: 9000 },
          ],
          children: [
            new TextRun({ text: dateStr, font: "Arial", size: 18, color: GREY }),
            new TextRun({ text: "\tGateGuard v1.0", font: "Arial", size: 18, color: GREY }),
            new TextRun({ text: "\tSayfa ", font: "Arial", size: 18, color: GREY }),
            new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: GREY }),
            new TextRun({ text: " / ", font: "Arial", size: 18, color: GREY }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], font: "Arial", size: 18, color: GREY }),
          ],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(__dirname + "/GateGuard-Saha-Test-Rehberi.docx", buf);
  console.log("OK -> GateGuard-Saha-Test-Rehberi.docx");
}).catch(e => { console.error(e); process.exit(1); });
