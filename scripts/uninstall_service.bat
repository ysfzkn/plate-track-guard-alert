@echo off
:: ============================================
::  GateGuard — Windows Service Uninstaller
::  Must run as Administrator
:: ============================================

net session >nul 2>&1
if errorlevel 1 (
    echo [HATA] Bu scripti Yonetici olarak calistirin!
    pause
    exit /b 1
)

set SERVICE_NAME=GateGuard

:: Locate NSSM binary
where nssm >nul 2>&1
if errorlevel 1 (
    if exist "%~dp0nssm.exe" (
        set NSSM=%~dp0nssm.exe
    ) else (
        echo [HATA] nssm.exe bulunamadi!
        pause
        exit /b 1
    )
) else (
    set NSSM=nssm
)

echo [*] GateGuard servisi durduruluyor...
%NSSM% stop %SERVICE_NAME% >nul 2>&1

echo [*] GateGuard servisi kaldiriliyor...
%NSSM% remove %SERVICE_NAME% confirm

echo.
echo [OK] Servis basariyla kaldirildi.
pause
