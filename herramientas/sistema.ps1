# Pedro: volumen, musica y pantalla con las teclas multimedia de Windows.
# Lo llama pc_mcp.py (herramienta control_pc) desde WSL. Solo simula teclas multimedia
# y apaga el monitor; no ejecuta programas ni descarga nada.
#   -Accion volumen -Valor 40          -> baja todo y sube hasta ~40 % (cada pulsacion = 2 %)
#   -Accion subir_volumen -Valor 10    -> sube ~10 %
#   -Accion bajar_volumen -Valor 10    -> baja ~10 %
#   -Accion silenciar                  -> alterna silencio / sonido
#   -Accion musica_pausa | musica_siguiente | musica_anterior
#   -Accion pantalla_apagar            -> apaga el monitor (se enciende al mover el mouse)
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("volumen", "subir_volumen", "bajar_volumen", "silenciar", "musica_pausa",
                 "musica_siguiente", "musica_anterior", "pantalla_apagar")]
    [string]$Accion,
    [ValidateRange(0, 100)]
    [int]$Valor = 10
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class PedroTeclas {
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
}
"@

# Teclas virtuales de Windows
$VOL_SILENCIO = 0xAD
$VOL_BAJAR = 0xAE
$VOL_SUBIR = 0xAF
$MUSICA_SIGUIENTE = 0xB0
$MUSICA_ANTERIOR = 0xB1
$MUSICA_PAUSA = 0xB3

function Pulsar([int]$Tecla, [int]$Veces = 1) {
    for ($i = 0; $i -lt $Veces; $i++) {
        [PedroTeclas]::keybd_event([byte]$Tecla, 0, 1, [UIntPtr]::Zero)   # pulsar (tecla extendida)
        [PedroTeclas]::keybd_event([byte]$Tecla, 0, 3, [UIntPtr]::Zero)   # soltar
        Start-Sleep -Milliseconds 15
    }
}

function Pasos([int]$Porcentaje) {
    return [int][math]::Floor(($Porcentaje + 1) / 2)   # cada pulsacion mueve 2 %
}

try {
    switch ($Accion) {
        "volumen" {
            Pulsar $VOL_BAJAR 50
            Pulsar $VOL_SUBIR (Pasos $Valor)
            "OK: volumen ~$Valor"
        }
        "subir_volumen" {
            Pulsar $VOL_SUBIR ([math]::Max(1, (Pasos $Valor)))
            "OK: volumen +$Valor"
        }
        "bajar_volumen" {
            Pulsar $VOL_BAJAR ([math]::Max(1, (Pasos $Valor)))
            "OK: volumen -$Valor"
        }
        "silenciar" { Pulsar $VOL_SILENCIO; "OK: silencio alternado" }
        "musica_pausa" { Pulsar $MUSICA_PAUSA; "OK: reproducir/pausa" }
        "musica_siguiente" { Pulsar $MUSICA_SIGUIENTE; "OK: siguiente" }
        "musica_anterior" { Pulsar $MUSICA_ANTERIOR; "OK: anterior" }
        "pantalla_apagar" {
            Start-Sleep -Milliseconds 800   # que no la despierte el ultimo movimiento
            # WM_SYSCOMMAND / SC_MONITORPOWER / 2 = apagar (PostMessage: no se queda esperando)
            [void][PedroTeclas]::PostMessage([IntPtr]0xFFFF, 0x0112, [IntPtr]0xF170, [IntPtr]2)
            "OK: pantalla apagada"
        }
    }
} catch {
    "ERROR: $($_.Exception.Message)"
    exit 1
}
