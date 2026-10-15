"""Cancelación de eco con la clase REAL de voz.py y el procesador de WebRTC (livekit):
simula el parlante (bloques de 2048 a 24 kHz) y el micrófono (bloques de 1280 a 16 kHz)
como los entregan los callbacks de sounddevice.   python3 tests/test_eco.py"""
import importlib.util
import os
import sys
import types

import numpy as np

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("voz_eco", os.path.join(RAIZ, "voz", "voz.py"))
voz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voz)

SR, SR2 = 16000, 24000
rng = np.random.default_rng(7)


def habla(sr, seg, f0, fase):
    t = np.arange(int(sr * seg)) / sr
    env = (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) ** 2
    return sum(np.sin(2 * np.pi * f0 * k * t + fase[k]) / k for k in range(1, 12)) * env


def correr(ganancia_eco, retraso_s, con_usuario=True):
    eco_obj = voz.CancelacionEco()
    eco_obj.ajustar_retraso(0.15)
    fase = rng.uniform(0, 6, 13)
    seg = 8
    ref24 = (habla(SR2, seg, 140, fase) * 8000).astype(np.int16)
    ref16 = habla(SR, seg, 140, fase) * 8000
    d = int(retraso_s * SR)
    h = np.zeros(d + 4000)
    h[d], h[d + 400], h[d + 1500] = ganancia_eco, ganancia_eco / 3, ganancia_eco / 6
    eco = np.convolve(ref16, h)[:len(ref16)]
    usuario = np.zeros_like(ref16)
    if con_usuario:
        u = habla(SR, 2, 210, rng.uniform(0, 6, 13)) * 2500
        usuario[5 * SR:5 * SR + len(u)] = u
    mic = np.clip(eco + usuario + rng.normal(0, 30, len(ref16)), -32768, 32767).astype(np.int16)
    salida = []
    # Intercalar como en la vida real: el parlante entrega 2048 muestras cada ~85 ms y el mic 1280 cada 80 ms
    i_ref = i_mic = 0
    t_ref = t_mic = 0.0
    while i_mic + 1280 <= len(mic):
        if t_ref <= t_mic and i_ref + 2048 <= len(ref24):
            eco_obj.referencia(ref24[i_ref:i_ref + 2048])
            i_ref += 2048
            t_ref += 2048 / SR2
        else:
            salida.append(eco_obj.limpiar(mic[i_mic:i_mic + 1280]))
            i_mic += 1280
            t_mic += 1280 / SR
    out = np.concatenate(salida)
    rms = lambda x: float(np.sqrt(np.mean(x.astype(float) ** 2)))  # noqa: E731
    return rms(mic[2 * SR:5 * SR]), rms(out[2 * SR:5 * SR]), rms(out[5 * SR:7 * SR]), rms(usuario[5 * SR:7 * SR])


# parlante cercano normal
m, o, ou, u = correr(0.6, 0.12)
print(f"parlante: eco {m:.0f} -> {o:.0f} ({20 * np.log10(m / max(o, 1)):.0f} dB); tu voz encima {ou:.0f} de {u:.0f}")
assert o < m / 20, "debe quitar al menos 26 dB de eco"
assert ou > o * 5, "tu voz debe sobresalir claramente del eco residual"
# TV por HDMI (retraso 300 ms)
m, o, ou, u = correr(0.35, 0.30)
print(f"TV:       eco {m:.0f} -> {o:.0f} ({20 * np.log10(m / max(o, 1)):.0f} dB); tu voz encima {ou:.0f} de {u:.0f}")
assert o < m / 20
assert ou > o * 5
# si livekit falla en medio, se apaga solo y devuelve el audio tal cual
e = voz.CancelacionEco()
e.apm = None
x = np.ones(1280, dtype=np.int16)
assert (e.limpiar(x) == x).all() and not e.activo
print("cancelación de eco: OK")
