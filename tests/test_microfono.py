"""Pruebas del micrófono por nombre (sin Windows ni micrófono real):  python3 tests/test_microfono.py"""
import importlib.util
import os
import sys
import tempfile
import types

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))   # sin PortAudio en Linux

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("voz_mic", os.path.join(RAIZ, "voz", "voz.py"))
voz = importlib.util.module_from_spec(spec)
sys.modules["voz_mic"] = voz
spec.loader.exec_module(voz)

APIS = [{"name": "MME"}, {"name": "Windows WASAPI"}]
DISPOSITIVOS = [
    {"name": "Asignador de sonido Microsoft - Input", "max_input_channels": 2, "hostapi": 0},
    {"name": "Micrófono (HyperX Cloud II Wirele", "max_input_channels": 1, "hostapi": 0},   # MME trunca a 31
    {"name": "Micrófono (Realtek(R) Audio)", "max_input_channels": 2, "hostapi": 0},
    {"name": "Altavoces (Realtek(R) Audio)", "max_input_channels": 0, "hostapi": 0},
    {"name": "Micrófono (HyperX Cloud II Wireless)", "max_input_channels": 1, "hostapi": 1},
]
NO_SIRVEN = {4}   # WASAPI no acepta 16 kHz


class SdFalso:
    def __init__(self, dispositivos):
        self.d = dispositivos

    def query_hostapis(self):
        return APIS

    def query_devices(self, i=None, kind=None):
        if kind == "input" and i is None:
            return {"name": "Asignador de sonido Microsoft - Input"}
        return self.d if i is None else self.d[i]

    def check_input_settings(self, device=None, **_):
        if device in NO_SIRVEN or self.d[device]["max_input_channels"] < 1:
            raise ValueError("no")


def con(dispositivos, mic_env=None, archivo=None):
    voz.sd = SdFalso(dispositivos)
    voz.MIC = mic_env
    tmp = tempfile.mkdtemp()
    voz.MIC_ARCHIVO = os.path.join(tmp, "microfono.txt")
    if archivo is not None:
        with open(voz.MIC_ARCHIVO, "w", encoding="utf-8") as f:
            f.write(archivo)
    return voz.resolver_mic()


# automático: predeterminado de Windows
d, n, a = con(DISPOSITIVOS)
assert d is None and "predeterminado" in n and not a, (d, n, a)
d, n, a = con(DISPOSITIVOS, mic_env="auto")
assert d is None
# guardado por nombre completo (WASAPI) -> encuentra el MME truncado que sí sirve a 16 kHz
d, n, a = con(DISPOSITIVOS, archivo="Micrófono (HyperX Cloud II Wireless)")
assert d == 1, (d, n, a)
# guardado con el nombre truncado -> igual lo encuentra
d, n, a = con(DISPOSITIVOS, archivo="Micrófono (HyperX Cloud II Wirele")
assert d == 1
# la numeración cambia (conectaron otro dispositivo antes): sigue encontrándolo por nombre
movidos = [DISPOSITIVOS[0], {"name": "Micrófono (Cámara web)", "max_input_channels": 1, "hostapi": 0}] + DISPOSITIVOS[1:]
NO_SIRVEN.clear(); NO_SIRVEN.add(5)
d, n, a = con(movidos, archivo="Micrófono (HyperX Cloud II Wireless)")
assert d == 2 and "HyperX" in n, (d, n)
# desconectado -> predeterminado de Windows con aviso
sin_hyperx = [x for x in DISPOSITIVOS if "HyperX" not in x["name"]]
d, n, a = con(sin_hyperx, archivo="Micrófono (HyperX Cloud II Wireless)")
assert d is None and "No encontré" in a, (d, a)
# número viejo que ya no existe -> predeterminado con aviso; número que existe -> se respeta
d, n, a = con(sin_hyperx, mic_env="7")
assert d is None and "ya no existe" in a
d, n, a = con(sin_hyperx, mic_env="1")
assert d == 1
# JARVIS_MIC por nombre parcial
NO_SIRVEN.clear(); NO_SIRVEN.add(4)
d, n, a = con(DISPOSITIVOS, mic_env="realtek")
assert d == 2, (d, n)
# elegir_mic guarda el NOMBRE y omite el asignador y los que no sirven
con(DISPOSITIVOS)
import builtins
respuestas = iter(["2"])
builtins.input = lambda *_: next(respuestas)
voz.elegir_mic()
assert open(voz.MIC_ARCHIVO, encoding="utf-8").read() == "Micrófono (Realtek(R) Audio)"
respuestas = iter(["0"])
voz.elegir_mic()
assert not os.path.exists(voz.MIC_ARCHIVO)
# pregunta por voz
assert voz._PREGUNTA_MIC.search("qué micrófono estás usando")
assert voz._PREGUNTA_MIC.search("cuál micrófono tienes")
assert not voz._PREGUNTA_MIC.search("abre la calculadora")
print("micrófono: 10 pruebas OK")

# ---------------------------------------------------------------- salida de audio (TV / audífonos)
SALIDAS = [
    {"name": "Asignador de sonido Microsoft - Output", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    {"name": "Altavoces (Realtek(R) Audio)", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    {"name": "LG TV SSCR2 (NVIDIA High Defini", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    {"name": "Auriculares (HyperX Cloud II Wi", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    {"name": "Micrófono (Realtek(R) Audio)", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0},
]


class SdSalida(SdFalso):
    def query_devices(self, i=None, kind=None):
        if kind == "output" and i is None:
            return {"name": "Altavoces (Realtek(R) Audio)"}
        return super().query_devices(i, kind)

    def check_output_settings(self, device=None, **_):
        if self.d[device]["max_output_channels"] < 1:
            raise ValueError("no")


class RepFalso:
    def __init__(self):
        self.disp = "sin cambiar"

    def cambiar_salida(self, d):
        self.disp = d
        return True


voz.sd = SdSalida(SALIDAS)
NO_SIRVEN.clear()
voz.PARLANTE_ARCHIVO = os.path.join(tempfile.mkdtemp(), "parlante.txt")
voz.rep = RepFalso()
r = voz.comando_salida("habla por el televisor")
assert voz.rep.disp == 2 and "LG TV" in r, (voz.rep.disp, r)
assert open(voz.PARLANTE_ARCHIVO, encoding="utf-8").read().startswith("LG TV")
assert voz.resolver_salida()[0] == 2                       # al reiniciar, sigue en la TV
r = voz.comando_salida("Necesito que te escuches en el televisor")
assert voz.rep.disp == 2 and r.startswith("Listo")
r = voz.comando_salida("pásate a los audífonos")
assert voz.rep.disp == 3, voz.rep.disp
r = voz.comando_salida("vuelve a hablar por el predeterminado")
assert voz.rep.disp is None and not os.path.exists(voz.PARLANTE_ARCHIVO)
assert voz.comando_salida("abre la calculadora") is None
assert voz.comando_salida("qué hora es") is None
voz.sd = SdSalida([x for x in SALIDAS if "TV" not in x["name"]])
r = voz.comando_salida("habla por la tele")
assert r.startswith("No encuentro un televisor"), r
assert voz._PREGUNTA_SALIDA.search("por dónde estás hablando")
print("salida de audio: 8 pruebas OK")
