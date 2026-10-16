# Jarvis - Voz (Windows)
# Ejecutar en PowerShell normal (NO hace falta administrador), en esta carpeta:
#   Set-ExecutionPolicy -Scope Process Bypass; .\iniciar-voz.ps1
# Opciones:  .\iniciar-voz.ps1 --teclado      (Enter para hablar)
#            .\iniciar-voz.ps1 --microfonos   (ver microfonos)
# Mientras corre: Ctrl+Alt+P = te escucha ya (o lo calla y te escucha). Escribe tmp\estado.json para el HUD.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1. uv (gestiona Python y dependencias solo; no ensucia tu sistema)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Instalando uv (una sola vez)..." -ForegroundColor Cyan
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. Comprobar que el cerebro este encendido
# Microfono: "auto" = el predeterminado de Windows, o el que elijas en la opcion 5 (se guarda por nombre).
if (-not $env:JARVIS_MIC) { $env:JARVIS_MIC = "auto" }
if (-not $env:JARVIS_API) { $env:JARVIS_API = "http://127.0.0.1:8787" }
# Atajo global para hablarle sin decir "Orion" (y callarlo si esta hablando). "no" lo desactiva.
if (-not $env:JARVIS_ATAJO) { $env:JARVIS_ATAJO = "ctrl+alt+p" }
try {
    $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$env:JARVIS_API/health"
} catch {
    Write-Host ""
    Write-Host "Jarvis no esta encendido." -ForegroundColor Yellow
    Write-Host "Enciendelo con la opcion 1 de Jarvis.bat."
    exit 1
}

# 3. Panel tipo JARVIS (HUD). JARVIS_HUD=no lo desactiva.
if ($env:JARVIS_HUD -ne "no" -and -not ($args | Where-Object { $_ -in "--microfonos", "--prueba", "--voces" })) {
    try { & (Join-Path $PSScriptRoot "..\hud\iniciar-hud.ps1") } catch { Write-Host "No pude abrir el panel (HUD): $_" }
}

# 4. Arrancar la voz (la primera vez descarga dependencias y modelos: unos minutos)
uv run voz.py @args
