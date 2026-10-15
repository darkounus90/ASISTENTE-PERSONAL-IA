# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "claude-agent-sdk>=0.2.100",
#   "aiohttp>=3.9",
#   "tzdata",
#   "mss>=9",
#   "pillow>=10",
# ]
# ///
"""Cerebro de Pedro sobre el motor de Claude Code (Claude Agent SDK).

Corre en Windows y expone en 127.0.0.1:8787 la misma API que usaba el cliente de voz
(/v1/chat/completions con streaming estilo OpenAI y /health), así que voz.py no cambia.

- Una sesión de Claude Code persistente (se retoma al reiniciar si tiene menos de 8 h).
- Herramientas de Claude Code: Bash, Read, Grep, Glob, Edit, WebFetch…
- Herramientas propias de Pedro (pc, grafo, google, claude-code, comet) como servidores MCP
  que corren en WSL vía herramientas/mcp.sh.
- Permisos decididos por código en politica.py (no por el prompt).
- Modo prueba (cabecera X-Pedro-Prueba: 1): sesión aparte y acciones simuladas.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
from politica import Politica, clasificar, PROHIBIDO  # noqa: E402

JARVIS = Path(os.environ.get("PEDRO_JARVIS", AQUI.parent))
DATOS = Path(os.environ.get("PEDRO_DATOS", Path.home() / ".pedro"))
PUERTO = int(os.environ.get("JARVIS_PORT", "8787"))
MODELO = os.environ.get("PEDRO_MODELO", "claude-sonnet-5-5")
MODELO_RAPIDO = os.environ.get("PEDRO_MODELO_RAPIDO", MODELO)
ESFUERZO = os.environ.get("PEDRO_ESFUERZO", "")   # low|medium|high (vacío = por defecto)
DEV = os.environ.get("PEDRO_DEV", r"C:\dev")
GRAFO = os.environ.get("PEDRO_GRAFO", r"C:\dev_grafo")
MCP_MODO = os.environ.get("PEDRO_MCP", "wsl")          # wsl | local (pruebas en Linux)
DISTRO = os.environ.get("PEDRO_WSL_DISTRO", "Ubuntu")
ESPERA_MAX = float(os.environ.get("PEDRO_ESPERA_MAX", "90"))   # seg. sin noticias del modelo
SESION_HORAS = float(os.environ.get("JARVIS_HISTORIAL_HORAS", "8"))
try:
    ZONA = ZoneInfo("America/Bogota")
except Exception:  # noqa: BLE001  (Windows sin base de zonas horarias)
    from datetime import timedelta, timezone
    ZONA = timezone(timedelta(hours=-5), "America/Bogota")   # Colombia no tiene horario de verano
LOG = AQUI / "cerebro.log"


# ------------------------------------------------------------------ registro
class _Tee:
    def __init__(self, consola, archivo):
        self.c, self.a = consola, archivo

    def write(self, txt):
        try:
            self.c.write(txt)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.a.write(txt)
            self.a.flush()
        except Exception:  # noqa: BLE001
            pass

    def flush(self):
        try:
            self.c.flush()
        except Exception:  # noqa: BLE001
            pass

    def __getattr__(self, n):
        return getattr(self.c, n)


def info(msg: str) -> None:
    print(f"[{datetime.now(ZONA):%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ configuración
def cargar_secretos() -> dict:
    env = {}
    ruta = DATOS / "secrets.env"
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.removeprefix("export ").split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(k):
            env.setdefault(k, os.environ[k])
    return env


def _a_wsl(ruta: Path) -> str:
    s = str(ruta).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m else s


def servidores_mcp() -> dict:
    archivos = {
        "pc": "herramientas/pc_mcp.py",
        "grafo": "herramientas/grafo_mcp.py",
        "google": "herramientas/google_mcp.py",
        "claude-code": "herramientas/claude_code_mcp.py",
        "comet": "comet_mcp.py",
    }
    out = {}
    for nombre, rel in archivos.items():
        if not (JARVIS / rel).exists():
            continue
        if MCP_MODO == "local":
            out[nombre] = {"type": "stdio", "command": sys.executable, "args": [str(JARVIS / rel)]}
        else:
            out[nombre] = {"type": "stdio", "command": "wsl.exe",
                           "args": ["-d", DISTRO, "--", "bash", _a_wsl(JARVIS / "herramientas" / "mcp.sh"), rel]}
    try:   # visión: captura en Python dentro del cerebro (sin scripts que el antivirus bloquee)
        from vision import crear_servidor
        if os.environ.get("PEDRO_VISION_FALSA"):
            from PIL import Image
            import io as _io

            def _falsa(ventana="", todas=False):
                b = _io.BytesIO()
                Image.new("RGB", (64, 40), (30, 30, 200)).save(b, "JPEG")
                return b.getvalue(), "pantalla de prueba (64x40)"
            out["vision"] = crear_servidor(_falsa)
        else:
            out["vision"] = crear_servidor()
    except Exception as e:  # noqa: BLE001
        info(f"visión no disponible: {e!r}")
    if MCP_MODO != "local" and shutil.which("npx"):
        out["comet-control"] = {"type": "stdio", "command": "cmd.exe",
                                "args": ["/c", "npx", "-y", "@playwright/mcp@latest",
                                         "--cdp-endpoint", "http://127.0.0.1:9222"]}
    return out


def _leer(nombre: str) -> str:
    try:
        return (JARVIS / nombre).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def prompt_sistema() -> str:
    apodos = _leer("apodos_proyectos.json")
    partes = [_leer("SOUL.md"),
              "# Sobre Andres (USER.md)\n" + _leer("USER.md"),
              f"# Tu memoria ({JARVIS / 'MEMORY.md'})\n" + _leer("MEMORY.md")]
    if apodos:
        partes.append("# Apodos de proyectos (lo que dice Andres → carpeta en C:\\dev)\n" + apodos)
    partes.append(f"# Entorno\n- Proyectos: {DEV}. Grafo de Obsidian: {GRAFO}. Archivos de Pedro: {JARVIS}.\n"
                  "- Bash corre en Git Bash de Windows: usa rutas como /c/dev/proyecto.\n"
                  "- Cada mensaje trae al final la hora local de Andres entre paréntesis.")
    return "\n\n".join(p for p in partes if p)


def bash_de_git() -> str | None:
    for c in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"), r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe",
              str(Path.home() / r"AppData\Local\Programs\Git\bin\bash.exe")):
        if c and Path(c).exists():
            return c
    return None


def ajustes_claude() -> str:
    """Reglas de lectura de secretos a nivel de Claude Code (también cubren Grep/Glob)."""
    DATOS.mkdir(parents=True, exist_ok=True)
    ruta = DATOS / "ajustes_claude.json"
    ruta.write_text(json.dumps({"permissions": {"deny": [
        "Read(**/.env)", "Read(**/.env.local)", "Read(**/.env.*.local)", "Read(**/.env.production)",
        "Read(**/.env.development)", "Read(**/*.pem)", "Read(**/secrets.env)", "Read(**/id_rsa*)",
        "Read(**/google_pedro.json)", "Read(~/.pedro/**)", "Read(~/.ssh/**)"]},
        "includeCoAuthoredBy": False}, indent=2), encoding="utf-8")
    return str(ruta)


def ahora_legible() -> str:
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
             "octubre", "noviembre", "diciembre"]
    a = datetime.now(ZONA)
    h = a.hour % 12 or 12
    momento = "a. m." if a.hour < 12 else "p. m."
    return f"{dias[a.weekday()]} {a.day} de {meses[a.month - 1]} de {a.year}, {h}:{a.minute:02d} {momento}"


# ------------------------------------------------------------------ cerebro
class Cerebro:
    def __init__(self, prueba: bool = False):
        self.prueba = prueba
        self.pol = Politica()
        self.cliente = None
        self.sesion_id: str | None = None
        self.lock = asyncio.Lock()
        self.ultima: dict = {}

    # --- sesión -------------------------------------------------------
    def _archivo_sesion(self) -> Path:
        return DATOS / "sesion.json"

    def _sesion_guardada(self) -> str | None:
        if self.prueba:
            return None
        try:
            d = json.loads(self._archivo_sesion().read_text(encoding="utf-8"))
            if time.time() - d["t"] < SESION_HORAS * 3600:
                return d["id"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    def _guardar_sesion(self, sid: str) -> None:
        self.sesion_id = sid
        if self.prueba:
            return
        try:
            DATOS.mkdir(parents=True, exist_ok=True)
            self._archivo_sesion().write_text(json.dumps({"id": sid, "t": time.time()}), encoding="utf-8")
        except OSError:
            pass

    def _opciones(self, retomar: str | None):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        secretos = cargar_secretos()
        env = {
            **secretos,
            "CLAUDE_CONFIG_DIR": str(DATOS / ("claude-prueba" if self.prueba else "claude")),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",   # gateways como nyxos no aceptan betas
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODELO_RAPIDO,
            "ANTHROPIC_SMALL_FAST_MODEL": MODELO_RAPIDO,
            "BASH_DEFAULT_TIMEOUT_MS": "60000",
            "MAX_MCP_OUTPUT_TOKENS": "60000",                 # capturas de pantalla
            "PYTHONIOENCODING": "utf-8",
        }
        git = bash_de_git()
        if git:
            env["CLAUDE_CODE_GIT_BASH_PATH"] = git
        cwd = DEV if Path(DEV).is_dir() else str(JARVIS)
        extra = [d for d in (str(JARVIS), GRAFO) if Path(d).is_dir() and d != cwd]
        opciones = dict(
            system_prompt=prompt_sistema(),
            model=MODELO,
            cwd=cwd,
            add_dirs=extra,
            tools=["Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"],
            mcp_servers=servidores_mcp(),
            strict_mcp_config=True,
            setting_sources=[],
            settings=ajustes_claude(),
            disallowed_tools=["NotebookEdit", "Task", "Agent", "TodoWrite", "ExitPlanMode",
                              "EnterPlanMode", "Skill", "SlashCommand"],
            permission_mode="default",
            can_use_tool=self._permiso,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._antes_de_herramienta])]},
            include_partial_messages=True,
            max_turns=25,
            env=env,
            stderr=lambda l: info(f"[claude] {l.rstrip()}") if l.strip() else None,
        )
        if ESFUERZO:
            opciones["effort"] = ESFUERZO
        if retomar:
            opciones["resume"] = retomar
        return ClaudeAgentOptions(**opciones)

    async def _conectar(self, nueva: bool) -> None:
        from claude_agent_sdk import ClaudeSDKClient

        await self._desconectar()
        retomar = None if nueva else (self.sesion_id or self._sesion_guardada())
        for intento in (retomar, None) if retomar else (None,):
            try:
                t = time.time()
                cliente = ClaudeSDKClient(options=self._opciones(intento))
                await cliente.connect()
                self.cliente = cliente
                info(f"{'[prueba] ' if self.prueba else ''}sesión {'retomada' if intento else 'nueva'} "
                     f"lista en {time.time() - t:.1f}s")
                if not intento:
                    self.sesion_id = None
                return
            except Exception as e:  # noqa: BLE001
                info(f"no pude {'retomar la sesión' if intento else 'iniciar Claude Code'}: {e!r}")
                if intento is None:
                    raise

    async def _desconectar(self) -> None:
        if self.cliente is not None:
            try:
                await asyncio.wait_for(self.cliente.disconnect(), 10)
            except Exception:  # noqa: BLE001
                pass
            self.cliente = None

    async def preparar(self) -> None:
        async with self.lock:
            if self.cliente is None:
                await self._conectar(nueva=False)

    # --- permisos -----------------------------------------------------
    async def _antes_de_herramienta(self, entrada, tool_use_id, contexto):  # noqa: ARG002
        nombre, args = entrada.get("tool_name", ""), entrada.get("tool_input") or {}
        ok, mensaje = self.pol.decidir(nombre, args)
        info(f"  herramienta {nombre} {json.dumps(args, ensure_ascii=False)[:160]} -> "
             f"{'OK' if ok else self.pol.traza[-1]['decision'] if self.pol.traza else 'NO'}")
        if ok:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": mensaje}}

    async def _permiso(self, herramienta, entrada, contexto):  # noqa: ARG002
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if clasificar(herramienta, entrada)[0] == PROHIBIDO:
            return PermissionResultDeny(message="Bloqueado por seguridad.")
        return PermissionResultAllow()   # la política ya decidió en el hook

    # --- turno --------------------------------------------------------
    async def turno(self, texto: str, nueva: bool, cola: asyncio.Queue) -> None:
        """Pone en `cola` trozos de texto y al final None. Se puede cancelar (interrupción)."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, SystemMessage, TextBlock, ToolUseBlock

        async with self.lock:
            t0 = time.time()
            self.ultima = {"texto": texto, "respuesta": "", "herramientas": [], "error": None}
            terminado = False
            try:
                if self.cliente is None or nueva:
                    await self._conectar(nueva=nueva)
                self.pol.nuevo_turno(texto, prueba=self.prueba)
                await self.cliente.query(f"{texto}\n\n(hora local: {ahora_legible()})")
                emitido_en_mensaje = 0
                hubo_texto = False
                primera = None
                flujo = self.cliente.receive_response().__aiter__()
                while True:
                    try:
                        msg = await asyncio.wait_for(flujo.__anext__(), ESPERA_MAX)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        info(f"el modelo no respondió en {ESPERA_MAX:.0f}s: interrumpo")
                        self.ultima["error"] = f"sin respuesta del modelo en {ESPERA_MAX:.0f}s"
                        await cola.put(("\n" if hubo_texto else "") +
                                       "El modelo se demoró demasiado y lo corté. Repítemelo, por favor.")
                        await self._desconectar()   # mata el pedido colgado; el próximo turno retoma la sesión
                        break
                    if isinstance(msg, SystemMessage):
                        if msg.subtype not in ("init",):
                            info(f"  [sistema] {msg.subtype}: {json.dumps(msg.data, ensure_ascii=False, default=str)[:400]}")
                        continue
                    if isinstance(msg, StreamEvent):
                        ev = msg.event or {}
                        if msg.parent_tool_use_id:
                            continue
                        if ev.get("type") == "message_start":
                            emitido_en_mensaje = 0
                            if hubo_texto:
                                await cola.put("\n")
                        elif ev.get("type") == "content_block_delta" and \
                                (ev.get("delta") or {}).get("type") == "text_delta":
                            trozo = ev["delta"].get("text", "")
                            if trozo:
                                primera = primera or time.time() - t0
                                emitido_en_mensaje += len(trozo)
                                hubo_texto = True
                                self.ultima["respuesta"] += trozo
                                await cola.put(trozo)
                    elif isinstance(msg, AssistantMessage):
                        if msg.parent_tool_use_id:
                            continue
                        if getattr(msg, "error", None):
                            self.ultima["error"] = str(msg.error)
                            info(f"  [api] error del modelo: {msg.error}")
                        for b in msg.content:
                            if isinstance(b, ToolUseBlock):
                                self.ultima["herramientas"].append({"nombre": b.name.split("__")[-1],
                                                                    "entrada": b.input})
                            elif isinstance(b, TextBlock) and emitido_en_mensaje == 0 and b.text:
                                if hubo_texto:
                                    await cola.put("\n")
                                hubo_texto = True
                                self.ultima["respuesta"] += b.text
                                await cola.put(b.text)
                        emitido_en_mensaje = 0
                    elif isinstance(msg, ResultMessage):
                        if msg.session_id:
                            self._guardar_sesion(msg.session_id)
                        if msg.is_error:
                            info(f"  [api] turno con error: {msg.subtype} {str(msg.result)[:300]}")
                        if msg.is_error and not hubo_texto:
                            detalle = msg.result or self.ultima.get("error") or msg.subtype
                            self.ultima["error"] = detalle
                            await cola.put(f"Tuve un problema con mi cerebro: {str(detalle)[:160]}")
                        self.ultima["costo_usd"] = msg.total_cost_usd
                terminado = not str(self.ultima.get("error") or "").startswith("sin respuesta")
                self.ultima["tiempos"] = {"primera_palabra": round(primera or 0, 2),
                                          "total": round(time.time() - t0, 2)}
                info(f"{'[prueba] ' if self.prueba else ''}turno listo en {time.time() - t0:.1f}s "
                     f"(1a palabra {primera or 0:.1f}s) · herramientas: "
                     f"{', '.join(h['nombre'] for h in self.ultima['herramientas']) or 'ninguna'}")
            except asyncio.CancelledError:
                info("interrumpido por Andres")
                raise
            except Exception as e:  # noqa: BLE001
                info(f"error en el turno: {e!r}")
                self.ultima["error"] = repr(e)
                await self._desconectar()
                await cola.put("Tuve un problema con mi cerebro; vuelve a decírmelo en un momento.")
            finally:
                self.ultima["traza"] = list(self.pol.traza)
                if not terminado and self.cliente is not None and not str(self.ultima.get("error") or "").startswith("sin respuesta"):
                    await self._interrumpir()
                await cola.put(None)

    async def _interrumpir(self) -> None:
        try:
            await asyncio.wait_for(self.cliente.interrupt(), 5)

            async def drenar():
                async for _ in self.cliente.receive_response():
                    pass
            await asyncio.wait_for(drenar(), 20)
        except Exception as e:  # noqa: BLE001
            info(f"no pude interrumpir limpio ({e!r}); reinicio la sesión")
            await self._desconectar()


# ------------------------------------------------------------------ servidor HTTP
def _ultimo_usuario(mensajes: list) -> tuple[str, int]:
    usuarios = [m for m in mensajes if m.get("role") == "user"]
    if not usuarios:
        return "", 0
    c = usuarios[-1].get("content", "")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c), len(usuarios)


def crear_app():
    from aiohttp import web

    cerebros = {False: Cerebro(prueba=False), True: Cerebro(prueba=True)}

    async def salud(_req):
        return web.json_response({"status": "ok", "cerebro": "claude-agent-sdk", "modelo": MODELO})

    async def traza(req):
        return web.json_response(cerebros[req.query.get("prueba") == "1"].ultima, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))

    async def chat(req):
        cuerpo = await req.json()
        texto, n_usuario = _ultimo_usuario(cuerpo.get("messages") or [])
        if not texto.strip():
            return web.json_response({"error": {"message": "mensaje vacío"}}, status=400)
        prueba = req.headers.get("X-Pedro-Prueba") == "1"
        nueva = req.headers.get("X-Pedro-Nueva") == "1" or (n_usuario == 1 and not prueba)
        cerebro = cerebros[prueba]
        cola: asyncio.Queue = asyncio.Queue()
        tarea = asyncio.create_task(cerebro.turno(texto, nueva, cola))
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if not cuerpo.get("stream"):
            partes = []
            while (t := await cola.get()) is not None:
                partes.append(t)
            await tarea
            return web.json_response({"id": cid, "object": "chat.completion", "model": MODELO,
                                      "choices": [{"index": 0, "finish_reason": "stop",
                                                   "message": {"role": "assistant", "content": "".join(partes)}}]})

        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8",
                                           "Cache-Control": "no-cache"})
        await resp.prepare(req)

        def chunk(delta: dict, fin: str | None = None) -> bytes:
            return ("data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "model": MODELO,
                                           "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]},
                                          ensure_ascii=False) + "\n\n").encode("utf-8")
        try:
            await resp.write(chunk({"role": "assistant"}))
            while True:
                try:
                    t = await asyncio.wait_for(cola.get(), 2.0)
                except asyncio.TimeoutError:
                    await resp.write(b": pensando\n\n")      # latido: detecta si Andres colgó
                    continue
                if t is None:
                    break
                await resp.write(chunk({"content": t}))
            await resp.write(chunk({}, "stop"))
            await resp.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError) as e:
            info(f"Andres cortó la respuesta ({e.__class__.__name__})")
            tarea.cancel()
        finally:
            try:
                await asyncio.wait_for(asyncio.shield(tarea), 30)
            except BaseException:  # noqa: BLE001
                pass
        return resp

    async def al_iniciar(app):  # noqa: ARG001
        try:
            (JARVIS / "cerebro.iniciado").write_text(datetime.now(ZONA).isoformat(), encoding="utf-8")
        except OSError:
            pass

        async def calentar():
            try:
                await cerebros[False].preparar()
            except Exception as e:  # noqa: BLE001
                info(f"no pude preparar la sesión al arrancar: {e!r}")
        asyncio.create_task(calentar())

    async def al_cerrar(app):  # noqa: ARG001
        for c in cerebros.values():
            await c._desconectar()

    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.router.add_get("/health", salud)
    app.router.add_get("/pedro/traza", traza)
    app.router.add_post("/v1/chat/completions", chat)
    app.on_startup.append(al_iniciar)
    app.on_cleanup.append(al_cerrar)
    return app


def main() -> None:
    from aiohttp import web

    try:
        sys.stdout = _Tee(sys.stdout, open(LOG, "a", encoding="utf-8"))
        sys.stderr = sys.stdout
    except OSError:
        pass
    # Aislar de cualquier sesión de Claude Code desde la que se haya lanzado esto
    for k in list(os.environ):
        if k.startswith(("CLAUDE_CODE_", "CLAUDECODE", "CCR_", "CLAUDE_PID", "CLAUDE_AUTO", "CLAUDE_AFTER",
                         "CLAUDE_CONFIG_DIR")) and k != "CLAUDE_CODE_GIT_BASH_PATH":
            os.environ.pop(k, None)
    secretos = cargar_secretos()
    info("── Pedro · cerebro (motor de Claude Code) ──")
    info(f"modelo {MODELO} · gateway {secretos.get('ANTHROPIC_BASE_URL', 'api.anthropic.com')} · "
         f"clave {'OK' if secretos.get('ANTHROPIC_API_KEY') or secretos.get('ANTHROPIC_AUTH_TOKEN') else 'FALTA'}")
    info(f"herramientas propias: {', '.join(servidores_mcp()) or 'ninguna'} · Git Bash: {bash_de_git() or 'NO encontrado'}")
    web.run_app(crear_app(), host="127.0.0.1", port=PUERTO, print=lambda *_: info(f"escuchando en 127.0.0.1:{PUERTO}"))


if __name__ == "__main__":
    main()
