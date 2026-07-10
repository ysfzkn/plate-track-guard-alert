# ============================================================
#  GateGuard - Tek Komutluk Build + Paketleme Script'i
#  Kullanim:
#     .\build.ps1              : build al + runtime dosyalarini yerlestir
#     .\build.ps1 -Zip         : yukaridakiler + dist'i ZIP'le
#     .\build.ps1 -Run         : build sonrasi exe'yi test icin calistir
#  PowerShell politikasi engellerse:
#     powershell -ExecutionPolicy Bypass -File .\build.ps1
# ============================================================

param(
    [switch]$Zip,   # dist\GateGuard -> GateGuard-prod.zip
    [switch]$Run    # build bitince exe'yi calistir
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$AppName = "GateGuard"
$DistDir = "dist\$AppName"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# Native komutlari (uv/pyinstaller) calistirir. EAP=Stop iken PowerShell 5.1
# native stderr'i (PyInstaller'in INFO satirlari dahil) fatal saydigi icin
# gecici olarak EAP=Continue'ya alir; exit kodunu dondurur. Cikti akisini
# birlestirmek (2>&1) ve Out-Host/Out-Null cagriyi yapan scriptblock icinde.
function Run-Cmd([scriptblock]$Cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Cmd
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}

# --- 1. PyInstaller kurulu mu? ---
Step "PyInstaller kontrol ediliyor"
$piCode = Run-Cmd { uv run python -c "import PyInstaller" 2>&1 | Out-Null }
if ($piCode -ne 0) {
    Warn "PyInstaller yok, kuruluyor (uv pip install pyinstaller)..."
    $insCode = Run-Cmd { uv pip install pyinstaller 2>&1 | Out-Host }
    if ($insCode -ne 0) { throw "PyInstaller kurulamadi (exit $insCode)" }
    Ok "PyInstaller kuruldu"
} else {
    Ok "PyInstaller mevcut"
}

# --- 2. Eski build'i temizle ---
Step "Eski build temizleniyor"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Ok "build ve dist silindi"

# --- 3. PyInstaller ile derle (spec hazir) ---
Step "PyInstaller derlemesi (birkac dakika surebilir)"
$buildCode = Run-Cmd { uv run pyinstaller GateGuard.spec --clean --noconfirm 2>&1 | Out-Host }
if ($buildCode -ne 0) { throw "Derleme basarisiz (exit $buildCode)" }
if (-not (Test-Path "$DistDir\$AppName.exe")) {
    throw "Derleme basarisiz: $DistDir\$AppName.exe olusmadi."
}
Ok "$DistDir\$AppName.exe olusturuldu"

# --- 4. Runtime dosyalarini exe'nin yanina yerlestir ---
# config.py: BASE_DIR = exe klasoru, bu dosyalar exe ile yan yana durmali
Step "Runtime dosyalari yerlestiriliyor"

# static: web arayuzu (ZORUNLU)
Copy-Item -Recurse -Force "static" "$DistDir\static"
Ok "static kopyalandi"

# models: sadece yolo_easyocr motoru icin (varsa)
if (Test-Path "models") {
    Copy-Item -Recurse -Force "models" "$DistDir\models"
    Ok "models kopyalandi"
} else {
    Warn "models yok (fast_alpr icin gerekmez) - atlandi"
}

# yolov8n.pt: Modul 2 insan tespit modeli (varsa, offline kurulum icin)
if (Test-Path "yolov8n.pt") {
    Copy-Item -Force "yolov8n.pt" "$DistDir\yolov8n.pt"
    Ok "yolov8n.pt kopyalandi (Modul 2)"
} else {
    Warn "yolov8n.pt yok - ilk calistirmada internetten inecek (Modul 2)"
}

# .env: mevcut varsa onu, yoksa ornegi sablon olarak koy
if (Test-Path ".env") {
    Copy-Item -Force ".env" "$DistDir\.env"
    Ok ".env kopyalandi (mevcut yapilandirma)"
} else {
    Copy-Item -Force ".env.example" "$DistDir\.env"
    Warn ".env yoktu - .env.example sablon olarak kopyalandi (prod tarafinda DUZENLE)"
}
# .env.example referans olarak her zaman eklensin
Copy-Item -Force ".env.example" "$DistDir\.env.example"

# scripts: servis kurulum yardimcilari
if (Test-Path "scripts") {
    Copy-Item -Recurse -Force "scripts" "$DistDir\scripts"
    Ok "scripts kopyalandi (servis kurulumu)"
}

# Saha rehberi
if (Test-Path "KURULUM.md") {
    Copy-Item -Force "KURULUM.md" "$DistDir\KURULUM.md"
    Ok "KURULUM.md kopyalandi"
}

# Yazilabilir bos klasorler (uygulama ilk calistiginda doldurur)
New-Item -ItemType Directory -Force "$DistDir\data", "$DistDir\logs", "$DistDir\moonwel_db" | Out-Null
Ok "data, logs, moonwel_db klasorleri olusturuldu"

# --- 5. Ozet ---
$sizeGB = "{0:N2}" -f ((Get-ChildItem -Recurse $DistDir | Measure-Object -Property Length -Sum).Sum / 1GB)
Step "Build tamamlandi"
Write-Host "    Cikti : $DistDir" -ForegroundColor Green
Write-Host "    Boyut : $sizeGB GB" -ForegroundColor Green

# --- 6. (Opsiyonel) ZIP ---
if ($Zip) {
    Step "ZIP paketleniyor"
    $zipPath = "$AppName-prod.zip"
    Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
    Compress-Archive -Path "$DistDir\*" -DestinationPath $zipPath
    Ok "$zipPath olusturuldu"
}

# --- 7. (Opsiyonel) Test calistirma ---
if ($Run) {
    Step "Exe test icin baslatiliyor (durdurmak icin Ctrl+C)"
    Push-Location $DistDir
    & ".\$AppName.exe"
    Pop-Location
}

Write-Host "`nBitti. Prod kurulumu icin dist\$AppName klasorunu (veya ZIP'i) hedef PC'ye tasi." -ForegroundColor Cyan
