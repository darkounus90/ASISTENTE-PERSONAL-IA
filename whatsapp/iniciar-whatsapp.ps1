# Orión por WhatsApp (Windows)
# Orión responde SOLO en tu chat "Mensajes para ti mismo". Nunca le escribe a nadie más.
# Ejecutar (no hace falta administrador):
#   powershell -NoProfile -ExecutionPolicy Bypass -File whatsapp\iniciar-whatsapp.ps1

$Host.UI.RawUI.WindowTitle = "Orión - WhatsApp (no cerrar)"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Salir([int]$codigo) {
    Write-Host ""
    Read-Host "Enter para cerrar esta ventana" | Out-Null
    exit $codigo
}

# 1. Node.js y npm
$node = Get-Command node.exe -ErrorAction SilentlyContinue
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) {
    Write-Host ""
    Write-Host "No encuentro Node.js (node y npm)." -ForegroundColor Yellow
    Write-Host "Instala Node.js LTS con la opcion 8 de Jarvis.bat y vuelve a abrir el menu."
    Salir 1
}
$version = (& node.exe -v).Trim()
$mayor = 0
[void][int]::TryParse(($version -replace '^v', '').Split('.')[0], [ref]$mayor)
if ($mayor -lt 20) {
    Write-Host ""
    Write-Host "Tienes Node.js $version y Orión por WhatsApp necesita la version 20 o mas nueva." -ForegroundColor Yellow
    Write-Host "Actualiza Node.js LTS (opcion 8 de Jarvis.bat o: winget upgrade OpenJS.NodeJS.LTS)."
    Salir 1
}

# 2. Dependencias (solo la primera vez o si cambio package.json)
$marca = Join-Path $PSScriptRoot "node_modules\.package-lock.json"
$hayQueInstalar = -not (Test-Path $marca)
if (-not $hayQueInstalar) {
    $hayQueInstalar = (Get-Item "package.json").LastWriteTime -gt (Get-Item $marca).LastWriteTime
}
if ($hayQueInstalar) {
    Write-Host "Instalando dependencias de Orión por WhatsApp (una sola vez, 1-2 minutos)..." -ForegroundColor Cyan
    & npm.cmd install --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "npm install fallo. Revisa tu conexion a internet y vuelve a intentar." -ForegroundColor Red
        Salir 1
    }
}

# 3. Arrancar (si se cae por un error inesperado, reintenta unas veces)
$intentos = 0
while ($true) {
    & node.exe pedro-whatsapp.js
    $codigo = $LASTEXITCODE
    switch ($codigo) {
        0 { Salir 0 }
        2 {
            Write-Host ""
            Write-Host "La sesion de WhatsApp se cerro. Para vincular de nuevo:" -ForegroundColor Yellow
            Write-Host "  1. Borra la carpeta $env:USERPROFILE\.pedro\whatsapp-auth"
            Write-Host "  2. Vuelve a iniciar Orión por WhatsApp y escanea el codigo QR."
            Salir 2
        }
        3 { Salir 3 }
        4 { Salir 4 }
    }
    $intentos++
    if ($intentos -ge 5) {
        Write-Host ""
        Write-Host "Orión por WhatsApp se cayo $intentos veces seguidas (codigo $codigo). Lo dejo apagado." -ForegroundColor Red
        Salir $codigo
    }
    Write-Host "Orión por WhatsApp se detuvo (codigo $codigo). Reintento en 15 segundos..." -ForegroundColor Yellow
    Start-Sleep -Seconds 15
}
