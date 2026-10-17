# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = [
#   "numpy>=1.24,<2.3",
#   "sounddevice>=0.4.6",
#   "faster-whisper>=1.1",
#   "nvidia-cublas-cu12; sys_platform == 'win32'",
#   "nvidia-cudnn-cu12>=9,<10; sys_platform == 'win32'",
#   "openwakeword==0.6.0",
#   "onnxruntime>=1.17",
#   "edge-tts>=6.1",
#   "miniaudio>=1.59",
#   "httpx>=0.27",
#   "livekit>=1.0",
# ]
# ///
"""
Voz de Jarvis para Windows (v2: respuesta en streaming).

Escucha "Hey Jarvis", transcribe lo que dices, se lo pasa a Jarvis (que corre
en WSL con `jarvis serve`) y va leyendo la respuesta frase por frase MIENTRAS
Claude la escribe, con un reproductor continuo (sin cortes entre frases).

Uso (normalmente desde Jarvis.bat):
    uv run voz.py               # palabra de activación "Hey Jarvis"
    uv run voz.py --teclado     # Enter para hablar
    uv run voz.py --microfonos  # lista micrófonos
    uv run voz.py --prueba      # medidor de micrófono y "Hey Jarvis"
    uv run voz.py --voces       # escuchar voces para elegir

Atajo global (Windows): Ctrl+Alt+P (env JARVIS_ATAJO) = te escucho ya, sin decir "Orión";
si está hablando, se calla y escucha.
Estado para el HUD: tmp/estado.json (en la carpeta de Orión), se reescribe en cada cambio.
Avisos proactivos: lee avisos.json (lo escribe el cerebro) y los dice cuando no está hablando.
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import collections
import json
import os
import queue
import re
import sys
import threading
import time

import numpy as np
import sounddevice as sd

# ----------------------------------------------------------------- CONFIG
API = os.environ.get("JARVIS_API", "http://127.0.0.1:8787").rstrip("/")
API_KEY = os.environ.get("OPENJARVIS_API_KEY", "")
MODELO = os.environ.get("JARVIS_MODEL", "claude-sonnet-5-5")
VOZ = os.environ.get("JARVIS_VOZ", "en-US-AndrewMultilingualNeural")
VELOCIDAD = os.environ.get("JARVIS_VELOCIDAD", "+4%")
TONO = os.environ.get("JARVIS_TONO", "-2Hz")
# ElevenLabs (voces humanas, p. ej. acento colombiano). Si hay key y voz, se usa en vez de Microsoft.
ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVEN_VOZ = os.environ.get("ELEVENLABS_VOICE_ID", "")
ELEVEN_MODELO = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")   # o eleven_multilingual_v2 (más calidad, más lento)
ELEVEN_BASE = os.environ.get("ELEVENLABS_BASE", "https://api.elevenlabs.io")
# VoiceStudio (voz local y gratis en tu GPU): nombre del perfil de voz creado en la app.
VS_URL = os.environ.get("VOICESTUDIO_URL", "http://127.0.0.1:3900").rstrip("/")
VS_VOZ = os.environ.get("VOICESTUDIO_VOZ", "")
VS_MODELO = os.environ.get("VOICESTUDIO_MODELO", "omnivoice")
VS_ESTILO = os.environ.get("VOICESTUDIO_ESTILO", "")   # opcional, ej. "acento colombiano, tono cálido"
# Vacío = el mejor según el equipo: large-v3-turbo en GPU (mucho más preciso), small en CPU
STT_MODELO = os.environ.get("JARVIS_STT", "")                   # tiny/base/small/medium/large-v3-turbo
STT_DEVICE = os.environ.get("JARVIS_STT_DEVICE", "auto")        # auto (GPU si hay), cuda o cpu
UMBRAL_WAKE = float(os.environ.get("JARVIS_UMBRAL", "0.5"))
# "nombre": Whisper escucha y se activa al oír "Jarvis" (funciona con acento en español)
# "hey": detector openWakeWord "Hey Jarvis" (entrenado en inglés)
ACTIVACION = os.environ.get("JARVIS_ACTIVACION", "nombre")
NOMBRE = os.environ.get("JARVIS_NOMBRE", "Orión")
AGENTE = os.environ.get("JARVIS_AGENTE", "simple")
INTERRUPCION = os.environ.get("JARVIS_INTERRUPCION", "1") != "0"      # callarse si le hablas
SENS_INTERRUPCION = float(os.environ.get("JARVIS_SENS_INTERRUPCION", "3.2"))  # más alto = menos sensible
BLOQUES_INTERRUPCION = 4   # ~0,3 s de voz sostenida para interrumpir
RESUMEN_HORA = os.environ.get("JARVIS_RESUMEN_HORA", "07:00")   # "" para desactivar
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # Documentos\jarvis
RECORDATORIOS = os.path.join(RAIZ, "recordatorios.json")
AVISOS = os.path.join(RAIZ, "avisos.json")                            # avisos proactivos del cerebro
AVISOS_HORAS = float(os.environ.get("JARVIS_AVISOS_HORAS", "12"))     # más viejos: se marcan sin leerlos
ESTADO_ARCHIVO = os.path.join(RAIZ, "tmp", "estado.json")             # lo lee el HUD
ATAJO = os.environ.get("JARVIS_ATAJO", "ctrl+alt+p")                  # "" o "no" para desactivarlo
PROMPT_RESUMEN = (
    "[voz] Dame mi resumen de la mañana, breve y para escuchar, en este orden: "
    "1) mi agenda de hoy (agenda); "
    "2) los correos importantes no leídos de las últimas 24 horas (correos_recientes con consulta "
    "'is:unread newer_than:1d -category:promotions -category:social'), solo los que requieran mi atención; "
    "3) el estado de mis proyectos en producción (estado_vigilancia, si la tienes): di solo si todo está "
    "bien o qué está caído o con errores; "
    "4) mis recordatorios de hoy (listar_recordatorios); "
    "5) cambios sin subir en mis proyectos activos: en los proyectos de C:\\dev en los que trabajé en los "
    "últimos días (máximo 5) mira git status de solo lectura (git -C <carpeta> status -sb) y dime cuáles "
    "tienen cambios sin commit o commits sin push. "
    "Si una herramienta no está disponible o falla, sáltate esa parte sin explicar el error. Si una parte "
    "no tiene nada importante, dilo en pocas palabras o sáltala. Sin listas, ids ni enlaces; máximo 8 "
    "frases en total.")
SEGUIMIENTO = float(os.environ.get("JARVIS_SEGUIMIENTO", "10"))  # seg. para seguir hablando sin "Hey Jarvis"
MIC = os.environ.get("JARVIS_MIC")                              # índice o nombre del micrófono

SR = 16000          # Hz del micrófono (lo que esperan Whisper y openWakeWord)
CHUNK = 1280        # 80 ms por bloque
# seg. de silencio que cierran tu frase (si quedas a medias, "y…", "que…", sigue escuchando igual)
SILENCIO_FIN = float(os.environ.get("JARVIS_SILENCIO", "1.0"))
AVISO_ESPERA = float(os.environ.get("JARVIS_AVISO_ESPERA", "15"))   # seg. callado antes de decir "dame un momento" (0 = nunca)
TIC = os.environ.get("JARVIS_TIC", "1") != "0"   # tic suave al entenderte: sabes que ya está pensando
MAX_FRASE = 25      # seg. máximos por frase
HISTORIAL = 12      # mensajes que recuerda en la conversación hablada
SR_TTS = 24000      # Hz de la voz sintetizada
PAUSA_FRASE = 0.18  # seg. de silencio natural entre frases

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class _Tee:
    """Muestra en pantalla y además guarda en registro.log (para diagnosticar)."""

    def __init__(self, consola, archivo):
        self.consola, self.archivo = consola, archivo

    def write(self, txt):
        self.consola.write(txt)
        try:
            self.archivo.write(txt)
            self.archivo.flush()
        except Exception:  # noqa: BLE001
            pass

    def flush(self):
        self.consola.flush()

    def __getattr__(self, nombre):
        return getattr(self.consola, nombre)


if __name__ == "__main__":   # solo al correrlo (importarlo en pruebas no toca el registro)
    try:
        _log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "registro.log"),
                    "a", encoding="utf-8")
        _log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        sys.stdout = _Tee(sys.stdout, _log)
        sys.stderr = _Tee(sys.stderr, _log)
    except Exception:  # noqa: BLE001
        pass


def info(msg: str) -> None:
    print(f"  {msg}", flush=True)


# ----------------------------------------------------------------- Micrófono (por NOMBRE, no por número)
# Windows renumera los dispositivos al conectar/desconectar, así que se guarda el NOMBRE en
# voz/microfono.txt (lo escribe --elegir-mic). JARVIS_MIC=auto (o vacío) = predeterminado de Windows.
MIC_ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "microfono.txt")
_ULTIMO_AUDIO = [time.time()]
_MIC_ACTUAL = ["(sin micrófono)"]
_ORDEN_API = {"MME": 0, "Windows DirectSound": 1, "Windows WASAPI": 2, "Windows WDM-KS": 3}


def _norm_mic(nombre: str) -> str:
    return re.sub(r"\s+", " ", (nombre or "").strip().lower())


def _entradas() -> list:
    """[(índice, nombre, api)] de los micrófonos que ve Windows."""
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] >= 1:
            out.append((i, d["name"], apis[d["hostapi"]]["name"]))
    return out


def _sirve(i) -> bool:
    try:
        sd.check_input_settings(device=i, samplerate=SR, channels=1, dtype="int16")
        return True
    except Exception:  # noqa: BLE001
        return False


def mic_preferido() -> str:
    m = (MIC or "").strip()
    if m and m.lower() not in ("auto", "predeterminado", "default"):
        return m
    try:
        with open(MIC_ARCHIVO, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def resolver_mic() -> tuple:
    """(dispositivo para sounddevice o None, nombre legible, aviso)."""
    pref = mic_preferido()
    aviso = ""
    if pref.isdigit():                       # compatibilidad con el número viejo
        i = int(pref)
        if any(e[0] == i for e in _entradas()) and _sirve(i):
            return i, sd.query_devices(i)["name"], ""
        aviso = f"El micrófono número {pref} ya no existe; uso el predeterminado de Windows."
    elif pref:
        q = _norm_mic(pref)
        cands = [e for e in _entradas()
                 if _norm_mic(e[1]).startswith(q[:31]) or q.startswith(_norm_mic(e[1])) or q in _norm_mic(e[1])]
        cands.sort(key=lambda e: _ORDEN_API.get(e[2], 9))
        for i, nombre, _api in cands:
            if _sirve(i):
                return i, nombre, ""
        aviso = f"No encontré el micrófono «{pref}» (¿desconectado?); uso el predeterminado de Windows."
    try:
        nombre = sd.query_devices(kind="input")["name"]
    except Exception:  # noqa: BLE001
        nombre = "(ninguno)"
    return None, f"{nombre} (predeterminado de Windows)", aviso


def dispositivo_mic():
    return resolver_mic()[0]


class Microfono:
    """Abre el micrófono y lo vuelve a abrir solo si se desconecta o cambia."""

    def __init__(self) -> None:
        self.stream = None
        self.lock = threading.Lock()

    def abrir(self, refrescar: bool = False) -> None:
        with self.lock:
            if self.stream is not None:
                try:
                    self.stream.abort()
                    self.stream.close()
                except Exception:  # noqa: BLE001
                    pass
                self.stream = None
            if refrescar:                      # que PortAudio vea micrófonos nuevos o desconectados
                if rep is not None:
                    rep.pausar_salida()
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:  # noqa: BLE001
                    pass
                if rep is not None:
                    rep.reanudar_salida()
            disp, nombre, aviso = resolver_mic()
            if aviso:
                info(f"⚠️  {aviso}")
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK,
                                         callback=_cb, device=disp)
            self.stream.start()
            _ULTIMO_AUDIO[0] = time.time()
            _MIC_ACTUAL[0] = nombre
            info(f"🎙️  Micrófono: {nombre}")

    def vigilar(self) -> None:
        """Si dejan de llegar bloques de audio (micrófono desconectado), busca otro."""
        while True:
            time.sleep(1.0)
            if time.time() - _ULTIMO_AUDIO[0] < 3.0:
                continue
            info("⚠️  El micrófono dejó de responder (¿se desconectó?). Buscando otro…")
            try:
                self.abrir(refrescar=True)
            except Exception as e:  # noqa: BLE001
                info(f"   No pude abrir ningún micrófono ({e.__class__.__name__}); reintento en 3 s.")
                _ULTIMO_AUDIO[0] = time.time()

    def cerrar(self) -> None:
        with self.lock:
            if self.stream is not None:
                try:
                    self.stream.close()
                except Exception:  # noqa: BLE001
                    pass
                self.stream = None


# ----------------------------------------------------------------- Parlante / salida (por NOMBRE)
PARLANTE_ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parlante.txt")
_SALIDA_ACTUAL = ["el parlante predeterminado"]
_TIPOS_SALIDA = {
    "tv": r"\btv\b|televis|hdmi|nvidia high def|display audio|amd high def|\blg\b|samsung|sony|hisense|tcl|philips|roku|chromecast",
    "audifonos": r"headset|headphone|auricular|aud[ií]fono|hyperx|corsair|razer|airpods|buds|cascos|diadema|hands-free",
    "parlantes": r"speaker|altavoc|parlante|bocina|realtek",
}


def _salidas() -> list:
    apis = sd.query_hostapis()
    return [(i, d["name"], apis[d["hostapi"]]["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_output_channels"] >= 1]


def _sirve_salida(i) -> bool:
    try:
        sd.check_output_settings(device=i, samplerate=SR_TTS, channels=1, dtype="int16")
        return True
    except Exception:  # noqa: BLE001
        return False


def _buscar_salida(patron: str) -> tuple:
    rx = re.compile(patron, re.I)
    cands = [e for e in _salidas() if rx.search(e[1]) and "asignador" not in e[1].lower()
             and "sound mapper" not in e[1].lower()]
    cands.sort(key=lambda e: _ORDEN_API.get(e[2], 9))
    for i, nombre, _api in cands:
        if _sirve_salida(i):
            return i, nombre
    return None, ""


def resolver_salida() -> tuple:
    """(dispositivo o None, nombre, aviso) del parlante guardado en voz/parlante.txt (o JARVIS_PARLANTE)."""
    pref = (os.environ.get("JARVIS_PARLANTE") or "").strip()
    if not pref or pref.lower() in ("auto", "predeterminado", "default"):
        try:
            with open(PARLANTE_ARCHIVO, encoding="utf-8") as f:
                pref = f.read().strip()
        except OSError:
            pref = ""
    if pref:
        i, nombre = _buscar_salida(re.escape(_norm_mic(pref)[:31]))
        if i is not None:
            return i, nombre, ""
        aviso = f"No encontré la salida «{pref}» (¿apagada o desconectada?); uso el parlante predeterminado."
    else:
        aviso = ""
    try:
        nombre = sd.query_devices(kind="output")["name"]
    except Exception:  # noqa: BLE001
        nombre = "?"
    return None, f"{nombre} (predeterminado de Windows)", aviso


_CAMBIAR_SALIDA = re.compile(
    r"\b(habla[rs]?|h[aá]blame|hables|vuelve|regresa|esc[uú]ch(ate|es|e)|suen[ae]s?|sal(ga|gas)?|ponte|pon|cambia(te)?|p[aá]sate|"
    r"pasa|usa|conecta(te)?|reproduce|o[ií]rte|oigas?)\b"
    r".{0,30}?\b(televisor|tele|tv|parlantes?|altavoces|bocinas|aud[ií]fonos|diadema|cascos|"
    r"auriculares|headset|predeterminado|normal)\b", re.I)
_PREGUNTA_SALIDA = re.compile(r"por\s+d[oó]nde\s+(est[aá]s\s+)?(hablando|sonando|sales)|qu[eé]\s+(parlante|salida)", re.I)


def comando_salida(texto: str) -> str | None:
    """Si el texto pide cambiar la salida de audio, la cambia y devuelve qué decir. Si no, None."""
    m = _CAMBIAR_SALIDA.search(texto or "")
    if not m:
        return None
    destino = m.group(m.lastindex).lower()
    if destino in ("predeterminado", "normal"):
        try:
            os.remove(PARLANTE_ARCHIVO)
        except OSError:
            pass
        rep.cambiar_salida(None)
        _SALIDA_ACTUAL[0] = resolver_salida()[1]
        return "Listo, vuelvo al parlante predeterminado."
    tipo = ("tv" if re.match(r"tele|tv", destino) else
            "audifonos" if re.match(r"aud|diadema|cascos|auric|headset", destino) else "parlantes")
    i, nombre = _buscar_salida(_TIPOS_SALIDA[tipo])
    if i is None:
        que = {"tv": "un televisor", "audifonos": "unos audífonos", "parlantes": "unos parlantes"}[tipo]
        extra = " ¿Está prendido y conectado por HDMI?" if tipo == "tv" else ""
        return f"No encuentro {que} como salida de sonido.{extra}"
    if not rep.cambiar_salida(i):
        return f"Encontré {nombre}, pero Windows no me deja usarlo ahora."
    with open(PARLANTE_ARCHIVO, "w", encoding="utf-8") as f:
        f.write(nombre)
    _SALIDA_ACTUAL[0] = nombre
    info(f"🔊 Salida: {nombre}")
    corto = re.sub(r"\s*\(.*$", "", nombre) or nombre
    return f"Listo, ahora hablo por {corto}."


def elegir_salida() -> None:
    vistos, lista = set(), []
    for i, nombre, api in sorted(_salidas(), key=lambda e: _ORDEN_API.get(e[2], 9)):
        clave = _norm_mic(nombre)[:31]
        if clave in vistos or "asignador" in clave or "sound mapper" in clave or not _sirve_salida(i):
            continue
        vistos.add(clave)
        lista.append(nombre)
    print("\n  Salidas de sonido (por dónde habla Orión):\n")
    print("   [0] Automático: el parlante predeterminado de Windows")
    for n, nombre in enumerate(lista, 1):
        print(f"   [{n}] {nombre}")
    try:
        r = input("\n  Número de la salida (Enter = no cambiar): ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if r == "0":
        try:
            os.remove(PARLANTE_ARCHIVO)
        except OSError:
            pass
        print("  Listo: usaré el parlante predeterminado.")
    elif r.isdigit() and 1 <= int(r) <= len(lista):
        with open(PARLANTE_ARCHIVO, "w", encoding="utf-8") as f:
            f.write(lista[int(r) - 1])
        print(f"  Listo: Orión hablará por «{lista[int(r) - 1]}».")


def elegir_mic() -> None:
    """Menú para elegir el micrófono; guarda su NOMBRE en voz/microfono.txt."""
    vistos, lista = set(), []
    for i, nombre, api in sorted(_entradas(), key=lambda e: _ORDEN_API.get(e[2], 9)):
        clave = _norm_mic(nombre)[:31]
        if clave in vistos or "asignador de sonido" in clave or "sound mapper" in clave or not _sirve(i):
            continue
        vistos.add(clave)
        lista.append((i, nombre))
    try:
        defecto = sd.query_devices(kind="input")["name"]
    except Exception:  # noqa: BLE001
        defecto = "?"
    actual = mic_preferido() or "automático"
    print("\n  Micrófonos disponibles:\n")
    print(f"   [0] Automático: el predeterminado de Windows (ahora: {defecto})")
    for n, (_i, nombre) in enumerate(lista, 1):
        print(f"   [{n}] {nombre}")
    print(f"\n  Actual: {actual}")
    try:
        r = input("\n  Número del micrófono (Enter = no cambiar): ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not r:
        return
    if r == "0":
        try:
            os.remove(MIC_ARCHIVO)
        except OSError:
            pass
        print("  Listo: usaré el predeterminado de Windows.")
        return
    if r.isdigit() and 1 <= int(r) <= len(lista):
        nombre = lista[int(r) - 1][1]
        with open(MIC_ARCHIVO, "w", encoding="utf-8") as f:
            f.write(nombre)
        print(f"  Listo: usaré «{nombre}» (por nombre: no importa si Windows cambia los números).")
    else:
        print("  Número no válido; no cambié nada.")


# ----------------------------------------------------------------- Eventos (recordatorios y resumen)
eventos: "queue.Queue[tuple]" = queue.Queue()
_TZ = __import__("datetime").timezone(__import__("datetime").timedelta(hours=-5))  # Colombia, sin horario de verano


def _ahora_local():
    from datetime import datetime
    return datetime.now(_TZ)


# ----------------------------------------------------------------- Estado (para el HUD)
# tmp/estado.json: {"estado": ..., "usuario": último texto tuyo, "pedro": último texto de Orión, "t": epoch}
ESTADOS = ("escuchando", "oyendo", "pensando", "hablando", "dormido", "apagado")
_ESTADO = {"estado": "apagado", "usuario": "", "pedro": "", "t": 0.0}
_ESTADO_LOCK = threading.Lock()
_ESTADO_ULTIMO = [0.0]        # cuándo se escribió por última vez
_ESTADO_PENDIENTE = [False]   # texto parcial guardado en memoria pero aún no escrito (límite 5/s)
_DORMIDO = [False]            # "Orión, duérmete": solo despierta con su nombre (o el atajo)


def _recortar(txt: str, n: int = 400) -> str:
    txt = (txt or "").strip()
    return txt if len(txt) <= n else "…" + txt[-(n - 1):]


def _leer_json(ruta: str):
    """Lee un JSON; None si no existe, está a medio escribir o está dañado."""
    try:
        with open(ruta, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _escribir_json(ruta: str, datos, indent: int | None = None) -> bool:
    """Escritura atómica: temporal en la misma carpeta + os.replace. En Windows, os.replace
    falla si otro proceso tiene el archivo abierto justo en ese instante: reintenta un poco."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = f"{ruta}.{os.getpid()}-{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=indent)
    for intento in range(6):
        try:
            os.replace(tmp, ruta)
            return True
        except PermissionError:
            time.sleep(0.03 * (intento + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def poner_estado(estado: str | None = None, usuario: str | None = None, pedro: str | None = None,
                 forzar: bool = False) -> None:
    """Actualiza tmp/estado.json. Los cambios de estado se escriben al instante; el texto parcial
    de Orión (streaming) máximo 5 veces por segundo (lo que quede pendiente sale en la próxima)."""
    with _ESTADO_LOCK:
        antes = dict(_ESTADO)
        if estado is not None:
            _ESTADO["estado"] = estado
        if usuario is not None:
            _ESTADO["usuario"] = _recortar(usuario)
        if pedro is not None:
            _ESTADO["pedro"] = _recortar(pedro)
        cambio_estado = _ESTADO["estado"] != antes["estado"]
        cambio_texto = _ESTADO["usuario"] != antes["usuario"] or _ESTADO["pedro"] != antes["pedro"]
        if not (forzar or cambio_estado or cambio_texto or _ESTADO_PENDIENTE[0]):
            return
        ahora = time.time()
        if not (forzar or cambio_estado) and ahora - _ESTADO_ULTIMO[0] < 0.2:
            _ESTADO_PENDIENTE[0] = True
            return
        _ESTADO["t"] = ahora
        _ESTADO_ULTIMO[0] = ahora
        _ESTADO_PENDIENTE[0] = False
        try:
            _escribir_json(ESTADO_ARCHIVO, dict(_ESTADO))
        except OSError:
            pass


def estado_reposo() -> None:
    poner_estado("dormido" if _DORMIDO[0] else "escuchando")


PID_ARCHIVO = os.path.join(RAIZ, "tmp", "voz.pid")


def unica_instancia() -> None:
    """Si ya hay otra voz escuchando, la cierro: dos a la vez oyen lo mismo, mandan dos pedidos
    y el cerebro los atiende en fila (la respuesta tarda el doble y sale mezclada)."""
    try:
        viejo = int(open(PID_ARCHIVO, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        viejo = 0
    if viejo and viejo != os.getpid() and sys.platform == "win32":
        import subprocess
        # solo si ese PID sigue siendo un voz.py (Windows recicla los números de proceso)
        cmd = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={viejo}' -ErrorAction SilentlyContinue; "
               "if ($p -and $p.CommandLine -match 'voz\\.py') { taskkill /PID $p.ProcessId /T /F | Out-Null; 'cerrada' }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=15)
            if "cerrada" in r.stdout:
                info("Cerré otra voz de Orión que seguía abierta.")
        except Exception:  # noqa: BLE001
            pass
    try:
        os.makedirs(os.path.dirname(PID_ARCHIVO), exist_ok=True)
        with open(PID_ARCHIVO, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def _al_salir() -> None:
    if _ESTADO["t"]:   # solo si esta instancia llegó a escribir estado
        poner_estado("apagado", forzar=True)
    try:
        if open(PID_ARCHIVO, encoding="utf-8").read().strip() == str(os.getpid()):
            os.remove(PID_ARCHIVO)
    except (OSError, ValueError):
        pass


_MANEJADOR_CONSOLA: list = []


def _avisar_cierre_consola() -> None:
    """Windows: si cierran la ventana con la X, atexit no corre; esto deja 'apagado' igual."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        tipo = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def manejador(evento):
            if evento in (2, 5, 6):   # cerrar ventana, cerrar sesión, apagar Windows
                _al_salir()
            return False              # que Windows/Python sigan con lo normal (Ctrl+C incluido)

        f = tipo(manejador)
        _MANEJADOR_CONSOLA.append(f)  # que no lo recoja el recolector de basura
        ctypes.windll.kernel32.SetConsoleCtrlHandler(f, True)
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------- Avisos proactivos (avisos.json)
# Lista de {"id", "texto" (ya redactado para voz), "cuando" (epoch), "prioridad": "alta"|"normal", "leido"}
_AVISOS_DICHOS: set = set()   # ids ya dichos en esta sesión (por si otro proceso borra mi marca)


def elegir_avisos(lista, dormido: bool, ahora: float, dichos: set,
                  max_horas: float = AVISOS_HORAS) -> tuple[list, dict]:
    """Decide qué avisos sin leer se dicen ya. Devuelve (para_decir, marcas {id: cambios}).
    Dormido: solo los de prioridad alta (los normales esperan a que despierte)."""
    para_decir, marcas, vistos = [], {}, set()
    if not isinstance(lista, list):
        return para_decir, marcas
    for a in lista:
        if not isinstance(a, dict) or a.get("leido") or a.get("id") in (None, ""):
            continue
        id_ = str(a["id"])
        texto = str(a.get("texto") or "").strip()
        if id_ in dichos or id_ in vistos or not texto:
            marcas.setdefault(id_, {"leido": True})   # ya lo dije, repetido o vacío: solo marcarlo
            continue
        try:
            cuando = float(a.get("cuando") or ahora)
        except (TypeError, ValueError):
            cuando = ahora
        if cuando > ahora + 1:
            continue                                  # es para más tarde
        if max_horas and ahora - cuando > max_horas * 3600:
            marcas[id_] = {"leido": True, "caducado": True}   # muy viejo: no lo digo
            continue
        alta = str(a.get("prioridad") or "normal").strip().lower() == "alta"
        if dormido and not alta:
            continue
        vistos.add(id_)
        para_decir.append({"id": id_, "texto": texto, "alta": alta, "cuando": cuando})
        marcas[id_] = {"leido": True}
    para_decir.sort(key=lambda a: (not a["alta"], a["cuando"]))
    return para_decir, marcas


def fusionar_avisos(actual, marcas: dict) -> list:
    """Aplica las marcas sobre la versión MÁS RECIENTE del archivo, por id: no borra, no
    reordena y conserva los avisos que el cerebro agregó mientras tanto."""
    salida = []
    for a in actual if isinstance(actual, list) else []:
        if isinstance(a, dict) and a.get("id") not in (None, "") and str(a["id"]) in marcas:
            a = {**a, **marcas[str(a["id"])]}
        salida.append(a)
    return salida


def revisar_avisos() -> None:
    if not os.path.exists(AVISOS):
        return
    lista = _leer_json(AVISOS)
    if lista is None:
        return   # se está escribiendo o está dañado: lo intento en 5 s
    para_decir, marcas = elegir_avisos(lista, _DORMIDO[0], time.time(), _AVISOS_DICHOS)
    for a in para_decir:
        _AVISOS_DICHOS.add(a["id"])
        eventos.put(("aviso", a["texto"], a["alta"]))
    if not marcas:
        return
    for _ in range(3):   # releo justo antes de escribir: el cerebro pudo agregar avisos
        fresca = _leer_json(AVISOS)
        if isinstance(fresca, list):
            nueva = fusionar_avisos(fresca, marcas)
            if nueva != fresca:
                _escribir_json(AVISOS, nueva, indent=2)
            return
        time.sleep(0.1)
    # no pude releerlo: no escribo nada (no quiero pisar avisos nuevos); _AVISOS_DICHOS evita repetirlos


def _revisar_recordatorios() -> None:
    if not os.path.exists(RECORDATORIOS):
        return
    with open(RECORDATORIOS, encoding="utf-8") as f:
        lista = json.load(f)
    vencidos = [r for r in lista if not r.get("hecho") and r.get("cuando", 0) <= time.time()]
    if vencidos:
        for r in vencidos:
            r["hecho"] = True
            eventos.put(("recordatorio", r.get("texto", "")))
        tmp = RECORDATORIOS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)
        os.replace(tmp, RECORDATORIOS)


def _revisar_resumen(marca: str) -> None:
    if RESUMEN_HORA and AGENTE != "simple":
        ahora = _ahora_local()
        hoy = ahora.strftime("%Y-%m-%d")
        ultimo = open(marca).read().strip() if os.path.exists(marca) else ""
        if ultimo != hoy and RESUMEN_HORA <= ahora.strftime("%H:%M") < "12:00":
            open(marca, "w").write(hoy)
            eventos.put(("resumen", ""))


def _latido() -> None:
    """Refresca "t" cada ~10 s (si el HUD ve un "t" viejo, la voz se cayó) y escribe texto pendiente."""
    if _ESTADO["t"]:
        poner_estado(forzar=time.time() - _ESTADO_ULTIMO[0] > 10)


def vigia() -> None:
    """Cada 5 s: recordatorios vencidos, avisos del cerebro, resumen de la mañana (una vez al día)
    y latido del estado. Un fallo en una parte no detiene las demás."""
    marca = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ultimo_resumen.txt")
    while True:
        for tarea in (_revisar_recordatorios, revisar_avisos, lambda: _revisar_resumen(marca), _latido):
            try:
                tarea()
            except Exception as e:  # noqa: BLE001
                print(f"  (vigía: {e.__class__.__name__}: {e})", flush=True)
        time.sleep(5)


# ----------------------------------------------------------------- Micrófono
cola: "queue.Queue[np.ndarray]" = queue.Queue()


# ----------------------------------------------------------------- Cancelación de eco (como Alexa)
# Orión sabe exactamente qué está diciendo (la "referencia") y se lo resta al micrófono con el
# procesador de audio de WebRTC (el de Google Meet). Así no se escucha a sí mismo aunque hable
# por la TV, y lo puedes interrumpir hablando normal. JARVIS_AEC=0 lo apaga.
AEC_ON = os.environ.get("JARVIS_AEC", "1") != "0"


class CancelacionEco:
    MARCO_MIC = SR // 100            # 10 ms a 16 kHz
    MARCO_REF = SR_TTS // 100        # 10 ms a 24 kHz

    def __init__(self) -> None:
        from livekit import rtc

        self.rtc = rtc
        self.apm = rtc.AudioProcessingModule(echo_cancellation=True, noise_suppression=True,
                                             high_pass_filter=True, auto_gain_control=False)
        self.lock = threading.Lock()
        self.ref: collections.deque[np.ndarray] = collections.deque()
        self.resto = np.zeros(0, dtype=np.int16)
        self.silencio = np.zeros(self.MARCO_REF, dtype=np.int16)
        self.activo = True

    def ajustar_retraso(self, segundos: float) -> None:
        try:
            self.apm.set_stream_delay_ms(int(max(0.0, min(segundos, 0.5)) * 1000))
        except Exception:  # noqa: BLE001
            pass

    def referencia(self, bloque: np.ndarray) -> None:
        """Lo que acaba de salir por el parlante (24 kHz), en marcos de 10 ms."""
        if not self.activo:
            return
        datos = np.concatenate([self.resto, bloque]) if len(self.resto) else bloque
        n = len(datos) // self.MARCO_REF * self.MARCO_REF
        with self.lock:
            for i in range(0, n, self.MARCO_REF):
                self.ref.append(datos[i:i + self.MARCO_REF].copy())
            while len(self.ref) > 100:          # nunca más de 1 s de atraso
                self.ref.popleft()
        self.resto = datos[n:].copy()

    def limpiar(self, mic: np.ndarray) -> np.ndarray:
        """Devuelve el audio del micrófono sin la voz de Orión."""
        if not self.activo:
            return mic
        try:
            out = np.empty_like(mic)
            for i in range(0, len(mic) - self.MARCO_MIC + 1, self.MARCO_MIC):
                with self.lock:
                    ref = self.ref.popleft() if self.ref else self.silencio
                self.apm.process_reverse_stream(self.rtc.AudioFrame(ref.tobytes(), SR_TTS, 1, self.MARCO_REF))
                marco = self.rtc.AudioFrame(mic[i:i + self.MARCO_MIC].tobytes(), SR, 1, self.MARCO_MIC)
                self.apm.process_stream(marco)
                out[i:i + self.MARCO_MIC] = np.frombuffer(bytes(marco.data), dtype=np.int16)
            return out
        except Exception as e:  # noqa: BLE001
            self.activo = False
            info(f"⚠️  La cancelación de eco falló ({e.__class__.__name__}); sigo sin ella.")
            return mic


ECO: "CancelacionEco | None" = None


def _cerrar_eco() -> None:
    """Libera el procesador antes de que Python cierre (evita un aviso feo de livekit al salir)."""
    global ECO
    if ECO is not None:
        ECO.activo = False
        try:
            ECO.apm._ffi_handle.dispose()   # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        ECO = None
    try:
        from livekit.rtc._ffi_client import FfiHandle
        FfiHandle.__del__ = lambda self: None   # el proceso ya termina: no liberar dos veces
    except Exception:  # noqa: BLE001
        pass


def iniciar_eco() -> None:
    global ECO
    if not AEC_ON:
        info("Cancelación de eco: apagada (JARVIS_AEC=0)")
        return
    try:
        ECO = CancelacionEco()
        atexit.register(_cerrar_eco)
        info("Cancelación de eco: activa (no me escucho a mí mismo)")
    except Exception as e:  # noqa: BLE001
        ECO = None
        info(f"⚠️  Sin cancelación de eco ({e.__class__.__name__}: {e}); uso el modo anterior.")


def _cb(indata, frames, t, status):  # noqa: ARG001
    _ULTIMO_AUDIO[0] = time.time()
    bloque = indata[:, 0].copy()
    if ECO is not None:
        bloque = ECO.limpiar(bloque)
    cola.put(bloque)


def vaciar_cola() -> None:
    while not cola.empty():
        try:
            cola.get_nowait()
        except queue.Empty:
            break


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) if len(x) else 0.0


ruido = collections.deque(maxlen=40)  # ~3 s de nivel de fondo


def umbral_voz() -> float:
    base = float(np.median(ruido)) if ruido else 200.0
    return max(base * 3.0, 500.0)


def grabar_frase(espera_max: float, previo: list | None = None) -> np.ndarray | None:
    """Graba hasta que dejes de hablar. None si no hablaste en `espera_max` seg.
    `previo`: audio ya capturado (cuando interrumpiste a Orión) para no perder tu inicio."""
    partes: list[np.ndarray] = list(previo or [])
    hablo = bool(previo)
    if hablo and not _DORMIDO[0]:
        poner_estado("oyendo")
    inicio = time.time()
    ultimo_sonido = time.time()
    limite = umbral_voz()
    while True:
        bloque = cola.get()
        if _ATAJO.is_set():
            return None       # pulsaste el atajo: el bucle principal te escucha desde cero
        nivel = rms(bloque)
        ahora = time.time()
        if nivel > (limite * 0.55 if hablo else limite):   # ya hablando: oído más fino
            if not hablo:
                hablo = True
                inicio = ahora
                if not _DORMIDO[0]:
                    poner_estado("oyendo")
            ultimo_sonido = ahora
        if hablo:
            partes.append(bloque)
            if ahora - ultimo_sonido > SILENCIO_FIN or ahora - inicio > MAX_FRASE:
                break
        else:
            partes = (partes + [bloque])[-5:]  # guarda 400 ms previos al inicio
            ruido.append(nivel)
            if ahora - inicio > espera_max or not eventos.empty():
                return None
    return np.concatenate(partes)


# ----------------------------------------------------------------- Reproductor continuo
class Reproductor:
    """Un único stream de salida abierto todo el tiempo. Las frases se van
    encolando y suenan una tras otra sin cortes ni chasquidos."""

    def __init__(self, dispositivo=None) -> None:
        self._buf: collections.deque[np.ndarray] = collections.deque()
        self._actual = np.zeros(0, dtype=np.int16)
        self._lock = threading.Lock()
        self.dispositivo = dispositivo
        try:
            self._stream = self._abrir(dispositivo)
        except Exception as e:  # noqa: BLE001
            info(f"⚠️  No pude abrir el parlante elegido ({e.__class__.__name__}); uso el predeterminado.")
            self.dispositivo = None
            self._stream = self._abrir(None)

    def _abrir(self, dispositivo):
        st = sd.OutputStream(samplerate=SR_TTS, channels=1, dtype="int16", latency="high",
                             blocksize=2048, callback=self._cb, device=dispositivo)
        st.start()
        return st

    def cambiar_salida(self, dispositivo) -> bool:
        """Pasa la voz a otro parlante (TV, audífonos…) sin reiniciar."""
        self.pausar_salida()
        try:
            self._stream = self._abrir(dispositivo)
            self.dispositivo = dispositivo
            return True
        except Exception as e:  # noqa: BLE001
            info(f"⚠️  No pude usar esa salida ({e.__class__.__name__}); vuelvo a la anterior.")
            try:
                self._stream = self._abrir(self.dispositivo)
            except Exception:  # noqa: BLE001
                self.dispositivo = None
                self._stream = self._abrir(None)
            return False

    def pausar_salida(self) -> None:
        try:
            self._stream.abort()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass

    def reanudar_salida(self) -> None:
        try:
            disp, _n, _a = resolver_salida()        # tras refrescar, los números pudieron cambiar
            self._stream = self._abrir(disp)
            self.dispositivo = disp
        except Exception as e:  # noqa: BLE001
            info(f"⚠️  No pude reabrir el parlante ({e.__class__.__name__}).")
            try:
                self._stream = self._abrir(None)
                self.dispositivo = None
            except Exception:  # noqa: BLE001
                pass

    def _cb(self, outdata, frames, t, status):  # noqa: ARG002
        out = outdata[:, 0]
        n = 0
        with self._lock:
            while n < frames:
                if len(self._actual) == 0:
                    if not self._buf:
                        break
                    self._actual = self._buf.popleft()
                k = min(frames - n, len(self._actual))
                out[n:n + k] = self._actual[:k]
                self._actual = self._actual[k:]
                n += k
        out[n:] = 0
        if ECO is not None:
            ECO.referencia(out)

    def agregar(self, audio: np.ndarray) -> None:
        with self._lock:
            self._buf.append(audio)

    def callar(self) -> None:
        with self._lock:
            self._buf.clear()
            self._actual = np.zeros(0, dtype=np.int16)

    def ocupado(self) -> bool:
        with self._lock:
            return bool(self._buf) or len(self._actual) > 0

    def esperar(self) -> None:
        while self.ocupado():
            time.sleep(0.03)


rep: Reproductor | None = None


def beep(freq: float = 880, dur: float = 0.12) -> None:
    t = np.linspace(0, dur, int(SR_TTS * dur), endpoint=False)
    onda = (8000 * np.sin(2 * np.pi * freq * t) * np.hanning(len(t))).astype(np.int16)
    rep.agregar(onda)
    rep.esperar()


# ----------------------------------------------------------------- Oír (Whisper)
_BEAM = [1]   # en GPU sobra potencia: beam 5 entiende mejor nombres y frases raras


def _dlls_cuda() -> None:
    """Windows: cuBLAS/cuDNN vienen en los paquetes pip nvidia-*; hay que decirle a Python dónde están."""
    import glob
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    for raiz in (spec.submodule_search_locations if spec else []):
        for d in glob.glob(os.path.join(raiz, "*", "bin")):
            try:
                os.add_dll_directory(d)
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def cargar_stt():
    from faster_whisper import WhisperModel

    if STT_DEVICE in ("auto", "cuda"):
        _dlls_cuda()
        for modelo in dict.fromkeys([STT_MODELO or "large-v3-turbo", "small"]):
            try:
                m = WhisperModel(modelo, device="cuda", compute_type="float16")
                # la primera vez la GPU compila sus kernels (~20 s); después es inmediato
                list(m.transcribe(np.zeros(SR, dtype=np.float32), language="es")[0])
                _BEAM[0] = 5
                info(f"Oído: Whisper {modelo} en GPU")
                return m
            except Exception as e:  # noqa: BLE001
                info(f"Whisper {modelo} no cargó en GPU ({e.__class__.__name__}: {str(e)[:120]})")
        info("Uso la CPU para oír (más lento y entiende menos)")
    modelo = STT_MODELO or "small"
    m = WhisperModel(modelo, device="cpu", compute_type="int8")
    m.transcribe(np.zeros(SR, dtype=np.float32), language="es")  # calentar
    info(f"Oído: Whisper {modelo} en CPU")
    return m


# Vocabulario de Andres: ayuda a Whisper con nombres propios y términos técnicos.
VOCABULARIO = os.environ.get("JARVIS_VOCABULARIO", (
    "Orión, Claude Code, 18 Kilates, la joyería, CHECK, Onnyx, Onnyx Software, VS Code, "
    "Comet, Vercel, Railway, Supabase, Next.js, NestJS, Notion, Obsidian, Dropi, Motojaponesa, "
    "recordatorio, abre la carpeta, abre el proyecto, lanza Claude Code. "
    # respuestas cortas: sin contexto Whisper escribía "C," por "sí" y "Aslo" por "hazlo"
    "Sí, hazlo. Dale. Hágale. No. Para. Listo."))

# Correcciones de lo que Whisper suele escuchar mal. Las de frase completa (^…$) solo cambian
# una respuesta suelta, para no tocar palabras normales dentro de una frase.
# Agrega las tuyas en voz\correcciones.json: {"regex": "reemplazo"}.
_CORRECCIONES_BASE = {
    r"^\W*(?:c|ci|se|sii+|s[ií]\s*s[ií])\W*$": "Sí.",
    r"^\W*(?:aslo|azlo|haslo|hasl[oa]|as[ií] lo)\W*$": "Hazlo.",
    r"^\W*(?:h?[aá]rgalo|h?[aá]galo|h?[aá]gale)(\W+s[ií])?\W*$": r"Hágalo\1.",
    r"^\W*(?:dal[eé]|bale|vale\s+dale)\W*$": "Dale.",
    r"\b18\s+kil[oó]metros\b": "18 Kilates",
    r"\b(?:[oó]nix|onyx|oni[xs])\b": "Onnyx",
}


def _cargar_correcciones() -> list:
    reglas = dict(_CORRECCIONES_BASE)
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "correcciones.json")
    extra = _leer_json(ruta) if os.path.exists(ruta) else None
    if isinstance(extra, dict):
        reglas.update({k: v for k, v in extra.items() if not k.startswith("_") and isinstance(v, str)})
    out = []
    for patron, cambio in reglas.items():
        try:
            out.append((re.compile(patron, re.IGNORECASE), cambio))
        except re.error as e:
            info(f"⚠️  Corrección inválida en correcciones.json ({patron!r}): {e}")
    return out


def corregir(texto: str) -> str:
    for patron, cambio in _CORRECCIONES:
        texto = patron.sub(cambio, texto)
    return texto


_CORRECCIONES = _cargar_correcciones()


_A_MEDIAS = re.compile(
    r"(?:\b(?:y|e|o|u|que|de|del|la|el|los|las|un|una|en|con|por|para|pero|porque|entonces|"
    r"como|cuando|si|a|al|mi|tu|su|eh|em|mmm+|este|esto|osea|o sea|digamos|bueno)|,|…|\.\.\.)\W*$",
    re.IGNORECASE)


def frase_a_medias(texto: str) -> bool:
    """¿La frase parece cortada? (termina en 'y', 'que', 'de', 'eh', coma…)"""
    return bool(_A_MEDIAS.search(texto.strip()))


def escuchar_completo(stt, audio: np.ndarray) -> str:
    """Transcribe y, si quedaste a medias, sigue escuchando (hasta 2 veces más)."""
    texto = transcribir(stt, audio)
    for _ in range(2):
        if not texto or not frase_a_medias(texto):
            break
        info("(sigo escuchando…)")
        resto = grabar_frase(espera_max=2.5)
        if resto is None:
            break
        audio = np.concatenate([audio, resto])
        texto = transcribir(stt, audio)
    return texto


# Frases que Whisper "alucina" con ruido, música o silencio (vienen de subtítulos de YouTube)
_ALUCINACION = re.compile(
    r"^\W*(?:¡?suscr[ií]bete.*|gracias(?: por ver.*)?|subt[ií]tulos.*|amara\.org.*|"
    r"hasta la pr[oó]xima.*|m[uú]sica|aplausos|risas|\.+)\W*$", re.IGNORECASE)
# Respuestas de una palabra que sí cuentan (p. ej. el "sí" que confirma un commit)
_CORTA = re.compile(r"^\W*(s[ií]|no|dale|h[aá]zlo|h[aá]gal[oe](\W+s[ií])?|claro|ok|okay|vale|correcto|exacto|perfecto|"
                    r"ese|esa|ambos|ambas|todos|ninguno|primero|segundo|tercero|uno|dos|tres)\W*$",
                    re.IGNORECASE)
_CONFIANZA = [0.0]   # avg_logprob de la última transcripción (0 = segura; < -0,8 = dudosa)
CONFIANZA_MIN = float(os.environ.get("JARVIS_CONFIANZA_MIN", "-0.75"))


def transcribir(stt, audio: np.ndarray) -> str:
    f = audio.astype(np.float32) / 32768.0
    segs, _ = stt.transcribe(f, language="es", beam_size=_BEAM[0], vad_filter=True,
                             initial_prompt=VOCABULARIO, condition_on_previous_text=False)
    textos, peso, suma = [], 0, 0.0
    for s in segs:
        if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:   # regla de Whisper: no era voz
            continue
        t = s.text.strip()
        if not t or _ALUCINACION.match(t):
            continue
        textos.append(t)
        peso += len(t)
        suma += s.avg_logprob * len(t)
    _CONFIANZA[0] = suma / peso if peso else -9.0
    return corregir(" ".join(textos).strip())


def para_mi(texto: str) -> str | None:
    """Frase dicha SIN el nombre (seguimiento o interrupción): ¿parece para Orión?
    None = ruido de fondo (juego, TV, otra persona); si no, el texto marcado para que el
    cerebro decida si responde o lo ignora."""
    dijo, resto = buscar_nombre(texto)
    if dijo:
        return resto or None
    if len(texto.split()) < 2 and not _CORTA.match(texto):
        return None
    if _CONFIANZA[0] < CONFIANZA_MIN:
        info(f"(transcripción dudosa {_CONFIANZA[0]:.2f}: parece ruido de fondo)")
        return None
    return f"[voz][sin nombre] {texto}"


# ----------------------------------------------------------------- Hablar (voz neural)
_ABREV = [
    (r"\bp\.\s?ej\.", "por ejemplo"), (r"\betc\.", "etcétera"), (r"\bvs\.?(?=\s)", "contra"),
    (r"\bapro?x\.", "aproximadamente"), (r"\bSr\.", "señor"), (r"\bSra\.", "señora"),
    (r"\bDr\.", "doctor"), (r"\bpág\.", "página"), (r"\bnúm\.", "número"), (r"\bmin\.", "minutos"),
    (r"\bseg\.", "segundos"), (r"\bEE\.?\s?UU\.?", "Estados Unidos"), (r"\bq\b", "que"),
]
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def _hora(m: re.Match) -> str:
    h, mi, suf = int(m.group(1)), m.group(2), (m.group(3) or "").lower().replace(".", "")
    tarde = suf == "pm" or (not suf and h >= 13)
    h12 = h % 12 or 12
    momento = ""
    if suf or h >= 13:
        momento = " de la noche" if tarde and h12 >= 7 and h12 != 12 else (
            " de la tarde" if tarde else (" de la mañana" if h12 != 12 else " del mediodía"))
    minutos = "" if not mi or mi == "00" else (" y media" if mi == "30" else f" y {int(mi)}")
    return f"{h12}{minutos}{momento}"


def limpiar_para_voz(txt: str) -> str:
    """Deja el texto como lo diría una persona: sin markdown, emojis ni símbolos."""
    txt = re.sub(r"```.*?```", " (te dejé código en pantalla) ", txt, flags=re.S)
    txt = re.sub(r"`([^`]*)`", r"\1", txt)
    txt = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", txt)
    txt = re.sub(r"https?://\S+", "el enlace", txt)
    txt = re.sub(r"\[\[([^\]|]+)(\|[^\]]*)?\]\]", r"\1", txt)      # [[nota]] de Obsidian
    txt = _EMOJI.sub("", txt)
    txt = re.sub(r"^\s*(\d+[.)]|[-•*])\s+", "", txt, flags=re.M)   # viñetas y listas numeradas
    txt = re.sub(r"[*_#>|~]+", "", txt)
    for patron, dicho in _ABREV:
        txt = re.sub(patron, dicho, txt, flags=re.I)
    txt = re.sub(r"\b(\d{1,2}):(\d{2})\s*([ap]\.?\s?m\.?)?(?![\w:])", _hora, txt, flags=re.I)
    txt = re.sub(r"\b(\d{1,2})()\s*([ap]\.?\s?m\.?)(?!\w)", _hora, txt, flags=re.I)
    txt = re.sub(r"(\d)\s*%", r"\1 por ciento", txt)
    txt = re.sub(r"\$\s?(\d[\d.,]*)", r"\1 pesos", txt)
    txt = re.sub(r"\s*&\s*", " y ", txt)
    txt = re.sub(r"\s+[–—-]\s+", ", ", txt)                       # guiones de inciso -> pausa
    txt = re.sub(r"\s*\(([^)]{1,80})\)", r", \1,", txt)             # paréntesis -> inciso hablado
    txt = re.sub(r"\s+/\s+|\by/o\b", " o ", txt)
    txt = re.sub(r"\s*\n+\s*", ". ", txt)
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"\s+([,.;:!?])", r"\1", txt)
    txt = re.sub(r"([:;!?])\s*\.(?!\.)", r"\1", txt)
    txt = re.sub(r"([,;:])\1+|,\s*([.!?])", lambda m: m.group(1) or m.group(2), txt)
    txt = re.sub(r"\.{2,}(?!\.)", "…", txt)
    return re.sub(r"(?:[.,]\s*){2,}", ". ", txt).strip(" ,")


async def _tts_mp3(texto: str, voz: str) -> bytes:
    import edge_tts

    buf = bytearray()
    async for parte in edge_tts.Communicate(texto, voz, rate=VELOCIDAD, pitch=TONO).stream():
        if parte["type"] == "audio":
            buf.extend(parte["data"])
    return bytes(buf)


_ELEVEN_FALLO = [False]
_VS_FALLO = [False]


def _voicestudio_pcm(texto: str) -> np.ndarray:
    import httpx

    cuerpo = {"model": VS_MODELO, "input": texto, "voice": VS_VOZ, "response_format": "pcm"}
    if VS_ESTILO:
        cuerpo["instructions"] = VS_ESTILO
    r = httpx.post(f"{VS_URL}/v1/audio/speech", json=cuerpo, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"VoiceStudio respondió {r.status_code}: {r.text[:200]}")
    return np.frombuffer(r.content, dtype="<i2").astype(np.int16)   # 24 kHz, 16 bits, mono


def _eleven_pcm(texto: str) -> np.ndarray:
    import httpx

    r = httpx.post(f"{ELEVEN_BASE}/v1/text-to-speech/{ELEVEN_VOZ}",
                   params={"output_format": "pcm_24000"},
                   headers={"xi-api-key": ELEVEN_KEY, "content-type": "application/json"},
                   json={"text": texto, "model_id": ELEVEN_MODELO,
                         "voice_settings": {"stability": 0.45, "similarity_boost": 0.8,
                                            "style": 0.15, "use_speaker_boost": True}},
                   timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs respondió {r.status_code}: {r.text[:200]}")
    return np.frombuffer(r.content, dtype="<i2").astype(np.int16)


def sintetizar(texto: str, voz: str | None = None) -> np.ndarray:
    if VS_VOZ and voz is None and not _VS_FALLO[0]:
        try:
            audio = _voicestudio_pcm(texto)
        except Exception as e:  # noqa: BLE001
            _VS_FALLO[0] = True   # no reintentar en cada frase
            info(f"⚠️  VoiceStudio falló ({e}); uso la voz de Microsoft. ¿Está abierta la app?")
            return sintetizar(texto, VOZ)
    elif ELEVEN_KEY and ELEVEN_VOZ and voz is None and not _ELEVEN_FALLO[0]:
        try:
            audio = _eleven_pcm(texto)
        except Exception as e:  # noqa: BLE001
            _ELEVEN_FALLO[0] = True   # no reintentar en cada frase: vuelve a la voz de Microsoft
            info(f"⚠️  ElevenLabs falló ({e}); uso la voz de Microsoft.")
            return sintetizar(texto, VOZ)
    else:
        import miniaudio

        mp3 = asyncio.run(_tts_mp3(texto, voz or VOZ))
        dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=1, sample_rate=SR_TTS)
        audio = np.array(dec.samples, dtype=np.int16)
    # recorta silencios de los extremos y añade una pausa natural corta
    voz_idx = np.nonzero(np.abs(audio) > 200)[0]
    if len(voz_idx):   # deja respirar el inicio (50 ms) y la cola (100 ms) para no comerse sílabas
        audio = audio[max(0, voz_idx[0] - 1200): voz_idx[-1] + 2400]
    audio = audio.astype(np.float32)
    n = min(len(audio) // 2, 240)                      # fundido de 10 ms: sin chasquidos
    if n:
        rampa = np.linspace(0.0, 1.0, n, dtype=np.float32)
        audio[:n] *= rampa
        audio[-n:] *= rampa[::-1]
    audio = audio.astype(np.int16)
    pausa = PAUSA_FRASE * (1.6 if texto.rstrip().endswith(("?", "!", ".", "…")) else 0.6)
    return np.concatenate([audio, np.zeros(int(SR_TTS * pausa), dtype=np.int16)])


def decir(texto: str) -> None:
    """Frase corta fija (avisos). Bloquea hasta terminar."""
    poner_estado("hablando", pedro=texto)
    try:
        rep.agregar(sintetizar(texto))
        rep.esperar()
    except Exception as e:  # noqa: BLE001
        info(f"⚠️  No pude generar la voz ({e.__class__.__name__}). ¿Hay internet?")
    finally:
        estado_reposo()


# ----------------------------------------------------------------- Partir en frases
_FIN_FRASE = re.compile(r"([.!?…;](?=\s)|:(?=\s)|\n)")
_NO_FIN = re.compile(r"(\b(?:p\. ?ej|etc|Sr|Sra|Dr|aprox|pág|núm|vs|[A-Z])\.|\d\.)$", re.I)


def extraer_frases(buf: str, primera: bool) -> tuple[list[str], str]:
    """Agrupa oraciones COMPLETAS en bloques de buen tamaño. Cortar a mitad de
    oración (o en trozos muy cortos) rompe la entonación y hace que la voz
    multilingüe a veces cambie de acento. La primera oración sale sola para
    empezar a hablar rápido; las siguientes se juntan hasta ~180 caracteres."""
    frases: list[str] = []
    minimo = 20 if primera else 140
    inicio = 0
    for m in _FIN_FRASE.finditer(buf):
        corte = m.end()
        trozo = buf[inicio:corte].strip()
        if _NO_FIN.search(trozo):
            continue               # "p. ej." o "2." no terminan la oración
        if len(trozo) >= minimo:
            frases.append(trozo)
            inicio = corte
            minimo = 140
    resto = buf[inicio:]
    if len(resto) > 320:           # oración larguísima sin punto: corta en la última coma
        c = resto.rfind(", ", 0, 300)
        if c > 60:
            frases.append(resto[:c + 1].strip())
            resto = resto[c + 1:]
    return frases, resto


# ----------------------------------------------------------------- Cerebro (Jarvis en WSL)
historial: list[dict] = []
_INTERRUMPIDA = [False]   # ¿la última respuesta fue cortada por Andres?
_MARCA = re.compile(r"\s*\[Andres me interrumpió aquí\]")
_IGNORO = re.compile(r"^\s*\[ignoro\]", re.IGNORECASE)   # el cerebro decidió que no le hablaban a él
_IGNORADA = [False]   # no sigo escuchando sin nombre tras esto (era ruido, o me pediste parar)
_STT: list = [None]   # Whisper, para oír "Orión, para" mientras pienso
HISTORIAL_ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historial.json")
HISTORIAL_HORAS = float(os.environ.get("JARVIS_HISTORIAL_HORAS", "8"))


def cargar_historial() -> None:
    """Retoma la conversación anterior si fue hace menos de HISTORIAL_HORAS."""
    try:
        with open(HISTORIAL_ARCHIVO, encoding="utf-8") as f:
            datos = json.load(f)
        if time.time() - datos.get("guardado", 0) < HISTORIAL_HORAS * 3600:
            for m in datos.get("mensajes", [])[-HISTORIAL:]:
                historial.append({**m, "content": _MARCA.sub("", m.get("content", ""))})
            info(f"Memoria: retomo la conversación anterior ({len(historial)} mensajes).")
    except (OSError, ValueError):
        pass


def guardar_historial() -> None:
    try:
        tmp = HISTORIAL_ARCHIVO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"guardado": time.time(), "mensajes": historial[-HISTORIAL:]}, f,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, HISTORIAL_ARCHIVO)
    except OSError:
        pass


def stream_respuesta(cancelar: threading.Event):
    """Genera los trozos de texto a medida que Claude los escribe."""
    import httpx

    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    cuerpo = {"model": MODELO, "messages": historial[-HISTORIAL:], "max_tokens": 600,
              "stream": True}
    with httpx.stream("POST", f"{API}/v1/chat/completions", headers=headers,
                      json=cuerpo, timeout=httpx.Timeout(120, connect=10)) as r:
        r.raise_for_status()
        for linea in r.iter_lines():
            if cancelar.is_set():
                return
            if not linea.startswith("data:"):
                continue
            dato = linea[5:].strip()
            if dato == "[DONE]":
                return
            try:
                delta = json.loads(dato)["choices"][0].get("delta", {}).get("content")
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            if delta:
                yield delta


def jarvis_en_linea() -> bool:
    import httpx

    try:
        return httpx.get(f"{API}/health", timeout=5).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def responder(texto: str, wake) -> list | None:
    """Pide la respuesta en streaming y la va diciendo frase por frase.
    Si dices "Hey Jarvis" mientras habla, se calla."""
    marca = next((m for m in ("[voz][sin nombre]", "[voz]") if texto.startswith(m)), "")
    cuerpo = texto[len(marca):].strip()
    marca = marca or "[voz]"
    if _INTERRUMPIDA[0]:
        marca += " (te interrumpí mientras hablabas)"
        _INTERRUMPIDA[0] = False
    contenido = f"{marca} {cuerpo}"
    historial.append({"role": "user", "content": contenido})
    poner_estado("pensando", pedro="")
    cancelar = threading.Event()
    frases_q: "queue.Queue[str | None]" = queue.Queue()
    completo: list[str] = []
    error: list[Exception] = []
    t0 = time.time()
    primer_audio: list[float] = []
    tiempos: dict[str, float] = {}

    def productor():
        buf, primera = "", True
        try:
            for trozo in stream_respuesta(cancelar):
                if not completo:
                    tiempos["primera_palabra"] = time.time() - t0
                completo.append(trozo)
                print(trozo, end="", flush=True)
                poner_estado(pedro="".join(completo))   # para el HUD (máx. 5 escrituras/s)
                buf += trozo
                frases, buf = extraer_frases(buf, primera)
                for f in frases:
                    primera = False
                    frases_q.put(f)
            if buf.strip():
                frases_q.put(buf.strip())
            tiempos["respuesta_completa"] = time.time() - t0
        except Exception as e:  # noqa: BLE001
            error.append(e)
        finally:
            frases_q.put(None)

    def locutor():
        while not cancelar.is_set():
            f = frases_q.get()
            if f is None:
                return
            if _IGNORO.match(f):
                continue
            limpio = limpiar_para_voz(f)
            if not limpio:
                continue
            try:
                t_voz = time.time()
                audio = sintetizar(limpio)
                tiempos.setdefault("voz_1a_frase", time.time() - t_voz)
            except Exception as e:  # noqa: BLE001
                error.append(e)
                continue
            if not cancelar.is_set():
                if not primer_audio:
                    primer_audio.append(time.time() - t0)
                    poner_estado("hablando")
                rep.agregar(audio)

    print(f"  {NOMBRE}: ", end="", flush=True)
    t_prod = threading.Thread(target=productor, daemon=True)
    t_loc = threading.Thread(target=locutor, daemon=True)
    t_prod.start()
    t_loc.start()
    if TIC:
        beep(1175, 0.05)   # después de lanzar el pedido: no le resta tiempo

    vaciar_cola()
    interrumpido = False
    previo: list | None = None          # audio tuyo si me interrumpiste hablando
    recientes = collections.deque(maxlen=8)
    eco: list[float] = []               # nivel de mi propia voz en el micrófono
    seguidos = 0
    try:
        import msvcrt  # Windows: detectar teclas sin bloquear
    except ImportError:
        msvcrt = None
    oida: list = []                     # mientras pienso: lo que vas diciendo, por si es "Orión, para"
    callado = 0
    parado = False
    avisos_espera = [(AVISO_ESPERA, "Dame un momento, estoy en eso."),
                     (AVISO_ESPERA * 3, "Sigo trabajando en eso, ya casi.")] if AVISO_ESPERA > 0 else []
    while t_loc.is_alive() or rep.ocupado():
        # Si el cerebro lleva rato encadenando herramientas sin decir nada, aviso que sigo vivo
        # (no entra al historial: es solo para que no parezca colgado)
        if avisos_espera and not completo and time.time() - t0 > avisos_espera[0][0]:
            frases_q.put(avisos_espera.pop(0)[1])
        if _ATAJO.is_set():             # atajo de teclado: me callo y te escucho (turno lo limpia)
            cancelar.set()
            rep.callar()
            interrumpido = True
            previo = []
            break
        if msvcrt is not None and msvcrt.kbhit():
            while msvcrt.kbhit():
                msvcrt.getwch()
            cancelar.set()
            rep.callar()
            interrumpido = True
            break
        try:
            bloque = cola.get(timeout=0.1)
        except queue.Empty:
            continue
        recientes.append(bloque)
        # Mientras pienso, el ruido NO me interrumpe (un juego o la TV cancelaban el pedido),
        # pero escucho por si me dices "Orión, para": transcribo cada frase al terminar.
        if not (primer_audio or rep.ocupado()) and _STT[0] is not None:
            if rms(bloque) > umbral_voz():
                if not oida:
                    oida = list(recientes)[-6:-1]   # 400 ms antes, para no cortar el "Orión"
                oida.append(bloque)
                callado = 0
            elif oida:
                oida.append(bloque)
                callado += 1
                if callado >= 8 or len(oida) > 75:   # ~0,6 s de silencio, o 6 s seguidos
                    if len(oida) >= 10 and orden_de_parar(transcribir(_STT[0], np.concatenate(oida))):
                        cancelar.set()
                        rep.callar()
                        parado = True
                        break
                    oida, callado = [], 0
        if INTERRUPCION and (primer_audio or rep.ocupado()):
            nivel = rms(bloque)
            if rep.ocupado() and len(eco) < 6:
                eco.append(nivel)       # primeros ~0,5 s hablando: aprendo cuánto me oigo a mí mismo
                continue
            base = float(np.median(eco)) if eco else 0.0
            # con cancelación de eco mi voz ya casi no llega: basta con que hables normal
            limite = max(base * SENS_INTERRUPCION, umbral_voz() * (1.0 if ECO is not None and ECO.activo else 1.5))
            seguidos = seguidos + 1 if nivel > limite else 0
            if seguidos >= BLOQUES_INTERRUPCION:
                cancelar.set()
                rep.callar()
                interrumpido = True
                previo = list(recientes)
                break
        if wake is not None and max(wake.predict(bloque).values()) > UMBRAL_WAKE:
            cancelar.set()
            rep.callar()
            wake.reset()
            interrumpido = True
            break
    print("", flush=True)
    if primer_audio:
        info(f"(empezó a hablar en {primer_audio[0]:.1f}s · Claude 1a palabra "
             f"{tiempos.get('primera_palabra', 0):.1f}s · respuesta completa "
             f"{tiempos.get('respuesta_completa', 0):.1f}s · voz 1a frase "
             f"{tiempos.get('voz_1a_frase', 0):.1f}s)")
    if interrumpido:
        info("(atajo: te escucho)" if previo == [] else
             "(me interrumpiste: te escucho)" if previo else "(interrumpido)")
    respuesta = "".join(completo)
    if parado:
        # "Orión, para" mientras pensaba: corto el pedido (el cerebro detiene sus herramientas)
        info("(me pediste parar: corto lo que estaba haciendo)")
        _IGNORADA[0] = True            # no sigo escuchando sin nombre después de esto
        historial.pop()
        poner_estado(pedro="", forzar=True)
        vaciar_cola()
        decir("Listo, paré.")
        return None
    if _IGNORO.match(respuesta):
        # el cerebro juzgó que no era para él: no queda en la conversación ni sigo escuchando
        info("(no era para mí: lo ignoro)")
        _IGNORADA[0] = True
        historial.pop()
        poner_estado(pedro="", forzar=True)
        vaciar_cola()
        return None
    poner_estado(pedro=respuesta, forzar=True)
    if respuesta:
        historial.append({"role": "assistant", "content": respuesta})
        _INTERRUMPIDA[0] = interrumpido
        guardar_historial()
    else:
        historial.pop()  # no guardes una pregunta sin respuesta
    if error and not respuesta:
        info(f"⚠️  No pude hablar con Jarvis: {error[0]}")
        decir("No pude comunicarme con el servidor.")
    print("", flush=True)
    if not previo:
        vaciar_cola()   # si me interrumpiste hablando, NO borro lo que sigues diciendo
    return previo


# ----------------------------------------------------------------- Activación por nombre
def _patron_nombre(nombre: str) -> str:
    """Regex tolerante a cómo Whisper escribe el nombre (tildes, espacios, variantes)."""
    variantes = {"orion": r"o\s?r[iíy]+[oóa]n|oreon|o'?ryan"}
    base = "".join(c for c in __import__("unicodedata").normalize("NFD", nombre.lower())
                   if __import__("unicodedata").category(c) != "Mn")
    if base in variantes:
        return variantes[base]
    clases = {"a": "[aá]", "e": "[eé]", "i": "[ií]", "o": "[oó]", "u": "[uúü]"}
    return "".join(clases.get(c, re.escape(c)) for c in base)


_NOMBRE = re.compile(r"\b(?:oye|hey|ey|ok|okay|hola)?[\s,]*(?:" + _patron_nombre(NOMBRE) + r")\b[\s,.:!¡?¿-]*",
                     re.IGNORECASE)


_PARAR = re.compile(
    r"^\W*(?:ya\W+)?(para|párale|parale|detente|cállate|callate|silencio|stop|basta|cancela|"
    r"olvídalo|olvidalo|nada|está bien|esta bien|ya está|ya esta|eso es todo|listo|"
    r"espera|esp[eé]rate|alto|frena|det[eé]n(?:te)?|no es eso|eso no es|eso no|"
    r"te equivocaste|entendiste mal|no entendiste)"
    r"(?:\W+(?:gracias|por favor|pedro|ori[oó]n|ya))*\W*$", re.IGNORECASE)
_DORMIR = re.compile(r"\b(duérmete|duermete|a dormir|modo silencio|en pausa|pausa|pausado|"
                     r"descansa|no escuches|deja de escuchar)\b", re.IGNORECASE)
_DESPERTAR = re.compile(r"\b(despierta|despiértate|despiertate|vuelve|activa|actívate|"
                        r"activate|estás ahí|estas ahi)\b", re.IGNORECASE)


_PREGUNTA_MIC = re.compile(r"\b(qu[eé]|cu[aá]l)\s+micr[oó]fono|micr[oó]fono\s+(est[aá]s|estas)\s+usando", re.I)


def es_parar(texto: str) -> bool:
    """"para", "Orión, para", "no, espera, Orión", "te equivocaste"… (toda la frase es la orden)."""
    def sin_no(s: str) -> str:
        return re.sub(r"^\W*no\W+", "", s, flags=re.IGNORECASE)

    for t in dict.fromkeys([texto.strip(), sin_no(texto.strip())]):
        m = _NOMBRE.match(t)
        if m:
            t = t[m.end():]
        if _PARAR.match(t) or _PARAR.match(sin_no(t)):
            return True
    return False


def orden_de_parar(texto: str) -> bool:
    """Mientras pienso solo paro si dices mi nombre (un juego o la TV también dicen "para")."""
    dijo, _ = buscar_nombre(texto)
    if dijo and es_parar(texto):
        info(f"(oí: {texto!r})")
        return True
    return False


def buscar_nombre(texto: str) -> tuple[bool, str]:
    """¿Dijo el nombre? Devuelve (sí/no, lo que dijo después del nombre)."""
    m = _NOMBRE.search(texto)
    if not m:
        return False, ""
    return True, texto[m.end():].strip()


# ----------------------------------------------------------------- Atajo de teclado global (Windows)
_ATAJO = threading.Event()      # se pulsó el atajo: escuchar ya (y callar a Orión si está hablando)
_ATAJO_ACTIVO = [False]
_MODIFICADORES = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "mayus": 0x4, "win": 0x8}
_TECLAS_ATAJO = {"space": 0x20, "espacio": 0x20, "pause": 0x13, "pausa": 0x13, "insert": 0x2D,
                 "home": 0x24, "inicio": 0x24, "end": 0x23, "fin": 0x23, "scrolllock": 0x91}


def parsear_atajo(texto: str) -> tuple[int, int] | None:
    """'ctrl+alt+p' -> (modificadores, tecla virtual) para RegisterHotKey. None = desactivado.
    ValueError si no se entiende."""
    t = re.sub(r"\s+", "", (texto or "").lower())
    if t in ("", "0", "no", "off", "ninguno", "desactivado"):
        return None
    partes = t.split("+")
    if any(not p for p in partes):
        raise ValueError("formato: ctrl+alt+p")
    *mods_txt, tecla = partes
    mods = 0
    for m in mods_txt:
        if m not in _MODIFICADORES:
            raise ValueError(f"modificador desconocido '{m}' (usa ctrl, alt, shift o win)")
        mods |= _MODIFICADORES[m]
    if len(tecla) == 1 and tecla.isascii() and tecla.isalnum():
        vk = ord(tecla.upper())
    elif re.fullmatch(r"f(?:[1-9]|1\d|2[0-4])", tecla):
        vk = 0x70 + int(tecla[1:]) - 1
    elif tecla in _TECLAS_ATAJO:
        vk = _TECLAS_ATAJO[tecla]
    else:
        raise ValueError(f"tecla desconocida '{tecla}'")
    if not mods and not 0x70 <= vk <= 0x87:
        raise ValueError("una tecla normal necesita ctrl, alt, shift o win")
    return mods, vk


def _hilo_atajo(mods: int, vk: int, listo: threading.Event) -> None:
    """RegisterHotKey + bucle de mensajes propio (el atajo pertenece a este hilo)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                       wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        if not user32.RegisterHotKey(None, 1, mods | 0x4000, vk):   # 0x4000 = sin autorrepetición
            err = ctypes.get_last_error()
            motivo = "ya lo usa otro programa" if err == 1409 else f"error {err}"
            info(f"⚠️  No pude registrar el atajo {ATAJO} ({motivo}). Sigo sin atajo; "
                 "cámbialo con JARVIS_ATAJO.")
            return
        _ATAJO_ACTIVO[0] = True
        listo.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:   # WM_HOTKEY
                _ATAJO.set()
    except Exception as e:  # noqa: BLE001
        info(f"⚠️  Atajo de teclado no disponible ({e.__class__.__name__}: {e}).")
    finally:
        listo.set()


def iniciar_atajo() -> None:
    try:
        teclas = parsear_atajo(ATAJO)
    except ValueError as e:
        info(f"⚠️  JARVIS_ATAJO='{ATAJO}' no es válido ({e}). Sigo sin atajo.")
        return
    if teclas is None:
        return
    if sys.platform != "win32":
        info("⚠️  El atajo de teclado solo funciona en Windows. Sigo sin atajo.")
        return
    listo = threading.Event()
    threading.Thread(target=_hilo_atajo, args=(*teclas, listo), daemon=True, name="atajo").start()
    listo.wait(3)
    if _ATAJO_ACTIVO[0]:
        info(f"Atajo: {ATAJO} para hablarme sin decir \"{NOMBRE}\" (si estoy hablando, me callo).")


def esperar_enter() -> str:
    """Modo --teclado: espera Enter, el atajo o un evento (recordatorio/aviso). Devuelve cuál."""
    try:
        import msvcrt
    except ImportError:
        input("  [Enter para hablar] ")
        return "enter"
    extra = f" · {ATAJO}" if _ATAJO_ACTIVO[0] else ""
    print(f"  [Enter para hablar{extra}] ", end="", flush=True)
    while True:
        if _ATAJO.is_set():
            print("", flush=True)
            return "atajo"
        if not eventos.empty():
            print("", flush=True)
            return "evento"
        while msvcrt.kbhit():
            if msvcrt.getwch() in "\r\n":
                print("", flush=True)
                return "enter"
        vaciar_cola()   # el micrófono sigue llenando la cola mientras esperas
        time.sleep(0.05)


# ----------------------------------------------------------------- Bucle principal
def procesar_eventos(wake) -> None:
    """Recordatorios, avisos del cerebro y resumen: se dicen entre turnos, nunca a mitad de frase."""
    while not eventos.empty():
        ev = eventos.get()
        tipo, texto = ev[0], ev[1]
        if tipo == "recordatorio":
            info(f"⏰ Recordatorio: {texto}")
            beep(988, 0.15)
            decir(f"Andrés, te recuerdo: {texto}")
        elif tipo == "aviso":
            alta = len(ev) > 2 and bool(ev[2])
            info(f"🔔 Aviso{' importante' if alta else ''}: {texto}")
            beep(784, 0.12)
            if alta:
                beep(988, 0.12)
            decir(texto[:600])   # el cerebro ya lo redactó para voz
        elif tipo == "resumen":
            info("☀️  Resumen de la mañana")
            decir("Buenos días, Andrés. Te doy el resumen de hoy.")
            responder(PROMPT_RESUMEN, wake)   # si pulsas el atajo, _ATAJO queda activo y el bucle te escucha


def turno(stt, wake, espera: float, texto_previo: str = "", directo: bool = True) -> bool:
    """Un turno de conversación. Devuelve True si hubo conversación.
    Si me interrumpes mientras hablo, sigo escuchándote y respondo a lo que dijiste.
    `directo`: me llamaste (nombre, atajo, Enter). Si no (seguimiento o interrupción por voz),
    lo que oiga pasa por `para_mi` para no responderle al juego o a la TV."""
    _IGNORADA[0] = False
    previo = None
    if texto_previo:
        print(f"\n  Tú: {texto_previo}", flush=True)
        poner_estado("pensando", usuario=texto_previo)
        previo = responder(texto_previo, wake)
        if previo is None:
            return not _IGNORADA[0]
        directo = False
    while True:
        if previo == []:                # me callaste con el atajo: escucho desde cero
            _ATAJO.clear()
            beep()
            espera = max(espera, 8)
            directo = True
        info("🎙️  Te escucho..." if not previo else "🎙️  Te escucho (me interrumpiste)...")
        estado_reposo()
        audio = grabar_frase(espera, previo)
        if audio is None:
            return False
        t0 = time.time()
        texto = escuchar_completo(stt, audio)
        if len(texto) < 2:
            info("(no entendí nada)")
            return False
        print(f"\n  Tú: {texto}   (entendido en {time.time() - t0:.1f}s)", flush=True)
        poner_estado("pensando", usuario=texto)
        if es_parar(texto):
            info("(ok, me detengo)")
            beep(440, 0.08)
            return False
        if not directo:
            texto = para_mi(texto)
            if texto is None:
                info("(no parecía para mí: lo ignoro)")
                return False
        previo = responder(texto, wake)
        if previo is None:
            return not _IGNORADA[0]
        directo = False


VOCES_CANDIDATAS = [
    ("1", "en-US-AndrewMultilingualNeural", "Andrew · multilingüe, masculina, muy natural"),
    ("2", "en-US-BrianMultilingualNeural", "Brian · multilingüe, masculina, cálida"),
    ("3", "en-US-AvaMultilingualNeural", "Ava · multilingüe, femenina, muy natural"),
    ("4", "en-US-EmmaMultilingualNeural", "Emma · multilingüe, femenina"),
    ("5", "fr-FR-RemyMultilingualNeural", "Rémy · multilingüe, masculina, grave"),
    ("6", "es-MX-JorgeNeural", "Jorge · México"),
    ("7", "es-ES-AlvaroNeural", "Álvaro · España"),
    ("8", "es-CO-SalomeNeural", "Salomé · Colombia"),
    ("9", "es-CO-GonzaloNeural", "Gonzalo · Colombia"),
]


def probar_voces() -> None:
    frase = ("Hola Andrés, soy Orión. Revisé tus pendientes: tienes dos entregas esta semana "
             "y el servidor de producción está estable. ¿Por dónde empezamos?")
    print("\n  Escucha cada voz. Ctrl+C para salir.\n")
    for num, voz, desc in VOCES_CANDIDATAS:
        print(f"  [{num}] {desc}", flush=True)
        try:
            rep.agregar(sintetizar(frase, voz))
            rep.esperar()
        except Exception as e:  # noqa: BLE001
            print(f"      (no disponible: {e.__class__.__name__})")
        time.sleep(0.4)
    print("\n  Dime el número de la que más te gustó.")


def prueba_mic() -> None:
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    download_models(model_names=["hey_jarvis"])
    wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    disp, nombre, aviso = resolver_mic()
    if aviso:
        print(f"\n  ⚠️  {aviso}")
    print(f"\n  Micrófono: {nombre}")
    print("  Habla normal y luego di 'Hey Jarvis' varias veces. Ctrl+C para salir.\n")
    pico = 0.0
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=CHUNK,
                        callback=_cb, device=disp):
        while True:
            bloque = cola.get()
            nivel = rms(bloque)
            score = float(max(wake.predict(bloque).values()))
            pico = max(pico * 0.97, score)
            barra = "#" * min(40, int(nivel / 100))
            marca = "  <-- HEY JARVIS!" if score > UMBRAL_WAKE else ""
            print(f"\r  nivel {int(nivel):5d} |{barra:<40}|  hey_jarvis {pico:0.2f}{marca}   ",
                  end="", flush=True)


def main() -> None:
    global rep
    ap = argparse.ArgumentParser(description="Voz de Jarvis")
    ap.add_argument("--teclado", action="store_true", help="Enter para hablar, sin 'Hey Jarvis'")
    ap.add_argument("--microfonos", action="store_true", help="Lista micrófonos y sale")
    ap.add_argument("--elegir-mic", action="store_true", help="Elige el micrófono (se guarda por nombre)")
    ap.add_argument("--elegir-salida", action="store_true", help="Elige el parlante/TV por donde habla")
    ap.add_argument("--voces", action="store_true", help="Escucha varias voces para elegir")
    ap.add_argument("--prueba", action="store_true", help="Medidor en vivo de micrófono y 'Hey Jarvis'")
    args = ap.parse_args()

    if args.microfonos:
        print(sd.query_devices())
        print("\n  Micrófono por defecto:", sd.query_devices(kind="input")["name"])
        print("  Orión usará:", resolver_mic()[1])
        return
    if args.elegir_mic:
        elegir_mic()
        return
    if args.prueba:
        prueba_mic()
        return

    if args.elegir_salida:
        elegir_salida()
        return
    disp_salida, nombre_salida, aviso_salida = resolver_salida()
    if aviso_salida:
        info(f"⚠️  {aviso_salida}")
    iniciar_eco()
    rep = Reproductor(disp_salida)
    _SALIDA_ACTUAL[0] = nombre_salida
    if args.voces:
        probar_voces()
        return

    print(f"\n  ── {NOMBRE} · voz ──────────────────────────────")
    unica_instancia()
    if not jarvis_en_linea():
        info(f"⚠️  Jarvis no responde en {API}.")
        sys.exit(1)
    info(f"Cerebro: {API} · modelo {MODELO}")

    cargar_historial()
    stt = cargar_stt()
    _STT[0] = stt
    wake = None
    if not args.teclado and ACTIVACION == "hey":
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        download_models(model_names=["hey_jarvis"])
        wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        info("Activación: di \"Hey Jarvis\"")
    elif not args.teclado:
        info(f"Activación: di \"{NOMBRE}\" (ej: \"{NOMBRE}, ¿qué hora es?\")")
    if VS_VOZ:
        info(f"Voz: VoiceStudio local ({VS_VOZ}, {VS_MODELO})")
    elif ELEVEN_KEY and ELEVEN_VOZ:
        info(f"Voz: ElevenLabs ({ELEVEN_VOZ}, {ELEVEN_MODELO})")
    else:
        info(f"Voz: {VOZ}")
    info(f"🔊 Salida: {_SALIDA_ACTUAL[0]}")

    mic = Microfono()
    mic.abrir()
    if ECO is not None:
        try:   # pista del retraso parlante -> micrófono; WebRTC lo afina solo (también por HDMI)
            ECO.ajustar_retraso(float(rep._stream.latency) + float(mic.stream.latency) + 0.02)
        except Exception:  # noqa: BLE001
            ECO.ajustar_retraso(0.15)
    threading.Thread(target=mic.vigilar, daemon=True).start()
    if True:
        atexit.register(_al_salir)
        _avisar_cierre_consola()
        decir(f"{NOMBRE} en línea.")
        threading.Thread(target=vigia, daemon=True).start()
        iniciar_atajo()
        print("  Listo. Ctrl+C para salir · cualquier tecla lo calla mientras habla.\n", flush=True)
        while True:
            procesar_eventos(wake)
            estado_reposo()
            if _ATAJO.is_set():
                # Atajo de teclado: como si hubieras dicho "Orión" (y si estaba en pausa, despierta)
                _ATAJO.clear()
                if _DORMIDO[0]:
                    _DORMIDO[0] = False
                    info("☀️  Despierto.")
                info(f"⌨️  Atajo {ATAJO}")
                vaciar_cola()
                beep()
                hubo = turno(stt, wake, espera=8)
            elif args.teclado:
                if esperar_enter() != "enter":
                    continue    # atajo o evento: los atiende el inicio del bucle
                vaciar_cola()
                beep()
                hubo = turno(stt, wake, espera=8)
            elif wake is None:
                # Modo "nombre": escucha cualquier frase y la transcribe
                audio = grabar_frase(espera_max=3600)
                if audio is None or len(audio) < SR * 0.4:
                    continue
                oido = escuchar_completo(stt, audio)
                if not oido:
                    continue
                dijo, resto = buscar_nombre(oido)
                if not dijo:
                    if not _DORMIDO[0]:
                        print(f"  (oí: {oido!r} — sin \"{NOMBRE}\", lo ignoro)", flush=True)
                    continue
                if _DORMIDO[0]:
                    if _DESPERTAR.search(resto) or not resto:
                        _DORMIDO[0] = False
                        info("☀️  Despierto.")
                        decir("Aquí estoy.")
                    continue
                if _DORMIR.search(resto):
                    _DORMIDO[0] = True
                    info(f"💤 En pausa. Di \"{NOMBRE}, despierta\" para volver.")
                    decir("Listo, quedo en pausa. Dime despierta cuando me necesites.")
                    continue
                respuesta_salida = comando_salida(resto)
                if respuesta_salida:
                    decir(respuesta_salida)
                    continue
                if _PREGUNTA_SALIDA.search(resto):
                    decir(f"Estoy hablando por {_SALIDA_ACTUAL[0].replace('(predeterminado de Windows)', ', el predeterminado de Windows')}.")
                    continue
                if _PREGUNTA_MIC.search(resto):
                    info(f"🎙️  Micrófono: {_MIC_ACTUAL[0]}")
                    decir(f"Estoy usando {_MIC_ACTUAL[0].replace('(predeterminado de Windows)', ', el predeterminado de Windows')}.")
                    continue
                if es_parar(resto):
                    info("(ok)")
                    beep(440, 0.08)
                    continue
                if len(resto) >= 3:
                    hubo = turno(stt, wake, espera=0, texto_previo=resto)
                else:
                    beep()
                    hubo = turno(stt, wake, espera=6)
            else:
                bloque = cola.get()
                ruido.append(rms(bloque))
                if max(wake.predict(bloque).values()) < UMBRAL_WAKE:
                    continue
                wake.reset()
                beep()
                hubo = turno(stt, wake, espera=6)
            # Modo conversación: puedes seguir hablando unos segundos sin "Hey Jarvis"
            while hubo and SEGUIMIENTO > 0:
                beep(660, 0.08)
                # si te hice una pregunta ("¿lo subo?"), te doy más tiempo para el "sí"
                pregunte = bool(historial) and historial[-1].get("role") == "assistant" and \
                    historial[-1].get("content", "").rstrip().endswith("?")
                hubo = turno(stt, wake, espera=SEGUIMIENTO * (2.5 if pregunte else 1), directo=False)
            if wake is not None:
                wake.reset()
            vaciar_cola()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _al_salir()
        print("\n  Hasta luego.")
    except Exception:
        import traceback

        traceback.print_exc()
        raise
