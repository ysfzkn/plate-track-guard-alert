// GateGuard — Site Yonetim Kuruluna Sunulacak Proje Teklif Dokumani
// Calistirma: node generate_proposal.js
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

const border = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
const borders = { top: border, bottom: border, left: border, right: border };

const p = (text, opts = {}) => new Paragraph({
  spacing: { after: 120, line: 300 },
  alignment: opts.align ?? AlignmentType.JUSTIFIED,
  children: [new TextRun({ text, font: "Arial", size: 22, bold: opts.bold, color: opts.color, italics: opts.italics })],
});

const h1 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 280, after: 160 },
  children: [new TextRun({ text, font: "Arial", size: 30, bold: true, color: ACCENT })],
});

const h2 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 200, after: 120 },
  children: [new TextRun({ text, font: "Arial", size: 25, bold: true, color: ACCENT })],
});

const bullet = (text) => new Paragraph({
  numbering: { reference: "bullets", level: 0 },
  spacing: { after: 80, line: 280 },
  children: [new TextRun({ text, font: "Arial", size: 22 })],
});

const bulletRich = (parts) => new Paragraph({
  numbering: { reference: "bullets", level: 0 },
  spacing: { after: 80, line: 280 },
  children: parts.map(([text, opts = {}]) =>
    new TextRun({ text, font: "Arial", size: 22, bold: opts.bold })),
});

const cell = (text, opts = {}) => new TableCell({
  borders,
  width: { size: opts.width, type: WidthType.DXA },
  shading: opts.shade ? { fill: opts.shade, type: ShadingType.CLEAR } : undefined,
  margins: { top: 100, bottom: 100, left: 140, right: 140 },
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

// Kapak
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 60 },
  children: [new TextRun({ text: "GateGuard", font: "Arial", size: 56, bold: true, color: ACCENT })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 60 },
  children: [new TextRun({
    text: "Kacak Gecis Tespit ve Guvenlik Ihlali Bildirim Sistemi",
    font: "Arial", size: 26, color: GREY,
  })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 280 },
  children: [new TextRun({
    text: "Site Yonetim Kuruluna Sunulan Proje Teklif Dokumani",
    font: "Arial", size: 22, italics: true, color: GREY,
  })],
}));

// Replace ASCII placeholders with proper Turkish chars after writing? No -- write Turkish directly.
// (above kept ASCII fallback to avoid encoding issues; let's go back and use Turkish text)
// We'll just rebuild with proper Turkish chars below by clearing array.
children.length = 0;

const TR_TITLE = "GateGuard";
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 60 },
  children: [new TextRun({ text: TR_TITLE, font: "Arial", size: 56, bold: true, color: ACCENT })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 60 },
  children: [new TextRun({
    text: "Kaçak Geçiş Tespit ve Güvenlik İhlali Bildirim Sistemi",
    font: "Arial", size: 26, color: GREY,
  })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 280 },
  children: [new TextRun({
    text: "Site Yönetim Kuruluna Sunulan Proje Teklif Dokümanı",
    font: "Arial", size: 22, italics: true, color: GREY,
  })],
}));

const today = new Date();
const dateStr = `${String(today.getDate()).padStart(2, '0')}.${String(today.getMonth()+1).padStart(2, '0')}.${today.getFullYear()}`;

children.push(new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: [2800, 6226],
  rows: [
    ["Doküman Türü", "Proje Teklif ve Fizibilite Raporu"],
    ["Hazırlanma Tarihi", dateStr],
    ["Sürüm", "1.0"],
    ["Hedef Kitle", "Site Yönetim Kurulu Üyeleri"],
    ["Proje Adı", "GateGuard — Akıllı Geçiş ve Güvenlik İzleme Sistemi"],
  ].map(([k, v]) => row([
    cell(k, { width: 2800, bold: true, shade: ACCENT_LIGHT }),
    cell(v, { width: 6226 }),
  ])),
}));

// 1. Yonetici Ozeti
children.push(h1("1. Yönetici Özeti"));
children.push(p("GateGuard, sitemizin giriş–çıkış noktalarında ve ortak alanlarında yaşanan güvenlik ihlallerini tespit etmek amacıyla geliştirilmiş, mevcut altyapıyla tam uyumlu çalışan bir akıllı izleme sistemidir. Sistem, halihazırda kullanılmakta olan Moonwell MW-305 RFID kapı kontrol cihazından bağımsız olarak, kameralar üzerinden gerçek zamanlı plaka tanıma ve insan tespiti yaparak yetkisiz geçişleri otomatik olarak raporlar."));
children.push(p("Bu doküman; mevcut güvenlik açıklarını, önerilen çözümün teknik mimarisini, beklenen faydaları ve uygulama planını site yönetim kurulunun değerlendirmesine sunar. Sistem; herhangi bir aylık abonelik ücreti gerektirmeyen, tek seferlik kurulum maliyetiyle sürekli çalışan, kurum içi kapalı ağda barındırılan bir yapıdadır."));

// 2. Mevcut Durum
children.push(h1("2. Mevcut Durumun Değerlendirilmesi"));
children.push(p("Sitemizin giriş güvenliği, RFID etiketli araç kartlarıyla çalışan Moonwell MW-305 sistemine dayanmaktadır. Bu sistem kart okutarak bariyer açma fonksiyonunu yerine getirmekle birlikte, aşağıdaki açıkları yapısal olarak içermektedir:"));
children.push(bullet("Bariyer açıldığı sırada, kart okutmayan ikinci bir aracın ardı sıra giriş yapması (tailgating) tespit edilememektedir."));
children.push(bullet("RFID kartını başkasına devreden veya kayıp/çalınmış kart kullanan kişiler ile sahte plaka kullanımları kayıt altına alınamamaktadır."));
children.push(bullet("Kayıt altına alınan plaka–zaman eşleşmesi yapılmadığından, geçmişe dönük olay incelemelerinde manuel olarak saatlerce kamera kaydı taranmak zorunda kalınmaktadır."));
children.push(bullet("Yaya ihlalleri (duvar/çit aşımı, gece saatlerinde ortak alanlara izinsiz giriş) için otomatik bir uyarı mekanizması bulunmamaktadır."));
children.push(p("Söz konusu açıklar, son aylarda site sakinleri tarafından yönetime iletilen güvenlik şikâyetlerinin temel kaynağı olarak görünmektedir.", { italics: true }));

// 3. Cozum
children.push(h1("3. Önerilen Çözüm: GateGuard Sistemi"));
children.push(p("GateGuard; mevcut kameraları kullanarak iki bağımsız modülde, 7/24 ve insan müdahalesine ihtiyaç duymadan çalışan bir tespit ve uyarı platformudur."));

children.push(h2("3.1. Modül 1 — Kaçak Araç Tespiti"));
children.push(bulletRich([["Plaka Tanıma: ", { bold: true }], ["Türk plakalarına özel optimize edilmiş yapay zekâ modeli (fast-ALPR) ile %96 doğruluk oranında plaka okuma."]]));
children.push(bulletRich([["RFID Eşleştirme: ", { bold: true }], ["Okunan plaka, Moonwell MW-305 veritabanındaki yetkili araç listesi ile gerçek zamanlı karşılaştırılır."]]));
children.push(bulletRich([["Otomatik Alarm: ", { bold: true }], ["Yetkisiz bir geçiş tespit edildiğinde güvenlik kulübesindeki ESP32 tabanlı siren ünitesi tetiklenir; aynı anda yönetim panelinde görsel uyarı belirir."]]));
children.push(bulletRich([["Delil Arşivi: ", { bold: true }], ["Her geçiş için plaka, tarih–saat, giriş/çıkış yönü ve fotoğraf otomatik kaydedilir. Geçmişe dönük arama ve filtreleme arayüzü sunulur."]]));

children.push(h2("3.2. Modül 2 — Yaya / Hırsızlık İhlali Tespiti"));
children.push(bulletRich([["İnsan Tespiti: ", { bold: true }], ["YOLOv8 derin öğrenme modeli ile çoklu kamerada eş zamanlı kişi algılama."]]));
children.push(bulletRich([["Bölge Tanımlama: ", { bold: true }], ["Her kamera için yetkisiz geçişe kapalı poligon bölgeler (çatı, bahçe, otopark içi vb.) yönetici tarafından çizilebilir."]]));
children.push(bulletRich([["Gece Modu: ", { bold: true }], ["Kullanıcı tanımlı saat aralığında (örn. 23:00–06:00) sıkı izleme; gündüz saatlerinde sadece kritik bölgelerde uyarı."]]));
children.push(bulletRich([["Oyalanma Tespiti: ", { bold: true }], ["Belirli bir noktada ön tanımlı süreden uzun kalan kişi otomatik olarak işaretlenir."]]));
children.push(bulletRich([["Video Klip Delili: ", { bold: true }], ["Olay anına ait kısa video klip otomatik arşivlenir."]]));

// 4. Mimari
children.push(h1("4. Teknik Mimari ve Veri Güvenliği"));
children.push(p("Sistem; site içinde konumlandırılan tek bir endüstriyel mini PC üzerinde çalışır. Hiçbir görüntü veya kişisel veri site dışına / bulut sağlayıcılarına gönderilmez. Mevcut IP kameralar (RTSP protokolü ile) ve mevcut Moonwell veritabanı doğrudan kullanılır; ek bir kamera veya kart sistemi alımı zorunlu değildir."));

const archRows = [
  ["Bileşen", "Açıklama"],
  ["Donanım", "1 adet endüstriyel mini PC (i5/i7, 16 GB RAM, 1 TB SSD, opsiyonel GPU). Mevcut IP kameralar."],
  ["Yazılım", "Yerel sunucu uygulaması (FastAPI), web tabanlı yönetim paneli, otomatik başlangıç servisi."],
  ["Veritabanı", "Yerel SQLite — şifrelenmiş ve yedekli."],
  ["Alarm Donanımı", "ESP32 tabanlı röle modülü + siren / strobe lamba."],
  ["Ağ", "Sadece site içi LAN. İnternet bağlantısı zorunlu değildir."],
  ["Erişim", "Kullanıcı rolleri: Güvenlik (canlı izleme) / Yönetici (tüm fonksiyonlar)."],
];
children.push(new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: [3000, 6026],
  rows: archRows.map(([k, v], i) => row([
    cell(k, { width: 3000, bold: true, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
    cell(v, { width: 6026, bold: i === 0, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
  ])),
}));

children.push(p("KVKK uyumu açısından; tüm görüntüler yalnızca yetkili rollerce görüntülenebilir, geçmiş kayıtlar yapılandırılabilir bir saklama süresi sonunda otomatik silinir, sistem giriş/çıkış işlemleri denetim kaydı (audit log) altında tutulur.", { italics: true }));

// 5. Faydalar
children.push(h1("5. Beklenen Faydalar ve Risk Azaltımı"));
const benefitRows = [
  [["Risk / İhtiyaç", true], ["GateGuard Çözümü", true]],
  [["Kart okutmadan tailgating ile giriş", false], ["Plaka–RFID karşılaştırması; eşleşmeyen geçişte anlık siren + kayıt", false]],
  [["Kayıp / devredilmiş kart kullanımı", false], ["Plaka beyaz listesiyle kontrol; kart sahibinden farklı plaka uyarısı", false]],
  [["Geçmiş olay incelemesinde saatlerce kamera taraması", false], ["Plaka veya tarihle saniyeler içinde arama, ilgili fotoğraf + klip", false]],
  [["Gece saatlerinde ortak alana izinsiz giriş", false], ["Yaya tespiti + bölge tanımı + gece modu uyarısı", false]],
  [["Aylık bulut / abonelik gideri", false], ["Tamamen yerel; sıfır aylık ücret", false]],
];
children.push(new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: [4513, 4513],
  rows: benefitRows.map((r, i) => row([
    cell(r[0][0], { width: 4513, bold: i === 0, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
    cell(r[1][0], { width: 4513, bold: i === 0, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : (i > 0 ? GREEN : undefined) }),
  ])),
}));

// 6. Yol Haritasi
children.push(h1("6. Uygulama Yol Haritası"));
const phaseRows = [
  ["Faz", "Süre", "Faaliyetler"],
  ["1", "1. Hafta", "Donanım kurulumu, mini PC ağa entegrasyonu, kamera bağlantı testi, Moonwell veritabanı erişim doğrulaması."],
  ["2", "2. Hafta", "Modül 1 (plaka tanıma) saha kalibrasyonu, ESP32 siren montajı, güvenlik personeli eğitimi."],
  ["3", "3.–4. Hafta", "Modül 2 (yaya tespiti) bölge tanımları, gece modu ayarı, kullanıcı kabul testleri."],
  ["4", "Sürekli", "Aylık performans raporu yönetime sunulur; yazılım güncellemeleri kapalı ağ üzerinden gerçekleştirilir."],
];
children.push(new Table({
  width: { size: 9026, type: WidthType.DXA },
  columnWidths: [1100, 2400, 5526],
  rows: phaseRows.map((r, i) => row([
    cell(r[0], { width: 1100, bold: true, align: AlignmentType.CENTER, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
    cell(r[1], { width: 2400, bold: i === 0, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
    cell(r[2], { width: 5526, bold: i === 0, shade: i === 0 ? ACCENT_LIGHT : undefined, color: i === 0 ? ACCENT : undefined }),
  ])),
}));

// 7. Yatirim
children.push(h1("7. Yatırım ve Geri Dönüş Değerlendirmesi"));
children.push(p("Sistemin maliyet kalemleri donanım (mini PC + alarm modülü) ve bir defaya mahsus kurulum hizmetinden ibarettir. Kesin tutarlar, sitemizin mevcut kamera sayısı ve kablolama ihtiyacına göre yönetim kurulu onayı sonrasında ayrıntılı bir teklifle sunulacaktır."));
children.push(p("Yatırımın geri dönüşü, aşağıdaki kalemlerle değerlendirilebilir:"));
children.push(bullet("Site sakinlerine yönelik hırsızlık vakalarının ve sigorta kayıplarının azaltılması."));
children.push(bullet("Güvenlik personelinin manuel kayıt taraması yerine olay yönetimine odaklanmasıyla operasyonel verim artışı."));
children.push(bullet("Aylık bulut hizmeti veya üçüncü parti yazılım abonelik giderinin sıfır olması."));
children.push(bullet("Olası bir adli soruşturmada saatler içinde delil sunabilme kapasitesi."));

// 8. Sonuc
children.push(h1("8. Sonuç ve Yönetim Kurulundan Talep"));
children.push(p("GateGuard; mevcut altyapıyı koruyarak, ek personel istihdamı gerektirmeden, sitemizin güvenlik standardını gözle görülür biçimde yükseltecek; site sakinlerinin huzurunu doğrudan etkileyen güncel açıkları kapatacak bir çözüm olarak değerlendirilmektedir."));
children.push(p("Bu doğrultuda Yönetim Kurulu’ndan talep edilen; projenin pilot uygulamaya alınmasının prensip olarak onaylanması ve ayrıntılı maliyet teklifinin hazırlanabilmesi için saha keşif çalışmasına yetki verilmesidir.", { bold: true }));

children.push(new Paragraph({
  spacing: { before: 600 },
  alignment: AlignmentType.RIGHT,
  children: [new TextRun({ text: "Saygılarımla,", font: "Arial", size: 22, italics: true, color: GREY })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.RIGHT,
  children: [new TextRun({ text: "Proje Sorumlusu", font: "Arial", size: 22, bold: true })],
}));

const doc = new Document({
  creator: "GateGuard Project",
  title: "GateGuard Proje Teklif Dokümanı",
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, font: "Arial", color: ACCENT },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [{
      reference: "bullets",
      levels: [{
        level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } },
      }],
    }],
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
            text: "GateGuard — Site Yönetim Kuruluna Teklif Dokümanı",
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
  fs.writeFileSync(__dirname + "/GateGuard-Proje-Teklifi.docx", buf);
  console.log("OK -> GateGuard-Proje-Teklifi.docx");
});
