# ============================================================
#  GateGuard - PROD Guvenli Guncelleme
#  Bu script PROD klasorunde (orn. C:\GateGuard) durur ve oradan calisir.
#  Yeni build'i uzerine acar ama AYARLARI ve VERITABANINI KORUR.
#
#  KORUNANLAR (asla dokunulmaz):  .env | data\ (DB) | static\screenshots
#                                 static\intrusion_clips | logs\
#  DEGISENLER: GateGuard.exe | _internal\ | static web dosyalari |
#              scripts\ | models\ | yolov8n.pt | KURULUM.md
#
#  Kullanim (PROD PC'de):
#     1) Yeni zip'i bir KENARA ac, orn.  C:\GateGuard_yeni\  (icinde GateGuard.exe)
#     2) powershell -ExecutionPolicy Bypass -File C:\GateGuard\update.ps1 -Source C:\GateGuard_yeni -Restart
#
#  -Source : yeni build'in GateGuard.exe iceren klasoru
#  -Restart: guncelleme sonrasi uygulamayi baslat (servis DEGILSE)
# ============================================================

param(
    [Parameter(Mandatory=$true)][string]$Source,   # YENI build klasoru (GateGuard.exe iceren)
    [string]$Dest = "",                             # MEVCUT kurulum klasoru (bos ise otomatik bulunur)
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

# Mevcut (eski) kurulum klasorunu belirle:
#   1) -Dest verildiyse onu kullan
#   2) verilmediyse: bu script mevcut kurulumun icinde mi? ($PSScriptRoot'ta GateGuard.exe var mi)
#   3) o da yoksa varsayilan  C:\GateGuard
#   4) hicbiri degilse net hata
if ($Dest -eq "") {
    if (Test-Path (Join-Path $PSScriptRoot "GateGuard.exe")) {
        $Dest = $PSScriptRoot
    } elseif (Test-Path "C:\GateGuard\GateGuard.exe") {
        $Dest = "C:\GateGuard"
    } else {
        throw "Mevcut GateGuard kurulumu bulunamadi. Kurulum klasorunu -Dest ile ver, orn:  -Dest C:\GateGuard"
    }
}

function Step($m){ Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "    [OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "    [!]  $m" -ForegroundColor Yellow }

# robocopy'yi guvenli calistir (exit 0-7 = basari, >=8 = hata)
function Robo($src, $dst, [string[]]$extra){
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $rargs = @($src, $dst) + $extra + @("/R:2","/W:2","/NFL","/NDL","/NJH","/NJS","/NP")
    & robocopy @rargs | Out-Null
    $code = $LASTEXITCODE; $ErrorActionPreference = $prev
    if ($code -ge 8) { throw "robocopy hatasi ($src -> $dst), kod $code" }
}

# --- 0. Dogrulama ---
Step "Dogrulama"
$srcExe = Join-Path $Source "GateGuard.exe"
if (-not (Test-Path $srcExe)) { throw "Kaynakta (YENI build) GateGuard.exe yok: $Source  (yeni zip'i acip GateGuard klasorunu -Source ver)" }
if (-not (Test-Path (Join-Path $Dest "GateGuard.exe"))) { throw "Hedef bir GateGuard kurulumu degil (GateGuard.exe yok): $Dest  (dogru klasoru -Dest ile ver)" }
if ((Resolve-Path $Source).Path -eq (Resolve-Path $Dest).Path) { throw "Kaynak (-Source) ve hedef (-Dest) ayni klasor olamaz" }
Ok "YENI build (kaynak) : $Source"
Ok "MEVCUT kurulum (hedef): $Dest"

# --- 1. Calisan uygulamayi durdur (exe/_internal kilidini birak) ---
Step "Calisan uygulama durduruluyor"
$p = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
& nssm stop GateGuard 2>$null | Out-Null           # NSSM servisi varsa
schtasks /end /tn "GateGuard" 2>$null | Out-Null    # gorev zamanlayici varsa
Get-Process GateGuard -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$ErrorActionPreference = $p
Start-Sleep -Seconds 2
Ok "Durduruldu (calisiyorduysa)"

# --- 2. KORUNACAKLARIN yedegi (.env + data) ---
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = Join-Path $Dest ("_backups\" + $stamp)
Step "Yedek aliniyor -> $bak"
New-Item -ItemType Directory -Force $bak | Out-Null
foreach ($item in @(".env","data")) {
    $srcP = Join-Path $Dest $item
    if (Test-Path $srcP) { Copy-Item -Recurse -Force $srcP (Join-Path $bak $item); Ok "yedek: $item" }
    else { Warn "$item yok (ilk kurulum olabilir)" }
}

# --- 3. Uygulama ikili + kutuphaneler ---
Step "Uygulama guncelleniyor (exe + _internal)"
Copy-Item -Force $srcExe (Join-Path $Dest "GateGuard.exe")
Robo (Join-Path $Source "_internal") (Join-Path $Dest "_internal") @("/MIR")   # tam degistir
Ok "exe + _internal"

# --- 4. Web arayuzu (static) - KANIT klasorlerine DOKUNMA ---
Step "Web arayuzu guncelleniyor (kanitlar korunuyor)"
Robo (Join-Path $Source "static") (Join-Path $Dest "static") `
     @("/E","/XD","screenshots","intrusion_clips","test_outputs","test_kamera_kayitlari")
Ok "static (screenshots + intrusion_clips korundu)"

# --- 5. Yardimci dosyalar ---
Step "Yardimci dosyalar"
foreach ($it in @("scripts","models")) {
    $s = Join-Path $Source $it
    if (Test-Path $s) { Robo $s (Join-Path $Dest $it) @("/E"); Ok $it }
}
foreach ($f in @("yolov8n.pt","KURULUM.md","update.ps1")) {
    $s = Join-Path $Source $f
    if (Test-Path $s) { Copy-Item -Force $s (Join-Path $Dest $f); Ok $f }
}

# .env ve data'ya HIC dokunulmadi -> ayarlar + DB korundu.
Step "Guncelleme tamamlandi"
Write-Host "    Korunan : .env | data\ (DB) | static\screenshots | static\intrusion_clips" -ForegroundColor Green
Write-Host "    Yedek   : $bak" -ForegroundColor Green

# --- 6. Baslat ---
if ($Restart) {
    Step "Uygulama baslatiliyor"
    Start-Process -FilePath (Join-Path $Dest "GateGuard.exe") -WorkingDirectory $Dest
    Ok "baslatildi"
} else {
    Write-Host "`nBaslatmak icin:  GateGuard.exe  (servis kullaniyorsan: nssm start GateGuard)" -ForegroundColor Cyan
}
