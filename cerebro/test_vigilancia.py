"""Pruebas de cerebro/vigilancia.py (sin red, sin Windows). Correr: python3 cerebro/test_vigilancia.py"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vigilancia as V  # noqa: E402

for _k in V.SECRETOS_VIGILANCIA + ("PEDRO_TAREAS_DIR", "PEDRO_VIGILANCIA"):   # aislar del entorno
    __import__("os").environ.pop(_k, None)

SITIO = "https://www.18kilates.example.com"
VERCEL = "https://api.vercel.com/v6/deployments?limit=20"


class Reloj:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def pasar(self, minutos: float) -> None:
        self.t += minutos * 60


class Red:
    """fetch falso: respuestas por URL (Respuesta, excepción o función)."""

    def __init__(self):
        self.r: dict = {}
        self.llamadas: list = []

    async def __call__(self, url, metodo="GET", headers=None, cuerpo_json=None, cuerpo=True):
        self.llamadas.append((metodo, url, headers, cuerpo_json))
        base = url.split("&teamId=")[0]
        x = self.r.get(url, self.r.get(base, V.Respuesta(200)))
        if callable(x) and not isinstance(x, V.Respuesta):
            x = x(url, cuerpo_json)
        if isinstance(x, BaseException):
            raise x
        return x


def armar(tmp: Path, config: dict | None = None, secretos: dict | None = None, tareas: Path | None = None):
    jarvis, datos = tmp / "jarvis", tmp / "datos"
    jarvis.mkdir(exist_ok=True)
    datos.mkdir(exist_ok=True)
    if config is not None:
        (jarvis / "vigilancia.json").write_text(json.dumps(config), encoding="utf-8")
    reloj, red, logs = Reloj(), Red(), []
    vig = V.Vigilante(jarvis, datos, secretos=lambda: dict(secretos or {}), log=logs.append, fetch=red,
                      reloj=reloj, tareas_dir=tareas or (tmp / "no-existe"))
    return vig, reloj, red, logs


def avisos(vig) -> list:
    try:
        return json.loads(vig.ruta_avisos.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def correr(vig):
    return asyncio.run(vig.ciclo())


# ------------------------------------------------------------------ sitios
def prueba_sitios():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = {"intervalo_min": 5, "sitios": [{"nombre": "18 Kilates", "url": SITIO}]}
        vig, reloj, red, _ = armar(tmp, cfg)
        assert correr(vig) == []                           # primera vez: arriba, en silencio
        red.r[SITIO] = V.Respuesta(503)
        reloj.pasar(5)
        assert correr(vig) == []                           # 1 fallo: todavía no
        red.r[SITIO] = V.ErrorRed("timeout")               # 2º fallo (internet ok: la sonda responde 200)
        reloj.pasar(5)
        n = correr(vig)
        assert len(n) == 1 and n[0]["prioridad"] == "alta", n
        assert n[0]["texto"] == "Andres, 18 Kilates no responde desde hace 5 minutos.", n[0]["texto"]
        assert n[0]["leido"] is False and isinstance(n[0]["cuando"], float)
        reloj.pasar(5)
        assert correr(vig) == []                           # sigue caído: no repite
        # reinicio del cerebro con el sitio caído: no vuelve a avisar
        vig2 = V.Vigilante(vig.jarvis, vig.datos, fetch=red, reloj=reloj, tareas_dir=tmp / "x")
        reloj.pasar(5)
        assert asyncio.run(vig2.ciclo()) == []
        red.r[SITIO] = V.Respuesta(200)
        reloj.pasar(5)
        n = asyncio.run(vig2.ciclo())
        assert len(n) == 1 and n[0]["prioridad"] == "normal", n
        assert n[0]["texto"].startswith("18 Kilates volvió a responder; estuvo caído unos 2"), n[0]["texto"]
        assert len(avisos(vig2)) == 2
        # 5xx dos veces seguidas -> aviso con el código
        red.r[SITIO] = V.Respuesta(502)
        for _ in range(2):
            reloj.pasar(5)
            n = asyncio.run(vig2.ciclo())
        assert len(n) == 1 and "error 502" in n[0]["texto"], n
        # un fallo suelto no avisa
        red.r[SITIO] = V.Respuesta(200)
        reloj.pasar(5)
        asyncio.run(vig2.ciclo())
        red.r[SITIO] = V.ErrorRed("x")
        reloj.pasar(5)
        assert asyncio.run(vig2.ciclo()) == []
        red.r[SITIO] = V.Respuesta(200)
        reloj.pasar(5)
        assert asyncio.run(vig2.ciclo()) == []


def prueba_sin_internet():
    with tempfile.TemporaryDirectory() as d:
        cfg = {"sitios": [{"nombre": "18 Kilates", "url": SITIO}]}
        vig, reloj, red, _ = armar(Path(d), cfg)
        correr(vig)
        red.r[SITIO] = V.ErrorRed("dns")
        red.r[V.SONDA] = V.ErrorRed("dns")
        for _ in range(4):
            reloj.pasar(5)
            assert correr(vig) == []                       # sin internet: no culpa al sitio
        assert vig.estado["sitios"]["18-kilates"]["arriba"] is True
        assert vig.espera() > 5 * 60                       # se espacia (backoff)
        assert "no hay internet" in asyncio.run(vig.resumen_estado())


# ------------------------------------------------------------------ Vercel
def dep(uid, nombre, estado, creado, prod=True):
    return {"uid": uid, "name": nombre, "state": estado, "readyState": estado, "created": creado,
            "target": "production" if prod else None}


def prueba_vercel():
    with tempfile.TemporaryDirectory() as d:
        vig, reloj, red, _ = armar(Path(d), {"vercel": {"proyectos": ["*"]}},
                                   {"VERCEL_TOKEN": "tok", "VERCEL_TEAM_ID": "team_1"})
        t = reloj.t * 1000
        viejos = [dep("d1", "kilates", "ERROR", t - 9e6), dep("d2", "check", "READY", t - 8e6)]
        red.r[VERCEL] = V.Respuesta(200, {"deployments": viejos})
        assert correr(vig) == []                           # primera vez: el ERROR viejo no se avisa
        metodo, url, headers, _ = red.llamadas[-1]
        assert url == VERCEL + "&teamId=team_1" and headers["Authorization"] == "Bearer tok"
        assert vig.estado["vercel"]["ultimo_uid"] == "d2"
        reloj.pasar(5)
        nuevos = [dep("d3", "kilates", "ERROR", t + 1000), dep("d4", "check", "BUILDING", t + 2000, prod=False)]
        red.r[VERCEL] = V.Respuesta(200, {"deployments": nuevos + viejos})
        n = correr(vig)
        assert [a["id"] for a in n] == ["vercel:d3:error"], n
        assert n[0]["prioridad"] == "alta" and "kilates" in n[0]["texto"] and "producción" in n[0]["texto"]
        reloj.pasar(5)
        assert correr(vig) == []                           # misma respuesta: nada nuevo
        reloj.pasar(5)
        nuevos = [dep("d5", "kilates", "READY", t + 3000), dep("d3", "kilates", "ERROR", t + 1000),
                  dep("d4", "check", "ERROR", t + 2000, prod=False)]
        red.r[VERCEL] = V.Respuesta(200, {"deployments": nuevos + viejos})
        n = correr(vig)
        ids = sorted(a["id"] for a in n)
        assert ids == ["vercel:d4:error", "vercel:d5:arreglado"], ids   # BUILDING -> ERROR, y se arregló
        arreglo = [a for a in n if a["id"] == "vercel:d5:arreglado"][0]
        assert arreglo["prioridad"] == "normal" and "se arregló" in arreglo["texto"]
        assert "de preview" in [a for a in n if a["id"] == "vercel:d4:error"][0]["texto"]
        assert len(avisos(vig)) == 3


def prueba_backoff_y_fallo_aislado():
    with tempfile.TemporaryDirectory() as d:
        cfg = {"sitios": [{"nombre": "Check", "url": "https://check.example.com"}]}
        vig, reloj, red, _ = armar(Path(d), cfg, {"VERCEL_TOKEN": "t", "RAILWAY_TOKEN": "r"})
        red.r[VERCEL] = V.ErrorRed("caído")
        red.r[V.RAILWAY_API] = lambda url, body: (_ for _ in ()).throw(RuntimeError("bug inesperado"))
        correr(vig)                                        # railway explota: no rompe lo demás
        assert vig.estado["sitios"]["check"]["arriba"] is True
        llamadas = []
        for _ in range(8):
            reloj.pasar(5)
            antes = len([c for c in red.llamadas if c[1].startswith(VERCEL)])
            correr(vig)
            llamadas.append(len([c for c in red.llamadas if c[1].startswith(VERCEL)]) - antes)
        assert sum(llamadas) < 8, llamadas                 # se salta ciclos tras fallos seguidos
        assert "no pude consultar Vercel: sin conexión" in asyncio.run(vig.resumen_estado())


# ------------------------------------------------------------------ GitHub y Railway
def prueba_github():
    with tempfile.TemporaryDirectory() as d:
        vig, reloj, red, _ = armar(Path(d), {"github": {"repos": ["darkounus90/x", "mal repo"]}},
                                   {"GITHUB_TOKEN": "g"})
        url = "https://api.github.com/repos/darkounus90/x/actions/runs?per_page=5"

        def run(i, concl, status="completed", minutos=0):
            ts = reloj.t + minutos * 60
            iso = __import__("datetime").datetime.fromtimestamp(ts, __import__("datetime").timezone.utc).isoformat()
            return {"id": i, "name": "build", "workflow_id": 7, "head_branch": "main", "status": status,
                    "conclusion": concl, "created_at": iso, "updated_at": iso}
        red.r[url] = V.Respuesta(200, {"workflow_runs": [run(1, "failure", minutos=-60)]})
        assert correr(vig) == []
        reloj.pasar(5)
        red.r[url] = V.Respuesta(200, {"workflow_runs": [run(2, None, "in_progress"), run(1, "failure", minutos=-65)]})
        assert correr(vig) == []
        reloj.pasar(5)
        red.r[url] = V.Respuesta(200, {"workflow_runs": [run(2, "failure", minutos=-5), run(1, "failure", minutos=-70)]})
        n = correr(vig)
        assert len(n) == 1 and n[0]["texto"] == "Andres, falló el CI build de x en la rama main." and n[0]["prioridad"] == "alta", n
        reloj.pasar(5)
        red.r[url] = V.Respuesta(200, {"workflow_runs": [run(3, "success"), run(2, "failure", minutos=-10)]})
        n = correr(vig)
        assert len(n) == 1 and "volvió a pasar" in n[0]["texto"] and n[0]["prioridad"] == "normal", n
        assert all("mal repo" not in c[1] for c in red.llamadas)


def prueba_railway():
    with tempfile.TemporaryDirectory() as d:
        vig, reloj, red, _ = armar(Path(d), {}, {"RAILWAY_TOKEN": "r"})
        estado = {"deps": [{"id": "a", "status": "SUCCESS", "createdAt": "2020-01-01T00:00:00Z", "serviceId": "s1"}]}

        def gql(url, body):
            q = body["query"]
            if "projects" in q:
                return V.Respuesta(200, {"data": {"projects": {"edges": [{"node": {"id": "p1", "name": "check"}}]}}})
            return V.Respuesta(200, {"data": {
                "project": {"name": "check", "services": {"edges": [{"node": {"id": "s1", "name": "api"}}]}},
                "deployments": {"edges": [{"node": n} for n in estado["deps"]]}}})
        red.r[V.RAILWAY_API] = gql
        assert correr(vig) == []
        reloj.pasar(5)
        estado["deps"].insert(0, {"id": "b", "status": "CRASHED", "createdAt": "2030-01-01T00:00:00Z", "serviceId": "s1"})
        n = correr(vig)
        assert len(n) == 1 and n[0]["texto"] == "Andres, en Railway se cayó api de check.", n
        reloj.pasar(5)
        assert correr(vig) == []
        red.r[V.RAILWAY_API] = V.Respuesta(200, {"errors": [{"message": "Not Authorized"}]})
        reloj.pasar(5)
        assert correr(vig) == []
        assert "Not Authorized" in vig.estado["fuentes"]["railway"]["error"]


# ------------------------------------------------------------------ primera vez
def prueba_primera_vez_sin_avisos():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        tareas = tmp / "tareas"
        tareas.mkdir()
        (tareas / "aaa111.json").write_text(json.dumps({"id": "aaa111", "proyecto": "prueba2", "estado": "falló",
                                                        "inicio": 1, "fin": 2}), encoding="utf-8")
        cfg = {"sitios": [{"nombre": "18 Kilates", "url": SITIO}], "github": {"repos": ["o/r"]}}
        vig, reloj, red, _ = armar(tmp, cfg, {"VERCEL_TOKEN": "t", "GITHUB_TOKEN": "g"}, tareas)
        red.r[SITIO] = V.Respuesta(500)
        red.r[VERCEL] = V.Respuesta(200, {"deployments": [dep("e1", "x", "ERROR", reloj.t * 1000 - 1)]})
        red.r["https://api.github.com/repos/o/r/actions/runs?per_page=5"] = V.Respuesta(200, {"workflow_runs": [
            {"id": 9, "name": "ci", "status": "completed", "conclusion": "failure", "created_at": "2026-01-01T00:00:00Z"}]})
        assert correr(vig) == []
        assert avisos(vig) == [] and not vig.ruta_avisos.exists()
        assert vig.ruta_estado.exists()
        e = json.loads(vig.ruta_estado.read_text(encoding="utf-8"))
        assert set(e) >= {"sitios", "vercel", "github", "tareas", "ultima_revision"}
        # sin vigilancia.json: valores por defecto (sin sitios) y aun así vigila tareas
        vig.ruta_config.unlink()
        assert vig.leer_config()["sitios"] == [] and vig.leer_config()["intervalo_min"] == 5


# ------------------------------------------------------------------ avisos.json
def prueba_fusion_avisos():
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "avisos.json"
        ruta.write_text(json.dumps([
            {"id": "voz-propio", "texto": "algo del cliente de voz", "cuando": 1.0, "prioridad": "normal", "leido": True,
             "extra": {"x": 1}},
            {"id": "a1", "texto": "uno", "cuando": 2.0, "prioridad": "alta", "leido": False}]), encoding="utf-8")
        nuevo = V.nuevo_aviso("a2", "dos", "normal", 3.0)
        veces = []

        def cliente_de_voz_marca_leido():   # el cliente de voz escribe justo antes de nuestro reemplazo
            if not veces:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
                datos[1]["leido"] = True
                datos.append({"id": "voz-2", "texto": "otro", "cuando": 2.5, "leido": False})
                ruta.write_text(json.dumps(datos), encoding="utf-8")
                import os
                os.utime(ruta, ns=(1, 1))                   # fuerza firma distinta aunque el reloj sea grueso
            veces.append(1)
        assert V.agregar_avisos(ruta, [nuevo, nuevo], _gancho=cliente_de_voz_marca_leido) == 1
        assert len(veces) == 2                              # detectó el cambio y reintentó
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        assert [a["id"] for a in datos] == ["voz-propio", "a1", "voz-2", "a2"], datos
        assert datos[0]["extra"] == {"x": 1} and datos[0]["leido"] is True
        assert datos[1]["leido"] is True                    # la marca del cliente de voz no se perdió
        assert V.agregar_avisos(ruta, [nuevo]) == 0         # dedupe por id
        # tope de 100: se van primero los leídos más viejos
        lista = [{"id": f"x{i}", "texto": "t", "cuando": float(i), "prioridad": "normal", "leido": i % 2 == 0}
                 for i in range(100)]
        ruta.write_text(json.dumps(lista), encoding="utf-8")
        V.agregar_avisos(ruta, [V.nuevo_aviso(f"n{i}", "t", "normal", 200.0 + i) for i in range(3)])
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        ids = [a["id"] for a in datos]
        assert len(datos) == 100 and ids[-3:] == ["n0", "n1", "n2"]
        assert "x0" not in ids and "x2" not in ids and "x4" not in ids and "x1" in ids
        # archivo corrupto: se aparta y se empieza de nuevo
        ruta.write_text("{roto", encoding="utf-8")
        assert V.agregar_avisos(ruta, [V.nuevo_aviso("z", "t", "normal", 1.0)]) == 1
        assert [a["id"] for a in json.loads(ruta.read_text(encoding="utf-8"))] == ["z"]
        assert list(Path(d).glob("avisos.json.corrupto-*"))
        assert not list(Path(d).glob(".*.tmp"))


# ------------------------------------------------------------------ tareas de Claude Code
def prueba_tareas():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        tareas = tmp / "tareas"
        tareas.mkdir()
        vig, reloj, red, _ = armar(tmp, None, None, tareas)

        def escribir(id_, estado, proyecto="prueba2", log=None, inicio=None):
            reg = {"id": id_, "proyecto": proyecto, "instruccion": "x", "inicio": int(inicio or reloj.t),
                   "pid": 5, "estado": estado, "fin": None if estado == "en curso" else int(reloj.t),
                   "codigo_salida": None if estado == "en curso" else 0}
            (tareas / f"{id_}.json").write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
            if log is not None:
                (tareas / f"{id_}.log").write_text(log, encoding="utf-8")
        escribir("t1", "en curso")
        escribir("t0", "terminada", inicio=reloj.t - 9999)
        assert correr(vig) == []                            # línea base silenciosa
        reloj.pasar(5)
        escribir("t1", "terminada", log="\x1b[1mListo.\x1b[0m\n\n**Resumen:**\n- Cambié el header en "
                 "`src/app/page.tsx` para que use el logo nuevo.\n- Agregué pruebas.\nPendiente: revisar el footer "
                 "en móvil y correr el build completo antes de hacer el commit final del proyecto.\n")
        escribir("t2", "en curso")
        n = correr(vig)
        assert len(n) == 1 and n[0]["id"] == "tarea:t1:terminada" and n[0]["prioridad"] == "normal", n
        txt = n[0]["texto"]
        assert txt.startswith("Claude terminó la tarea en prueba2: Cambié el header en src/app/page.tsx"), txt
        assert len(txt.split(": ", 1)[1].split()) <= 25 and txt.endswith("…"), txt
        reloj.pasar(5)
        assert correr(vig) == []
        escribir("t2", "cancelada")                         # cancelada por Andres: no avisa
        escribir("t3", "falló", proyecto="check", log="Error: no encontré el módulo zod")   # nueva y ya terminada
        reloj.pasar(5)
        n = correr(vig)
        assert [a["id"] for a in n] == ["tarea:t3:falló"] and n[0]["prioridad"] == "alta", n
        assert n[0]["texto"] == "La tarea de Claude en check falló: Error: no encontré el módulo zod.", n[0]["texto"]
        txt = asyncio.run(vig.resumen_estado())
        assert "t3 en check: falló" in txt and "Sin configurar: Vercel (falta VERCEL_TOKEN)" in txt, txt
        assert "Avisos sin leer: 2." in txt


def prueba_resumir():
    assert V.resumir("") == ""
    assert V.resumir("uno\n\ndos\ntres\ncuatro") == "dos tres cuatro."
    largo = "Resumen: " + " ".join(f"p{i}" for i in range(40))
    r = V.resumir(largo)
    assert len(r.split()) == 25 and r.endswith("…"), r


# ------------------------------------------------------------------ herramientas
def prueba_herramientas():
    with tempfile.TemporaryDirectory() as d:
        vig, reloj, red, _ = armar(Path(d), None)
        for mala in ("http://a.com", "https://localhost/x", "https://127.0.0.1", "https://user:pw@a.com",
                     "https://10.0.0.5", "https://a b.com", "ftp://a.com", ""):
            try:
                asyncio.run(vig.vigilar_sitio("x", mala))
                raise AssertionError(f"aceptó {mala!r}")
            except ValueError:
                pass
        assert not vig.ruta_config.exists()
        msg = asyncio.run(vig.vigilar_sitio("18 Kilates", "18kilates.example.com"))
        assert "agregué «18 Kilates» (https://18kilates.example.com)" in msg and "responde bien" in msg, msg
        cfg = json.loads(vig.ruta_config.read_text(encoding="utf-8"))
        assert cfg["sitios"] == [{"nombre": "18 Kilates", "url": "https://18kilates.example.com"}]
        assert cfg["intervalo_min"] == 5 and "vercel" in cfg
        red.r["https://check.example.com/salud"] = V.Respuesta(503)
        msg = asyncio.run(vig.vigilar_sitio("Check", "https://check.example.com/salud"))
        assert "está fallando (error 503)" in msg, msg
        msg = asyncio.run(vig.vigilar_sitio("18 kilates", "https://nuevo.example.com"))
        assert "actualicé" in msg
        assert len(vig.leer_config()["sitios"]) == 2
        correr(vig)
        assert "18-kilates" in vig.estado["sitios"]
        assert asyncio.run(vig.dejar_de_vigilar("18 KILATES")) == "Listo, dejé de vigilar «18 Kilates»."
        assert "18-kilates" not in vig.estado["sitios"]
        assert [s["nombre"] for s in vig.leer_config()["sitios"]] == ["Check"]
        try:
            asyncio.run(vig.dejar_de_vigilar("nada"))
            raise AssertionError("debió fallar")
        except ValueError as e:
            assert "Vigilo: Check" in str(e)
        txt = asyncio.run(vig.resumen_estado())
        assert "Check" in txt and "GitHub Actions (falta GITHUB_TOKEN)" in txt and "Railway (falta RAILWAY_TOKEN)" in txt
        # servidor MCP en proceso (como vision.py)
        srv = V.crear_servidor(vig)
        assert srv["type"] == "sdk" and srv["name"] == "vigilancia", srv


def prueba_bucle():
    async def principal():
        with tempfile.TemporaryDirectory() as d:
            vig, reloj, red, _ = armar(Path(d), None)
            vig.iniciar(retraso=0)
            await asyncio.sleep(0.2)
            assert vig.estado.get("ultima_revision") == reloj.t
            await vig.detener()
            assert vig._tarea is None
    asyncio.run(principal())


if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("prueba_") and callable(v)]
    for p in pruebas:
        p()
        print(f"ok  {p.__name__}")
    print(f"\n{len(pruebas)} pruebas de vigilancia pasaron.")
