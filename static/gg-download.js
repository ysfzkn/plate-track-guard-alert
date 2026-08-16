// ── Ortak indirme kartı: ilerleme çubuğu + bitince kalıcı "Aç" butonu ─────────
// Excel/dosya indirmeleri için. Sunucu dosyayı diske (İndirilenler) yazar ve
// X-Export-File header'ı döner; "Aç" os.startfile ile aynı makinede açar.
// auth-client.js window.fetch'e Bearer token'ı otomatik ekler.

let _indirCard = null;
function _indirCardEnsure() {
    if (_indirCard && document.body.contains(_indirCard)) return _indirCard;
    const c = document.createElement('div');
    c.id = 'indir-card';
    c.className = 'fixed right-4 z-[75] w-80 max-w-[92vw] bg-gray-900 border border-gray-700 rounded-xl shadow-2xl p-4';
    c.style.bottom = '5rem';
    document.body.appendChild(c);
    _indirCard = c;
    return c;
}
function _indirCardKapat() {
    if (_indirCard) {
        if (_indirCard._objUrl) { try { URL.revokeObjectURL(_indirCard._objUrl); } catch (e) {} }
        _indirCard.remove(); _indirCard = null;
    }
}
function _mb(b) { return (b / 1048576).toFixed(1) + ' MB'; }
function _boyut(b) { return b < 1048576 ? Math.max(1, Math.round(b / 1024)) + ' KB' : (b / 1048576).toFixed(1) + ' MB'; }

async function _indirmeBaslat(url, adAlt) {
    const c = _indirCardEnsure();
    c._objUrl = null;
    c.innerHTML = `
      <div class="flex items-center justify-between mb-2">
        <div class="text-sm font-semibold flex items-center gap-2">📊 ${adAlt} hazırlanıyor…</div>
        <button onclick="_indirCardKapat()" class="text-gray-500 hover:text-white text-lg leading-none" title="Kapat">×</button>
      </div>
      <div class="text-xs text-gray-500 mb-2" id="indir-durum">Lütfen bekleyin…</div>
      <div class="w-full h-2.5 bg-gray-800 rounded-full overflow-hidden">
        <div id="indir-bar" class="h-full bg-gradient-to-r from-cyan-500 to-blue-500 rounded-full transition-all duration-200" style="width:12%"></div>
      </div>
      <div class="text-[11px] text-gray-500 mt-1 text-right" id="indir-yuzde">Başlatılıyor…</div>`;
    const $bar = () => document.getElementById('indir-bar');
    const $durum = () => document.getElementById('indir-durum');
    const $yuzde = () => document.getElementById('indir-yuzde');

    // Sunucu hazırlarken belirsiz ilerleme: %12→%85; asıl indirme son %15.
    let fake = 12;
    const fakeTimer = setInterval(() => {
        fake = Math.min(85, fake + Math.max(0.4, (85 - fake) * 0.05));
        if ($bar()) $bar().style.width = fake + '%';
        if ($yuzde()) $yuzde().textContent = 'Hazırlanıyor… %' + Math.round(fake);
    }, 200);

    try {
        const resp = await fetch(url);   // auth-client Bearer token'ı ekler
        clearInterval(fakeTimer);
        if (!resp.ok) throw new Error('Sunucu hatası (HTTP ' + resp.status + ')');

        const total = +resp.headers.get('Content-Length') || 0;
        if ($durum()) $durum().textContent = 'İndiriliyor…';

        let blob;
        if (resp.body && resp.body.getReader) {
            const reader = resp.body.getReader();
            const chunks = []; let received = 0;
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                chunks.push(value); received += value.length;
                const pct = total ? Math.round(received / total * 100) : 0;
                if ($bar()) $bar().style.width = (total ? (85 + pct * 0.15) : 92) + '%';
                if ($yuzde()) $yuzde().textContent = total
                    ? ('%' + pct + ' · ' + _mb(received) + ' / ' + _mb(total))
                    : (_mb(received) + ' indirildi');
            }
            blob = new Blob(chunks, { type: resp.headers.get('Content-Type') || 'application/octet-stream' });
        } else {
            blob = await resp.blob();
        }

        let fname = adAlt.toLowerCase().replace(/\s+/g, '_') + '.xlsx';
        const cd = resp.headers.get('Content-Disposition') || '';
        const m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);
        if (m) fname = decodeURIComponent(m[1]);
        const savedFile = resp.headers.get('X-Export-File') || '';
        const savedWhere = resp.headers.get('X-Export-Where') || '';

        const objUrl = URL.createObjectURL(blob);
        c._objUrl = objUrl;

        if (!savedFile) {   // sunucu kaydedemediyse tarayıcı indirmesine düş
            const a = document.createElement('a');
            a.href = objUrl; a.download = fname;
            document.body.appendChild(a); a.click(); a.remove();
        }

        const yerMsg = savedWhere === 'indirilenler'
            ? '💾 İndirilenler klasörüne kaydedildi.'
            : (savedFile ? '💾 Uygulama klasörüne kaydedildi.'
                         : '💾 Tarayıcı indirmesi denendi (İndirilenler).');

        c.innerHTML = `
          <div class="flex items-center justify-between mb-2">
            <div class="text-sm font-semibold text-emerald-400 flex items-center gap-2">✅ Excel hazır</div>
            <button onclick="_indirCardKapat()" class="text-gray-400 hover:text-white text-xl leading-none" title="Kapat">×</button>
          </div>
          <div class="text-xs text-gray-400 mb-1 truncate" title="${fname}">${fname} · ${_boyut(blob.size)}</div>
          <div class="text-[11px] text-gray-500 mb-3">${yerMsg}</div>
          <div class="w-full h-2.5 bg-gray-800 rounded-full overflow-hidden mb-3">
            <div class="h-full bg-emerald-500 rounded-full" style="width:100%"></div>
          </div>
          <div id="indir-hint" class="text-[11px] text-red-400 mb-2 hidden"></div>
          <div class="flex gap-2">
            <button id="indir-ac" class="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white text-sm py-2.5 rounded-lg font-bold shadow">📂 Excel'de Aç</button>
            <button onclick="_indirCardKapat()" class="bg-slate-600 hover:bg-slate-500 text-white text-sm py-2.5 px-5 rounded-lg font-semibold border border-slate-400">Kapat</button>
          </div>`;
        const acBtn = document.getElementById('indir-ac');
        const hint = () => document.getElementById('indir-hint');
        acBtn.onclick = async () => {
            acBtn.disabled = true; const eski = acBtn.textContent; acBtn.textContent = '⏳ Açılıyor…';
            if (savedFile) {
                try {
                    const r = await fetch('/api/open-export', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ file: savedFile }),
                    });
                    if (!r.ok) { let msg = 'HTTP ' + r.status; try { msg = (await r.json()).detail || msg; } catch (e) {} throw new Error(msg); }
                    _indirCardKapat();
                } catch (e) {
                    acBtn.disabled = false; acBtn.textContent = eski;
                    if (hint()) { hint().textContent = 'Açılamadı: ' + ((e && e.message) || e); hint().classList.remove('hidden'); }
                }
            } else {
                const a2 = document.createElement('a');
                a2.href = objUrl; a2.download = fname;
                document.body.appendChild(a2); a2.click(); a2.remove();
                _indirCardKapat();
            }
        };
    } catch (e) {
        clearInterval(fakeTimer);
        c.innerHTML = `
          <div class="flex items-center justify-between mb-2">
            <div class="text-sm font-semibold text-red-400 flex items-center gap-2">❌ İndirme başarısız</div>
            <button onclick="_indirCardKapat()" class="text-gray-500 hover:text-white text-lg leading-none" title="Kapat">×</button>
          </div>
          <div class="text-xs text-gray-400 mb-3">${(e && e.message) || e}</div>
          <button onclick="_indirCardKapat()" class="w-full bg-slate-600 hover:bg-slate-500 text-white text-sm py-2 rounded-lg border border-slate-400">Kapat</button>`;
    }
}
