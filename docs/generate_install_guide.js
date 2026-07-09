// GateGuard — Donanım Kurulum Kılavuzu (Word doküman üreticisi)
// Çalıştırma: node generate_install_guide.js
const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
  BorderStyle, WidthType, ShadingType, PageNumber, TabStopType,
} = require('docx');

const ACCENT = "1F3864";
const ACCENT_LIGHT = "D9E2F3";
const GREY = "595959";
const RED = "C00000";
const GREEN = "548235";
const ORANGE = "C65911";
const CODE_BG = "F2F2F2";
const WARN_BG = "FFF2CC";

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

const bullet = (text) => new Paragraph({
  numbering: { reference: "bullets", level: 0 },
  spacing: { after: 60, line: 280 },
  children: [new TextRun({ text, font: "Arial", size: 22 })],
});

const bulletRich = (parts) => new Paragraph({
  numbering: { reference: "bullets", level: 0 },
  spacing: { after: 60, line: 280 },
  children: parts.map(([text, opts = {}]) =>
    new TextRun({ text, font: opts.font || "Arial", size: 22, bold: opts.bold, color: opts.color, italics: opts.italics })),
});

const numbered = (text) => new Paragraph({
  numbering: { reference: "numbers", level: 0 },
  spacing: { after: 60, line: 280 },
  children: [new TextRun({ text, font: "Arial", size: 22 })],
});

const numberedRich = (parts) => new Paragraph({
  numbering: { reference: "numbers", level: 0 },
  spacing: { after: 60, line: 280 },
  children: parts.map(([text, opts = {}]) =>
    new TextRun({ text, font: opts.font || "Arial", size: 22, bold: opts.bold, color: opts.color, italics: opts.italics })),
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

const note = (label, body, color) => new Paragraph({
  spacing: { before: 140, after: 140 },
  shading: { fill: color === RED ? "FCE4E4" : color === ORANGE ? WARN_BG : ACCENT_LIGHT, type: ShadingType.CLEAR },
  border: {
    left:   { style: BorderStyle.SINGLE, size: 18, color: color || ACCENT },
    top:    { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
    right:  { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD" },
  },
  children: [
    new TextRun({ text: label + " ", font: "Arial", size: 22, bold: true, color: color || ACCENT }),
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

const children = [];

// ──────────── KAPAK ────────────
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 60, before: 1200 },
  children: [new TextRun({ text: "GateGuard", font: "Arial", size: 56, bold: true, color: ACCENT })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 80 },
  children: [new TextRun({
    text: "Donanım Kurulum Kılavuzu",
    font: "Arial", size: 36, bold: true, color: GREY,
  })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 600 },
  children: [new TextRun({
    text: "Adım Adım Bağlantı, Yazılım Kurulumu ve İlk Test",
    font: "Arial", size: 24, italics: true, color: GREY,
  })],
}));

const today = new Date();
const dateStr = `${String(today.getDate()).padStart(2,'0')}.${String(today.getMonth()+1).padStart(2,'0')}.${today.getFullYear()}`;
children.push(new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: [3000, 6026],
  rows: [
    ["Doküman", "Donanım + Yazılım Kurulum Kılavuzu"],
    ["Hedef Kitle", "Donanım deneyimi olmayan site yöneticisi"],
    ["Kapsam", "ESP32 + alarm modülleri kurulumu + GateGuard yazılım yapılandırması"],
    ["Tahmini Süre", "Donanım birleştirme: 1 saat · Yazılım kurulumu: 1-2 saat"],
    ["Tahmini Bütçe", "500-700 TL (Türkiye perakende)"],
    ["Tarih", dateStr],
  ].map(([k, v]) => row([
    cell(k, { width: 3000, bold: true, shade: ACCENT_LIGHT }),
    cell(v, { width: 6026 }),
  ])),
}));

// ──────────── BÖLÜM 1 ────────────
children.push(h1("1. Bu Doküman Kime?"));
children.push(p("Bu kılavuz, elektronik veya donanım deneyimi olmayan birinin GateGuard'ın alarm tarafını sıfırdan kurabilmesi için yazıldı. Lehim yok, programlama bilgisi gerekmiyor. Sadece bir tornavida, jumper kabloları ve birkaç vidayla tüm bağlantı yapılabilir."));
children.push(p("Sitenizde halihazırda şunlar var varsayılıyor:"));
children.push(bullet("Bir adet bilgisayar (PC veya mini PC), Windows kurulu."));
children.push(bullet("Bir veya birden fazla IP kamera (RTSP destekli, Dahua/Hikvision vb.)."));
children.push(bullet("Çalışan bir Wi-Fi ağı (router) — kameralar ve PC aynı ağda."));
children.push(p("Bu kılavuz yalnızca eksik olan kısmı — alarm sistemini — anlatır. Toplam ~500-700 TL'lik bir bütçe ve 2-3 saatlik bir mesai yeterlidir."));
children.push(note("ÖNEMLİ:",
  "220 volt elektrikle hiç temas etmeyeceksin. Tüm bağlantılar 12V veya 5V güvenli seviyede. Adaptörler 220V'yi düşürüp veriyor.", RED));

// ──────────── BÖLÜM 2 ────────────
children.push(h1("2. Sistem Nasıl Çalışıyor?"));
children.push(p("GateGuard üç ana parçadan oluşur:"));
children.push(bulletRich([
  ["Bilgisayar (PC): ", { bold: true }],
  ["Kameralardan görüntü alır, plakaları okur, izinsiz kişileri tespit eder. Bir alarm tetiklendiğinde Wi-Fi üzerinden komut gönderir."]
]));
children.push(bulletRich([
  ["ESP32 + Röle: ", { bold: true }],
  ["Güvenlik kulübesindeki minik kontrolcü. Wi-Fi'den komut alır, siren ve flaşörü tetikler."]
]));
children.push(bulletRich([
  ["Siren + Strobe (kırmızı flaşör): ", { bold: true }],
  ["Fiziksel uyarı verir. Yüksek sesli alarm + yanıp sönen ışık."]
]));
children.push(p("Olayların sırası: 1) Kamera bir izinsiz aracı veya kişiyi yakalar. 2) PC değerlendirir, alarm vermeye karar verir. 3) PC Wi-Fi üzerinden ESP32'ye 'alarm aç' komutu gönderir. 4) ESP32 röleyi tetikler, siren ve flaşör çalışır. 5) Operatör panelden 'Sustur' düğmesine basınca komut tersine işler."));

// ──────────── BÖLÜM 3 ────────────
children.push(h1("3. Alacağın 7 Modül"));
children.push(p("Aşağıdaki listeyi siparişte veya hırdavata giderken yanına al. Hiçbiri özel marka gerektirmez — \"X arıyorum\" diye sorsan çalışan dükkanlar yönlendirebilir."));

const moduleList = [
  { n: "1", name: "ESP32 DevKit Kartı", price: "150-220 TL",
    what: "Avuç içi büyüklüğünde, yeşil-mavi devre kartı. Üstünde minik Wi-Fi modülü ve USB girişi var.",
    job: "Bilgisayar gibi kendi başına program çalıştırır. Wi-Fi'ye bağlanır. PC'mizden \"alarm aç!\" komutu geldiğinde elektrik sinyali üretir.",
    looks: "Telefon SIM kartından biraz büyük, kredi kartının yarısı kadar. Üstünde 'ESP32-WROOM-32' yazar.",
    buy: "\"ESP32 DevKit V1 — 38 pin — USB\" yazan herhangi biri. Robotistan, Direnç, Mertcanat'ta var.",
    notes: "USB-Micro veya USB-C kablosu ürüne göre değişir; satın alırken sor (ayrı 20 TL).",
  },
  { n: "2", name: "1-Kanal 5V Röle Modülü", price: "25-45 TL",
    what: "Kibrit kutusu büyüklüğünde, üstünde mavi renkli bir 'ses çıkaran kutucuk' (röle) ve birkaç pin olan kart.",
    job: "Elektronik anahtar. ESP32'den gelen küçük sinyali, 12V'lik sireni açıp kapamak için kullanır. Çat sesi çıkarır.",
    looks: "Üzerinde '5V 1 Channel Relay Module' yazar. 3 vidalı terminal var: COM, NO, NC.",
    buy: "\"5V 1 kanal röle modülü Arduino uyumlu\". Opto-izolasyonlu olanı tercih et (Optocoupler yazar).",
    notes: "",
  },
  { n: "3", name: "12V DC Siren", price: "100-200 TL",
    what: "Klasik güvenlik sireni. Yumurta büyüklüğünde, plastik, 100-120 dB ses.",
    job: "Alarm anında yüksek sesli uyarı verir. Caydırıcıdır.",
    looks: "Bisiklet kornası boyutunda, plastik koruyucu içinde. İki kablo çıkar (kırmızı +, siyah -).",
    buy: "\"12 Volt DC Siren — güvenlik sireni\" → online (Trendyol, Hepsiburada) veya hırdavatçı.",
    notes: "220V değil, 12V DC olduğundan emin ol! Kutuda \"12V DC\" yazılı olmalı.",
  },
  { n: "4", name: "12V Strobe Işık (Kırmızı LED)", price: "60-150 TL",
    what: "Yanıp sönen kırmızı LED flaşör.",
    job: "Görsel uyarı; gece uzaktan görünür.",
    looks: "Bisiklet arka stop lambasından biraz büyük, polis ışığı gibi. Dış mekana dayanıklı.",
    buy: "\"12V Strobe Işık LED Kırmızı dış mekan\". IP54 veya üstü tercih.",
    notes: "",
  },
  { n: "5", name: "12V 2A DC Adaptör", price: "50-90 TL",
    what: "Modem adaptörüne benzeyen güç kaynağı; 220V prizden 12V çıkarır.",
    job: "Sirenin ve strobe'un 12V'sini sağlar.",
    looks: "Avuç içi büyüklüğünde plastik kutu, sonunda iki kablo veya yuvarlak jak.",
    buy: "\"12V 2A DC Adaptör\". Modemcide / hırdavatta bulunur. Tercihen vida terminalli, yoksa jakı kesilebilir.",
    notes: "1A bile yeterli (siren+strobe ~300mA çeker), 2A güvenlik payı.",
  },
  { n: "6", name: "5V USB Adaptör + Kablo", price: "0-50 TL",
    what: "Telefon şarjı.",
    job: "ESP32'yi besler.",
    looks: "Bilinen telefon şarjı.",
    buy: "Evdeki eski telefon şarjı yeterli (sıfır TL!). Yeni alırsan herhangi bir 5V/1A telefon şarjı.",
    notes: "ESP32'nin USB girişine uyduğundan emin ol (USB-Micro veya USB-C).",
  },
  { n: "7", name: "Erkek-Dişi Jumper Kabloları", price: "15-30 TL",
    what: "İnce renkli kablolar, uçlarında metal pin.",
    job: "Lehim yapmadan ESP32 ile röle arasını bağlar.",
    looks: "Plastik kaplı 20 cm renkli kablo, ucunda 3-4 mm metal çubuk.",
    buy: "\"Dupont kablo 20cm Erkek-Dişi 40'lı set\". Online ~20 TL.",
    notes: "Erkek-Dişi olduğundan emin ol (bir uç pin, diğer uç delik). Karışık set de olur.",
  },
];

for (const m of moduleList) {
  children.push(h3(`${m.n}. ${m.name}  —  ${m.price}`));
  children.push(bulletRich([["Nedir? ", { bold: true }], [m.what]]));
  children.push(bulletRich([["Ne işe yarar? ", { bold: true }], [m.job]]));
  children.push(bulletRich([["Neye benzer? ", { bold: true }], [m.looks]]));
  children.push(bulletRich([["Ne almalıyım? ", { bold: true }], [m.buy]]));
  if (m.notes) {
    children.push(bulletRich([["Not: ", { bold: true, color: ORANGE }], [m.notes]]));
  }
}

children.push(h3("Ekstra (önerilir): Wago Klemens — 10-20 TL"));
children.push(p("İki veya daha fazla kabloyu lehimsiz birleştiren plastik klips. Sirenin (+) ve strobe'un (+) ucunu, röleden gelen tek kabloya bağlamak için. Şeker büyüklüğünde plastik; kabloyu sokunca tutan kıskaç. Yoksa kabloları iç içe büküp izolasyon bandıyla sarabilirsin ama wago çok daha temiz."));

children.push(h2("Toplam Bütçe"));
children.push(makeTable([
  ["Senaryo", "Tutar"],
  ["En ucuz (mevcut telefon şarjı + AliExpress)", "~300-400 TL"],
  ["Pratik (Türkiye perakende, hızlı temin)", "~500-700 TL"],
  ["Önerilen (yedek + proje kutusu + UPS)", "~2.500-3.000 TL"],
], [5500, 3526]));

children.push(h2("Öneri: Yedek + Plastik Kutu (Toplam +250 TL)"));
children.push(bullet("Yedek ESP32 + röle (~200 TL): Saha problemine karşı hemen değiştirebilesin."));
children.push(bullet("Proje kutusu (~30-60 TL): ESP32 ve röleyi tek kutuya monte et — toz ve su sıçramasından korur."));
children.push(bullet("1A cam sigorta (~5 TL): 12V devresine eklenirse kısa devre güvenliği."));

// ──────────── BÖLÜM 4 ────────────
children.push(h1("4. Bağlantı Şeması — Hangi Kablo Nereye?"));
children.push(note("LEHİM YOK:",
  "Tüm bağlantılar jumper kabloları takarak ve tornavida ile vidaları sıkıştırarak yapılır. Lehim havyası gerekmez.", GREEN));

children.push(h2("Mantığı Önce Anlayalım"));
children.push(p("12V'lik adaptörü direkt sirene takarsan sürekli çalar. Adaptör ile siren arasına bir röle (elektronik anahtar) koyarsak, röle 'açık' iken siren susar, 'kapalı' iken çalar."));
children.push(p("Röleyi de ESP32 kontrol ediyor. ESP32'ye komut PC'den Wi-Fi ile gelir. Zincir şöyle:"));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 80, after: 80 },
  children: [new TextRun({ text: "PC  →  Wi-Fi  →  ESP32  →  Röle  →  Siren + Strobe",
                          font: "Arial", size: 26, bold: true, color: ACCENT })],
}));

children.push(h2("Görsel Şema"));
children.push(code(
"[ Prizden 220V çıkıyor → Adaptörler 12V ve 5V'a düşürüyor ]\n\n" +
"220V Priz #1 ───[ 5V USB adaptör ]───[USB kablo]─→ ESP32 USB girişi\n" +
"                                                       │\n" +
"                                                       ▼\n" +
"                         ┌──────── ESP32 ────────┐\n" +
"                         │  GPIO 4 (sinyal çıkışı)│ ── jumper ── IN  ┐\n" +
"                         │  GND   (toprak)        │ ── jumper ── GND │ RÖLE\n" +
"                         │  5V    (besleme)       │ ── jumper ── VCC ┘\n" +
"                         └────────────────────────┘\n\n" +
"220V Priz #2 ───[ 12V 2A adaptör ]\n" +
"                       │ (+)              ┌─── COM\n" +
"                       └──────────────────┤\n" +
"                                          │  NO ─── (siren+ ve strobe+)\n" +
"                       ┌──────────────────┤\n" +
"                       │ (-)              └ (kontaklar — açılır/kapanır anahtar)\n" +
"                       │\n" +
"                       ├──── SİREN  (-)\n" +
"                       └──── STROBE (-)"));

children.push(h2("5 Adımda Bağlantı"));

children.push(h3("Adım 1: ESP32'yi Röle'ye Bağla (3 jumper kablo)"));
children.push(p("ESP32 kartının kenarında küçük metal pinler dizili; her pin üstünde yazısı var. 3 jumper al, aşağıdaki gibi tak:"));
children.push(makeTable([
  ["Jumper", "ESP32 ucu (pin yazısı)", "Röle modülü ucu"],
  ["Mavi (örn.)", "GPIO 4  veya  D4", "IN"],
  ["Siyah", "GND", "GND"],
  ["Kırmızı", "5V  veya  Vin", "VCC"],
], [1800, 3500, 3726]));
children.push(p("Jumper'ın bir ucu pin (erkek), diğer ucu delik (dişi). Erkek ucu ESP32'ye, dişi ucu röle'ye takarsın. Tıpır tıpır geçer, zorla itme."));

children.push(h3("Adım 2: 12V Adaptörü Hazırla"));
children.push(p("12V adaptörü prize TAKMA henüz. Ucundaki yuvarlak DC jak'ı (varsa) makasla kes, kablo iki damar göreceksin:"));
children.push(bullet("(+) artı kablo — genelde kırmızı veya beyaz çizgili"));
children.push(bullet("(-) eksi kablo — genelde siyah veya düz"));
children.push(p("Hangisinin hangisi olduğu adaptörün üstünde küçücük yazıyla yazılı olabilir, yoksa multimetreyle ölçülür."));

children.push(h3("Adım 3: 12V (+) → Röle COM"));
children.push(p("Röle modülünün üstünde 3 vidalı terminal var: NC, COM, NO. Tornavidayla COM'un vidasını gevşet, 12V (+) kabloyu içeri sok, vidayı sıkıştır. Çekiştirip kontrol et çıkmasın."));

children.push(h3("Adım 4: Röle NO → Siren+Strobe (+)"));
children.push(p("Röle'nin NO terminalinden 50 cm tek damar kablo çık (jumper'ın artakaldığı bile olur). Bu kablo wago klemense gider:"));
children.push(numbered("Wago klemensin ilk deliğine: NO'dan gelen kablo."));
children.push(numbered("İkinci deliğe: Sirenin (+) kablosu."));
children.push(numbered("Üçüncü deliğe: Strobe'un (+) kablosu."));
children.push(p("Wago'nun kolunu indir, kabloları kıstırır. Hepsi birbirine bağlanır."));

children.push(h3("Adım 5: Siren+Strobe (-) → 12V Adaptör (-)"));
children.push(p("İkinci bir wago klemens al:"));
children.push(bullet("Adaptörün (-) ucu → birinci delik"));
children.push(bullet("Sirenin (-) ucu → ikinci delik"));
children.push(bullet("Strobe'un (-) ucu → üçüncü delik"));

children.push(note("KONTROL:",
  "İlk açılışta siren çalmamalı. Eğer çalıyorsa NO ile NC karışmış demektir, kabloyu COM'dan NC'ye al, NO'da olanı NC'ye yer değiştir.", ORANGE));

// ──────────── BÖLÜM 5 ────────────
children.push(h1("5. ESP32 Firmware Yükleme (Yazılım)"));
children.push(p("ESP32'nin bir kez programlanması gerek. Bilgisayar başına 1 saatlik iş, ileride bir daha yapmazsın."));

children.push(h2("5.1. Arduino IDE Kur (Bedava)"));
children.push(numbered("Tarayıcıdan arduino.cc adresine git → \"Software\" → \"Arduino IDE 2.x\" Windows installer'ı indir."));
children.push(numbered("Kurulum sırasında çıkan sürücü onaylarını kabul et."));
children.push(numbered("Arduino IDE'yi aç."));

children.push(h2("5.2. ESP32 Desteği Ekle"));
children.push(numbered("File → Preferences menüsünü aç."));
children.push(numbered("\"Additional Boards Manager URLs\" kutusuna şunu yapıştır:"));
children.push(code("https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"));
children.push(numbered("OK ile kapat. Tools → Board → Boards Manager... menüsüne gir, arama kutusuna \"esp32\" yaz, \"esp32 by Espressif Systems\" paketini Install et (~150 MB)."));

children.push(h2("5.3. ESP32'yi PC'ye Bağla"));
children.push(numbered("USB kablo ile ESP32'yi bilgisayara tak. Windows yeni bir COM portu göstermeli (Aygıt Yöneticisi'nde Ports altında, örn. COM3)."));
children.push(numbered("COM port görünmüyorsa: ESP32'nin USB-Serial çipi için sürücü gerekli. Kart üstündeki çipte \"CH340\" yazıyorsa CH340 driver, \"CP2102\" yazıyorsa CP2102 driver yükle (Google'dan ücretsiz)."));
children.push(numbered("Arduino IDE'de Tools → Board → \"ESP32 Dev Module\" seç."));
children.push(numbered("Tools → Port → COM3 (sizdeki COM numarası)."));

children.push(h2("5.4. Kodu Aç ve Wi-Fi Bilgilerini Yaz"));
children.push(numbered("File → Open → projede esp32/esp32_relay.ino dosyasını aç."));
children.push(numbered("Kodun başında, 21-22. satırlarda Wi-Fi bilgilerini gör. Site Wi-Fi'sine göre düzenle:"));
children.push(code(
  "const char* WIFI_SSID     = \"SITE_WIFI_ADI\";\n" +
  "const char* WIFI_PASSWORD = \"SITE_WIFI_SIFRESI\";"
));

children.push(h2("5.5. Yükle (Upload)"));
children.push(numbered("Sketch → Upload (yukarı ok düğmesi). 30-60 sn sürebilir."));
children.push(numbered("\"Done uploading\" mesajını gör."));
children.push(numbered("Tools → Serial Monitor menüsünü aç. Sağ alt köşede \"115200 baud\" seç."));
children.push(numbered("Ekranda \"Connecting to WiFi...\" sonra \"IP address: 192.168.X.Y\" yazmalı. BU IP'Yİ NOT ET — sistem bunu kullanacak."));

children.push(h2("5.6. Tarayıcıdan Test Et"));
children.push(numbered("PC'den herhangi bir tarayıcı aç. Adres çubuğuna sırayla şunları yaz:"));
children.push(bulletRich([["http://ESP32_IP/status  ", { font: "Consolas", bold: true }], ["→ ekrana {\"relay\":\"off\"} JSON dönmeli"]]));
children.push(bulletRich([["http://ESP32_IP/alarm/on  ", { font: "Consolas", bold: true }], ["→ SİREN ÇALMALI + STROBE YANMALI"]]));
children.push(bulletRich([["http://ESP32_IP/alarm/off  ", { font: "Consolas", bold: true }], ["→ Susmalı"]]));
children.push(note("BAŞARI:",
  "Bu üç komut çalışıyorsa donanım kurulumu tamam, GateGuard yazılım kurulumuna geçebilirsin.", GREEN));

children.push(h2("5.7. ESP32'ye Sabit IP Ver"));
children.push(p("Router her açılışta ESP32'ye farklı IP verebilir. GateGuard'ın sabit bir IP'ye bağlanması için router'dan DHCP rezervasyonu yap:"));
children.push(numbered("Tarayıcıdan router admin paneline gir (genelde 192.168.1.1 veya 192.168.0.1)."));
children.push(numbered("\"DHCP\" / \"Bağlı Cihazlar\" listesine git. ESP32 cihazını bul (MAC adresi Serial Monitor'da yazılıdır)."));
children.push(numbered("\"DHCP Reservation\" / \"IP Adresini Sabitle\" seçeneğiyle istediğin IP'yi (örn. 192.168.1.50) ata."));
children.push(numbered("ESP32'yi yeniden başlat (USB'yi çıkar-tak), yeni IP'ye bağlanmalı."));

// ──────────── BÖLÜM 6 ────────────
children.push(h1("6. GateGuard Yazılımı Kurulumu"));
children.push(p("PC'de GateGuard zaten kuruluysa atla. İlk kurulum yapacaksan KURULUM.md dosyasını da takip et."));

children.push(h2("6.1. Ön Hazırlık"));
children.push(numbered("Python 3.11+ kurulu olmalı (python.org'dan)."));
children.push(numberedRich([["Microsoft Access Database Engine 2016 kur (64-bit) — Moonwell MDB okuması için. Microsoft sitesinden bedava: ", {}], ["microsoft.com/en-us/download/details.aspx?id=54920", { font: "Consolas", color: ACCENT }]]));
children.push(numbered("PowerShell aç, proje klasörüne git."));
children.push(numbered("Bağımlılıkları yükle:"));
children.push(code("pip install -r requirements.txt\n# veya\nuv sync --extra intrusion"));

children.push(h2("6.2. .env Dosyasını Düzenle"));
children.push(p("Proje klasöründe .env dosyasını metin editöründe aç. Aşağıdakileri kendi değerlerinle güncelle:"));
children.push(code(
  "# Modül 1 — bariyer kamerası\n" +
  "RTSP_URL=rtsp://admin:SIFRE@192.168.1.101:554/cam/realmonitor?channel=1\n" +
  "\n" +
  "# ESP32 alarm modülü\n" +
  "ESP32_IP=192.168.1.50\n" +
  "MOCK_MODE=false\n" +
  "\n" +
  "# Moonwell MDB (varsa)\n" +
  "MDB_PATH=C:\\Moonwell\\users.mdb\n" +
  "\n" +
  "# Modül 2 — yaya tespiti\n" +
  "ENABLE_INTRUSION_MODULE=true\n" +
  "USE_GPU_FOR_PERSON=auto\n" +
  "INTRUSION_CONFIDENCE=0.30\n" +
  "\n" +
  "# Auth — boş bırak, ilk başlatmada otomatik üretilir\n" +
  "GATEGUARD_TOKEN="
));

children.push(h2("6.3. (Performans) ONNX Model Üret — 1 Kez"));
children.push(p("YOLO modelini ONNX'e çevirirsek CPU'da 2-3 kat hızlanır. PowerShell'de:"));
children.push(code("python scripts/export_onnx.py --benchmark"));
children.push(p("Sonunda models/yolov8n.onnx üretilir. .env'ye ekle:"));
children.push(code("YOLO_PERSON_MODEL=models/yolov8n.onnx"));

children.push(h2("6.4. İlk Başlatma"));
children.push(numbered("start.bat dosyasına çift tıkla. Konsolda 'GateGuard başlatılıyor...' mesajı görmeli."));
children.push(numbered("Tarayıcı otomatik açılır, http://localhost:8000 adresine gider."));
children.push(numberedRich([["İlk açılışta token üretilir, data/auth_token.txt dosyasında saklanır. Bu dosyayı aç, içindeki uzun karakter dizisini kopyala, tarayıcıdaki giriş kutusuna yapıştır.", {}]]));
children.push(numbered("\"Beni Hatırla\" işaretliyse bir daha istemez (tarayıcı localStorage'a kaydeder)."));

// ──────────── BÖLÜM 7 ────────────
children.push(h1("7. Kameraları Tanıtma ve Bölge Çizme"));
children.push(p("GateGuard panelinden:"));

children.push(h2("7.1. Kamera Ekleme"));
children.push(numbered("Üst menüden 📹 ikona veya /cameras adresine git."));
children.push(numbered("\"Yeni Kamera\" düğmesi → her kamera için:"));
children.push(bulletRich([["Ad: ", { bold: true }], ["Örn. \"Bariyer\", \"Bahçe Duvarı\", \"Otopark\""]]));
children.push(bulletRich([["RTSP URL: ", { bold: true }], ["Kameradan aldığın stream adresi (kullanıcı:şifre@IP:port/yol)"]]));
children.push(bulletRich([["Rol: ", { bold: true }], ["'plate' (sadece plaka) / 'intrusion' (sadece kişi) / 'both' (ikisi de)"]]));

children.push(h2("7.2. Bölge Çizme (Sadece Modül 2 kameraları için)"));
children.push(numbered("Üst menüden 🌙 (Gece İzleme) → kamerayı seç → \"Bölge Düzenle\" düğmesi."));
children.push(numbered("Canlı görüntü üstüne istediğin sayıda nokta tıklayarak poligon (kapalı alan) çiz."));
children.push(numbered("Bölgenin ayarları:"));
children.push(bulletRich([["Sadece gece aktif: ", { bold: true }], ["Gündüz tetiklenmesin (örneğin bahçede bekçi varsa)"]]));
children.push(bulletRich([["Loitering süresi: ", { bold: true }], ["Kaç saniye bölgede kalırsa alarm? (varsayılan 1 sn)"]]));
children.push(bulletRich([["Hareket fallback (perimeter): ", { bold: true }], ["Duvar üstü için aktif et. YOLO insan tanımasa bile süpheli hareketi yakalar (kedi/köpek hariç)"]]));
children.push(numbered("\"Kaydet\" butonuyla bitir."));

children.push(h2("7.3. Test"));
children.push(numbered("Özet panelde canlı kamera grid'inde kameraları görmeli; FPS 1.5-3 olmalı."));
children.push(numbered("Bölgenin içinden geç — birkaç saniye sonra alarm tetiklenmeli. Tetiklenmezse loitering süresini düşür veya güven eşiğini ayarla."));

// ──────────── BÖLÜM 8 ────────────
children.push(h1("8. İlk Test Senaryoları"));
children.push(p("Donanım + yazılım kurulu, kameralar tanıtılı. Şimdi sistemin gerçek senaryolarda doğru çalıştığını sınamalısın."));

children.push(h2("8.1. Siren Testi (Donanım)"));
children.push(numbered("GateGuard panelden Özet sayfasında \"Hırsızlık Alarmı\" toggle'ı açık olmalı (yeşil)."));
children.push(numbered("Tarayıcıdan elle alarm tetikle: http://ESP32_IP/alarm/on → siren çalmalı."));
children.push(numbered("Panelden \"SUSTUR\" butonuna bas → siren susmalı."));

children.push(h2("8.2. Plaka Testi (Modül 1)"));
children.push(numbered("Bir aracı bariyere yaklaştır. Panel sağ üstte \"Geçişler\" tabına bak."));
children.push(numbered("Plaka okunmalı, doğruysa \"Yetkili\" yeşil işaretle, değilse \"Kaçak\" kırmızı + SİREN ÇALMALI."));
children.push(numbered("Birkaç araç dene (yetkili + yetkisiz). Plakaların doğru okunduğunu kontrol et."));

children.push(h2("8.3. Yaya Testi (Modül 2)"));
children.push(numbered("Geceleyin / perimeter bölgesi içinden bir kişi geçsin."));
children.push(numbered("Birkaç saniye içinde alarm tetiklenmeli + screenshot + 15 sn video klip kaydedilmeli."));
children.push(numbered("Galeride veya \"Gece İzleme\" sayfasında olayı bul, oynat. Doğruluk kontrolü yap."));

children.push(h2("8.4. Hayvan Testi (Yanlış Alarm)"));
children.push(numbered("Bölge içine bir kedi/köpek girsin."));
children.push(numbered("Alarm TETİKLENMEMELİ — sistem hayvan-insan ayrımı yapmalı."));
children.push(numbered("Tetiklenirse: classifier güven eşiğini yükselt veya bölgeyi yer seviyesinden uzak tut (duvar üstü ideal)."));

children.push(h2("8.5. Bağlantı Kesilme Testi"));
children.push(numbered("ESP32'nin USB kablosunu çıkar."));
children.push(numbered("2-3 dakika içinde panelde kırmızı banner çıkmalı: \"Siren bağlantısı yok!\""));
children.push(numbered("USB'yi geri tak → 1 dk içinde banner kaybolmalı."));

// ──────────── BÖLÜM 9 ────────────
children.push(h1("9. Sorun Giderme (FAQ)"));

const faqs = [
  ["Siren sürekli çalıyor, alarm tetiklemese de durmuyor", "Röle modülü 'low-level trigger' tipinde. ESP32 firmware'inde RELAY_ON_STATE sabitini LOW yap (esp32_relay.ino içinde), tekrar Upload et. Veya kabloları COM-NO yerine COM-NC'ye al."],
  ["ESP32 IP almıyor, Wi-Fi'ye bağlanmıyor", "1) Wi-Fi SSID/şifreyi tekrar kontrol et (büyük-küçük harf duyarlı). 2) Site Wi-Fi 5GHz olabilir; ESP32 sadece 2.4GHz destekler — router'ı 2.4GHz'e ayarla veya ayrı bir 2.4GHz SSID aç. 3) Kulübeye Wi-Fi sinyali yetersiz olabilir; Range Extender şart."],
  ["Tarayıcıdan ESP32 IP açılmıyor", "PC ve ESP32 aynı ağda mı kontrol et. PC'den 'ping 192.168.1.50' cevap verir mi? Vermezse router'da MAC filtering / firewall'a bak."],
  ["Kameralardan görüntü gelmiyor", "RTSP URL doğru mu? Kullanıcı:şifre@ kısmı kameraya tanımlı mı? VLC ile aynı URL'yi açıp test et."],
  ["Plaka çok düşük güvenle okunuyor", ".env'de INTRUSION_CONFIDENCE değil, plaka için ALPR ayarları geçerli. Kameranın açısı düşür (60° altı), gece görüş kalitesini artır."],
  ["Sistem yavaş çalışıyor (FPS 1'in altı)", "ONNX modeli kullan: scripts/export_onnx.py + YOLO_PERSON_MODEL=models/yolov8n.onnx. Kamera sayısını azalt veya çözünürlüğü 720p'ye düşür."],
  ["Disk doluyor", "Retention sweeper otomatik silmeli. Manuel: ayarlardan INTRUSION_RETENTION_DAYS değerini 30'a düşür."],
  ["Token unutuldu, panele giremiyorum", "PC'de data/auth_token.txt dosyasını aç, içindeki kodu kopyala."],
  ["Siren çalıyor ama kimse yok (yanlış alarm)", "Bölge sınırını daha sıkı çiz. Loitering süresini yükselt. Güven eşiğini (INTRUSION_CONFIDENCE) 0.30 → 0.35'e çıkar."],
  ["Sistem alarm verdiği halde fiziksel siren çalmıyor", "1) ESP32 Wi-Fi'de mi (Serial Monitor'da bak). 2) Adaptör fişe takılı mı? 3) Manuel test: http://ESP32_IP/alarm/on tarayıcıdan çağrıldığında çalıyor mu?"],
];
for (const [q, a] of faqs) {
  children.push(h3("S: " + q));
  children.push(p("C: " + a));
}

// ──────────── BÖLÜM 10 ────────────
children.push(h1("10. Hızlı Referans Kartı"));
children.push(p("Bu sayfayı yazdırıp PC'nin yanına as. Sahada en sık ihtiyaç duyacağın bilgi."));

children.push(h2("Önemli IP Adresleri"));
children.push(makeTable([
  ["Cihaz", "IP (örnek)", "Açıklama"],
  ["GateGuard PC", "192.168.1.10", "Yönetim paneli"],
  ["ESP32 (siren)", "192.168.1.50", "Alarm modülü"],
  ["Kamera 1 (bariyer)", "192.168.1.101", "Plaka kamerası"],
  ["Kamera 2 (perimeter)", "192.168.1.102", "Yaya kamerası"],
  ["Kamera 3 (opsiyonel)", "192.168.1.103", "Ek kamera"],
], [2500, 2500, 4026]));

children.push(h2("Kritik Komutlar"));
children.push(code("# Sunucuyu başlat (PC'de)\nstart.bat"));
children.push(code("# Tarayıcıdan panel\nhttp://localhost:8000"));
children.push(code("# Siren elle test\nhttp://192.168.1.50/alarm/on\nhttp://192.168.1.50/alarm/off"));
children.push(code("# Sistem sağlık kontrolü\nhttp://localhost:8000/api/health"));
children.push(code("# Logları canlı izle (PowerShell)\nGet-Content data\\logs\\app.log -Wait -Tail 50"));
children.push(code("# Token'ı oku\ntype data\\auth_token.txt"));

children.push(h2("Klavye Kısayolları"));
children.push(makeTable([
  ["Tuş", "İşlev"],
  ["1 / 2 / 3 / 4", "Tab değiştir (Özet / Canlı / Geçmiş / Galeri)"],
  ["S", "Alarmı sustur"],
  ["R", "MDB sync"],
  ["T", "Tema değiştir (gece/gündüz)"],
  ["?", "Bu yardım penceresi"],
  ["Esc", "Modal kapat"],
], [2000, 7026]));

children.push(h2("Acil Durum"));
children.push(numbered("Siren susmuyor → Panelde \"SUSTUR\" butonu. Çalışmazsa → ESP32'nin USB güç adaptörünü prizden çek."));
children.push(numbered("Sistem cevap vermiyor → start.bat'ı kapat, yeniden çalıştır."));
children.push(numbered("Tarayıcı açılmıyor → http://localhost:8000 adresini elle yaz."));
children.push(numbered("Hiçbir şey çalışmıyor → PC'yi yeniden başlat. start.bat'ı bilgisayar açıldığında otomatik çalışacak şekilde Görev Zamanlayıcısına ekle."));

children.push(p(" "));
children.push(note("YEDEKLEME:",
  "Her hafta data/gateguard.db dosyasının kopyasını al. Bir USB belleğe veya yöneticinin e-postasına gönder. Bu kayıt corruption olursa kurtarman imkansız değil ama can sıkıcı olur.", ORANGE));

// ──────────── DOKÜMAN ────────────
const doc = new Document({
  creator: "GateGuard Project",
  title: "GateGuard Donanım Kurulum Kılavuzu",
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
        size: { width: 11906, height: 16838 },   // A4
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({
            text: "GateGuard — Donanım Kurulum Kılavuzu",
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
  fs.writeFileSync(__dirname + "/GateGuard-Donanim-Kurulum-Kilavuzu.docx", buf);
  console.log("OK -> GateGuard-Donanim-Kurulum-Kilavuzu.docx");
}).catch(e => { console.error(e); process.exit(1); });
