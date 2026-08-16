/* GateGuard — paylasilan goruntu buyutec'i (zoom + pan + pinch).
 *
 * Amac: arka/hirsizlik kameralarinin goruntusu NEREDE gorunuyorsa orada
 * yakinlastirilabilsin — ozet gridindeki kucuk kutucuk, kamera duzenleme
 * onizlemesi, kacak alarmi penceresi, gece izleme gridi ve tam ekran.
 *
 * Kullanim:
 *   ggZoomAc(url, 'Kamera 2', 'Canlı görüntü');
 *   ggZoomAc(url, 'Kamera 2', 'Canlı', { live: true });   // 1 sn'de bir tazeler
 *
 * Kendi CSS'ini ve modalini enjekte eder; sayfanin temasindan bagimsizdir
 * (siyah zemin + beyaz kontroller her iki temada da okunur).
 */
(function () {
    'use strict';
    if (window.ggZoomAc) return;   // iki kez yuklenirse tek ornek kalsin

    var css = ''
        + '.ggz-back{position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,.93);display:none}'
        + '.ggz-back.open{display:block}'
        + '.ggz-vp{position:absolute;inset:0;overflow:hidden;user-select:none;touch-action:none;cursor:zoom-in}'
        + '.ggz-img{position:absolute;left:50%;top:50%;max-width:none;max-height:92vh;'
        + '        transform:translate(-50%,-50%);transform-origin:center;will-change:transform}'
        + '.ggz-bar{position:absolute;top:0;left:0;right:0;display:flex;align-items:center;gap:.75rem;'
        + '        padding:.6rem .9rem;background:linear-gradient(180deg,rgba(0,0,0,.85),transparent);'
        + '        color:#fff;pointer-events:none}'
        + '.ggz-title{font-size:1.05rem;font-weight:800;text-shadow:0 1px 3px rgba(0,0,0,.9)}'
        + '.ggz-info{font-size:.85rem;color:#cbd5e1;text-shadow:0 1px 3px rgba(0,0,0,.9)}'
        + '.ggz-live{display:inline-flex;align-items:center;gap:.35rem;font-size:.75rem;font-weight:800;'
        + '        background:rgba(220,38,38,.9);padding:.15rem .5rem;border-radius:999px}'
        + '.ggz-live i{width:.45rem;height:.45rem;border-radius:999px;background:#fff;'
        + '        animation:ggzBlink 1.1s ease-in-out infinite;font-style:normal}'
        + '@keyframes ggzBlink{0%,100%{opacity:1}50%{opacity:.25}}'
        + '.ggz-ctrl{position:absolute;bottom:.9rem;right:.9rem;display:flex;align-items:center;gap:.3rem;'
        + '        background:rgba(0,0,0,.8);border-radius:.6rem;padding:.25rem}'
        + '.ggz-btn{display:inline-flex;align-items:center;justify-content:center;height:2.2rem;min-width:2.2rem;'
        + '        padding:0 .5rem;background:#334155;color:#fff;border:none;border-radius:.4rem;'
        + '        font-size:1rem;font-weight:700;line-height:1;cursor:pointer;transition:background .12s;'
        + '        text-decoration:none}'
        + '.ggz-btn:hover{background:#475569}'
        + '.ggz-lbl{min-width:3.6rem;text-align:center;color:#f8fafc;font-size:.8rem;font-weight:700;'
        + '        font-variant-numeric:tabular-nums}'
        + '.ggz-close{position:absolute;top:.7rem;right:.9rem;width:2.4rem;height:2.4rem;font-size:1.15rem}'
        + '.ggz-hint{position:absolute;bottom:.9rem;left:.9rem;font-size:.78rem;color:rgba(255,255,255,.88);'
        + '        background:rgba(0,0,0,.6);border-radius:.35rem;padding:.25rem .5rem;pointer-events:none;'
        + '        transition:opacity .15s}';

    var st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);

    var back = document.createElement('div');
    back.className = 'ggz-back';
    back.innerHTML = ''
        + '<div class="ggz-vp" id="ggz-vp">'
        + '  <img class="ggz-img" id="ggz-img" alt="" draggable="false">'
        + '  <div class="ggz-bar">'
        + '    <span class="ggz-title" id="ggz-title"></span>'
        + '    <span class="ggz-info" id="ggz-info"></span>'
        + '    <span class="ggz-live" id="ggz-live" style="display:none"><i></i>CANLI</span>'
        + '  </div>'
        + '  <div class="ggz-ctrl">'
        + '    <button class="ggz-btn" id="ggz-out" title="Uzaklaştır">−</button>'
        + '    <span class="ggz-lbl" id="ggz-lbl">%100</span>'
        + '    <button class="ggz-btn" id="ggz-in" title="Yakınlaştır">+</button>'
        + '    <button class="ggz-btn" id="ggz-rst" title="Sıfırla" style="font-size:.8rem">Sıfırla</button>'
        + '    <a class="ggz-btn" id="ggz-dl" download title="İndir" style="font-size:.8rem">⤓</a>'
        + '  </div>'
        + '  <button class="ggz-btn ggz-close" id="ggz-x" title="Kapat (Esc)">✕</button>'
        + '  <div class="ggz-hint" id="ggz-hint">🔍 Fare tekeri / çift tık ile yakınlaştır · sürükleyerek gez</div>'
        + '</div>';
    (document.body || document.documentElement).appendChild(back);

    var $ = function (id) { return document.getElementById(id); };
    var vp = $('ggz-vp'), img = $('ggz-img');
    var z = { s: 1, tx: 0, ty: 0, drag: false, x0: 0, y0: 0 };
    var liveTimer = null, liveBase = '';

    function apply() {
        img.style.transform = 'translate(calc(-50% + ' + z.tx + 'px), calc(-50% + ' + z.ty + 'px)) scale(' + z.s + ')';
        $('ggz-lbl').textContent = '%' + Math.round(z.s * 100);
        vp.style.cursor = z.s > 1 ? (z.drag ? 'grabbing' : 'grab') : 'zoom-in';
        $('ggz-hint').style.opacity = z.s > 1 ? '0' : '1';
    }
    function setS(s) {
        z.s = Math.max(1, Math.min(8, s));
        if (z.s === 1) { z.tx = 0; z.ty = 0; }
        apply();
    }
    function reset() { z.s = 1; z.tx = 0; z.ty = 0; z.drag = false; apply(); }

    // Canli kaynaklarda URL'i her saniye tazele — zoom/pan bozulmadan goruntu akar.
    function liveTick() {
        var u = liveBase + (liveBase.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now();
        img.src = window.ggAuthedUrl ? window.ggAuthedUrl(u) : u;
    }

    function ac(url, title, info, opts) {
        opts = opts || {};
        if (!url) return;
        reset();
        $('ggz-title').textContent = title || '';
        $('ggz-info').textContent = info || '';
        $('ggz-live').style.display = opts.live ? '' : 'none';
        var dl = $('ggz-dl');
        dl.href = url;
        dl.setAttribute('download', ((title || 'goruntu') + '').replace(/[^\w.-]+/g, '_') + '.jpg');
        clearInterval(liveTimer); liveTimer = null;
        if (opts.live) {
            liveBase = String(url).split('?')[0];
            liveTick();
            liveTimer = setInterval(function () { if (!document.hidden) liveTick(); }, 1000);
        } else {
            img.src = url;
        }
        back.classList.add('open');
    }
    function kapat() {
        clearInterval(liveTimer); liveTimer = null;
        back.classList.remove('open');
        img.src = '';
    }

    $('ggz-in').onclick = function (e) { e.stopPropagation(); setS(z.s + 0.5); };
    $('ggz-out').onclick = function (e) { e.stopPropagation(); setS(z.s - 0.5); };
    $('ggz-rst').onclick = function (e) { e.stopPropagation(); reset(); };
    $('ggz-dl').onclick = function (e) { e.stopPropagation(); };
    $('ggz-x').onclick = function (e) { e.stopPropagation(); kapat(); };
    back.addEventListener('click', function (e) { if (e.target === back || e.target === vp) kapat(); });
    document.addEventListener('keydown', function (e) {
        if (!back.classList.contains('open')) return;
        if (e.key === 'Escape') { kapat(); e.stopPropagation(); }
        else if (e.key === '+' || e.key === '=') setS(z.s + 0.5);
        else if (e.key === '-') setS(z.s - 0.5);
        else if (e.key === '0') reset();
    }, true);

    vp.addEventListener('wheel', function (e) {
        e.preventDefault();
        setS(z.s + (e.deltaY < 0 ? 0.4 : -0.4));
    }, { passive: false });
    vp.addEventListener('dblclick', function () { setS(z.s > 1 ? 1 : 2.5); });

    var pointers = new Map(), pinchDist = 0, pinchScale = 1;
    vp.addEventListener('pointerdown', function (e) {
        if (e.target.closest('button') || e.target.closest('a')) return;
        try { vp.setPointerCapture(e.pointerId); } catch (x) {}
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 2) {
            var p = [].slice.call(pointers.values());
            pinchDist = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
            pinchScale = z.s;
        } else {
            z.drag = true; z.x0 = e.clientX - z.tx; z.y0 = e.clientY - z.ty; apply();
        }
    });
    vp.addEventListener('pointermove', function (e) {
        if (!pointers.has(e.pointerId)) return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 2) {
            var p = [].slice.call(pointers.values());
            var d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
            if (pinchDist > 0) setS(pinchScale * d / pinchDist);
        } else if (z.drag && z.s > 1) {
            z.tx = e.clientX - z.x0; z.ty = e.clientY - z.y0; apply();
        }
    });
    function up(e) {
        pointers.delete(e.pointerId);
        if (pointers.size < 2) pinchDist = 0;
        if (pointers.size === 0) { z.drag = false; apply(); }
    }
    vp.addEventListener('pointerup', up);
    vp.addEventListener('pointercancel', up);

    window.ggZoomAc = ac;
    window.ggZoomKapat = kapat;
})();
