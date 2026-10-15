# Pedro: arrancar solo al iniciar sesion en Windows.
# Crea (o quita con -Quitar) el acceso directo "Pedro" en la carpeta Inicio de tu usuario
# (shell:startup). Abre Jarvis.bat con el argumento "auto": enciende el cerebro y la voz
# sin mostrar el menu, en una ventana minimizada. No necesita administrador.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\instalar-autoarranque.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\instalar-autoarranque.ps1 -Quitar
param(
    [switch]$Quitar
)
$ErrorActionPreference = "Stop"

$raiz = Split-Path -Parent $PSScriptRoot          # carpeta de Pedro (la de Jarvis.bat)
$bat = Join-Path $raiz "Jarvis.bat"
$inicio = [Environment]::GetFolderPath("Startup")  # = shell:startup
$acceso = Join-Path $inicio "Pedro.lnk"

if ($Quitar) {
    if (Test-Path -LiteralPath $acceso) {
        Remove-Item -LiteralPath $acceso
        Write-Host "Listo: Pedro ya no arranca solo al iniciar Windows."
    } else {
        Write-Host "No habia acceso directo de arranque automatico. Nada que quitar."
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $bat)) {
    Write-Host "No encontre $bat" -ForegroundColor Red
    exit 1
}
if (-not $inicio) {
    Write-Host "No encontre la carpeta de Inicio de Windows." -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($acceso)
$lnk.TargetPath = $bat
$lnk.Arguments = "auto"
$lnk.WorkingDirectory = $raiz
$lnk.WindowStyle = 7                               # 7 = minimizada
$lnk.Description = "Pedro: asistente por voz (arranque automatico)"
$lnk.Save()

Write-Host "Listo: Pedro arrancara solo (minimizado) cada vez que inicies sesion."
Write-Host "Acceso directo: $acceso"
Write-Host "Para quitarlo: tools\instalar-autoarranque.ps1 -Quitar"
