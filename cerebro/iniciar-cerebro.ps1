# Pedro - cerebro nuevo (motor de Claude Code). Lo abre Jarvis.bat; no hace falta correrlo a mano.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Pedro - cerebro (no cerrar)"
try { Start-Transcript -Path (Join-Path $PSScriptRoot "arranque.log") -Force | Out-Null } catch {}
Write-Host ("Arranque: " + (Get-Date))

# 1. uv (gestiona Python y dependencias)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Instalando uv (una sola vez)..." -ForegroundColor Cyan
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "Paso 1 listo: uv en $((Get-Command uv -ErrorAction SilentlyContinue).Source)"
# 2. Clave de nyxos: se copia una sola vez desde Ubuntu a C:\Users\<tu>\.pedro\secrets.env
$datos = Join-Path $env:USERPROFILE ".pedro"
New-Item -ItemType Directory -Force -Path $datos | Out-Null
$secretos = Join-Path $datos "secrets.env"
if (-not (Test-Path $secretos)) {
    Write-Host "Copiando tu clave de nyxos desde Ubuntu..." -ForegroundColor Cyan
    $contenido = wsl.exe -d Ubuntu -- bash -c "cat ~/.openjarvis/secrets.env 2>/dev/null"
    if ($contenido) {
        [System.IO.File]::WriteAllLines($secretos, [string[]]$contenido)
        Write-Host "  Clave copiada." -ForegroundColor Green
    } else {
        Write-Host "  No encontre la clave en Ubuntu. Crea $secretos con estas dos lineas:" -ForegroundColor Yellow
        Write-Host "    ANTHROPIC_API_KEY=tu_clave_de_nyxos"
        Write-Host "    ANTHROPIC_BASE_URL=https://api.nyxos.shop"
    }
}

Write-Host ("Paso 2 listo: clave " + $(if (Test-Path $secretos) { "presente" } else { "FALTA" }))
# 3. Git Bash (Claude Code lo necesita en Windows)
$git = @("C:\Program Files\Git\bin\bash.exe", "C:\Program Files (x86)\Git\bin\bash.exe",
         "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $git) {
    Write-Host "Falta Git para Windows (Claude Code lo usa). Instalando con winget..." -ForegroundColor Yellow
    winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
}

Write-Host "Paso 3 listo: Git Bash = $git"
# 4. Apagar el cerebro viejo de Ubuntu si sigue prendido (usa el mismo puerto)
wsl.exe -d Ubuntu -- bash -c "pkill -f 'jarvis serve' 2>/dev/null; true" | Out-Null

# 5. Arrancar (la primera vez descarga dependencias: 1-2 minutos)
Write-Host "Paso 4: descargando dependencias y encendiendo (la primera vez puede tardar varios minutos)..." -ForegroundColor Cyan
uv run --python 3.12 pedro_cerebro.py 2>&1 | ForEach-Object { "$_" }
Write-Host ""
Write-Host "El cerebro se detuvo. Revisa cerebro\cerebro.log" -ForegroundColor Yellow
Start-Sleep -Seconds 30
