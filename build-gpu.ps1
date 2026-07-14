# ============================================================
#  GateGuard - CUDA (GPU) Build + Paketleme
#  Ayri bir GPU sanal ortami (.venv-gpu) kurar, CUDA'li torch yukler,
#  exe'yi uretir, GPU env'ini (.env.saha-gpu) .env olarak icine koyar ve zipler.
#  CPU dev ortamini (.venv) HIC bozmaz.
#
#  Kullanim:
#     .\build-gpu.ps1                 : GPU build + GateGuard-gpu-prod.zip
#     .\build-gpu.ps1 -Cuda cu118     : daha eski surucu icin CUDA 11.8 wheel'leri
#     .\build-gpu.ps1 -Fresh          : .venv-gpu'yu sifirdan kur
#  Politika engellerse:
#     powershell -ExecutionPolicy Bypass -File .\build-gpu.ps1
#
#  NOT: Build makinesinde GPU OLMASI GEREKMEZ - CUDA kutuphaneleri torch
#  wheel'i ile paketlenir; GPU sadece HEDEF PC'de calisma aninda gerekir.
#  HEDEF PC'de guncel Nvidia surucusu olmali (CUDA toolkit gerekmez).
# ============================================================

param(
    [string]$Cuda = "cu121",   # cu121 (varsayilan) | cu118 | cu124
    [switch]$Fresh,            # .venv-gpu'yu sil ve yeniden kur
    [switch]$NoZip             # zip uretme
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$AppName = "GateGuard"
$DistDir = "dist\$AppName"
$GpuVenv = ".venv-gpu"
$Py      = "$GpuVenv\Scripts\python.exe"
$TorchIndex = "https://download.pytorch.org/whl/$Cuda"

function Step($m){ Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "    [OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "    [!]  $m" -ForegroundColor Yellow }

# Native komutlari (uv/pyinstaller) EAP=Stop altinda stderr'e takilmadan calistir
function Run-Cmd([scriptblock]$Cmd){
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Cmd
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}

# --- 0. uv var mi? ---
Step "uv kontrol"
Run-Cmd { uv --version 2>&1 | Out-Null } | Out-Null
if ($LASTEXITCODE -ne 0) { throw "uv bulunamadi. https://docs.astral.sh/uv ile kur." }
Ok "uv mevcut"

# --- 1. GPU sanal ortami ---
Step "GPU sanal ortami (.venv-gpu)"
if ($Fresh -and (Test-Path $GpuVenv)) { Remove-Item -Recurse -Force $GpuVenv }
if (-not (Test-Path $Py)) {
    $c = Run-Cmd { uv venv $GpuVenv --python 3.11 2>&1 | Out-Host }
    if ($c -ne 0) { throw "Sanal ortam olusturulamadi (exit $c)" }
    Ok ".venv-gpu olusturuldu"
} else { Ok ".venv-gpu mevcut (yeniden kurmak icin -Fresh)" }

# --- 2. Bagimliliklar (CPU torch dahil gelir) + PyInstaller ---
Step "Bagimliliklar kuruluyor (requirements + intrusion + pyinstaller)"
$c = Run-Cmd { uv pip install --python $Py -r requirements.txt 2>&1 | Out-Host }
if ($c -ne 0) { throw "requirements kurulamadi (exit $c)" }
$c = Run-Cmd { uv pip install --python $Py ultralytics lap pyinstaller 2>&1 | Out-Host }
if ($c -ne 0) { throw "ultralytics/lap/pyinstaller kurulamadi (exit $c)" }
Ok "Temel paketler kuruldu"

# --- 3. CUDA'li torch (CPU torch'un uzerine ZORLA yaz) ---
# --reinstall-package SART: requirements adimi ultralytics ile CPU torch'u
# kurar; onsuz uv "zaten kurulu" deyip CUDA surumunu ATLAR (build +cpu cikar).
# torch/torchvision bagimliliklari (numpy vb.) zaten kurulu oldugu icin
# cu index'i sadece bu iki paketi getirir, cakisma olmaz.
Step "CUDA torch kuruluyor ($Cuda) - CPU torch uzerine zorla, buyuk indirme"
$c = Run-Cmd { uv pip install --python $Py --reinstall-package torch --reinstall-package torchvision --index-url $TorchIndex torch torchvision 2>&1 | Out-Host }
if ($c -ne 0) { throw "CUDA torch kurulamadi (exit $c). Baska CUDA icin -Cuda cu121 dene." }
# Dogrula: GERCEKTEN CUDA derlemesi mi? Degilse DUR (CPU build'i GPU sanip gondermeyelim).
$torchInfo = & $Py -c "import torch,sys; sys.stdout.write(torch.__version__)" 2>&1
Ok "torch: $torchInfo"
if ($torchInfo -notmatch "\+cu") {
    throw "torch CPU derlemesi kuruldu ($torchInfo) - GPU CALISMAZ. CUDA index'inden kurulum atlandi/basarisiz. Internet baglantisini ve -Cuda surumunu ($Cuda) kontrol edip tekrar calistir."
}

# --- 4. Eski ciktilari temizle ---
Step "Eski build temizleniyor"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Ok "build ve dist silindi"

# --- 5. PyInstaller (GPU venv'inden) ---
Step "PyInstaller derlemesi (torch+CUDA buyuk - birkac dakika)"
$c = Run-Cmd { & $Py -m PyInstaller GateGuard.spec --clean --noconfirm 2>&1 | Out-Host }
if ($c -ne 0) { throw "Derleme basarisiz (exit $c)" }
if (-not (Test-Path "$DistDir\$AppName.exe")) { throw "Derleme basarisiz: exe olusmadi." }
Ok "$DistDir\$AppName.exe olusturuldu"

# --- 6. Runtime dosyalari (config.py: BASE_DIR = exe klasoru) ---
Step "Runtime dosyalari yerlestiriliyor"
Copy-Item -Recurse -Force "static" "$DistDir\static"; Ok "static"
if (Test-Path "models") { Copy-Item -Recurse -Force "models" "$DistDir\models"; Ok "models" }
if (Test-Path "yolov8n.pt") { Copy-Item -Force "yolov8n.pt" "$DistDir\yolov8n.pt"; Ok "yolov8n.pt" }
else { Warn "yolov8n.pt yok - ilk calistirmada inecek (internet gerekir)" }

# GPU env'ini .env olarak koy (zip icinde hazir gelsin)
if (Test-Path ".env.saha-gpu") {
    Copy-Item -Force ".env.saha-gpu" "$DistDir\.env"
    Copy-Item -Force ".env.saha-gpu" "$DistDir\.env.saha-gpu"
    Ok ".env (GPU profili) yerlestirildi - sahada RTSP/MDB/ESP32'yi doldur"
} else {
    Warn ".env.saha-gpu bulunamadi - .env.example kopyalaniyor"
    Copy-Item -Force ".env.example" "$DistDir\.env"
}
if (Test-Path ".env.example") { Copy-Item -Force ".env.example" "$DistDir\.env.example" }
if (Test-Path "scripts") { Copy-Item -Recurse -Force "scripts" "$DistDir\scripts"; Ok "scripts" }
if (Test-Path "KURULUM.md") { Copy-Item -Force "KURULUM.md" "$DistDir\KURULUM.md" }
if (Test-Path "update.ps1") { Copy-Item -Force "update.ps1" "$DistDir\update.ps1"; Ok "update.ps1" }
if (Test-Path "GUNCELLEME.txt") { Copy-Item -Force "GUNCELLEME.txt" "$DistDir\GUNCELLEME.txt"; Ok "GUNCELLEME.txt" }
if (Test-Path "GUNCELLE.bat") { Copy-Item -Force "GUNCELLE.bat" "$DistDir\GUNCELLE.bat"; Ok "GUNCELLE.bat" }

New-Item -ItemType Directory -Force "$DistDir\data", "$DistDir\logs", "$DistDir\moonwel_db" | Out-Null
Ok "data, logs, moonwel_db olusturuldu"

# --- 7. Ozet + ZIP ---
$sizeGB = "{0:N2}" -f ((Get-ChildItem -Recurse $DistDir | Measure-Object -Property Length -Sum).Sum / 1GB)
Step "GPU build tamamlandi"
Write-Host "    Cikti : $DistDir  ($sizeGB GB)" -ForegroundColor Green
Write-Host "    CUDA  : $Cuda | torch $torchInfo" -ForegroundColor Green

if (-not $NoZip) {
    Step "ZIP paketleniyor"
    $zip = "$AppName-gpu-prod.zip"
    $zipAbs = Join-Path (Get-Location).Path $zip
    Remove-Item -Force $zipAbs -ErrorAction SilentlyContinue
    try {
        # .NET ZipFile: Compress-Archive'den daha hizli/saglam (2+ GB dist icin).
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory((Resolve-Path $DistDir).Path, $zipAbs)
        Ok "$zip olusturuldu"
    } catch {
        Warn "ZIP olusturulamadi: $($_.Exception.Message)"
        Warn "Sebep genelde: CALISAN bir GateGuard.exe VEYA antivirus _internal DLL'lerini kilitliyor."
        Warn "ZIP SART DEGIL -> '$DistDir' klasorunu OLDUGU GIBI flash'a kopyalayabilirsin."
        Warn "Ya da: calisan GateGuard'i kapat + antivirusu duraklat, sonra tekrar dene."
    }
}

Write-Host "`nBitti. Hedef PC'de GUNCEL Nvidia surucusu olmali. GPU kullanimini" -ForegroundColor Cyan
Write-Host "logs\app.log icinde cihaz satirlarindan ve Gorev Yoneticisi > GPU'dan dogrula." -ForegroundColor Cyan
