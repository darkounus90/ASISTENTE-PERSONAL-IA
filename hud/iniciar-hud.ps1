# Orion - HUD (panel tipo JARVIS en una esquina de la pantalla).
# Lo abre Jarvis.bat junto con la voz; tambien se puede abrir a mano:
#   powershell -NoProfile -ExecutionPolicy Bypass -File hud\iniciar-hud.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File hud\iniciar-hud.ps1 --demo
# Si ya hay un HUD abierto, no abre otro (lo trae al frente).
# No usa Set-Location: se puede llamar desde otro script sin cambiarle la carpeta.

$ErrorActionPreference = "Continue"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    Write-Host "HUD: falta uv (Jarvis.bat lo instala la primera vez)." -ForegroundColor Yellow
    exit 1
}
$hud = Join-Path $PSScriptRoot "hud.py"
$argumentos = @("run", "--python", "3.12", "`"$hud`"") + $args
# Oculto: sin consola extra; la ventana del HUD si se ve.
Start-Process -FilePath $uv -ArgumentList $argumentos -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
