"""Pruebas de la lógica del HUD (sin ventana, no necesita tkinter):  python hud/test_hud.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hud  # noqa: E402
from hud import (elegir_aviso, ajustar_a_area, esquina_cercana, fps_de, interpretar, leer_preferencias,  # noqa: E402
                 mezclar, posicion_esquina, recortar, siguiente_esquina, y_al_cambiar_alto)

AHORA = 1_760_000_000.0


def prueba_estados_normales():
    for est in ("escuchando", "oyendo", "pensando", "hablando", "dormido"):
        v = interpretar({"estado": est, "usuario": "hola", "pedro": "qué más", "t": AHORA - 1}, AHORA)
        assert v.clave == est, (est, v)
        assert v.usuario == "hola" and v.pedro == "qué más"
    assert interpretar({"estado": "pensando", "t": AHORA}, AHORA).etiqueta == "Pensando"
    assert interpretar({"estado": "ESCUCHANDO ", "t": AHORA}, AHORA).clave == "escuchando"


def prueba_sin_conexion():
    assert interpretar(None, AHORA).clave == "sin_conexion"               # archivo no existe
    assert interpretar("basura", AHORA).clave == "sin_conexion"           # JSON que no es objeto
    assert interpretar([], AHORA).clave == "sin_conexion"
    v = interpretar({"estado": "apagado", "usuario": "a", "pedro": "b", "t": AHORA}, AHORA)
    assert v.clave == "sin_conexion" and v.detalle == "Orión está apagado" and v.pedro == "b"
    assert interpretar({}, AHORA).clave == "sin_conexion"                 # sin estado
    # viejo: más de 30 s sin señal
    v = interpretar({"estado": "escuchando", "t": AHORA - 31}, AHORA)
    assert v.clave == "sin_conexion" and "31 s" in v.detalle, v
    assert interpretar({"estado": "escuchando", "t": AHORA - 29}, AHORA).clave == "escuchando"
    assert "min" in interpretar({"estado": "hablando", "t": AHORA - 600}, AHORA).detalle
    # dormido nunca se vuelve "sin conexión" por viejo
    assert interpretar({"estado": "dormido", "t": AHORA - 9999}, AHORA).clave == "dormido"
    assert interpretar({"estado": "dormido"}, AHORA).clave == "dormido"
    # sin t ni fecha de archivo: no hay forma de saber si vive
    assert interpretar({"estado": "escuchando"}, AHORA).clave == "sin_conexion"


def prueba_latido_por_fecha_de_archivo():
    # t viejo pero el archivo se tocó hace poco: vivo
    assert interpretar({"estado": "escuchando", "t": AHORA - 500}, AHORA, mtime=AHORA - 2).clave == "escuchando"
    assert interpretar({"estado": "escuchando"}, AHORA, mtime=AHORA - 2).clave == "escuchando"
    assert interpretar({"estado": "escuchando"}, AHORA, mtime=AHORA - 60).clave == "sin_conexion"
    # t con tipos raros
    for t in ("no", None, True, float("nan"), [1]):
        assert interpretar({"estado": "oyendo", "t": t}, AHORA, mtime=AHORA).clave == "oyendo", t
    assert interpretar({"estado": "oyendo", "t": str(AHORA)}, AHORA).clave == "oyendo"


def prueba_campos_raros():
    v = interpretar({"estado": "hablando", "usuario": None, "pedro": {"x": 1}, "t": AHORA}, AHORA)
    assert v.usuario == "" and v.pedro == ""
    v = interpretar({"estado": "hablando", "usuario": "  hola\n\n  mundo  ", "t": AHORA}, AHORA)
    assert v.usuario == "hola mundo"
    v = interpretar({"estado": "transcribiendo", "t": AHORA}, AHORA)          # estado futuro desconocido
    assert v.clave == "escuchando" and v.etiqueta == "Transcribiendo", v
    assert interpretar({"estado": 5, "t": AHORA}, AHORA).etiqueta == "5"


def prueba_fps():
    assert fps_de(interpretar({"estado": "dormido"}, AHORA)) <= 5
    assert fps_de(interpretar(None, AHORA)) <= 5
    assert fps_de(interpretar({"estado": "pensando", "t": AHORA}, AHORA)) >= 20
    assert fps_de(interpretar({"estado": "hablando", "t": AHORA}, AHORA)) >= 20
    assert fps_de(interpretar({"estado": "dormido"}, AHORA), hay_aviso=True) >= 10


def _cabe(n):
    return lambda s: len(s) <= n


def prueba_recortar():
    assert recortar("", _cabe(10)) == []
    assert recortar("hola", _cabe(10)) == ["hola"]
    assert recortar("uno dos tres", _cabe(7)) == ["uno dos", "tres"]
    r = recortar("uno dos tres cuatro cinco seis", _cabe(9), 2)
    assert len(r) == 2 and r[-1].endswith("…") and all(len(x) <= 9 for x in r), r
    r = recortar("supercalifragilisticoespialidoso fin", _cabe(10), 2)
    assert r[0] == "supercalif" and len(r) == 2 and r[1].endswith("…") and len(r[1]) <= 10, r
    r = recortar("a " * 200, _cabe(20), 2)
    assert len(r) == 2 and r[1].endswith("…")
    r = recortar("exacto en dos", _cabe(6), 2)          # "exacto" / "en dos": cabe justo, sin «…»
    assert r == ["exacto", "en dos"], r
    assert recortar("x" * 50, _cabe(1), 2)[0] == "x"    # nunca se queda sin avanzar
    assert recortar("hola\nmundo\tcruel", _cabe(40)) == ["hola mundo cruel"]


def prueba_avisos():
    assert elegir_aviso(None, set()) == (None, set())
    assert elegir_aviso({"no": "lista"}, set()) == (None, set())
    assert elegir_aviso([{"id": 1, "texto": "x", "leido": True}], set()) == (None, set())
    avisos = [
        {"id": "a", "texto": "viejo", "cuando": "2026-10-15T08:00:00", "prioridad": "normal", "leido": False},
        {"id": "b", "texto": "nuevo", "cuando": "2026-10-15T09:00:00", "prioridad": "alta", "leido": False},
        {"id": "c", "texto": "leído", "cuando": "2026-10-15T10:00:00", "prioridad": "alta", "leido": True},
        "basura", {"id": "d", "texto": "  ", "leido": False},
    ]
    mostrados: set = set()
    a, ids = elegir_aviso(avisos, mostrados)
    assert a["id"] == "b" and hud.es_urgente(a), a
    mostrados |= ids
    assert elegir_aviso(avisos, mostrados)[0] is None         # no se repite
    assert elegir_aviso(avisos, mostrados)[0] is None         # ni el viejo "a" después
    avisos.append({"id": "e", "texto": "otro", "cuando": 1_900_000_000, "leido": False})
    a, ids = elegir_aviso(avisos, mostrados)
    assert a["id"] == "e" and not hud.es_urgente(a)
    # sin fecha entendible: gana el último de la lista
    a, _ = elegir_aviso([{"id": 1, "texto": "x", "cuando": "??", "leido": False},
                         {"id": 2, "texto": "y", "leido": False}], set())
    assert a["id"] == 2
    # fecha con Z y epoch mezclados
    a, _ = elegir_aviso([{"id": 1, "texto": "x", "cuando": "2030-01-01T00:00:00Z", "leido": False},
                         {"id": 2, "texto": "y", "cuando": 1_000, "leido": False}], set())
    assert a["id"] == 1
    # sin id: se identifica por texto + fecha
    a, ids = elegir_aviso([{"texto": "sin id", "cuando": 5, "leido": False}], set())
    assert a["texto"] == "sin id" and len(ids) == 1
    assert elegir_aviso([{"texto": "sin id", "cuando": 5, "leido": False}], ids)[0] is None
    assert hud.es_urgente({"prioridad": "ALTA"}) and not hud.es_urgente({"prioridad": None})


def prueba_esquinas_y_posicion():
    area = (0, 0, 1920, 1040)
    assert posicion_esquina("abajo-derecha", area, 360, 180, 16) == (1920 - 360 - 16, 1040 - 180 - 16)
    assert posicion_esquina("arriba-izquierda", area, 360, 180, 16) == (16, 16)
    assert esquina_cercana(1500, 800, 360, 180, area) == "abajo-derecha"
    assert esquina_cercana(10, 10, 360, 180, area) == "arriba-izquierda"
    vistas = set()
    e = "abajo-derecha"
    for _ in range(4):
        e = siguiente_esquina(e)
        vistas.add(e)
    assert e == "abajo-derecha" and len(vistas) == 4
    assert siguiente_esquina("raro") == "abajo-derecha"
    assert ajustar_a_area(5000, -50, 360, 180, area) == (1920 - 360, 0)
    assert ajustar_a_area(-1500, 300, 360, 180, (-1920, 0, 0, 1080)) == (-1500, 300)   # monitor a la izquierda
    # crece hacia arriba si está abajo, hacia abajo si está arriba
    assert y_al_cambiar_alto(844, 180, 84, area) == 940
    assert y_al_cambiar_alto(16, 84, 180, area) == 16


def prueba_preferencias():
    assert leer_preferencias(None) == {}
    assert leer_preferencias("{roto") == {}
    assert leer_preferencias("[1,2]") == {}
    assert leer_preferencias('{"x": 10, "y": "20", "texto": false}') == {"x": 10, "y": 20, "texto": False}
    assert leer_preferencias('{"x": 10}') == {}


def prueba_mezclar():
    assert mezclar("#000000", "#ffffff", 0) == "#000000"
    assert mezclar("#000000", "#ffffff", 1) == "#ffffff"
    assert mezclar("#000000", "#ffffff", 2) == "#ffffff"
    assert mezclar("#000000", "#ff0000", 0.5) == "#800000"


def prueba_fuente_archivos():
    with tempfile.TemporaryDirectory() as d:
        raiz = Path(d)
        f = hud.FuenteArchivos(raiz)
        assert f.leer_estado(AHORA) == (None, None)            # no existe tmp/
        (raiz / "tmp").mkdir()
        ruta = raiz / "tmp" / "estado.json"
        ruta.write_text(json.dumps({"estado": "hablando", "pedro": "hola", "t": time.time()}), encoding="utf-8")
        datos, mtime = f.leer_estado(time.time())
        assert datos["estado"] == "hablando" and mtime is not None
        assert interpretar(datos, time.time(), mtime).clave == "hablando"
        time.sleep(0.01)
        ruta.write_text('{"estado": "pensa', encoding="utf-8")   # a medio escribir: conserva el último bueno
        datos2, mtime2 = f.leer_estado(time.time())
        assert datos2["estado"] == "hablando" and mtime2 == mtime
        ruta.write_bytes("﻿".encode() + json.dumps({"estado": "dormido"}).encode())   # con BOM (PowerShell)
        assert f.leer_estado(time.time())[0]["estado"] == "dormido"
        ruta.unlink()
        assert f.leer_estado(time.time()) == (None, None)
        # corrupto desde el inicio
        f2 = hud.FuenteArchivos(raiz)
        ruta.write_text("{{{", encoding="utf-8")
        assert interpretar(*f2.leer_estado(time.time())[:1], time.time()).clave == "sin_conexion"
        # avisos: solo lectura, ignora lo que no sea lista
        assert f.leer_avisos(AHORA) is None
        av = raiz / "avisos.json"
        av.write_text(json.dumps([{"id": 1, "texto": "x", "leido": False}]), encoding="utf-8")
        antes = av.read_bytes()
        assert f.leer_avisos(AHORA)[0]["id"] == 1
        assert av.read_bytes() == antes


def prueba_demo():
    f = hud.FuenteDemo()
    vistos = set()
    for i in range(0, int(f.ciclo * 10)):
        datos, m = f.leer_estado(f.t0 + i / 10)
        vistos.add(interpretar(datos, f.t0 + i / 10, m).clave)
    assert vistos == {"escuchando", "oyendo", "pensando", "hablando", "dormido", "sin_conexion"}, vistos
    avisos = f.leer_avisos(f.t0 + 20)
    a, _ = elegir_aviso(avisos, set())
    assert a and hud.es_urgente(a)
    for est in ("pensando", "hablando", "aviso"):
        datos, m = hud.FuenteDemo(est).leer_estado(time.time())
        assert interpretar(datos, time.time(), m).clave == ("escuchando" if est == "aviso" else est)


def prueba_una_sola_instancia():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    uno = hud.tomar_instancia(puerto)
    assert uno is not None
    try:
        assert hud.tomar_instancia(puerto) is None, "dejó abrir un segundo HUD"
    finally:
        uno.close()
    dos = hud.tomar_instancia(puerto)                # al cerrar el primero, se puede volver a abrir
    assert dos is not None
    dos.close()


if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("prueba_")]
    for p in pruebas:
        p()
        print(f"OK  {p.__name__}")
    print(f"\n{len(pruebas)} pruebas del HUD pasaron.")
