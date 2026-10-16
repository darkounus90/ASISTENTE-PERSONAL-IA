# Orión: listar, LEER y escribir en terminales abiertas.
# Lo llama pc_mcp.py desde WSL. El texto llega en Base64 (UTF-8) para no pelear con comillas.
#   -Listar                         -> "clase<TAB>titulo" por ventana de terminal
#   -Leer -Titulo X [-Lineas 60]    -> ultimas lineas visibles/recientes de esa terminal (UI Automation)
#   -Titulo X -B64 ... [-Tecla t] [-Enter]  -> escribe (pegando) y/o pulsa una tecla
param(
    [switch]$Listar,
    [switch]$Leer,
    [int]$Lineas = 60,
    [string]$Titulo = "",
    [string]$B64 = "",
    [string]$Tecla = "",
    [switch]$Enter,
    [switch]$Exacta
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class PedroWin {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
$AE = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]

# Clases de ventana de terminal: Windows Terminal, consola clasica, y VS Code/otros por nombre
$clasesTerminal = @("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass", "PseudoConsoleWindow", "mintty", "VirtualConsoleClass")

function Get-Terminales {
    $hijos = $AE::RootElement.FindAll($Scope::Children, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($v in $hijos) {
        try {
            $c = $v.Current
            if ($c.Name -and ($clasesTerminal -contains $c.ClassName)) {
                [pscustomobject]@{ Titulo = $c.Name; Clase = $c.ClassName; Handle = [IntPtr]$c.NativeWindowHandle; Elemento = $v }
            }
        } catch {}
    }
}

function Nombre-Clase($clase) {
    switch ($clase) {
        "CASCADIA_HOSTING_WINDOW_CLASS" { "Windows Terminal" }
        "ConsoleWindowClass" { "Consola" }
        "mintty" { "Git Bash" }
        default { $clase }
    }
}

if ($Listar) {
    Get-Terminales | ForEach-Object { "{0}`t{1}" -f (Nombre-Clase $_.Clase), $_.Titulo }
    exit 0
}

$todas = @(Get-Terminales)
if ($Exacta) { $candidatas = @($todas | Where-Object { $_.Titulo -eq $Titulo }) }
else { $candidatas = @($todas | Where-Object { $_.Titulo -like "*$Titulo*" }) }
if ($candidatas.Count -gt 1) {
    $exacta = @($candidatas | Where-Object { $_.Titulo -eq $Titulo })
    if ($exacta.Count -eq 1) { $candidatas = $exacta }
}
if ($candidatas.Count -eq 0) {
    $hay = ($todas | ForEach-Object { $_.Titulo }) -join " | "
    Write-Output "ERROR: no encontre una terminal con '$Titulo' en el titulo. Abiertas: $hay"
    exit 2
}
if ($candidatas.Count -gt 1) {
    Write-Output ("ERROR: hay varias terminales con '$Titulo': " + (($candidatas | ForEach-Object { $_.Titulo }) -join " | "))
    exit 3
}
$ventana = $candidatas[0]

# ---------------------------------------------------------------- LEER (sin robar el foco)
if ($Leer) {
    $condTexto = New-Object System.Windows.Automation.PropertyCondition($AE::IsTextPatternAvailableProperty, $true)
    $elementos = @()
    if ([bool]$ventana.Elemento.GetCurrentPropertyValue($AE::IsTextPatternAvailableProperty)) { $elementos += $ventana.Elemento }
    $elementos += @($ventana.Elemento.FindAll($Scope::Descendants, $condTexto))
    $mejor = $null; $largo = -1
    foreach ($el in $elementos) {
        try {
            $patron = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
            $txt = $patron.DocumentRange.GetText(-1)
            $foco = $false
            try { $foco = $el.Current.HasKeyboardFocus } catch {}
            $puntaje = $txt.TrimEnd().Length + $(if ($foco) { 10000000 } else { 0 })
            if ($puntaje -gt $largo) { $mejor = $txt; $largo = $puntaje }
        } catch {}
    }
    if ($null -eq $mejor) {
        Write-Output "ERROR: esa ventana no deja leer su texto (las terminales integradas de VS Code no se pueden leer; usa Windows Terminal)."
        exit 5
    }
    $filas = $mejor -split "`r?`n"
    $fin = $filas.Count - 1
    while ($fin -ge 0 -and -not $filas[$fin].Trim()) { $fin-- }
    if ($fin -lt 0) { Write-Output ("OK: " + $ventana.Titulo); Write-Output "(la terminal esta vacia)"; exit 0 }
    $ini = [Math]::Max(0, $fin - $Lineas + 1)
    Write-Output ("OK: " + $ventana.Titulo)
    $filas[$ini..$fin] | ForEach-Object { $_.TrimEnd() }
    exit 0
}

# ---------------------------------------------------------------- ESCRIBIR
$h = $ventana.Handle
if ([PedroWin]::IsIconic($h)) { [PedroWin]::ShowWindow($h, 9) | Out-Null }
# Truco de la tecla Alt: evita el bloqueo de primer plano de Windows
[PedroWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
[PedroWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[PedroWin]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 350

Add-Type -AssemblyName System.Windows.Forms
if ($B64) {
    $texto = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($B64))
    $anterior = $null
    try { $anterior = Get-Clipboard -Raw } catch {}
    Set-Clipboard -Value $texto
    Start-Sleep -Milliseconds 120
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 400
    if ($null -ne $anterior -and $anterior -ne "") { Set-Clipboard -Value $anterior }
}

$mapa = @{ "enter" = "{ENTER}"; "escape" = "{ESC}"; "ctrl+c" = "^c"; "tab" = "{TAB}";
           "arriba" = "{UP}"; "abajo" = "{DOWN}"; "y" = "y"; "n" = "n"; "1" = "1"; "2" = "2"; "3" = "3" }
if ($Tecla) {
    if (-not $mapa.ContainsKey($Tecla)) { Write-Output "ERROR: tecla no permitida: $Tecla"; exit 4 }
    [System.Windows.Forms.SendKeys]::SendWait($mapa[$Tecla])
}
if ($Enter) { Start-Sleep -Milliseconds 120; [System.Windows.Forms.SendKeys]::SendWait("{ENTER}") }
Write-Output ("OK: " + $ventana.Titulo)
