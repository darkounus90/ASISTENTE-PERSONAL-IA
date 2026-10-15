# Pedro: captura de pantalla para que pueda "ver".
#   -Ruta C:\...\x.jpg                 -> pantalla principal
#   -Ruta ... -Todas                   -> todos los monitores
#   -Ruta ... -Titulo "parte titulo"   -> solo esa ventana (aunque este tapada)
# Reduce la imagen a 1568 px de lado mayor (lo optimo para Claude) y la guarda en JPEG.
param(
    [Parameter(Mandatory = $true)][string]$Ruta,
    [string]$Titulo = "",
    [switch]$Todas
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class PedroVer {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}
"@
[PedroVer]::SetProcessDPIAware() | Out-Null

function Guardar($bmp) {
    $max = 1568
    $escala = [Math]::Min(1.0, $max / [Math]::Max($bmp.Width, $bmp.Height))
    $w = [int]($bmp.Width * $escala); $h = [int]($bmp.Height * $escala)
    $final = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($final)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($bmp, 0, 0, $w, $h)
    $g.Dispose()
    $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
    $p = New-Object System.Drawing.Imaging.EncoderParameters 1
    $p.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality, [long]80)
    $final.Save($Ruta, $codec, $p)
    $final.Dispose()
    return "$w x $h"
}

if ($Titulo) {
    Add-Type -AssemblyName UIAutomationClient
    $AE = [System.Windows.Automation.AutomationElement]
    $hijos = $AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    $cand = @($hijos | Where-Object { try { $_.Current.Name -like "*$Titulo*" } catch { $false } })
    if ($cand.Count -eq 0) { Write-Output "ERROR: no encontre una ventana con '$Titulo' en el titulo."; exit 2 }
    $v = $cand[0]
    $h = [IntPtr]$v.Current.NativeWindowHandle
    if ([PedroVer]::IsIconic($h)) { [PedroVer]::ShowWindow($h, 9) | Out-Null; Start-Sleep -Milliseconds 400 }
    $r = New-Object PedroVer+RECT
    [PedroVer]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
    if ($w -le 0 -or $hh -le 0) { Write-Output "ERROR: la ventana no tiene tamano visible."; exit 3 }
    $bmp = New-Object System.Drawing.Bitmap $w, $hh
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    $ok = [PedroVer]::PrintWindow($h, $hdc, 2)   # 2 = PW_RENDERFULLCONTENT (sirve aunque este tapada)
    $g.ReleaseHdc($hdc)
    if (-not $ok) { $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size) }
    $g.Dispose()
    $tam = Guardar $bmp
    $bmp.Dispose()
    Write-Output ("OK: ventana '" + $v.Current.Name + "' " + $tam)
    exit 0
}

if ($Todas) { $area = [System.Windows.Forms.SystemInformation]::VirtualScreen }
else { $area = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds }
$bmp = New-Object System.Drawing.Bitmap $area.Width, $area.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($area.X, $area.Y, 0, 0, $bmp.Size)
$g.Dispose()
$tam = Guardar $bmp
$bmp.Dispose()
Write-Output ("OK: pantalla " + $(if ($Todas) { "completa (todos los monitores)" } else { "principal" }) + " " + $tam)
