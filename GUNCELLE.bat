@echo off
chcp 65001 >nul 2>&1
title GateGuard Guncelleme
setlocal enableextensions

REM ================================================================
REM  GateGuard - Cift tikla guncelle
REM  Bu dosyayi YENI build zip'ini actigin klasorden cift tikla.
REM  Ayarlar (.env) ve veritabani (data) KORUNUR; kurulum guncellenir.
REM ================================================================

REM --- Yonetici degilse UAC ile yukselt ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Yonetici izni isteniyor...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

REM --- Kaynak = bu .bat'in klasoru (yeni build) ---
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

if not exist "%SRC%\update.ps1" (
    echo.
    echo [HATA] update.ps1 bu klasorde yok:
    echo        %SRC%
    echo Bu dosyayi, YENI GateGuard zip'ini actigin klasorden calistir.
    echo.
    pause
    exit /b 1
)

REM --- Hedef = mevcut kurulum (once C:\GateGuard) ---
set "DEST=C:\GateGuard"
if exist "%DEST%\GateGuard.exe" goto run

echo.
echo Mevcut kurulum "C:\GateGuard" icinde bulunamadi.
set /p DEST="Kurulum klasorunu yaz (orn: D:\GateGuard) ve Enter: "
if not exist "%DEST%\GateGuard.exe" (
    echo.
    echo [HATA] Bu klasorde GateGuard.exe yok:
    echo        %DEST%
    echo.
    pause
    exit /b 1
)

:run
echo.
echo   Yeni build (kaynak) : %SRC%
echo   Mevcut kurulum      : %DEST%
echo.
echo   Guncelleme basliyor... (.env ve data korunacak)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%SRC%\update.ps1" -Source "%SRC%" -Dest "%DEST%" -Restart
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
    echo   [TAMAM] Guncelleme bitti. Tarayici: http://localhost:8000
) else (
    echo   [HATA] Guncelleme sirasinda sorun olustu (kod %RC%). Yukaridaki mesaji oku.
)
echo.
pause
