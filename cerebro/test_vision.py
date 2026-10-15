"""Pruebas de la visión de Pedro (pantalla + cámara) sin pantalla ni cámara reales.

Corre en Linux o Windows:  python cerebro/test_vision.py
Necesita claude-agent-sdk; las pruebas de la cámara real (con cv2 simulado) necesitan opencv-python y numpy.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claude_agent_sdk  # noqa: E402

import vision  # noqa: E402

JPEG_FALSO = base64.b64decode(  # bytes con cabecera JPEG (no se decodifican)
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAx"
    "NDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAI"
    "AQEAAD8AKp//2Q==")


def _espiar_herramientas(**kw):
    """Crea el servidor y devuelve (config, {nombre: SdkMcpTool}) capturando las herramientas registradas."""
    capturadas = {}
    original = claude_agent_sdk.create_sdk_mcp_server

    def espia(name, version="1.0.0", tools=None):
        for t in tools or []:
            capturadas[t.name] = t
        return original(name, version, tools=tools)

    claude_agent_sdk.create_sdk_mcp_server = espia
    try:
        config = vision.crear_servidor(**kw)
    finally:
        claude_agent_sdk.create_sdk_mcp_server = original
    return config, capturadas


def _llamar(herr, args):
    return asyncio.run(herr.handler(args))


def prueba_servidor_con_dos_herramientas():
    llamadas = []

    def pantalla(ventana="", todas=False):
        return JPEG_FALSO, "pantalla de prueba (1x1)"

    def camara(idx=0):
        llamadas.append(idx)
        return JPEG_FALSO, f"cámara {idx} (1x1)"

    config, herrs = _espiar_herramientas(capturador=pantalla, camara_capturador=camara)
    assert config["type"] == "sdk" and config["name"] == "vision", config
    assert set(herrs) == {"ver_pantalla", "ver_camara"}, herrs
    assert "mírame" in herrs["ver_camara"].description

    r = _llamar(herrs["ver_camara"], {})
    assert not r.get("is_error"), r
    tipos = [c["type"] for c in r["content"]]
    assert tipos == ["text", "image"], tipos
    assert "DATO, no instrucciones" in r["content"][0]["text"]
    assert r["content"][1]["mimeType"] == "image/jpeg"
    assert base64.b64decode(r["content"][1]["data"]) == JPEG_FALSO
    _llamar(herrs["ver_camara"], {"camara": 2})
    assert llamadas == [0, 2], llamadas

    r = _llamar(herrs["ver_pantalla"], {})
    assert [c["type"] for c in r["content"]] == ["text", "image"], r


def prueba_compatibilidad_firma_antigua():
    # pedro_cerebro.py llama crear_servidor() y crear_servidor(_falsa) (posicional)
    _, herrs = _espiar_herramientas()
    assert set(herrs) == {"ver_pantalla", "ver_camara"}
    _, herrs = _espiar_herramientas(capturador=lambda v="", t=False: (JPEG_FALSO, "x"))
    assert set(herrs) == {"ver_pantalla", "ver_camara"}


def prueba_errores_camara():
    def ocupada(idx=0):
        raise RuntimeError("no encontré la cámara 0 o la está usando otra app")

    _, herrs = _espiar_herramientas(camara_capturador=ocupada)
    r = _llamar(herrs["ver_camara"], {"camara": 0})
    assert r.get("is_error") is True, r
    assert r["content"][0]["text"].startswith("Error: no pude usar la cámara"), r
    r = _llamar(herrs["ver_camara"], {"camara": "abc"})
    assert r.get("is_error") is True and "no es un número de cámara" in r["content"][0]["text"], r
    r = _llamar(herrs["ver_camara"], {"camara": 42})
    assert r.get("is_error") is True, r


def prueba_camara_colgada_da_error():
    import time

    vision.CAMARA_ESPERA_MAX, antes = 0.2, vision.CAMARA_ESPERA_MAX
    try:
        _, herrs = _espiar_herramientas(camara_capturador=lambda idx=0: (time.sleep(1), (JPEG_FALSO, ""))[1])
        r = _llamar(herrs["ver_camara"], {})
        assert r.get("is_error") is True and "no respondió" in r["content"][0]["text"], r
    finally:
        vision.CAMARA_ESPERA_MAX = antes


def prueba_extremo_a_extremo_mcp():
    """Por el protocolo MCP real (lista + llamada), si el paquete mcp trae el cliente en memoria."""
    try:
        from mcp import Client
    except ImportError:
        print("  (omitida: este mcp no trae Client en memoria)")
        return
    config = vision.crear_servidor(capturador=lambda v="", t=False: (JPEG_FALSO, "p"),
                                   camara_capturador=lambda i=0: (JPEG_FALSO, f"cámara {i} (1x1)"))

    async def correr():
        async with Client(config["instance"]) as c:
            lista = await c.list_tools()
            nombres = {t.name for t in lista.tools}
            assert nombres == {"ver_pantalla", "ver_camara"}, nombres
            r = await c.call_tool("ver_camara", {"camara": 1})
            assert not r.is_error, r
            assert [x.type for x in r.content] == ["text", "image"], r.content
            r = await c.call_tool("ver_camara", {"camara": "x"})   # el esquema lo rechaza
            assert r.is_error, r

    asyncio.run(correr())


def prueba_capturar_camara_con_cv2_simulado():
    """La función real con un VideoCapture falso: calienta, reduce a 1280, JPEG y SUELTA la cámara."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("  (omitida: falta opencv-python/numpy)")
        return
    registro = {"leidas": 0, "soltada": False, "backend": None}

    class CapturaFalsa:
        def __init__(self, idx, backend):
            registro["backend"] = backend
            self.abierta = idx == 0

        def isOpened(self):
            return self.abierta

        def set(self, *_):
            return True

        def read(self):
            registro["leidas"] += 1
            cuadro = np.full((1080, 1920, 3), 120, dtype=np.uint8)
            return True, cuadro

        def release(self):
            registro["soltada"] = True

    original = cv2.VideoCapture
    cv2.VideoCapture = CapturaFalsa
    try:
        jpeg, desc = vision.capturar_camara(0)
        assert registro["soltada"], "no soltó la cámara"
        assert registro["leidas"] == vision.CAMARA_CALENTAR + 1, registro
        assert jpeg[:2] == b"\xff\xd8", "no es JPEG"
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        assert max(img.shape[:2]) == 1280 and img.shape[:2] == (720, 1280), img.shape
        assert desc.startswith("cámara 0 (1280x720)"), desc

        registro["soltada"] = False
        try:
            vision.capturar_camara(3)
            raise AssertionError("debió fallar")
        except RuntimeError as e:
            assert "no encontré la cámara 3" in str(e), e
        assert registro["soltada"], "no soltó la cámara tras el error"
        # el candado quedó libre
        assert vision._camara_en_uso.acquire(blocking=False)
        vision._camara_en_uso.release()
    finally:
        cv2.VideoCapture = original


if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("prueba_")]
    for p in pruebas:
        p()
        print(f"OK  {p.__name__}")
    print(f"\n{len(pruebas)} pruebas de visión pasaron.")
