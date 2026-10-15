# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""HUD de Pedro: panel pequeño, siempre encima, que muestra en qué está Pedro.

Lee (solo lectura) lo que escribe el cliente de voz:
  <jarvis>/tmp/estado.json  {"estado": "escuchando"|"oyendo"|"pensando"|"hablando"|"dormido"|"apagado",
                             "usuario": str, "pedro": str, "t": epoch}
  <jarvis>/avisos.json      [{"id", "texto", "cuando", "prioridad", "leido"}]   (nunca lo escribe)
y guarda solo su propia posición en hud/posicion.json.

Uso:
  uv run --python 3.12 hud\\hud.py               (normal)
  uv run --python 3.12 hud\\hud.py --demo        (recorre los estados con texto de ejemplo)
  uv run --python 3.12 hud\\hud.py --demo pensando   (se queda en un estado; también: aviso, aviso-normal)
Clic izquierdo y arrastrar = mover · doble clic = ocultar/mostrar texto · clic derecho = menú.

Solo usa la biblioteca estándar (tkinter). La lógica de qué mostrar está en funciones puras
(interpretar, recortar, elegir_aviso, posiciones) que prueba hud/test_hud.py sin pantalla.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HUD_DIR = Path(__file__).resolve().parent
RAIZ = HUD_DIR.parent
PUERTO = int(os.environ.get("PEDRO_HUD_PUERTO", "47833"))

SONDEO_MS = 150          # cada cuánto mira estado.json
SONDEO_AVISOS_S = 1.0    # cada cuánto mira avisos.json
VIEJO_S = 30.0           # sin noticias del cliente de voz por más de esto = sin conexión
AVISO_S = 6.0            # cuánto se ve un aviso
REAFIRMAR_ENCIMA_S = 5.0

# ------------------------------------------------------------------ paleta
FONDO = "#0a1117"
PANEL_BORDE = "#173540"
SEPARADOR = "#13262e"
TEXTO = "#e2edf1"
TEXTO_2 = "#8fa5ae"
TENUE = "#4c6772"
ACENTO = "#35c9d6"
CLAVE_TRANSPARENTE = "#010203"   # color que Windows vuelve transparente (esquinas redondeadas)


@dataclass(frozen=True)
class Estilo:
    etiqueta: str
    detalle: str
    color: str
    fps: int


ESTILOS = {
    "escuchando": Estilo("Escuchando", "Di «Pedro, …»", "#22d3ee", 12),
    "oyendo": Estilo("Te escucho", "Sigue hablando…", "#7ff0ff", 24),
    "pensando": Estilo("Pensando", "Trabajando en tu pedido", "#f5b13d", 24),
    "hablando": Estilo("Hablando", "Respondiendo", "#2dd4bf", 24),
    "dormido": Estilo("En reposo", "Di «Pedro, despierta»", "#66737d", 5),
    "sin_conexion": Estilo("Sin conexión", "Esperando al cliente de voz", "#ef4444", 5),
}
FPS_AVISO = 15


# ------------------------------------------------------------------ lógica pura (probada en test_hud.py)
@dataclass(frozen=True)
class Vista:
    clave: str          # una de ESTILOS
    etiqueta: str
    detalle: str
    color: str
    fps: int
    usuario: str = ""
    pedro: str = ""


def _limpio(v) -> str:
    if v is None or isinstance(v, (dict, list, bool)):
        return ""
    return " ".join(str(v).split())


def _numero(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _hace(segundos: float) -> str:
    s = max(0, int(segundos))
    if s < 90:
        return f"{s} s"
    if s < 90 * 60:
        return f"{round(s / 60)} min"
    return f"{round(s / 3600)} h"


def _vista(clave: str, usuario: str = "", pedro: str = "", etiqueta: str = "", detalle: str = "") -> Vista:
    e = ESTILOS[clave]
    return Vista(clave, etiqueta or e.etiqueta, detalle or e.detalle, e.color, e.fps, usuario, pedro)


def interpretar(datos, ahora: float, mtime: float | None = None) -> Vista:
    """Qué mostrar para el contenido de estado.json (`datos`, None si falta o está dañado).

    La "última señal" es lo más reciente entre el campo t y la fecha del archivo, así que el cliente
    de voz puede latir reescribiendo t o simplemente tocando el archivo.
    """
    if not isinstance(datos, dict):
        return _vista("sin_conexion")
    estado = _limpio(datos.get("estado")).lower()
    usuario, pedro = _limpio(datos.get("usuario")), _limpio(datos.get("pedro"))
    if estado in ("", "apagado"):
        return _vista("sin_conexion", usuario, pedro,
                      detalle="Pedro está apagado" if estado == "apagado" else "")
    marcas = [m for m in (_numero(datos.get("t")), _numero(mtime)) if m is not None]
    ultima = max(marcas) if marcas else None
    if estado != "dormido" and (ultima is None or ahora - ultima > VIEJO_S):
        detalle = f"Sin señal desde hace {_hace(ahora - ultima)}" if ultima is not None else ""
        return _vista("sin_conexion", usuario, pedro, detalle=detalle)
    if estado in ESTILOS and estado != "sin_conexion":
        return _vista(estado, usuario, pedro)
    # estado nuevo que el HUD no conoce: se muestra tal cual con el estilo tranquilo
    return _vista("escuchando", usuario, pedro, etiqueta=estado.capitalize()[:24], detalle=" ")


def fps_de(vista: Vista, hay_aviso: bool = False) -> int:
    return max(vista.fps, FPS_AVISO) if hay_aviso else vista.fps


def _prefijo_que_cabe(palabra: str, cabe) -> int:
    lo, hi = 1, len(palabra)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cabe(palabra[:mid]):
            lo = mid
        else:
            hi = mid - 1
    return lo


def recortar(texto: str, cabe, max_lineas: int = 2) -> list[str]:
    """Parte `texto` en a lo sumo `max_lineas` líneas que cumplan `cabe(linea)`; si sobra, termina en «…»."""
    palabras = _limpio(texto).split()
    lineas: list[str] = []
    actual, i = "", 0
    while i < len(palabras) and len(lineas) < max_lineas:
        p = palabras[i]
        prueba = f"{actual} {p}" if actual else p
        if cabe(prueba):
            actual, i = prueba, i + 1
        elif actual:
            lineas.append(actual)
            actual = ""
        else:                            # una sola palabra más ancha que la línea: se corta
            n = _prefijo_que_cabe(p, cabe)
            lineas.append(p[:n])
            palabras[i] = p[n:]
    if actual:
        lineas.append(actual)
    if i < len(palabras) and lineas:
        ultima = lineas[-1]
        while ultima and not cabe(ultima + "…"):
            ultima = ultima[:-1]
        lineas[-1] = ultima.rstrip(" ,.;:") + "…"
    return lineas


def _cuando(v) -> float:
    n = _numero(v)
    if n is not None:
        return n
    if isinstance(v, str) and v.strip():
        try:
            return datetime.fromisoformat(v.strip().replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return float("-inf")


def id_aviso(a: dict) -> str:
    if a.get("id") not in (None, ""):
        return f"id:{a['id']}"
    return f"txt:{_limpio(a.get('texto'))}|{a.get('cuando')}"


def elegir_aviso(avisos, mostrados: set) -> tuple[dict | None, set]:
    """(aviso a mostrar o None, ids que pasan a "ya mostrados").

    Muestra solo el NO leído más nuevo (por `cuando`; si empata o no se entiende, el último de la lista)
    que aún no se haya mostrado; los no leídos más viejos se dan por vistos para no encadenar avisos.
    """
    if not isinstance(avisos, list):
        return None, set()
    pendientes = [(i, a) for i, a in enumerate(avisos)
                  if isinstance(a, dict) and not a.get("leido") and _limpio(a.get("texto"))]
    if not pendientes:
        return None, set()
    _, nuevo = max(pendientes, key=lambda ia: (_cuando(ia[1].get("cuando")), ia[0]))
    ids = {id_aviso(a) for _, a in pendientes}
    if id_aviso(nuevo) in mostrados:
        return None, ids - mostrados
    return nuevo, ids


def es_urgente(aviso: dict) -> bool:
    return _limpio(aviso.get("prioridad")).lower() in ("alta", "urgente", "high")


ESQUINAS = ("abajo-derecha", "abajo-izquierda", "arriba-izquierda", "arriba-derecha")


def posicion_esquina(esquina: str, area: tuple, w: int, h: int, margen: int = 16) -> tuple[int, int]:
    x0, y0, x1, y1 = area
    x = x1 - w - margen if "derecha" in esquina else x0 + margen
    y = y1 - h - margen if "abajo" in esquina else y0 + margen
    return int(x), int(y)


def esquina_cercana(x: int, y: int, w: int, h: int, area: tuple) -> str:
    x0, y0, x1, y1 = area
    der = x + w / 2 >= (x0 + x1) / 2
    abajo = y + h / 2 >= (y0 + y1) / 2
    return f"{'abajo' if abajo else 'arriba'}-{'derecha' if der else 'izquierda'}"


def siguiente_esquina(esquina: str) -> str:
    i = ESQUINAS.index(esquina) if esquina in ESQUINAS else -1
    return ESQUINAS[(i + 1) % len(ESQUINAS)]


def ajustar_a_area(x: int, y: int, w: int, h: int, area: tuple) -> tuple[int, int]:
    """Mete la ventana completa dentro del área (si no cabe, la alinea arriba a la izquierda)."""
    x0, y0, x1, y1 = area
    x = min(max(x, x0), max(x0, x1 - w))
    y = min(max(y, y0), max(y0, y1 - h))
    return int(x), int(y)


def y_al_cambiar_alto(y: int, h_viejo: int, h_nuevo: int, area: tuple) -> int:
    """Si la ventana está en la mitad de abajo, crece/encoge hacia arriba (el borde inferior no se mueve)."""
    _, y0, _, y1 = area
    if y + h_viejo / 2 >= (y0 + y1) / 2:
        return int(y + h_viejo - h_nuevo)
    return int(y)


def leer_preferencias(texto: str | None) -> dict:
    try:
        d = json.loads(texto) if texto else {}
    except (ValueError, TypeError):
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    if _numero(d.get("x")) is not None and _numero(d.get("y")) is not None:
        out["x"], out["y"] = int(_numero(d["x"])), int(_numero(d["y"]))
    if isinstance(d.get("texto"), bool):
        out["texto"] = d["texto"]
    return out


def mezclar(a: str, b: str, t: float) -> str:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    ra, ga, ba = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    rb, gb, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


# ------------------------------------------------------------------ lectura de archivos
def _leer_bytes(ruta: Path) -> bytes:
    """Lee sin estorbar al escritor: en Windows abre compartiendo también el borrado, para que el
    os.replace() atómico del cliente de voz no falle si coincide con una lectura del HUD."""
    if sys.platform == "win32":
        try:
            return _leer_bytes_windows(ruta)
        except FileNotFoundError:
            raise
        except Exception:  # noqa: BLE001 - si algo de ctypes falla, lectura normal
            pass
    with open(ruta, "rb") as f:
        return f.read()


def _leer_bytes_windows(ruta: Path) -> bytes:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    GENERIC_READ, COMPARTIR_TODO, OPEN_EXISTING, NORMAL = 0x80000000, 0x7, 3, 0x80
    h = k32.CreateFileW(str(ruta), GENERIC_READ, COMPARTIR_TODO, None, OPEN_EXISTING, NORMAL, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        err = ctypes.get_last_error()
        if err in (2, 3):
            raise FileNotFoundError(str(ruta))
        raise OSError(err, "CreateFileW")
    fd = msvcrt.open_osfhandle(h, os.O_RDONLY)
    with open(fd, "rb", closefd=True) as f:
        return f.read()


def _json_de(b: bytes):
    try:
        return json.loads(b.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None


class FuenteArchivos:
    """Lee estado.json y avisos.json solo cuando cambian (fecha o tamaño)."""

    def __init__(self, raiz: Path):
        self.estado = raiz / "tmp" / "estado.json"
        self.avisos = raiz / "avisos.json"
        self._firma_e = self._firma_a = None
        self._datos_e = self._datos_a = None
        self._mtime_e = None

    @staticmethod
    def _firma(ruta: Path):
        try:
            st = os.stat(ruta)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def leer_estado(self, ahora: float):
        firma = self._firma(self.estado)
        if firma is None:
            self._firma_e, self._datos_e, self._mtime_e = None, None, None
        elif firma != self._firma_e:
            try:
                datos = _json_de(_leer_bytes(self.estado))
            except OSError:
                datos = None
            if isinstance(datos, dict):
                self._datos_e, self._firma_e, self._mtime_e = datos, firma, firma[0] / 1e9
            elif self._datos_e is None:
                self._firma_e = firma       # dañado y sin dato previo: sin conexión
            # si quedó a medio escribir se conserva el último bueno (con su fecha) y se reintenta
        return self._datos_e, self._mtime_e

    def leer_avisos(self, ahora: float):
        firma = self._firma(self.avisos)
        if firma is None:
            self._firma_a, self._datos_a = None, None
        elif firma != self._firma_a:
            try:
                datos = _json_de(_leer_bytes(self.avisos))
            except OSError:
                datos = None
            if isinstance(datos, list):
                self._datos_a, self._firma_a = datos, firma
        return self._datos_a


class FuenteDemo:
    """Estados y textos de ejemplo para ver el HUD sin el cliente de voz."""

    PREGUNTA = "Pedro, ¿qué tengo pendiente para hoy?"
    RESPUESTA = ("Tienes una reunión a las tres de la tarde y dos correos sin responder. "
                 "¿Quieres que te lea el primero?")
    GUION = [  # (estado, usuario, pedro, segundos)
        ("dormido", "", "", 3.0),
        ("escuchando", "", "", 3.0),
        ("oyendo", PREGUNTA, "", 2.5),
        ("pensando", PREGUNTA, "", 3.5),
        ("hablando", PREGUNTA, RESPUESTA, 5.0),
        ("escuchando", PREGUNTA, RESPUESTA, 4.0),
        ("oyendo", "Pedro, mira esto, ¿qué tengo en la mano?", RESPUESTA, 2.0),
        ("pensando", "Pedro, mira esto, ¿qué tengo en la mano?", "", 3.0),
        ("hablando", "Pedro, mira esto, ¿qué tengo en la mano?",
         "Es una taza de café con el logo de Onnyx. Por cierto, ya casi es la hora de tu reunión.", 5.0),
        ("apagado", "", "", 3.5),
    ]
    AVISOS = [  # (segundo del ciclo en que aparece, prioridad, texto)
        (16.5, "alta", "El servidor de producción no responde desde hace 5 minutos."),
        (24.0, "normal", "En 15 minutos tienes la reunión de las 3:00 p. m."),
    ]

    def __init__(self, fijo: str | None = None):
        self.fijo = fijo
        self.t0 = time.time()
        self.ciclo = sum(g[3] for g in self.GUION)

    def leer_estado(self, ahora: float):
        if self.fijo in ("aviso", "aviso-normal"):
            return {"estado": "escuchando", "usuario": self.PREGUNTA, "pedro": self.RESPUESTA, "t": ahora}, ahora
        if self.fijo:
            for est, u, p, _ in self.GUION:
                if est == self.fijo and (u or p or est in ("dormido", "apagado")):
                    usuario, pedro = (u or self.PREGUNTA), (p or self.RESPUESTA)
                    if est in ("oyendo", "pensando"):
                        pedro = ""
                    return {"estado": est, "usuario": usuario, "pedro": pedro, "t": ahora}, ahora
            return {"estado": self.fijo, "usuario": self.PREGUNTA, "pedro": self.RESPUESTA, "t": ahora}, ahora
        r = (ahora - self.t0) % self.ciclo
        for est, u, p, dur in self.GUION:
            if r < dur:
                return {"estado": est, "usuario": u, "pedro": p, "t": ahora}, ahora
            r -= dur
        return None, None

    def leer_avisos(self, ahora: float):
        transcurrido = ahora - self.t0
        if self.fijo in ("aviso", "aviso-normal"):
            n = int(transcurrido // (AVISO_S + 2))
            prio = "alta" if self.fijo == "aviso" else "normal"
            texto = self.AVISOS[0][2] if prio == "alta" else self.AVISOS[1][2]
            return [{"id": f"demo-{n}", "texto": texto, "cuando": ahora, "prioridad": prio, "leido": False}]
        if self.fijo:
            return []
        vuelta, r = divmod(transcurrido, self.ciclo)
        return [{"id": f"demo-{int(vuelta)}-{i}", "texto": txt, "cuando": self.t0 + vuelta * self.ciclo + seg,
                 "prioridad": prio, "leido": False}
                for i, (seg, prio, txt) in enumerate(self.AVISOS) if r >= seg]


# ------------------------------------------------------------------ una sola instancia
def tomar_instancia(puerto: int):
    """Socket en 127.0.0.1:puerto; si ya lo tiene otro HUD devuelve None (y le pide que se muestre)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:   # en Linux/mac permite reabrir enseguida (no deja escuchar dos veces en el mismo puerto)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", puerto))
        s.listen(2)
        s.setblocking(False)
        return s
    except OSError:
        s.close()
        try:
            with socket.create_connection(("127.0.0.1", puerto), timeout=1) as c:
                c.sendall(b"mostrar\n")
        except OSError:
            pass
        return None


# ------------------------------------------------------------------ registro de errores
_ya_registrado: set = set()


def _log(msg: str) -> None:
    if msg in _ya_registrado:
        return
    _ya_registrado.add(msg)
    linea = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    if sys.stderr is not None:
        try:
            sys.stderr.write(linea)
        except Exception:  # noqa: BLE001
            pass
    try:
        ruta = HUD_DIR / "hud.log"
        if ruta.exists() and ruta.stat().st_size > 200_000:
            ruta.unlink()
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(linea)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ pantalla (Windows con respaldo)
def _area_monitor(x: int, y: int, por_defecto: tuple) -> tuple:
    """Área de trabajo (sin la barra de tareas) del monitor que contiene (x, y)."""
    if sys.platform != "win32":
        return por_defecto
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        u32 = ctypes.windll.user32
        u32.MonitorFromPoint.restype = wintypes.HANDLE
        u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        mon = u32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)   # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if mon and u32.GetMonitorInfoW(mon, ctypes.byref(info)):
            r = info.rcWork
            return (r.left, r.top, r.right, r.bottom)
    except Exception as e:  # noqa: BLE001
        _log(f"área de monitor: {e!r}")
    return por_defecto


def _punto_en_algun_monitor(x: int, y: int) -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        u32.MonitorFromPoint.restype = wintypes.HANDLE
        u32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        return bool(u32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 0))   # MONITOR_DEFAULTTONULL
    except Exception:  # noqa: BLE001
        return True


def _ventana_activa():
    """La ventana que tiene el foco (Windows), para devolvérselo: el HUD no debe quitarle el teclado
    a la consola de voz (modo --teclado)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        u32 = ctypes.windll.user32
        u32.GetForegroundWindow.restype = ctypes.c_void_p
        return u32.GetForegroundWindow()
    except Exception:  # noqa: BLE001
        return None


def _devolver_foco(hwnd) -> None:
    if not hwnd or sys.platform != "win32":
        return
    try:
        import ctypes

        u32 = ctypes.windll.user32
        u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass


def _dpi_nitido() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # nítido en pantallas con escala 125-150 %
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ ventana
class HUD:
    ANCHO = 360
    CABECERA = 84
    MARGEN = 14

    def __init__(self, fuente, puerto_socket=None, esquina: str | None = None, guardar: bool = True):
        import tkinter as tk
        import tkinter.font as tkfont

        self.tk = tk
        self.fuente = fuente
        self.sock = puerto_socket
        self.guardar = guardar
        self.root = root = tk.Tk()
        root.withdraw()
        root.title("Pedro HUD")
        self.esc = max(1.0, root.winfo_fpixels("1i") / 96.0)

        familias = set(tkfont.families(root))
        fam = next((f for f in ("Segoe UI", "Segoe UI Variable Text", "Inter", "DejaVu Sans", "Helvetica")
                    if f in familias), "TkDefaultFont")
        fam_fuerte = "Segoe UI Semibold" if "Segoe UI Semibold" in familias else fam
        peso = "normal" if fam_fuerte != fam else "bold"
        self.f_rotulo = tkfont.Font(root, family=fam, size=7, weight="bold")
        self.f_reloj = tkfont.Font(root, family=fam, size=8)
        self.f_estado = tkfont.Font(root, family=fam_fuerte, size=15, weight=peso)
        self.f_detalle = tkfont.Font(root, family=fam, size=9)
        self.f_texto = tkfont.Font(root, family=fam, size=9)
        self.f_aviso = tkfont.Font(root, family=fam_fuerte, size=10, weight=peso)

        self.w = self._px(self.ANCHO)
        self.lh = max(self._px(16), self.f_texto.metrics("linespace") + self._px(1))
        self.ancho_texto = self.w - self._px(self.MARGEN + 44 + self.MARGEN)
        self.h_compacto = self._px(self.CABECERA)
        self.h_completo = self.h_compacto + self._px(10) + 4 * self.lh + self._px(8) + self._px(12)

        prefs = leer_preferencias(self._leer_texto(HUD_DIR / "posicion.json")) if guardar else {}
        self.mostrar_texto = prefs.get("texto", True)
        self.vista = interpretar(None, time.time())
        self.mostrados: set = set()
        self.aviso: dict | None = None
        self.aviso_hasta = 0.0
        self.aviso_desde = 0.0
        self.ultimo_aviso_sondeo = 0.0
        self.ultimo_encima = 0.0
        self.reloj_txt = ""
        self.cache: dict = {}
        self.textos_cache: dict = {}
        self.arrastre = None
        self.h = self._alto_deseado()

        # ventana sin bordes, siempre encima y semitransparente
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        fondo_ventana = FONDO
        if sys.platform == "win32":
            try:
                root.attributes("-transparentcolor", CLAVE_TRANSPARENTE)
                fondo_ventana = CLAVE_TRANSPARENTE
            except tk.TclError:
                pass
        try:
            root.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        root.configure(bg=fondo_ventana)
        self.c = c = tk.Canvas(root, width=self.w, height=self.h, bg=fondo_ventana, highlightthickness=0, bd=0)
        c.pack(fill="both", expand=True)
        self._crear_elementos()

        # posición: la guardada si sigue en pantalla, si no la esquina
        pant = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        if esquina:
            x, y = posicion_esquina(esquina, _area_monitor(0, 0, pant), self.w, self.h, self._px(16))
        elif "x" in prefs and _punto_en_algun_monitor(prefs["x"] + self.w // 2, prefs["y"] + self.h // 2):
            area = _area_monitor(prefs["x"] + self.w // 2, prefs["y"] + self.h // 2, pant)
            x, y = ajustar_a_area(prefs["x"], prefs["y"], self.w, self.h, area)
        else:
            x, y = posicion_esquina("abajo-derecha", _area_monitor(0, 0, pant), self.w, self.h, self._px(16))
        self.x, self.y = x, y
        root.geometry(f"{self.w}x{self.h}+{x}+{y}")

        # interacción
        for sec, fn in (("<ButtonPress-1>", self._agarrar), ("<B1-Motion>", self._arrastrar),
                        ("<ButtonRelease-1>", self._soltar), ("<Double-Button-1>", lambda e: self._alternar_texto()),
                        ("<Button-3>", self._menu_contextual)):
            c.bind(sec, fn)
        self.menu = tk.Menu(root, tearoff=0, bg="#0f1b22", fg=TEXTO, activebackground="#12414b",
                            activeforeground="#ffffff", bd=0, relief="flat", font=self.f_detalle)
        self.menu.add_command(label="Ocultar texto", command=self._alternar_texto)
        self.menu.add_command(label="Mover a otra esquina", command=self._otra_esquina)
        self.menu.add_separator()
        self.menu.add_command(label="Cerrar", command=self.cerrar)
        self._etiqueta_menu()

        previa = _ventana_activa()
        root.deiconify()
        root.after(60, lambda: _devolver_foco(previa))
        root.after(10, self._sondear)
        root.after(30, self._animar)

    # -------------------------------------------------------------- utilidades
    def _px(self, v: float) -> int:
        return int(round(v * self.esc))

    @staticmethod
    def _leer_texto(ruta: Path) -> str | None:
        try:
            return ruta.read_text(encoding="utf-8")
        except OSError:
            return None

    def _cfg(self, item, **kw):
        """itemconfigure solo de lo que cambió (menos trabajo para Tk = menos CPU)."""
        cambios = {k: v for k, v in kw.items() if self.cache.get((item, k)) != v}
        if cambios:
            for k, v in cambios.items():
                self.cache[(item, k)] = v
            self.c.itemconfigure(item, **cambios)

    def _alto_deseado(self) -> int:
        con_texto = self.mostrar_texto or (self.aviso is not None)
        return self.h_completo if con_texto else self.h_compacto

    @staticmethod
    def _puntos_redondeados(x0, y0, x1, y1, r):
        return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
                x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]

    # -------------------------------------------------------------- dibujo
    def _crear_elementos(self):
        c, p = self.c, self._px
        m = p(self.MARGEN)
        self.panel = c.create_polygon(self._puntos_redondeados(0, 0, self.w - 1, self.h - 1, p(14)),
                                      smooth=True, splinesteps=12, fill=FONDO, outline=PANEL_BORDE, width=1)
        # anillo tipo reactor
        self.cx, self.cy = p(12 + 30), p(self.CABECERA / 2)
        cx, cy = self.cx, self.cy
        self.r_ext, self.r_anillo = p(30), p(22)
        self.exterior = c.create_oval(cx - self.r_ext, cy - self.r_ext, cx + self.r_ext, cy + self.r_ext,
                                      outline=SEPARADOR, width=1)
        self.marcas = []
        for i in range(24):
            ang = math.radians(i * 15)
            r0, r1 = p(25.5), p(28 if i % 2 else 27)
            self.marcas.append(c.create_line(cx + r0 * math.cos(ang), cy + r0 * math.sin(ang),
                                             cx + r1 * math.cos(ang), cy + r1 * math.sin(ang),
                                             fill=TENUE, width=max(1, p(1.5) if i % 6 == 0 else p(1))))
        self.ondas = [c.create_oval(0, 0, 0, 0, outline=FONDO, width=p(2), state="hidden") for _ in range(2)]
        self.anillo = c.create_oval(cx - self.r_anillo, cy - self.r_anillo, cx + self.r_anillo, cy + self.r_anillo,
                                    outline=TENUE, width=p(3))
        self.arcos = [c.create_arc(0, 0, 0, 0, style="arc", outline=FONDO, width=p(3), state="hidden"),
                      c.create_arc(0, 0, 0, 0, style="arc", outline=FONDO, width=p(2), state="hidden")]
        self.halo2 = c.create_oval(0, 0, 0, 0, fill=FONDO, outline="")
        self.halo1 = c.create_oval(0, 0, 0, 0, fill=FONDO, outline="")
        self.nucleo = c.create_oval(0, 0, 0, 0, fill=FONDO, outline="")
        self.barras = [c.create_rectangle(0, 0, 0, 0, fill=FONDO, outline="", state="hidden") for _ in range(5)]

        # cabecera de texto
        x0 = p(12 + 60 + 16)
        self.rotulo = c.create_text(x0, p(20), text="PEDRO", anchor="w", fill=ACENTO, font=self.f_rotulo)
        self.reloj = c.create_text(self.w - m, p(20), text="", anchor="e", fill=TENUE, font=self.f_reloj)
        self.t_estado = c.create_text(x0, p(43), text="", anchor="w", fill=TEXTO, font=self.f_estado)
        self.t_detalle = c.create_text(x0, p(65), text="", anchor="w", fill=TEXTO_2, font=self.f_detalle)

        # conversación
        y = self.h_compacto
        self.sep = c.create_line(m, y, self.w - m, y, fill=SEPARADOR)
        y1 = y + p(10)
        y2 = y1 + 2 * self.lh + p(8)
        xt = m + p(44)
        self.r_usuario = c.create_text(m, y1 + p(1), text="TÚ", anchor="nw", fill=TENUE, font=self.f_rotulo)
        self.r_pedro = c.create_text(m, y2 + p(1), text="PEDRO", anchor="nw", fill=TENUE, font=self.f_rotulo)
        self.l_usuario = [c.create_text(xt, y1 + i * self.lh - p(2), text="", anchor="nw", fill=TEXTO_2,
                                        font=self.f_texto) for i in range(2)]
        self.l_pedro = [c.create_text(xt, y2 + i * self.lh - p(2), text="", anchor="nw", fill=TEXTO,
                                      font=self.f_texto) for i in range(2)]
        self.items_conversacion = [self.sep, self.r_usuario, self.r_pedro, *self.l_usuario, *self.l_pedro]

        # aviso (encima de la conversación)
        bx0, by0, bx1, by1 = m - p(4), y + p(8), self.w - m + p(4), self.h_completo - p(8)
        self.aviso_caja = c.create_polygon(self._puntos_redondeados(bx0, by0, bx1, by1, p(10)), smooth=True,
                                           splinesteps=8, fill=FONDO, outline=FONDO, width=1, state="hidden")
        self.aviso_rotulo = c.create_text(bx0 + p(12), by0 + p(10), text="", anchor="nw", fill=TEXTO,
                                          font=self.f_rotulo, state="hidden")
        la = self.f_aviso.metrics("linespace")
        n_lineas = max(1, min(3, int((by1 - p(16)) - (by0 + p(26))) // la))
        self.aviso_lineas = [c.create_text(bx0 + p(12), by0 + p(26) + i * la, text="", anchor="nw",
                                           fill=TEXTO, font=self.f_aviso, state="hidden") for i in range(n_lineas)]
        self.aviso_barra = c.create_line(bx0 + p(12), by1 - p(8), bx1 - p(12), by1 - p(8), fill=FONDO,
                                         width=p(2), state="hidden")
        self.aviso_x = (bx0 + p(12), bx1 - p(12))
        self.ancho_aviso = (bx1 - p(12)) - (bx0 + p(12))
        self.items_aviso = [self.aviso_caja, self.aviso_rotulo, *self.aviso_lineas, self.aviso_barra]

    def _poner_vista(self, v: Vista):
        etiqueta_color = mezclar(v.color, "#ffffff", 0.18) if v.clave != "dormido" else TEXTO_2
        self._cfg(self.t_estado, text=v.etiqueta, fill=etiqueta_color)
        self._cfg(self.t_detalle, text=v.detalle.strip())
        apagado = v.clave in ("sin_conexion", "dormido")
        for rotulo, texto in ((self.r_usuario, v.usuario), (self.r_pedro, v.pedro)):
            self._cfg(rotulo, fill=TENUE if texto else mezclar(TENUE, FONDO, 0.5))
        for lista, texto, color in ((self.l_usuario, v.usuario, TEXTO_2), (self.l_pedro, v.pedro, TEXTO)):
            clave = (id(lista), texto, self.ancho_texto)
            lineas = self.textos_cache.get(clave)
            if lineas is None:
                lineas = recortar(texto, lambda s: self.f_texto.measure(s) <= self.ancho_texto, 2)
                if len(self.textos_cache) > 64:
                    self.textos_cache.clear()
                self.textos_cache[clave] = lineas
            for i, item in enumerate(lista):
                self._cfg(item, text=lineas[i] if i < len(lineas) else "",
                          fill=mezclar(color, FONDO, 0.45) if apagado else color)
        tono = mezclar(ACENTO, FONDO, 0.5) if apagado else ACENTO
        self._cfg(self.rotulo, fill=tono)

    def _poner_aviso(self, aviso: dict | None):
        estado = "hidden" if aviso is None else "normal"
        for it in self.items_aviso:
            self._cfg(it, state=estado)
        for it in self.items_conversacion:
            self._cfg(it, state="hidden" if (aviso is not None or not self.mostrar_texto) else "normal")
        if aviso is None:
            return
        urgente = es_urgente(aviso)
        color = "#ef4444" if urgente else "#2dd4bf"
        self._cfg(self.aviso_caja, fill=mezclar(FONDO, color, 0.16), outline=mezclar(FONDO, color, 0.7))
        self._cfg(self.aviso_rotulo, text="AVISO URGENTE" if urgente else "AVISO",
                  fill=mezclar(color, "#ffffff", 0.25))
        lineas = recortar(_limpio(aviso.get("texto")), lambda s: self.f_aviso.measure(s) <= self.ancho_aviso,
                          len(self.aviso_lineas))
        for i, it in enumerate(self.aviso_lineas):
            self._cfg(it, text=lineas[i] if i < len(lineas) else "")
        self._cfg(self.aviso_barra, fill=color)

    def _dibujar(self, t: float):
        c, p = self.c, self._px
        cx, cy, k, col = self.cx, self.cy, self.vista.clave, self.vista.color
        ondas, arcos, barras = [], [], None
        if k == "escuchando":
            s = 0.5 + 0.5 * math.sin(2 * math.pi * t / 2.6)
            anillo, marcas = mezclar(FONDO, col, 0.55 + 0.4 * s), mezclar(FONDO, col, 0.32)
            r_nucleo, r_halo = p(6), p(10 + 2.5 * s)
            nucleo, halo = mezclar(col, "#ffffff", 0.25 * s), mezclar(FONDO, col, 0.18 + 0.14 * s)
        elif k == "oyendo":
            s = 0.5 + 0.5 * math.sin(2 * math.pi * t / 0.7)
            anillo, marcas = mezclar(col, "#ffffff", 0.2), mezclar(FONDO, col, 0.6)
            r_nucleo, r_halo = p(7 + 1.2 * s), p(13 + 2 * s)
            nucleo, halo = mezclar(col, "#ffffff", 0.55), mezclar(FONDO, col, 0.38)
            for j in range(2):
                f = ((t / 1.1) + j * 0.5) % 1.0
                ondas.append((p(22) + f * p(13), mezclar(col, FONDO, 0.25 + 0.75 * f)))
        elif k == "pensando":
            s = 0.5 + 0.5 * math.sin(2 * math.pi * t / 0.9)
            anillo, marcas = mezclar(FONDO, col, 0.28), mezclar(FONDO, col, 0.3)
            r_nucleo, r_halo = p(5 + s), p(9 + 1.5 * s)
            nucleo, halo = mezclar(col, "#ffffff", 0.15), mezclar(FONDO, col, 0.2 + 0.1 * s)
            arcos = [(self.r_anillo, (-t * 260) % 360, 100, col),
                     (p(27), (t * 160) % 360, 55, mezclar(FONDO, col, 0.75))]
        elif k == "hablando":
            anillo, marcas = mezclar(FONDO, col, 0.75), mezclar(FONDO, col, 0.4)
            r_nucleo, r_halo = 0, p(15)
            nucleo, halo = FONDO, mezclar(FONDO, col, 0.12)
            barras = []
            for i in range(5):
                x = (i - 2) * p(5)
                tope = 2 * math.sqrt(max(0.0, p(15) ** 2 - x ** 2)) - p(4)
                n = (0.55 + 0.45 * math.sin(t * (7.1 + 1.7 * i) + i * 1.3)) * \
                    (0.6 + 0.4 * math.sin(t * (3.3 + 0.9 * i) + i * 2.1))
                barras.append((x, max(p(3), tope * (0.25 + 0.75 * abs(n)))))
        elif k == "dormido":
            s = 0.5 + 0.5 * math.sin(2 * math.pi * t / 6.0)
            anillo, marcas = mezclar(FONDO, col, 0.45 + 0.12 * s), mezclar(FONDO, col, 0.22)
            r_nucleo, r_halo = p(4), p(7)
            nucleo, halo = mezclar(FONDO, col, 0.55), mezclar(FONDO, col, 0.12 + 0.05 * s)
        else:  # sin conexión
            s = 0.5 + 0.5 * math.sin(2 * math.pi * t / 2.0)
            anillo, marcas = mezclar(FONDO, col, 0.35 + 0.6 * s), mezclar(FONDO, col, 0.25)
            r_nucleo, r_halo = p(4), p(7)
            nucleo, halo = mezclar(FONDO, col, 0.5 + 0.4 * s), mezclar(FONDO, col, 0.1)

        self._cfg(self.anillo, outline=anillo)
        self._cfg(self.exterior, outline=mezclar(FONDO, col, 0.2))
        if self.cache.get(("marcas",)) != marcas:
            self.cache[("marcas",)] = marcas
            for it in self.marcas:
                c.itemconfigure(it, fill=marcas)
        r_halo2 = r_halo + p(4)
        c.coords(self.halo2, cx - r_halo2, cy - r_halo2, cx + r_halo2, cy + r_halo2)
        self._cfg(self.halo2, fill=mezclar(FONDO, halo, 0.5))
        c.coords(self.halo1, cx - r_halo, cy - r_halo, cx + r_halo, cy + r_halo)
        self._cfg(self.halo1, fill=halo)
        if r_nucleo:
            c.coords(self.nucleo, cx - r_nucleo, cy - r_nucleo, cx + r_nucleo, cy + r_nucleo)
            self._cfg(self.nucleo, fill=nucleo, state="normal")
        else:
            self._cfg(self.nucleo, state="hidden")
        for i, it in enumerate(self.ondas):
            if i < len(ondas):
                r, color = ondas[i]
                c.coords(it, cx - r, cy - r, cx + r, cy + r)
                self._cfg(it, outline=color, state="normal")
            else:
                self._cfg(it, state="hidden")
        for i, it in enumerate(self.arcos):
            if i < len(arcos):
                r, inicio, ext, color = arcos[i]
                c.coords(it, cx - r, cy - r, cx + r, cy + r)
                c.itemconfigure(it, start=inicio, extent=ext)
                self._cfg(it, outline=color, state="normal")
            else:
                self._cfg(it, state="hidden")
        ancho_barra = p(3)
        for i, it in enumerate(self.barras):
            if barras:
                x, alto = barras[i]
                c.coords(it, cx + x - ancho_barra / 2, cy - alto / 2, cx + x + ancho_barra / 2, cy + alto / 2)
                self._cfg(it, fill=mezclar(col, "#ffffff", 0.15), state="normal")
            else:
                self._cfg(it, state="hidden")
        if self.aviso is not None:
            resto = max(0.0, (self.aviso_hasta - time.time()) / AVISO_S)
            xa, xb = self.aviso_x
            y = c.coords(self.aviso_barra)[1]
            c.coords(self.aviso_barra, xa, y, xa + (xb - xa) * resto, y)

    # -------------------------------------------------------------- bucles
    def _animar(self):
        try:
            self._dibujar(time.time())
        except Exception as e:  # noqa: BLE001
            _log(f"animación: {e!r}")
        self.root.after(max(30, int(1000 / fps_de(self.vista, self.aviso is not None))), self._animar)

    def _sondear(self):
        ahora = time.time()
        try:
            datos, mtime = self.fuente.leer_estado(ahora)
            vista = interpretar(datos, ahora, mtime)
            if vista != self.vista:
                self.vista = vista
                self._poner_vista(vista)
            reloj = time.strftime("%H:%M")
            if reloj != self.reloj_txt:
                self.reloj_txt = reloj
                self._cfg(self.reloj, text=reloj)
            if self.aviso is not None and ahora >= self.aviso_hasta:
                self.aviso = None
                self._aplicar_aviso()
            if ahora - self.ultimo_aviso_sondeo >= SONDEO_AVISOS_S:
                self.ultimo_aviso_sondeo = ahora
                nuevo, ids = elegir_aviso(self.fuente.leer_avisos(ahora), self.mostrados)
                self.mostrados |= ids
                if nuevo is not None:
                    self.aviso, self.aviso_desde, self.aviso_hasta = nuevo, ahora, ahora + AVISO_S
                    self._aplicar_aviso()
            if sys.platform == "win32" and ahora - self.ultimo_encima >= REAFIRMAR_ENCIMA_S:
                self.ultimo_encima = ahora
                self.root.attributes("-topmost", True)
            self._atender_otra_instancia()
        except Exception as e:  # noqa: BLE001
            _log(f"sondeo: {e!r}")
        self.root.after(SONDEO_MS, self._sondear)

    def _atender_otra_instancia(self):
        if self.sock is None:
            return
        try:
            conn, _ = self.sock.accept()
        except (BlockingIOError, OSError):
            return
        try:
            conn.close()
        finally:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)

    # -------------------------------------------------------------- tamaño, posición y menú
    def _area(self) -> tuple:
        pant = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        return _area_monitor(self.x + self.w // 2, self.y + self.h // 2, pant)

    def _redimensionar(self):
        nuevo = self._alto_deseado()
        if nuevo == self.h:
            return
        area = self._area()
        self.y = y_al_cambiar_alto(self.y, self.h, nuevo, area)
        self.h = nuevo
        self.x, self.y = ajustar_a_area(self.x, self.y, self.w, self.h, area)
        self.c.configure(height=self.h)
        self.c.coords(self.panel, *self._puntos_redondeados(0, 0, self.w - 1, self.h - 1, self._px(14)))
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")

    def _aplicar_aviso(self):
        self._poner_aviso(self.aviso)
        self._redimensionar()

    def _alternar_texto(self):
        self.mostrar_texto = not self.mostrar_texto
        self._etiqueta_menu()
        self._poner_aviso(self.aviso)
        self._redimensionar()
        self._guardar()

    def _etiqueta_menu(self):
        self.menu.entryconfigure(0, label="Ocultar texto" if self.mostrar_texto else "Mostrar texto")

    def _otra_esquina(self):
        area = self._area()
        esq = siguiente_esquina(esquina_cercana(self.x, self.y, self.w, self.h, area))
        self.x, self.y = posicion_esquina(esq, area, self.w, self.h, self._px(16))
        self.root.geometry(f"+{self.x}+{self.y}")
        self._guardar()

    def _menu_contextual(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _agarrar(self, e):
        self.arrastre = (e.x_root - self.x, e.y_root - self.y, False)

    def _arrastrar(self, e):
        if not self.arrastre:
            return
        dx, dy, _ = self.arrastre
        self.x, self.y = e.x_root - dx, e.y_root - dy
        self.arrastre = (dx, dy, True)
        self.root.geometry(f"+{self.x}+{self.y}")

    def _soltar(self, _e):
        if self.arrastre and self.arrastre[2]:
            self._guardar()
        self.arrastre = None

    def _guardar(self):
        if not self.guardar:
            return
        ruta = HUD_DIR / "posicion.json"
        try:
            tmp = ruta.with_suffix(".tmp")
            tmp.write_text(json.dumps({"x": self.x, "y": self.y, "texto": self.mostrar_texto}), encoding="utf-8")
            os.replace(tmp, ruta)
        except OSError as e:
            _log(f"no pude guardar la posición: {e!r}")

    def cerrar(self):
        try:
            if self.sock is not None:
                self.sock.close()
        finally:
            self.root.destroy()

    def correr(self):
        self._poner_vista(self.vista)
        self._poner_aviso(None)
        self.root.mainloop()


# ------------------------------------------------------------------ entrada
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HUD de Pedro (panel tipo JARVIS)")
    ap.add_argument("--demo", nargs="?", const="ciclo", default=None,
                    help="estados de ejemplo; opcional: escuchando|oyendo|pensando|hablando|dormido|apagado|"
                         "aviso|aviso-normal para quedarse en uno")
    ap.add_argument("--esquina", choices=ESQUINAS, help="empezar en esta esquina (ignora la posición guardada)")
    ap.add_argument("--raiz", type=Path, default=RAIZ, help="carpeta de Pedro (por defecto, la que contiene hud/)")
    ap.add_argument("--puerto", type=int, default=PUERTO, help="puerto local para no abrir dos HUD")
    a = ap.parse_args(argv)

    demo = a.demo is not None
    sock = tomar_instancia(a.puerto + (1 if demo else 0))
    if sock is None:
        print("El HUD de Pedro ya está abierto.", file=sys.stderr if sys.stderr else None)
        return 0
    fuente = FuenteDemo(None if a.demo == "ciclo" else a.demo) if demo else FuenteArchivos(a.raiz)
    _dpi_nitido()
    try:
        hud = HUD(fuente, sock, esquina=a.esquina, guardar=not demo)
    except Exception as e:  # noqa: BLE001
        _log(f"no pude abrir la ventana: {e!r}")
        sock.close()
        return 1
    try:
        hud.correr()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
