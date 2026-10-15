# Jarvis - Parte 1: preparar Windows
# Ejecutar en PowerShell COMO ADMINISTRADOR:
#   Set-ExecutionPolicy -Scope Process Bypass; .\1-preparar-windows.ps1

$ErrorActionPreference = "Stop"

function Paso($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "    OK  $t" -ForegroundColor Green }
function Ojo($t)  { Write-Host "    !!  $t" -ForegroundColor Yellow }

# --- Debe correr como administrador ---
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Abre PowerShell como Administrador y vuelve a ejecutar este script." -ForegroundColor Red
    exit 1
}

# --- 1. GPU / driver NVIDIA ---
Paso "Revisando la GPU NVIDIA"
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $gpu = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
    Ok "GPU detectada: $gpu"
} else {
    Ojo "No encontre nvidia-smi. Instala el driver mas reciente de NVIDIA para la RTX 5070:"
    Ojo "https://www.nvidia.com/Download/index.aspx  (luego vuelve a correr este script)"
    exit 1
}

# --- 2. Memoria para WSL2 ---
Paso "Configurando memoria de WSL2 (.wslconfig)"
$wslcfg = Join-Path $env:USERPROFILE ".wslconfig"
if (Test-Path $wslcfg) {
    Copy-Item $wslcfg "$wslcfg.bak" -Force
    Ok "Respaldo del .wslconfig anterior en $wslcfg.bak"
}
@"
[wsl2]
memory=20GB
swap=8GB
"@ | Set-Content -Path $wslcfg -Encoding ASCII
Ok "WSL2 podra usar 20 GB de RAM (+8 GB de swap)"

# --- 3. WSL2 + Ubuntu ---
Paso "Revisando WSL2 y Ubuntu"
$distros = ""
try { $distros = (wsl.exe -l -q) -join " " -replace "`0", "" } catch { $distros = "" }

if ($distros -match "Ubuntu") {
    Ok "Ubuntu ya esta instalado"
    wsl.exe --update | Out-Null
    wsl.exe --shutdown
    Ok "WSL reiniciado para aplicar la memoria nueva"
    Write-Host "`nListo. Siguiente paso:" -ForegroundColor Green
    Write-Host "  1. Abre 'Ubuntu' desde el menu Inicio"
    Write-Host "  2. Ve a la carpeta donde descargaste estos archivos, por ejemplo:"
    Write-Host "       cd /mnt/c/Users/$env:USERNAME/Downloads/jarvis"
    Write-Host "  3. Ejecuta:  bash 2-instalar-jarvis.sh"
} else {
    Ojo "Instalando WSL2 con Ubuntu (esto activa funciones de Windows)..."
    wsl.exe --install -d Ubuntu
    Write-Host "`nREINICIA el PC." -ForegroundColor Yellow
    Write-Host "Al volver, Ubuntu se abrira y te pedira crear usuario y clave de Linux."
    Write-Host "Despues, dentro de Ubuntu:"
    Write-Host "  cd /mnt/c/Users/$env:USERNAME/Downloads/jarvis"
    Write-Host "  bash 2-instalar-jarvis.sh"
}
