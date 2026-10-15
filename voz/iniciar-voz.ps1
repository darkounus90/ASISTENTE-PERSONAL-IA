# Jarvis - Voz (Windows)
# Ejecutar en PowerShell normal (NO hace falta administrador), en esta carpeta:
#   Set-ExecutionPolicy -Scope Process Bypass; .\iniciar-voz.ps1
# Opciones:  .\iniciar-voz.ps1 --teclado      (Enter para hablar)
#            .\iniciar-voz.ps1 --microfonos   (ver microfonos)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1. uv (gestiona Python y dependencias solo; no ensucia tu sistema)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Instalando uv (una sola vez)..." -ForegroundColor Cyan
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. Comprobar que Jarvis (WSL) este encendido
# Microfono (indice de --microfonos). Cambialo aqui si cambias de micro.
if (-not $env:JARVIS_MIC) { $env:JARVIS_MIC = "3" }
if (-not $env:JARVIS_API) { $env:JARVIS_API = "http://127.0.0.1:8787" }
try {
    $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$env:JARVIS_API/health"
} catch {
    Write-Host ""
    Write-Host "Jarvis no esta encendido." -ForegroundColor Yellow
    Write-Host "Enciendelo con la opcion 1 de Jarvis.bat."
    exit 1
}

# 3. Arrancar la voz (la primera vez descarga dependencias y modelos: unos minutos)
uv run voz.py @args
