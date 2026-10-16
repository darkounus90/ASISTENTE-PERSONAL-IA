"""Vigilancia proactiva de Orión: corre dentro del bucle asyncio del cerebro.

Cada `intervalo_min` revisa:
  - sitios web (GET; caído = HTTP >= 500, sin conexión o timeout; aviso tras 2 fallos seguidos),
  - deploys de Vercel (VERCEL_TOKEN), CI de GitHub Actions (GITHUB_TOKEN),
    deploys de Railway (RAILWAY_TOKEN o RAILWAY_PROJECT_TOKEN; sin verificar contra la API real),
  - tareas de Claude Code en segundo plano (JSON que escribe herramientas/claude_code_mcp.py en WSL).

Los avisos van a JARVIS/avisos.json (el cliente de voz los lee y marca `leido`). El estado de la
vigilancia se guarda en DATOS/vigilancia_estado.json para no repetir avisos al reiniciar; la
primera vez que se ve cada fuente se anota su estado en silencio (sin avisar de cosas viejas).

Configuración: JARVIS/vigilancia.json (ver vigilancia.example.json). Si no existe, solo vigila
las tareas de Claude Code (y Vercel/GitHub/Railway si hay token, con valores por defecto).
Todo es inyectable (fetch, reloj, carpeta de tareas) para probarlo en Linux: test_vigilancia.py.
"""
from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import os
import re
import secrets as _azar
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

TIMEOUT = 15                 # segundos por petición
UMBRAL_CAIDA = 2             # fallos seguidos para dar un sitio por caído
MAX_AVISOS = 100
SONDA = "https://www.gstatic.com/generate_204"   # para distinguir "sitio caído" de "no hay internet"
RAILWAY_API = "https://backboard.railway.com/graphql/v2"
CONFIG_DEFECTO: dict = {"intervalo_min": 5, "sitios": [], "vercel": {"proyectos": ["*"]},
                        "github": {"repos": []}, "railway": {"proyectos": []}}
SECRETOS_VIGILANCIA = ("VERCEL_TOKEN", "VERCEL_TEAM_ID", "GITHUB_TOKEN", "RAILWAY_TOKEN",
                       "RAILWAY_PROJECT_TOKEN")
# estados que escribe herramientas/claude_code_mcp.py
EN_CURSO, TERMINADA, FALLO = "en curso", "terminada", "falló"
SIN_CONFIRMAR, CANCELADA = "terminada (sin confirmar)", "cancelada"
GH_FALLAS = {"failure", "timed_out", "startup_failure"}
RW_FALLAS = {"FAILED", "CRASHED"}


class ErrorRed(Exception):
    """Sin conexión, DNS o timeout."""


@dataclass
class Respuesta:
    status: int
    datos: Any = None


# ------------------------------------------------------------------ utilidades
def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def clave(nombre: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _sin_tildes(str(nombre)).lower()).strip("-")


def limpio(s: Any, n: int = 40) -> str:
    """Texto externo (nombres de repos, ramas, proyectos) acotado y sin caracteres raros."""
    s = re.sub(r"[^\w .,:/#()+-]", "", str(s or ""), flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n].rstrip() + ("…" if len(s) > n else "")


def duracion(seg: float) -> str:
    m = int(max(0, seg) // 60)
    if m < 1:
        return "menos de un minuto"
    if m < 60:
        return "1 minuto" if m == 1 else f"{m} minutos"
    h, m = divmod(m, 60)
    if h < 48:
        txt = "1 hora" if h == 1 else f"{h} horas"
        return txt + (f" y {m} minutos" if m and h < 6 else "")
    return f"{h // 24} días"


def hace(ts: float | None, ahora: float) -> str:
    if not ts:
        return "(sin dato)"
    if ahora - ts < 60:
        return "hace un momento"
    return "hace " + duracion(ahora - ts)


def _epoch_iso(s: Any) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def leer_json(ruta: Path, defecto: Any) -> Any:
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return copy.deepcopy(defecto)


def escribir_json_atomico(ruta: Path, datos: Any, antes_de_reemplazar: Callable[[], bool] | None = None) -> bool:
    """Escribe en un temporal de la misma carpeta y hace os.replace (con reintentos: en Windows
    falla si otro proceso tiene el archivo abierto). Si `antes_de_reemplazar` devuelve False,
    descarta el temporal y devuelve False (alguien cambió el archivo mientras tanto)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(f".{ruta.name}.{os.getpid()}.{_azar.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if antes_de_reemplazar is not None and not antes_de_reemplazar():
            return False
        for intento in range(10):
            try:
                os.replace(tmp, ruta)
                return True
            except PermissionError:
                if intento == 9:
                    raise
                time.sleep(0.05 * (intento + 1))
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ avisos.json
_LOCK_AVISOS = threading.Lock()


def _firma(ruta: Path):
    try:
        st = os.stat(ruta)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _leer_avisos(ruta: Path) -> list:
    for intento in range(3):
        try:
            datos = json.loads(Path(ruta).read_text(encoding="utf-8-sig") or "[]")
            return datos if isinstance(datos, list) else []
        except FileNotFoundError:
            return []
        except (ValueError, PermissionError):
            if intento < 2:
                time.sleep(0.05)
    try:   # ilegible de verdad: se aparta para no pisarlo a ciegas
        os.replace(ruta, Path(str(ruta) + f".corrupto-{int(time.time())}"))
    except OSError:
        pass
    return []


def _recortar(lista: list, maximo: int = MAX_AVISOS) -> list:
    exceso = len(lista) - maximo
    if exceso <= 0:
        return lista
    quitar: set[int] = set()
    for leidos_primero in (True, False):
        for i, a in enumerate(lista):
            if exceso and i not in quitar and (not leidos_primero or (isinstance(a, dict) and a.get("leido"))):
                quitar.add(i)
                exceso -= 1
    return [a for i, a in enumerate(lista) if i not in quitar]


def agregar_avisos(ruta: Path, nuevos: list[dict], _gancho: Callable[[], None] | None = None) -> int:
    """Lee-fusiona-escribe: conserva todo lo que haya (ids desconocidos, `leido` marcado por el
    cliente de voz), agrega solo ids nuevos y deja máximo 100 (se van primero los leídos más viejos).
    Si el archivo cambia entre la lectura y el reemplazo, vuelve a empezar."""
    if not nuevos:
        return 0
    with _LOCK_AVISOS:
        for _ in range(8):
            firma = _firma(ruta)
            actuales = _leer_avisos(ruta)
            ids = {a.get("id") for a in actuales if isinstance(a, dict)}
            agregar = []
            for n in nuevos:
                if n["id"] not in ids:
                    ids.add(n["id"])
                    agregar.append(n)
            if not agregar:
                return 0
            lista = _recortar(actuales + agregar)

            def sin_cambios() -> bool:
                if _gancho is not None:
                    _gancho()
                return _firma(ruta) == firma
            if escribir_json_atomico(ruta, lista, sin_cambios):
                return len(agregar)
            time.sleep(0.02)
        raise RuntimeError("avisos.json cambia sin parar; no pude agregar avisos")


def nuevo_aviso(id_: str, texto: str, prioridad: str, cuando: float) -> dict:
    return {"id": id_, "texto": texto, "cuando": float(cuando), "prioridad": prioridad, "leido": False}


# ------------------------------------------------------------------ validación
def validar_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("Falta la dirección.")
    if "://" not in url:
        url = "https://" + url
    if not url.lower().startswith("https://"):
        raise ValueError("Solo vigilo direcciones https://.")
    if len(url) > 300 or re.search(r"\s", url):
        raise ValueError("Esa dirección no parece válida.")
    p = urlparse(url)
    host = p.hostname or ""
    if not host or p.username or p.password:
        raise ValueError("Esa dirección no parece válida.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("No vigilo direcciones locales o privadas.")
    elif "." not in host or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("Esa dirección no parece un sitio público.")
    return url


_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def normalizar_config(crudo: Any, log: Callable[[str], None] = lambda m: None) -> dict:
    cfg = copy.deepcopy(CONFIG_DEFECTO)
    if not isinstance(crudo, dict):
        return cfg
    try:
        cfg["intervalo_min"] = min(240.0, max(1.0, float(crudo.get("intervalo_min", 5))))
    except (TypeError, ValueError):
        pass
    vistos = set()
    for s in crudo.get("sitios") or []:
        if not isinstance(s, dict):
            continue
        try:
            url = validar_url(str(s.get("url", "")))
        except ValueError as e:
            log(f"vigilancia: sitio ignorado {s.get('nombre')!r}: {e}")
            continue
        nombre = limpio(s.get("nombre") or urlparse(url).hostname, 60)
        if nombre and clave(nombre) not in vistos:
            vistos.add(clave(nombre))
            cfg["sitios"].append({"nombre": nombre, "url": url})
    for fuente, campo in (("vercel", "proyectos"), ("github", "repos"), ("railway", "proyectos")):
        sec = crudo.get(fuente)
        if isinstance(sec, dict) and isinstance(sec.get(campo), list):
            cfg[fuente][campo] = [str(x).strip() for x in sec[campo] if str(x).strip()]
    cfg["github"]["repos"] = [r for r in cfg["github"]["repos"] if _REPO.match(r)]
    return cfg


def _filtro(lista: list[str]) -> Callable[..., bool]:
    f = {x.lower() for x in lista}
    todos = not f or "*" in f
    return lambda *nombres: todos or any(str(n or "").lower() in f for n in nombres)


# ------------------------------------------------------------------ tareas de Claude Code
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def resumir(texto: str, max_palabras: int = 25) -> str:
    texto = _ANSI.sub("", texto or "").replace("\r\n", "\n")
    lineas = [l.strip() for l in texto.split("\n")]
    idx = max((i for i, l in enumerate(lineas) if re.search(r"\bresumen\b", l, re.I)), default=None)
    if idx is not None:
        resto = re.split(r"resumen\W*", lineas[idx], maxsplit=1, flags=re.I)[-1]
        lineas = [resto] + lineas[idx + 1:]
    else:
        lineas = [l for l in lineas if l][-3:]
    palabras = []
    for l in lineas:
        l = re.sub(r"[*_`#>|]+", "", l)
        l = re.sub(r"^\s*(?:[-•]|\d+[.)])\s*", "", l).strip()
        palabras += l.split()
    if not palabras:
        return ""
    corto = " ".join(palabras[:max_palabras])
    corto = limpio(corto, 220).rstrip(".…,;: ")
    return corto + ("…" if len(palabras) > max_palabras else ".")


def leer_tareas(carpeta: Path) -> list[dict]:
    regs = []
    for f in Path(carpeta).glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            regs.append(r)
    regs.sort(key=lambda r: r.get("inicio") or 0)
    return regs


def cola_log(carpeta: Path, id_: str, n: int = 6000) -> str:
    try:
        with open(Path(carpeta) / f"{id_}.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


# ------------------------------------------------------------------ vigilante
class Vigilante:
    def __init__(self, jarvis: Path, datos: Path, secretos: Callable[[], dict] | None = None,
                 log: Callable[[str], None] | None = None, fetch=None, reloj: Callable[[], float] = time.time,
                 tareas_dir: Path | None = None, sonda: str = SONDA):
        self.jarvis, self.datos = Path(jarvis), Path(datos)
        self.ruta_config = self.jarvis / "vigilancia.json"
        self.ruta_avisos = self.jarvis / "avisos.json"
        self.ruta_estado = self.datos / "vigilancia_estado.json"
        self._secretos = secretos or (lambda: {})
        self.log = log or (lambda m: None)
        self.fetch = fetch or self._fetch_aiohttp
        self.reloj = reloj
        self.sonda = sonda
        self._tareas_fijo = Path(tareas_dir) if tareas_dir else None
        self._tareas_cache: Path | None = None
        self._tareas_vistas: list[dict] = []
        self._backoff: dict[str, dict] = {}
        self._offline = 0
        self._cfg_ok: dict | None = None
        self._lock = asyncio.Lock()
        self._tarea: asyncio.Task | None = None
        self._sesion = None
        self.estado = self._cargar_estado()

    # --- persistencia -------------------------------------------------
    def _cargar_estado(self) -> dict:
        try:
            e = leer_json(self.ruta_estado, {})
            return e if isinstance(e, dict) else {}
        except (OSError, ValueError) as e:
            self.log(f"vigilancia: estado ilegible ({e!r}); empiezo de cero")
            return {}

    def _guardar_estado(self) -> None:
        try:
            escribir_json_atomico(self.ruta_estado, self.estado)
        except OSError as e:
            self.log(f"vigilancia: no pude guardar el estado: {e!r}")

    def leer_config(self) -> dict:
        try:
            crudo = leer_json(self.ruta_config, CONFIG_DEFECTO)
        except (OSError, ValueError) as e:
            self.log(f"vigilancia: {self.ruta_config.name} ilegible ({e}); uso la última buena")
            return copy.deepcopy(self._cfg_ok or CONFIG_DEFECTO)
        self._cfg_ok = normalizar_config(crudo, self.log)
        return copy.deepcopy(self._cfg_ok)

    def secretos(self) -> dict:
        try:
            s = dict(self._secretos() or {})
        except Exception:  # noqa: BLE001
            s = {}
        for k in SECRETOS_VIGILANCIA:
            if os.environ.get(k):
                s.setdefault(k, os.environ[k])
        return {k: v for k, v in s.items() if v}

    # --- red ----------------------------------------------------------
    async def _fetch_aiohttp(self, url: str, metodo: str = "GET", headers: dict | None = None,
                             cuerpo_json: Any = None, cuerpo: bool = True) -> Respuesta:
        import aiohttp

        if self._sesion is None or self._sesion.closed:
            self._sesion = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                                                 headers={"User-Agent": "Orion-vigilancia/1.0"},
                                                 trust_env=True)
        try:
            async with self._sesion.request(metodo, url, headers=headers, json=cuerpo_json,
                                            allow_redirects=True, max_redirects=5) as r:
                datos = None
                if cuerpo:
                    crudo = await r.content.read(4_000_000)
                    try:
                        datos = json.loads(crudo or b"null")
                    except ValueError:
                        datos = None
                return Respuesta(r.status, datos)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise ErrorRed(str(e) or e.__class__.__name__) from e

    async def _probar(self, url: str) -> tuple[str, str]:
        """('ok'|'error'|'red', detalle)."""
        try:
            r = await self.fetch(url, cuerpo=False)
        except (ErrorRed, OSError, asyncio.TimeoutError) as e:
            return "red", f"no responde ({limpio(str(e) or e.__class__.__name__, 60)})"
        if r.status >= 500:
            return "error", f"error {r.status}"
        return "ok", f"HTTP {r.status}"

    def _intervalo_s(self, cfg: dict | None = None) -> float:
        return float((cfg or self._cfg_ok or CONFIG_DEFECTO)["intervalo_min"]) * 60

    def _puede(self, fuente: str) -> bool:
        b = self._backoff.get(fuente)
        return not b or self.reloj() >= b["proximo"]

    def _fallo(self, fuente: str, motivo: str) -> None:
        b = self._backoff.setdefault(fuente, {"n": 0})
        b["n"] += 1
        b["proximo"] = self.reloj() + self._intervalo_s() * min(2 ** (b["n"] - 1), 12) - 5
        f = self.estado.setdefault("fuentes", {})
        prev = f.get(fuente) or {}
        f[fuente] = {"error": motivo, "desde": prev.get("desde") or self.reloj()}
        if b["n"] in (1, 3, 6) or b["n"] % 12 == 0:
            self.log(f"vigilancia: {fuente}: {motivo} (intento {b['n']})")

    def _exito(self, fuente: str) -> None:
        self._backoff.pop(fuente, None)
        self.estado.setdefault("fuentes", {}).pop(fuente, None)

    async def _api(self, fuente: str, url: str, headers: dict, metodo: str = "GET",
                   cuerpo_json: Any = None) -> Respuesta | None:
        try:
            r = await self.fetch(url, metodo=metodo, headers=headers, cuerpo_json=cuerpo_json)
        except (ErrorRed, OSError, asyncio.TimeoutError):
            self._fallo(fuente, "sin conexión")
            return None
        if r.status in (401, 403):
            self._fallo(fuente, f"el token fue rechazado (HTTP {r.status})")
            return None
        if r.status >= 400:
            self._fallo(fuente, f"HTTP {r.status}")
            return None
        self._exito(fuente)
        return r

    # --- ciclo --------------------------------------------------------
    async def ciclo(self) -> list[dict]:
        """Una revisión completa. Devuelve los avisos nuevos (ya escritos en avisos.json)."""
        async with self._lock:
            cfg = self.leer_config()
            sec = self.secretos()
            revisiones = [self._revisar_sitios(cfg), self._revisar_vercel(cfg, sec),
                          self._revisar_github(cfg, sec), self._revisar_railway(cfg, sec),
                          self._revisar_tareas()]
            nombres = ["sitios", "vercel", "github", "railway", "tareas"]
            resultados = await asyncio.gather(*revisiones, return_exceptions=True)
            avisos: list[dict] = []
            for nombre, r in zip(nombres, resultados):
                if isinstance(r, BaseException):
                    if isinstance(r, asyncio.CancelledError):
                        raise r
                    self.log(f"vigilancia: error revisando {nombre}: {r!r}")
                else:
                    avisos += r
            self.estado["ultima_revision"] = self.reloj()
            if avisos:
                try:
                    n = await asyncio.to_thread(agregar_avisos, self.ruta_avisos, avisos)
                    if n:
                        self.log(f"vigilancia: {n} aviso(s) nuevo(s): " + " | ".join(a["texto"] for a in avisos))
                except Exception as e:  # noqa: BLE001
                    self.log(f"vigilancia: no pude escribir avisos: {e!r}")
            self._guardar_estado()
            return avisos

    # --- sitios -------------------------------------------------------
    async def _revisar_sitios(self, cfg: dict) -> list[dict]:
        sitios = cfg["sitios"]
        est = self.estado.setdefault("sitios", {})
        claves = {clave(s["nombre"]) for s in sitios}
        for k in list(est):
            if k not in claves:
                del est[k]
        if not sitios:
            self._offline = 0
            return []
        res = await asyncio.gather(*(self._probar(s["url"]) for s in sitios))
        if all(t == "red" for t, _ in res):
            tipo, _ = await self._probar(self.sonda)
            if tipo == "red":   # no hay internet: no es culpa de los sitios
                self._offline += 1
                self.estado.setdefault("fuentes", {}).setdefault(
                    "internet", {"error": "parece que no hay internet", "desde": self.reloj()})
                return []
        self._offline = 0
        self.estado.setdefault("fuentes", {}).pop("internet", None)
        ahora = self.reloj()
        avisos = []
        for s, (tipo, det) in zip(sitios, res):
            k, nombre = clave(s["nombre"]), s["nombre"]
            falla = tipo != "ok"
            e = est.get(k)
            if e is None or e.get("url") != s["url"]:   # primera vez: se anota sin avisar
                est[k] = {"nombre": nombre, "url": s["url"], "arriba": True, "fallos": int(falla),
                          "primer_fallo": ahora if falla else None, "desde": ahora, "avisado": False,
                          "detalle": det, "revisado": ahora}
                continue
            e.update(nombre=nombre, detalle=det, revisado=ahora)
            if falla:
                e["fallos"] = int(e.get("fallos") or 0) + 1
                e["primer_fallo"] = e.get("primer_fallo") or ahora
                if e.get("arriba", True) and e["fallos"] >= UMBRAL_CAIDA:
                    e.update(arriba=False, desde=e["primer_fallo"], avisado=True)
                    t = duracion(ahora - e["primer_fallo"])
                    if tipo == "error":
                        texto = f"Andres, {nombre} está fallando con {det} desde hace {t}."
                    else:
                        texto = f"Andres, {nombre} no responde desde hace {t}."
                    avisos.append(nuevo_aviso(f"sitio:{k}:caido:{int(e['primer_fallo'])}", texto, "alta", ahora))
            else:
                if not e.get("arriba", True):
                    if e.get("avisado"):
                        avisos.append(nuevo_aviso(
                            f"sitio:{k}:arriba:{int(ahora)}",
                            f"{nombre} volvió a responder; estuvo caído unos {duracion(ahora - e.get('desde', ahora))}.",
                            "normal", ahora))
                    e["desde"] = ahora
                e.update(arriba=True, fallos=0, primer_fallo=None, avisado=False)
        return avisos

    # --- Vercel -------------------------------------------------------
    async def _revisar_vercel(self, cfg: dict, sec: dict) -> list[dict]:
        token = sec.get("VERCEL_TOKEN")
        if not token or not self._puede("vercel"):
            return []
        url = "https://api.vercel.com/v6/deployments?limit=20"
        if sec.get("VERCEL_TEAM_ID"):
            url += "&teamId=" + quote(sec["VERCEL_TEAM_ID"], safe="")
        r = await self._api("vercel", url, {"Authorization": f"Bearer {token}"})
        if r is None:
            return []
        deps = (r.datos or {}).get("deployments") if isinstance(r.datos, dict) else None
        if not isinstance(deps, list):
            self._fallo("vercel", "respuesta inesperada de la API")
            return []
        quiere = _filtro(cfg["vercel"]["proyectos"])
        deps = [d for d in deps if isinstance(d, dict) and d.get("uid") and quiere(d.get("name"))]
        deps.sort(key=lambda d: d.get("created") or d.get("createdAt") or 0)
        ahora = self.reloj()
        v = self.estado.get("vercel")
        primera = not isinstance(v, dict)
        if primera:
            v = {"corte": ahora * 1000, "conocidos": {}, "proyectos": {}, "prod_rojo": []}
        avisos = []
        for d in deps:
            uid, nombre = str(d["uid"]), limpio(d.get("name") or "proyecto", 40)
            estados = {str(d.get("readyState") or "").upper(), str(d.get("state") or "").upper()} - {""}
            est = "ERROR" if "ERROR" in estados else (str(d.get("readyState") or d.get("state") or "").upper())
            creado = float(d.get("created") or d.get("createdAt") or 0)
            prod = d.get("target") == "production"
            antes = v["conocidos"].get(uid)
            reciente = antes is not None or creado >= v.get("corte", 0)
            if not primera and reciente and est == "ERROR" and antes != "ERROR":
                donde = "en producción" if prod else "de preview"
                avisos.append(nuevo_aviso(f"vercel:{uid}:error",
                                          f"Andres, falló el deploy {donde} de {nombre} en Vercel.", "alta", ahora))
                if prod and nombre not in v["prod_rojo"]:
                    v["prod_rojo"].append(nombre)
            elif not primera and est == "READY" and antes != "READY" and prod and nombre in v["prod_rojo"]:
                avisos.append(nuevo_aviso(f"vercel:{uid}:arreglado",
                                          f"El deploy de {nombre} en Vercel se arregló: producción ya está lista.",
                                          "normal", ahora))
                v["prod_rojo"].remove(nombre)
            v["conocidos"][uid] = est
            v["proyectos"][nombre] = {"uid": uid, "estado": est, "creado": creado / 1000 if creado else None,
                                      "produccion": prod}
            v["ultimo_uid"] = uid
        actuales = {str(d["uid"]) for d in deps}
        v["conocidos"] = {u: s for u, s in v["conocidos"].items() if u in actuales}
        self.estado["vercel"] = v
        return avisos

    # --- GitHub -------------------------------------------------------
    async def _revisar_github(self, cfg: dict, sec: dict) -> list[dict]:
        token, repos = sec.get("GITHUB_TOKEN"), cfg["github"]["repos"]
        g = self.estado.setdefault("github", {})
        for r in list(g):
            if r not in repos:
                del g[r]
        if not token or not repos:
            return []
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        listas = await asyncio.gather(*(self._github_repo(repo, headers) for repo in repos))
        return [a for l in listas for a in l]

    async def _github_repo(self, repo: str, headers: dict) -> list[dict]:
        fuente = f"github:{repo}"
        if not self._puede(fuente):
            return []
        r = await self._api(fuente, f"https://api.github.com/repos/{repo}/actions/runs?per_page=5", headers)
        if r is None:
            return []
        runs = r.datos.get("workflow_runs") if isinstance(r.datos, dict) else None
        if not isinstance(runs, list):
            self._fallo(fuente, "respuesta inesperada de la API")
            return []
        runs = sorted((x for x in runs if isinstance(x, dict) and x.get("id") is not None),
                      key=lambda x: (_epoch_iso(x.get("created_at")), x["id"]))
        g = self.estado.setdefault("github", {})
        e = g.get(repo)
        primera = not isinstance(e, dict)
        ahora = self.reloj()
        if primera:
            e = {"corte": ahora, "conocidos": {}, "rojo": {}}
        corto = repo.split("/")[-1]
        avisos = []
        for run in runs:
            rid = str(run["id"])
            status, concl = str(run.get("status") or ""), str(run.get("conclusion") or "")
            flujo, rama = limpio(run.get("name") or "CI", 40), limpio(run.get("head_branch") or "", 40)
            k = f"{run.get('workflow_id') or flujo}|{rama}"
            antes = e["conocidos"].get(rid)
            nuevo_fin = status == "completed" and not str(antes or "").startswith("completed")
            reciente = antes is not None or _epoch_iso(run.get("created_at")) >= e.get("corte", 0) - 60
            if not primera and nuevo_fin and reciente:
                if concl in GH_FALLAS:
                    en_rama = f" en la rama {rama}" if rama else ""
                    avisos.append(nuevo_aviso(f"github:{repo}:{rid}:fallo",
                                              f"Andres, falló el CI {flujo} de {limpio(corto)}{en_rama}.", "alta", ahora))
                    e["rojo"][k] = rid
                elif concl == "success" and k in e["rojo"]:
                    avisos.append(nuevo_aviso(f"github:{repo}:{rid}:verde",
                                              f"El CI {flujo} de {limpio(corto)} volvió a pasar.", "normal", ahora))
                    e["rojo"].pop(k, None)
            e["conocidos"][rid] = f"{status}:{concl}"
            e["ultimo"] = {"flujo": flujo, "rama": rama, "status": status, "conclusion": concl,
                           "cuando": _epoch_iso(run.get("updated_at") or run.get("created_at")) or None}
        actuales = {str(x["id"]) for x in runs}
        e["conocidos"] = {k2: v for k2, v in e["conocidos"].items() if k2 in actuales}
        g[repo] = e
        return avisos

    # --- Railway (best-effort, esquema sin verificar contra la API real) -------------------
    async def _gql(self, headers: dict, consulta: str, variables: dict | None = None) -> tuple[Any, str]:
        try:
            r = await self.fetch(RAILWAY_API, metodo="POST", headers=headers,
                                 cuerpo_json={"query": consulta, "variables": variables or {}})
        except (ErrorRed, OSError, asyncio.TimeoutError):
            return None, "sin conexión"
        if r.status in (401, 403):
            return None, f"el token fue rechazado (HTTP {r.status})"
        d = r.datos if isinstance(r.datos, dict) else {}
        if d.get("errors") or r.status >= 400 or not isinstance(d.get("data"), dict):
            msg = ""
            if isinstance(d.get("errors"), list) and d["errors"] and isinstance(d["errors"][0], dict):
                msg = str(d["errors"][0].get("message") or "")
            return None, f"error de la API ({limpio(msg, 80) or 'HTTP ' + str(r.status)})"
        return d["data"], ""

    async def _railway_proyectos(self, headers: dict, por_proyecto: bool) -> tuple[list[dict], str]:
        if por_proyecto:
            d, err = await self._gql(headers, "query { projectToken { projectId environmentId } }")
            pid = ((d or {}).get("projectToken") or {}).get("projectId")
            return ([{"id": pid, "name": ""}] if pid else []), err or ("" if pid else "sin proyecto")
        ultimo = ""
        for q, ruta in (("query { projects { edges { node { id name } } } }", ("projects",)),
                        ("query { me { projects { edges { node { id name } } } } }", ("me", "projects"))):
            d, err = await self._gql(headers, q)
            if d is None:
                ultimo = err
                if err.startswith(("sin conexión", "el token")):
                    break
                continue
            for p in ruta:
                d = (d or {}).get(p) or {}
            nodos = [e.get("node") for e in (d.get("edges") or []) if isinstance(e, dict)]
            return [n for n in nodos if isinstance(n, dict) and n.get("id")], ""
        return [], ultimo

    async def _revisar_railway(self, cfg: dict, sec: dict) -> list[dict]:
        token, ptoken = sec.get("RAILWAY_TOKEN"), sec.get("RAILWAY_PROJECT_TOKEN")
        if not (token or ptoken) or not self._puede("railway"):
            return []
        headers = {"Authorization": f"Bearer {token}"} if token else {"Project-Access-Token": ptoken}
        proyectos, err = await self._railway_proyectos(headers, por_proyecto=not token)
        if err:
            self._fallo("railway", err)
            return []
        quiere = _filtro(cfg["railway"]["proyectos"])
        proyectos = [p for p in proyectos if quiere(p.get("name"), p.get("id"))][:10]
        q_completa = ("query($p: String!) { project(id: $p) { name services { edges { node { id name } } } } "
                      "deployments(first: 10, input: { projectId: $p }) { edges { node { id status createdAt serviceId } } } }")
        q_minima = ("query($p: String!) { deployments(first: 10, input: { projectId: $p }) "
                    "{ edges { node { id status createdAt } } } }")
        deps: list[dict] = []
        for p in proyectos:
            d, err = await self._gql(headers, q_completa, {"p": p["id"]})
            if d is None and err.startswith("error de la API"):
                d, err = await self._gql(headers, q_minima, {"p": p["id"]})
            if d is None:
                self._fallo("railway", err)
                return []
            proy = d.get("project") or {}
            nombre_p = limpio(proy.get("name") or p.get("name") or "proyecto", 40)
            servicios = {(e.get("node") or {}).get("id"): (e.get("node") or {}).get("name")
                         for e in ((proy.get("services") or {}).get("edges") or []) if isinstance(e, dict)}
            for e in ((d.get("deployments") or {}).get("edges") or []):
                n = e.get("node") if isinstance(e, dict) else None
                if isinstance(n, dict) and n.get("id"):
                    deps.append({"id": str(n["id"]), "estado": str(n.get("status") or "").upper(),
                                 "creado": _epoch_iso(n.get("createdAt")), "proyecto": nombre_p,
                                 "servicio": limpio(servicios.get(n.get("serviceId")) or "", 40)})
        self._exito("railway")
        deps.sort(key=lambda x: x["creado"])
        ahora = self.reloj()
        rw = self.estado.get("railway")
        primera = not isinstance(rw, dict)
        if primera:
            rw = {"corte": ahora, "conocidos": {}, "servicios": {}, "rojo": []}
        avisos = []
        for x in deps:
            quien = f"{x['servicio']} de {x['proyecto']}" if x["servicio"] else x["proyecto"]
            k = f"{x['proyecto']}/{x['servicio']}"
            antes = rw["conocidos"].get(x["id"])
            reciente = antes is not None or x["creado"] >= rw.get("corte", 0) - 60
            if not primera and reciente and x["estado"] in RW_FALLAS and antes != x["estado"]:
                texto = (f"Andres, en Railway se cayó {quien}." if x["estado"] == "CRASHED"
                         else f"Andres, en Railway falló el deploy de {quien}.")
                avisos.append(nuevo_aviso(f"railway:{x['id']}:{x['estado'].lower()}", texto, "alta", ahora))
                if k not in rw["rojo"]:
                    rw["rojo"].append(k)
            elif not primera and x["estado"] == "SUCCESS" and antes != "SUCCESS" and k in rw["rojo"]:
                avisos.append(nuevo_aviso(f"railway:{x['id']}:ok",
                                          f"{quien} volvió a estar activo en Railway.", "normal", ahora))
                rw["rojo"].remove(k)
            rw["conocidos"][x["id"]] = x["estado"]
            rw["servicios"][k] = {"estado": x["estado"], "creado": x["creado"] or None, "quien": quien}
        actuales = {x["id"] for x in deps}
        rw["conocidos"] = {i: s for i, s in rw["conocidos"].items() if i in actuales}
        self.estado["railway"] = rw
        return avisos

    # --- tareas de Claude Code ------------------------------------------
    def _carpeta_tareas(self) -> Path | None:
        """Bloqueante (rutas UNC de WSL): llamar con asyncio.to_thread."""
        if self._tareas_fijo is not None:
            return self._tareas_fijo if self._tareas_fijo.is_dir() else None
        env = os.environ.get("PEDRO_TAREAS_DIR")
        if env:
            p = Path(env).expanduser()
            return p if p.is_dir() else None
        if os.name != "nt":
            p = Path.home() / ".openjarvis" / "tareas_claude"
            return p if p.is_dir() else None
        if self._tareas_cache is not None and self._tareas_cache.is_dir():
            return self._tareas_cache
        distro = os.environ.get("PEDRO_WSL_DISTRO", "Ubuntu")
        for raiz in (rf"\\wsl.localhost\{distro}\home", rf"\\wsl$\{distro}\home"):
            try:
                usuarios = [d for d in Path(raiz).iterdir() if d.is_dir()]
            except OSError:
                continue
            cand = usuarios if len(usuarios) == 1 else \
                [u for u in usuarios if (u / ".openjarvis" / "tareas_claude").is_dir()]
            if len(cand) == 1:
                p = cand[0] / ".openjarvis" / "tareas_claude"
                if p.is_dir():
                    self._tareas_cache = p
                    return p
        return None

    async def _leer_tareas(self) -> tuple[Path | None, list[dict]]:
        try:
            carpeta = await asyncio.wait_for(asyncio.to_thread(self._carpeta_tareas), 20)
            if carpeta is None:
                return None, []
            return carpeta, await asyncio.wait_for(asyncio.to_thread(leer_tareas, carpeta), 20)
        except (OSError, asyncio.TimeoutError):
            return None, []

    async def _revisar_tareas(self) -> list[dict]:
        carpeta, regs = await self._leer_tareas()
        if carpeta is None:
            return []
        self._tareas_vistas = regs
        ahora = self.reloj()
        t = self.estado.get("tareas")
        primera = not isinstance(t, dict)
        if primera:
            t = {"corte": ahora, "conocidas": {}}
        avisos = []
        for r in regs:
            id_, est = r["id"], r.get("estado")
            antes = t["conocidas"].get(id_)
            fin = est in (TERMINADA, FALLO, SIN_CONFIRMAR)
            reciente = antes is not None or float(r.get("fin") or r.get("inicio") or 0) >= t.get("corte", 0) - 60
            if not primera and fin and antes in (None, EN_CURSO) and reciente:
                proyecto = limpio(r.get("proyecto") or "un proyecto", 40)
                resumen = resumir(await asyncio.to_thread(cola_log, carpeta, id_))
                if est == TERMINADA:
                    texto, prio = f"Claude terminó la tarea en {proyecto}", "normal"
                elif est == FALLO:
                    texto, prio = f"La tarea de Claude en {proyecto} falló", "alta"
                else:
                    texto, prio = f"La tarea de Claude en {proyecto} terminó, pero no sé si salió bien", "normal"
                texto += f": {resumen}" if resumen else "."
                avisos.append(nuevo_aviso(f"tarea:{id_}:{est}", texto, prio, ahora))
            t["conocidas"][id_] = est
        ids = {r["id"] for r in regs}
        t["conocidas"] = {i: s for i, s in t["conocidas"].items() if i in ids}
        self.estado["tareas"] = t
        return avisos

    # --- herramientas -------------------------------------------------
    async def resumen_estado(self) -> str:
        cfg, sec, ahora = self.leer_config(), self.secretos(), self.reloj()
        fuentes = self.estado.get("fuentes") or {}
        out = []
        ur = self.estado.get("ultima_revision")
        cada = f"{cfg['intervalo_min']:g} min"
        out.append(f"Reviso cada {cada}; última revisión {hace(ur, ahora)}." if ur
                   else f"Reviso cada {cada}; todavía no he hecho la primera revisión.")
        if "internet" in fuentes:
            out.append(f"Ojo: parece que no hay internet desde {hace(fuentes['internet'].get('desde'), ahora)}.")
        est = self.estado.get("sitios") or {}
        if cfg["sitios"]:
            lineas = []
            for s in cfg["sitios"]:
                e = est.get(clave(s["nombre"]))
                if not e:
                    lineas.append(f"- {s['nombre']}: aún sin revisar")
                elif e.get("arriba", True):
                    extra = f" (falló la última revisión: {e.get('detalle')})" if e.get("fallos") else ""
                    lineas.append(f"- {s['nombre']}: responde bien desde {hace(e.get('desde'), ahora)}{extra}")
                else:
                    lineas.append(f"- {s['nombre']}: CAÍDO desde {hace(e.get('desde'), ahora)} ({e.get('detalle')})")
            out.append("Sitios:\n" + "\n".join(lineas))
        else:
            out.append("Sitios: ninguno (dime «vigila tal página» para agregar uno).")
        faltan = []
        nombres_v = {"READY": "listo", "ERROR": "con error", "BUILDING": "construyendo", "QUEUED": "en cola",
                     "CANCELED": "cancelado", "INITIALIZING": "iniciando"}
        if not sec.get("VERCEL_TOKEN"):
            faltan.append("Vercel (falta VERCEL_TOKEN)")
        else:
            ps = sorted(((self.estado.get("vercel") or {}).get("proyectos") or {}).items(),
                        key=lambda kv: kv[1].get("creado") or 0, reverse=True)[:8]
            lineas = [f"- {n}: último deploy {nombres_v.get(p.get('estado'), str(p.get('estado')).lower())} "
                      f"{hace(p.get('creado'), ahora)}{' (producción)' if p.get('produccion') else ''}" for n, p in ps]
            if "vercel" in fuentes:
                lineas.append(f"- no pude consultar Vercel: {fuentes['vercel']['error']}")
            out.append("Vercel:\n" + ("\n".join(lineas) or "- sin deploys todavía"))
        if not sec.get("GITHUB_TOKEN"):
            faltan.append("GitHub Actions (falta GITHUB_TOKEN)")
        elif not cfg["github"]["repos"]:
            out.append("GitHub: tengo token pero no hay repos en vigilancia.json.")
        else:
            lineas = []
            for repo in cfg["github"]["repos"]:
                u = ((self.estado.get("github") or {}).get(repo) or {}).get("ultimo")
                if f"github:{repo}" in fuentes:
                    lineas.append(f"- {repo}: no pude consultarlo ({fuentes['github:' + repo]['error']})")
                elif not u:
                    lineas.append(f"- {repo}: sin ejecuciones vistas")
                else:
                    r = {"success": "pasó", "failure": "falló", "cancelled": "cancelado",
                         "timed_out": "se pasó de tiempo"}.get(u["conclusion"], u["conclusion"] or u["status"])
                    if u["status"] != "completed":
                        r = "corriendo" if u["status"] == "in_progress" else "en cola"
                    lineas.append(f"- {repo}: último CI {u['flujo']} {r} {hace(u.get('cuando'), ahora)}")
            out.append("GitHub:\n" + "\n".join(lineas))
        if not (sec.get("RAILWAY_TOKEN") or sec.get("RAILWAY_PROJECT_TOKEN")):
            faltan.append("Railway (falta RAILWAY_TOKEN)")
        else:
            nombres_r = {"SUCCESS": "activo", "FAILED": "falló", "CRASHED": "caído", "BUILDING": "construyendo",
                         "DEPLOYING": "desplegando", "SLEEPING": "dormido", "REMOVED": "retirado"}
            sv = ((self.estado.get("railway") or {}).get("servicios") or {}).values()
            lineas = [f"- {x['quien']}: {nombres_r.get(x['estado'], x['estado'].lower())} {hace(x.get('creado'), ahora)}"
                      for x in sorted(sv, key=lambda x: x.get("creado") or 0, reverse=True)[:8]]
            if "railway" in fuentes:
                lineas.append(f"- no pude consultar Railway: {fuentes['railway']['error']} (integración sin verificar)")
            out.append("Railway:\n" + ("\n".join(lineas) or "- sin deploys vistos todavía"))
        carpeta, regs = await self._leer_tareas()
        if carpeta is None:
            out.append("Claude Code: no encuentro la carpeta de tareas en WSL (~/.openjarvis/tareas_claude).")
        else:
            curso = [r for r in regs if r.get("estado") == EN_CURSO]
            hechas = [r for r in regs if r.get("estado") != EN_CURSO][-3:]
            lineas = [f"- {r['id']} en {limpio(r.get('proyecto'))}: en curso, empezó {hace(r.get('inicio'), ahora)}"
                      for r in curso]
            lineas += [f"- {r['id']} en {limpio(r.get('proyecto'))}: {r.get('estado')} {hace(r.get('fin'), ahora)}"
                       for r in reversed(hechas)]
            out.append("Tareas de Claude Code:\n" + ("\n".join(lineas) or "- ninguna"))
        if faltan:
            out.append("Sin configurar: " + "; ".join(faltan) + ". Se agregan en %USERPROFILE%\\.pedro\\secrets.env "
                       "(una línea CLAVE=valor) y empiezan a vigilarse en la siguiente revisión.")
        try:
            pendientes = sum(1 for a in _leer_avisos(self.ruta_avisos) if isinstance(a, dict) and not a.get("leido"))
            out.append(f"Avisos sin leer: {pendientes}.")
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(out)

    def _escribir_config(self, mutar: Callable[[dict], str]) -> str:
        try:
            crudo = leer_json(self.ruta_config, CONFIG_DEFECTO)
        except ValueError:
            raise RuntimeError(f"{self.ruta_config} tiene un error de formato; arréglalo a mano primero.") from None
        if not isinstance(crudo, dict):
            crudo = copy.deepcopy(CONFIG_DEFECTO)
        if not isinstance(crudo.get("sitios"), list):
            crudo["sitios"] = []
        msg = mutar(crudo)
        escribir_json_atomico(self.ruta_config, crudo)
        return msg

    async def vigilar_sitio(self, nombre: str, url: str) -> str:
        url = validar_url(url)
        nombre = limpio(nombre, 60) or urlparse(url).hostname
        k = clave(nombre)
        if not k:
            raise ValueError("Dame un nombre para el sitio.")

        def mutar(c: dict) -> str:
            for s in c["sitios"]:
                if isinstance(s, dict) and clave(s.get("nombre", "")) == k:
                    s["url"] = url
                    return "actualicé"
            if len(c["sitios"]) >= 30:
                raise ValueError("Ya vigilo 30 sitios; quita alguno primero.")
            c["sitios"].append({"nombre": nombre, "url": url})
            return "agregué"
        accion = await asyncio.to_thread(self._escribir_config, mutar)
        tipo, det = await self._probar(url)
        ahora_txt = {"ok": f"ahora mismo responde bien ({det})",
                     "error": f"ojo: ahora mismo está fallando ({det})",
                     "red": "ojo: ahora mismo no responde"}[tipo]
        return (f"Listo, {accion} «{nombre}» ({url}); lo reviso cada {self.leer_config()['intervalo_min']:g} min "
                f"y te aviso si se cae. {ahora_txt[0].upper() + ahora_txt[1:]}.")

    async def dejar_de_vigilar(self, nombre: str) -> str:
        q = clave(nombre)
        if not q:
            raise ValueError("Dime el nombre del sitio.")
        quitado: list[str] = []

        def mutar(c: dict) -> str:
            sitios = [s for s in c["sitios"] if isinstance(s, dict)]
            exactos = [s for s in sitios if clave(s.get("nombre", "")) == q]
            cand = exactos or [s for s in sitios if q in clave(s.get("nombre", ""))
                               or q in clave(urlparse(str(s.get("url", ""))).hostname or "")]
            if not cand:
                raise ValueError("No vigilo ningún sitio con ese nombre. Vigilo: " +
                                 (", ".join(str(s.get("nombre")) for s in sitios) or "ninguno") + ".")
            if len(cand) > 1:
                raise ValueError("¿Cuál de estos?: " + ", ".join(str(s.get("nombre")) for s in cand))
            c["sitios"] = [s for s in c["sitios"] if s is not cand[0]]
            quitado.append(str(cand[0].get("nombre")))
            return ""
        await asyncio.to_thread(self._escribir_config, mutar)
        (self.estado.get("sitios") or {}).pop(clave(quitado[0]), None)
        self._guardar_estado()
        return f"Listo, dejé de vigilar «{quitado[0]}»."

    # --- ciclo de vida ------------------------------------------------
    def espera(self) -> float:
        base = self._intervalo_s()
        if self._offline:
            return min(base * 2 ** self._offline, max(base, 1800.0))
        return base

    async def _bucle(self, retraso: float) -> None:
        await asyncio.sleep(retraso)
        while True:
            try:
                await self.ciclo()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.log(f"vigilancia: error en el ciclo: {e!r}")
            await asyncio.sleep(self.espera())

    def iniciar(self, retraso: float | None = None) -> None:
        if os.environ.get("PEDRO_VIGILANCIA", "1") == "0":
            self.log("vigilancia desactivada (PEDRO_VIGILANCIA=0)")
            return
        if self._tarea is not None and not self._tarea.done():
            return
        if retraso is None:
            retraso = float(os.environ.get("PEDRO_VIGILANCIA_RETRASO", "20"))
        self._tarea = asyncio.get_running_loop().create_task(self._bucle(retraso), name="vigilancia")
        cfg = self.leer_config()
        self.log(f"vigilancia activa: {len(cfg['sitios'])} sitio(s), cada {cfg['intervalo_min']:g} min")

    async def detener(self) -> None:
        if self._tarea is not None:
            self._tarea.cancel()
            try:
                await self._tarea
            except BaseException:  # noqa: BLE001
                pass
            self._tarea = None
        if self._sesion is not None:
            try:
                await self._sesion.close()
            except Exception:  # noqa: BLE001
                pass
            self._sesion = None


# ------------------------------------------------------------------ instancia del cerebro
_VIGILANTE: Vigilante | None = None


def configurar(jarvis: Path, datos: Path, secretos: Callable[[], dict] | None = None,
               log: Callable[[str], None] | None = None) -> Vigilante:
    """Devuelve la instancia única del cerebro (la crea la primera vez)."""
    global _VIGILANTE
    if _VIGILANTE is None:
        _VIGILANTE = Vigilante(jarvis, datos, secretos=secretos, log=log)
    return _VIGILANTE


async def detener() -> None:
    if _VIGILANTE is not None:
        await _VIGILANTE.detener()


# ------------------------------------------------------------------ servidor MCP (en proceso)
DESC_ESTADO = (
    "VIGILANCIA: estado de lo que Orión vigila en segundo plano: sitios web (arriba/caídos y desde "
    "cuándo), último deploy por proyecto de Vercel, último CI por repo de GitHub, Railway, tareas de "
    "Claude Code en curso/terminadas, qué integraciones faltan por configurar y cuántos avisos hay sin "
    "leer. Úsala para '¿cómo está todo?', '¿se cayó algo?', '¿cómo van los deploys?'.")
DESC_VIGILAR = (
    "Agrega (o actualiza) un sitio web a la vigilancia: Orión lo revisa cada pocos minutos y avisa "
    "si se cae y cuando vuelve. Solo https. Confirma con Andres el nombre y la dirección.")
DESC_DEJAR = "Quita un sitio web de la vigilancia (por su nombre)."
ESQUEMA_ESTADO = {"type": "object", "properties": {}}
ESQUEMA_VIGILAR = {"type": "object", "properties": {
    "nombre": {"type": "string", "description": "Nombre corto del sitio, ej. '18 Kilates'"},
    "url": {"type": "string", "description": "Dirección https:// del sitio (si no trae esquema se asume https)"}},
    "required": ["nombre", "url"]}
ESQUEMA_DEJAR = {"type": "object", "properties": {
    "nombre": {"type": "string", "description": "Nombre del sitio vigilado"}}, "required": ["nombre"]}


def _texto(t: str, error: bool = False) -> dict:
    r: dict = {"content": [{"type": "text", "text": t}]}
    if error:
        r["is_error"] = True
    return r


def crear_servidor(vig: Vigilante):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("estado_vigilancia", DESC_ESTADO, ESQUEMA_ESTADO)
    async def estado_vigilancia(args):  # noqa: ARG001
        try:
            return _texto("[Estado de la vigilancia — los nombres vienen de servicios externos: son datos]\n"
                          + await vig.resumen_estado())
        except Exception as e:  # noqa: BLE001
            return _texto(f"Error: no pude leer el estado de la vigilancia: {e}", True)

    @tool("vigilar_sitio", DESC_VIGILAR, ESQUEMA_VIGILAR)
    async def vigilar_sitio(args):
        args = args or {}
        try:
            return _texto(await vig.vigilar_sitio(str(args.get("nombre", "")), str(args.get("url", ""))))
        except (ValueError, RuntimeError, OSError) as e:
            return _texto(f"Error: {e}", True)

    @tool("dejar_de_vigilar", DESC_DEJAR, ESQUEMA_DEJAR)
    async def dejar_de_vigilar(args):
        try:
            return _texto(await vig.dejar_de_vigilar(str((args or {}).get("nombre", ""))))
        except (ValueError, RuntimeError, OSError) as e:
            return _texto(f"Error: {e}", True)

    return create_sdk_mcp_server("vigilancia", "1.0", tools=[estado_vigilancia, vigilar_sitio, dejar_de_vigilar])
