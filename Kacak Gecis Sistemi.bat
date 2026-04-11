@echo off
chcp 65001 >nul 2>&1
title GateGuard - Kaçak Araç Tespit Sistemi
color 0A

echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║   GateGuard - Kaçak Araç Tespit Sistemi     ║
echo   ║   Kamera Tabanlı Plaka Tanıma ve Alarm      ║
echo   ╚══════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ── Python kontrolü ──────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo   [HATA] Python bulunamadı!
    echo          Lütfen Python 3.10 veya üstünü kurun:
    echo          https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: ── Sanal ortam kontrolü ─────────────────────────────
if exist "venv\Scripts\python.exe" (
    echo   [*] Sanal ortam etkinleştiriliyor...
    call venv\Scripts\activate.bat
)

:: ── .env dosyasından ayarları oku ────────────────────
set "MOCK_MODE_DISPLAY=BİLİNMİYOR"
set "RTSP_DISPLAY=Yapılandırılmamış"
set "ESP32_DISPLAY=Yapılandırılmamış"

if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if "%%A"=="MOCK_MODE" (
            if /i "%%B"=="true" (
                set "MOCK_MODE_DISPLAY=TEST (Mock Kamera)"
            ) else (
                set "MOCK_MODE_DISPLAY=CANLI (Gerçek Kamera)"
            )
        )
        if "%%A"=="RTSP_URL" set "RTSP_DISPLAY=%%B"
        if "%%A"=="ESP32_IP" set "ESP32_DISPLAY=%%B"
    )
) else (
    echo   [UYARI] .env dosyası bulunamadı!
    echo           .env.example dosyasını .env olarak kopyalayın
    echo           ve ayarlarınızı düzenleyin.
    echo.
    echo   copy .env.example .env
    echo.
    pause
    exit /b 1
)

:: ── Yapılandırma özeti ───────────────────────────────
echo   ┌──────────────────────────────────────────────┐
echo   │  YAPILANDIRMA                                │
echo   ├──────────────────────────────────────────────┤
echo   │  Çalışma Modu : %MOCK_MODE_DISPLAY%
echo   │  Kamera       : %RTSP_DISPLAY%
echo   │  ESP32 Alarm  : %ESP32_DISPLAY%
echo   │  Web Arayüzü  : http://localhost:8000
echo   └──────────────────────────────────────────────┘
echo.

:: ── Tarayıcıyı aç (3 saniye gecikme ile) ────────────
echo   [*] Tarayıcı 3 saniye sonra açılacak...
start "" /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

:: ── Sunucuyu başlat ──────────────────────────────────
echo   [*] Sunucu başlatılıyor...
echo   [*] Durdurmak için Ctrl+C basın.
echo.
echo   ══════════════════════════════════════════════
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000

if errorlevel 1 (
    echo.
    echo   [HATA] Sunucu başlatılamadı!
    echo          Detaylar için logs\app.log dosyasına bakın.
    echo.
    echo   Yaygın çözümler:
    echo     - pip install -r requirements.txt
    echo     - .env dosyanızı kontrol edin
    echo     - 8000 portunun kullanılmadığından emin olun
    echo.
    pause
)
