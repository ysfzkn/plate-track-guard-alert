@echo off
chcp 65001 >nul 2>&1
title GateGuard
color 0A

echo.
echo   GateGuard - Kacak Arac Tespit Sistemi
echo   ======================================
echo.

cd /d "%~dp0"

:: Python kontrolu
python --version >nul 2>&1
if errorlevel 1 (
    echo   [HATA] Python bulunamadi!
    echo   https://www.python.org/downloads/
    pause
    exit /b 1
)

:: venv varsa aktifle
if exist ".venv\Scripts\activate.bat" (
    echo   [*] Sanal ortam aktif...
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    echo   [*] Sanal ortam aktif...
    call venv\Scripts\activate.bat
)

:: .env kontrolu
if not exist ".env" (
    echo   [HATA] .env dosyasi bulunamadi!
    echo   .env.example dosyasini .env olarak kopyalayin.
    pause
    exit /b 1
)

:: Mod goster
findstr /i "MOCK_MODE=true" .env >nul 2>&1
if errorlevel 1 (
    echo   Mod: CANLI
) else (
    echo   Mod: TEST
)

echo   Web: http://localhost:8000
echo.

:: 3 sn sonra tarayici ac
start "" /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

echo   Sunucu baslatiliyor...
echo   Durdurmak icin Ctrl+C basin.
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000

if errorlevel 1 (
    echo.
    echo   [HATA] Sunucu baslatma hatasi!
    echo   logs\app.log dosyasina bakin.
    pause
)
