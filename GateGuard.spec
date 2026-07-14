# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['fast_alpr', 'fast_plate_ocr', 'open_image_models', 'onnxruntime', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on', 'multipart', 'cv2', 'aiosqlite', 'httpx', 'dotenv']
tmp_ret = collect_all('fast_alpr')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('fast_plate_ocr')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('open_image_models')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ── Module 2 (intrusion): ultralytics YOLO + torch ──
# torch is CPU in the normal build and CUDA in the GPU build (build-gpu.ps1);
# collect_all bundles whichever flavour is installed in the build venv, plus
# ultralytics' data files (bytetrack.yaml, default.yaml, …) that a frozen app
# needs. Wrapped in try/except so a slim CPU-only checkout still builds.
for _pkg in ('ultralytics', 'torch', 'torchvision', 'lap'):
    try:
        _r = collect_all(_pkg)
        datas += _r[0]; binaries += _r[1]; hiddenimports += _r[2]
    except Exception:
        pass

# ── Native pencere modu: pywebview + pythonnet (Windows EdgeChromium backend) ──
# pywebview platform backend'ini dinamik yukler; PyInstaller kacirmasin diye
# collect_all + acik hiddenimports. pythonnet contrib hook'lariyla gelir.
hiddenimports += [
    'webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium',
    'clr', 'proxy_tools', 'bottle',
    # Sistem tepsisi (tray) — X ile kapatinca app tepside kalir
    'pystray', 'pystray._win32', 'PIL.Image',
]
for _pkg in ('webview', 'clr_loader', 'pystray'):
    try:
        _r = collect_all(_pkg)
        datas += _r[0]; binaries += _r[1]; hiddenimports += _r[2]
    except Exception:
        pass


a = Analysis(
    ['C:\\Users\\asus\\Desktop\\code\\Out of work\\plate-track-guard-alert\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GateGuard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # console=False → Electron benzeri: yaninda siyah terminal ACILMAZ, sadece
    # native pencere gelir. Loglar logs/app.log'a yazilir; baslatma hatasinda
    # main.py native bir MessageBox gosterir (print/input pencereli modda calismaz).
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\asus\\Desktop\\code\\Out of work\\plate-track-guard-alert\\static\\favicon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GateGuard',
)
