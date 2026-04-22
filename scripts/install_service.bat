@echo off
:: ============================================
::  GateGuard — Windows Service Installer
::  Uses NSSM (https://nssm.cc/download)
::  Must run as Administrator
:: ============================================

net session >nul 2>&1
if errorlevel 1 (
    echo [HATA] Bu scripti Yonetici olarak calistirin!
    echo Sag tik ^> Yonetici olarak calistir
    pause
    exit /b 1
)

set SERVICE_NAME=GateGuard
set APP_DIR=%~dp0
set APP_DIR=%APP_DIR:~0,-1%

:: Locate NSSM binary
where nssm >nul 2>&1
if errorlevel 1 (
    if exist "%APP_DIR%\nssm.exe" (
        set NSSM=%APP_DIR%\nssm.exe
    ) else (
        echo [HATA] nssm.exe bulunamadi!
        echo.
        echo NSSM'yi indirin: https://nssm.cc/download
        echo nssm.exe dosyasini proje klasorune koyun veya PATH'e ekleyin.
        pause
        exit /b 1
    )
) else (
    set NSSM=nssm
)

:: Detect Python — prefer venv, fall back to system
set PYTHON_PATH=
if exist "%APP_DIR%\venv\Scripts\python.exe" (
    set PYTHON_PATH=%APP_DIR%\venv\Scripts\python.exe
    echo [*] Sanal ortam Python kullaniliyor: %PYTHON_PATH%
) else (
    for /f "tokens=*" %%i in ('where python 2^>nul') do (
        set PYTHON_PATH=%%i
        goto :found_python
    )
    echo [HATA] Python bulunamadi!
    pause
    exit /b 1
)
:found_python

echo.
echo ============================================
echo  GateGuard Windows Servis Kurulumu
echo ============================================
echo  Servis Adi : %SERVICE_NAME%
echo  Proje Yolu : %APP_DIR%
echo  Python     : %PYTHON_PATH%
echo ============================================
echo.

:: Remove existing service if present
%NSSM% status %SERVICE_NAME% >nul 2>&1
if not errorlevel 1 (
    echo [*] Mevcut servis kaldiriliyor...
    %NSSM% stop %SERVICE_NAME% >nul 2>&1
    %NSSM% remove %SERVICE_NAME% confirm >nul 2>&1
    timeout /t 2 >nul
)

:: Install service
echo [*] Servis kuruluyor...
%NSSM% install %SERVICE_NAME% "%PYTHON_PATH%" "-m uvicorn main:app --host 0.0.0.0 --port 8000"

:: Service metadata
%NSSM% set %SERVICE_NAME% AppDirectory "%APP_DIR%"
%NSSM% set %SERVICE_NAME% DisplayName "GateGuard - Unauthorized Vehicle Detection"
%NSSM% set %SERVICE_NAME% Description "Camera-based ALPR system with ESP32 alarm relay"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START

:: Auto-restart on crash (10s delay)
%NSSM% set %SERVICE_NAME% AppExit Default Restart
%NSSM% set %SERVICE_NAME% AppRestartDelay 10000

:: Log rotation (stdout/stderr → logs/)
%NSSM% set %SERVICE_NAME% AppStdout "%APP_DIR%\logs\service.log"
%NSSM% set %SERVICE_NAME% AppStderr "%APP_DIR%\logs\service_error.log"
%NSSM% set %SERVICE_NAME% AppStdoutCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppStderrCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760

:: Ensure unbuffered Python output for real-time logging
%NSSM% set %SERVICE_NAME% AppEnvironmentExtra PYTHONUNBUFFERED=1

:: Create logs directory
if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs"

:: Start the service
echo [*] Servis baslatiliyor...
%NSSM% start %SERVICE_NAME%

echo.
echo ============================================
echo  KURULUM TAMAMLANDI!
echo ============================================
echo.
echo  Servis durumu:   nssm status %SERVICE_NAME%
echo  Servisi durdur:  nssm stop %SERVICE_NAME%
echo  Servisi baslat:  nssm start %SERVICE_NAME%
echo  Servisi kaldir:  nssm remove %SERVICE_NAME%
echo.
echo  Tarayicida acin: http://localhost:8000
echo.
pause
