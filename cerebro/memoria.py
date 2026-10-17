"""Memoria de Orión en el segundo cerebro de Andres (su grafo propio, C:\\dev\\_grafo).

Dos cosas, sin que Andres tenga que pedirlas:
  1. RECORDAR (gratis, local): antes de cada pregunta busca en el grafo las notas que tienen que ver
     (por nombre/título/resumen) y le pasa a Orión sus resúmenes. Cada nota una sola vez por conversación.
  2. GUARDAR (una llamada corta al modelo cada ~6 intercambios, al renovar la conversación, tras 10 min
     de silencio y al apagar): extrae lo duradero de lo hablado y lo escribe con las reglas de su
     CLAUDE.md: historial fechado en la nota que corresponda, nota nueva en su carpeta (idea, persona,
     decisión…) enlazada y añadida a su MOC, y una línea en el Diario de hoy.
Nunca borra ni reescribe lo que hay; nunca guarda claves, contraseñas, números de cuenta ni salud.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "herramientas"))
import grafo_mcp as grafo  # noqa: E402

CADA = int(os.environ.get("PEDRO_MEMORIA_CADA", "6"))              # intercambios entre guardados
INACTIVIDAD = float(os.environ.get("PEDRO_MEMORIA_INACTIVIDAD", "600"))
ACTIVA = os.environ.get("PEDRO_MEMORIA", "1") != "0"
MAX_ACCIONES = 8

_PREFIJO = re.compile(r"^\s*(\[[^\]]*\]\s*)+")
_TRIVIAL = re.compile(r"^\W*(s[ií]|no|ok|vale|dale|listo|gracias|hola|bueno|ajá|aja|perfecto|sigue|para|"
                      r"c[aá]llate|shh|espera)\W*$", re.I)
_SECRETO = re.compile(r"(contrase[ñn]a|password|passwd|clave)\s*(es|era|:|=)|"
                      r"\b(sk|pk|ghp|gho|github_pat|xox[bp]|AKIA)[-_][A-Za-z0-9_\-]{8,}|"
                      r"\b(?=\w*\d)(?=\w*[A-Za-z])\w{28,}\b|\b\d{12,19}\b|\bcvv\b", re.I)

SISTEMA = """Eres el organizador del segundo cerebro de Andres (un grafo de notas .md). Te paso un trozo de \
su conversación con Orión, su asistente. Decide qué vale la pena GUARDAR para el futuro y responde SOLO con JSON:
{"acciones": [ ... ]}   (lista vacía si no hay nada que valga la pena; es lo normal en charlas triviales)

Acciones posibles:
- {"accion": "historial", "nota": "<id EXISTENTE>", "texto": "<hecho nuevo, una o dos frases>"}
  Para novedades de algo que ya tiene nota (proyecto, persona, decisión…).
- {"accion": "crear", "tipo": "idea|persona|decision|aprendizaje|recurso|estudio|proyecto", "titulo": "...",
   "resumen": "<una frase que se entienda sola>", "contenido": "<markdown breve, opcional>",
   "enlaces": ["<ids existentes relacionados>"], "mapa": "<cat-… si aplica>"}
  Solo para algo nuevo e importante que no tenga nota. SIEMPRE con al menos un enlace o mapa.
- {"accion": "diario", "texto": "<una línea: qué se habló/decidió/hizo en este trozo>", "enlaces": ["<ids>"]}
  Como mucho una por trozo y solo si pasó algo con sustancia.

Reglas:
- Guarda solo lo que Andres dijo, decidió, pidió recordar o lo que concluyeron juntos y le servirá después: \
decisiones, ideas de negocio, estado y planes de sus proyectos, personas y su relación con él, gustos y \
metas suyas, aprendizajes. Datos sobre Andres mismo (gustos, rutinas, metas) → historial de la nota "andres" \
(si no existe, créala: tipo persona, título "Andres", mapa vacío, enlaces ["INDICE"]).
- NO guardes: saludos, órdenes del momento (abrir, poner música, volumen, la hora, el clima), resultados de \
búsquedas que él no adoptó, lo que Orión dijo que haría pero no hizo, errores técnicos de Orión.
- NUNCA guardes contraseñas, claves, tokens, números de cuenta o tarjeta, ni datos de salud.
- No inventes ni deduzcas: si no está claro, no lo guardes. No repitas lo que ya dicen las notas listadas.
- Usa los ids EXACTOS de la lista de notas. Escribe en español, tercera persona ("Andres decidió…"), corto.
- Máximo 6 acciones."""


def _limpio(t: str) -> str:
    return " ".join(_PREFIJO.sub("", t or "").split())


class Memoria:
    def __init__(self, info, secretos, modelo: str, http_post=None):
        self.info = info
        self.secretos = secretos          # función -> dict con ANTHROPIC_*
        self.modelo = modelo
        self.http_post = http_post        # inyectable en pruebas
        self.pendientes: list[tuple[str, str]] = []
        self.vistas: set[str] = set()
        self._tarea: asyncio.Task | None = None
        self._temporizador: asyncio.TimerHandle | None = None
        self.activa = ACTIVA and os.path.isdir(grafo.GRAFO)
        if not self.activa:
            info(f"memoria del grafo apagada ({'PEDRO_MEMORIA=0' if not ACTIVA else 'no encuentro ' + grafo.GRAFO})")

    # --- recordar ------------------------------------------------------
    def nueva_conversacion(self) -> None:
        self.vistas.clear()

    def contexto_para(self, texto: str) -> str:
        """Resúmenes de las notas relacionadas con lo que dijo Andres (vacío si nada aplica)."""
        if not self.activa:
            return ""
        t = _limpio(texto)
        if len(t) < 6 or _TRIVIAL.match(t):
            return ""
        try:
            notas = grafo.relacionadas(t, n=3, excluir=self.vistas)
        except Exception as e:  # noqa: BLE001
            self.info(f"memoria: no pude buscar en el grafo: {e!r}")
            return ""
        if not notas:
            return ""
        self.vistas.update(n["id"] for n in notas)
        self.info("  memoria: del grafo → " + ", ".join(n["id"] for n in notas))
        return "\n".join(f"- [[{n['id']}]] ({n['carpeta']}): {n['resumen'] or n['titulo']}" for n in notas)

    # --- guardar -------------------------------------------------------
    def registrar(self, pregunta: str, respuesta: str, error: bool = False) -> None:
        """Anota un intercambio; cada CADA intercambios (o tras un rato de silencio) guarda en el grafo."""
        if not self.activa or error:
            return
        p = _limpio(pregunta)
        if not p or _TRIVIAL.match(p):
            return
        self.pendientes.append((p[:600], " ".join((respuesta or "").split())[:900]))
        if len(self.pendientes) >= CADA:
            self.consolidar_luego()
        else:
            self._programar_inactividad()

    def _programar_inactividad(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._temporizador:
            self._temporizador.cancel()
        self._temporizador = loop.call_later(INACTIVIDAD, self.consolidar_luego)

    def consolidar_luego(self) -> None:
        if not self.pendientes or (self._tarea and not self._tarea.done()):
            return
        try:
            self._tarea = asyncio.get_running_loop().create_task(self.consolidar())
        except RuntimeError:
            pass

    async def consolidar(self, espera: float = 60) -> list[str]:
        if not self.activa or not self.pendientes:
            return []
        if self._temporizador:
            self._temporizador.cancel()
            self._temporizador = None
        lote, self.pendientes = self.pendientes, []
        t0 = time.time()
        try:
            texto = await asyncio.wait_for(self._preguntar(self._prompt(lote)), espera)
            acciones = self._parsear(texto)
            hechos = await asyncio.to_thread(self.aplicar, acciones)
            self.info(f"memoria: {len(lote)} intercambios → {len(hechos)} cambios en el grafo "
                      f"({time.time() - t0:.1f}s){': ' + ' | '.join(hechos) if hechos else ''}")
            return hechos
        except Exception as e:  # noqa: BLE001
            self.info(f"memoria: no pude guardar en el grafo: {e!r}")
            if len(lote) < 3 * CADA:          # se reintenta con el próximo lote
                self.pendientes = lote + self.pendientes
            return []

    def _prompt(self, lote) -> str:
        charla = "\n".join(f"Andres: {p}\nOrión: {r}" for p, r in lote)
        try:
            idx = grafo.indice()
            cerca = grafo.relacionadas(" ".join(p for p, _ in lote), n=15, minimo=1)
        except Exception:  # noqa: BLE001
            idx, cerca = [], []
        mapas = [n["id"] for n in idx if n["carpeta"] == "Mapas"]
        proyectos = [n["id"] for n in idx if n["carpeta"] == "Proyectos"]
        andres = next((n for n in idx if n["id"] == "andres"), None)
        notas = "\n".join(f"- {n['id']} ({n['carpeta']}): {n['resumen'][:160]}" for n in cerca)
        return (f"Fecha: {grafo.hoy()}\n\n# Notas que ya existen y parecen relacionadas\n{notas or '(ninguna)'}\n"
                f"\n# Mapas (MOC): {', '.join(mapas)}\n# Proyectos: {', '.join(proyectos)}\n"
                f"# Nota 'andres': {'existe' if andres else 'no existe todavía'}\n"
                f"\n# Conversación\n{charla}\n\nResponde solo el JSON.")

    async def _preguntar(self, prompt: str) -> str:
        s = self.secretos() if callable(self.secretos) else (self.secretos or {})
        base = (s.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
        cab = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        if s.get("ANTHROPIC_AUTH_TOKEN"):
            cab["authorization"] = f"Bearer {s['ANTHROPIC_AUTH_TOKEN']}"
        if s.get("ANTHROPIC_API_KEY"):
            cab["x-api-key"] = s["ANTHROPIC_API_KEY"]
        cuerpo = {"model": self.modelo, "max_tokens": 1500, "system": SISTEMA,
                  "messages": [{"role": "user", "content": prompt}]}
        if self.http_post:
            datos = await self.http_post(f"{base}/v1/messages", cab, cuerpo)
        else:
            import aiohttp
            async with aiohttp.ClientSession() as ses:
                async with ses.post(f"{base}/v1/messages", headers=cab, json=cuerpo,
                                    timeout=aiohttp.ClientTimeout(total=55)) as r:
                    datos = await r.json(content_type=None)
                    if r.status >= 400:
                        raise RuntimeError(f"HTTP {r.status}: {str(datos)[:200]}")
        u = datos.get("usage") or {}
        if u:
            self.info(f"  memoria: tokens entrada {u.get('input_tokens', 0)} · salida {u.get('output_tokens', 0)}")
        return "".join(b.get("text", "") for b in datos.get("content", []) if b.get("type") == "text")

    @staticmethod
    def _parsear(texto: str) -> list[dict]:
        texto = re.sub(r"^```(json)?|```$", "", (texto or "").strip(), flags=re.M).strip()
        i, j = texto.find("{"), texto.rfind("}")
        if i < 0 or j <= i:
            return []
        try:
            datos = json.loads(texto[i:j + 1])
        except ValueError:
            return []
        acciones = datos.get("acciones") if isinstance(datos, dict) else None
        if not isinstance(acciones, list):
            return []
        return [a for a in acciones if isinstance(a, dict)][:MAX_ACCIONES]

    def aplicar(self, acciones: list[dict]) -> list[str]:
        hechos = []
        for a in acciones:
            todo = json.dumps(a, ensure_ascii=False)
            if _SECRETO.search(todo):
                self.info("memoria: descarto una acción que parecía tener datos sensibles")
                continue
            try:
                tipo = a.get("accion")
                if tipo == "historial":
                    if grafo.slug(str(a.get("nota", ""))) == "andres" and \
                            not any(n["id"] == "andres" for n in grafo.indice()):
                        grafo.crear_nota("persona", "Andres", "Andres, el dueño de este segundo cerebro: gustos, metas "
                                         "y rutinas que le ha contado a Orión.", "", ["🏠 Inicio", "INDICE"])
                    r = grafo.agregar_a_nota(str(a.get("nota", "")), str(a.get("texto", ""))[:700])
                elif tipo == "crear":
                    r = grafo.crear_nota(str(a.get("tipo", "")), str(a.get("titulo", "")), str(a.get("resumen", "")),
                                         str(a.get("contenido", "") or "")[:3000], a.get("enlaces") or [],
                                         str(a.get("mapa", "") or ""))
                elif tipo == "diario":
                    r = grafo.anotar_diario(str(a.get("texto", ""))[:400], a.get("enlaces") or [])
                else:
                    continue
                hechos.append(r)
            except Exception as e:  # noqa: BLE001
                self.info(f"memoria: acción descartada ({e}): {todo[:160]}")
        return hechos
