"""Servidor MCP mínimo (stdio) para que Orión use Comet en Windows desde WSL.

Herramientas:
  abrir_en_comet(url)  -> abre la URL en TU Comet normal (con tus sesiones), para que la veas.
  abrir_comet_pedro()  -> abre el Comet con el perfil de Orión (el que él puede controlar).

Sin dependencias: habla JSON-RPC por stdin/stdout.
"""
import json
import os
import subprocess
import sys
from urllib.parse import urlparse

PUERTO_CDP = 9222


def _win(cmd: str) -> str:
    r = subprocess.run(["cmd.exe", "/c", cmd], capture_output=True, text=True,
                       cwd="/mnt/c", timeout=20)
    return r.stdout.strip()


_RUTA: list = []


def ruta_comet() -> str | None:
    if _RUTA:
        return _RUTA[0]
    if os.environ.get("JARVIS_COMET"):
        _RUTA.append(os.environ["JARVIS_COMET"])
        return _RUTA[0]
    local = _win("echo %LOCALAPPDATA%")
    progs = [_win("echo %ProgramFiles%"), _win("echo %ProgramFiles(x86)%")]
    candidatos = [rf"{local}\Perplexity\Comet\Application\comet.exe"] + \
                 [rf"{p}\Perplexity\Comet\Application\comet.exe" for p in progs if p]
    for c in candidatos:
        if os.path.exists(_a_linux(c)):
            _RUTA.append(c)
            return c
    return None


def _a_linux(ruta_win: str) -> str:
    return "/mnt/" + ruta_win[0].lower() + ruta_win[2:].replace("\\", "/")


def _lanzar(*argumentos: str) -> None:
    """Ejecuta comet.exe directamente (sin cmd.exe: nada de comillas que escapar)."""
    exe = ruta_comet()
    if not exe:
        raise RuntimeError("No encontré Comet instalado (busqué en AppData y Archivos de programa).")
    subprocess.Popen([_a_linux(exe), *argumentos], cwd="/mnt/c", start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def abrir_en_comet(url: str) -> str:
    u = urlparse(url if "://" in url else "https://" + url)
    if u.scheme not in ("http", "https") or not u.netloc or "." not in u.netloc:
        return f"URL no válida: {url}"
    destino = u.geturl()
    _lanzar(destino)
    return f"Abrí {destino} en el Comet de Andres."


def abrir_comet_pedro() -> str:
    perfil = _win("echo %LOCALAPPDATA%") + r"\PedroComet"
    _lanzar(f"--remote-debugging-port={PUERTO_CDP}", f"--user-data-dir={perfil}",
            "--no-first-run", "--no-default-browser-check")
    return "Abrí el Comet de Orión (perfil propio, controlable)."


HERRAMIENTAS = {
    "abrir_en_comet": (abrir_en_comet, {
        "description": "Abre una página web en el navegador Comet de Andres para que ÉL la vea "
                       "(usa sus sesiones). No devuelve el contenido de la página.",
        "inputSchema": {"type": "object",
                        "properties": {"url": {"type": "string", "description": "Dirección web"}},
                        "required": ["url"]}}),
    "abrir_comet_pedro": (abrir_comet_pedro, {
        "description": "Abre el Comet con el perfil propio de Orión (necesario antes de usar las "
                       "herramientas browser_* que controlan el navegador).",
        "inputSchema": {"type": "object", "properties": {}}}),
}


def responder(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error:
        msg["error"] = {"code": -32000, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for linea in sys.stdin:
        linea = linea.strip()
        if not linea:
            continue
        try:
            req = json.loads(linea)
        except ValueError:
            continue
        metodo, id_ = req.get("method"), req.get("id")
        if id_ is None:
            continue  # notificación
        if metodo == "initialize":
            responder(id_, {"protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "comet", "version": "1.0"}})
        elif metodo == "tools/list":
            responder(id_, {"tools": [{"name": n, **spec} for n, (_, spec) in HERRAMIENTAS.items()]})
        elif metodo == "tools/call":
            p = req.get("params", {})
            fn = HERRAMIENTAS.get(p.get("name"), (None,))[0]
            if not fn:
                responder(id_, error=f"Herramienta desconocida: {p.get('name')}")
                continue
            try:
                texto = fn(**(p.get("arguments") or {}))
                responder(id_, {"content": [{"type": "text", "text": texto}], "isError": False})
            except Exception as e:  # noqa: BLE001
                responder(id_, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        elif metodo == "ping":
            responder(id_, {})
        else:
            responder(id_, error=f"Método no soportado: {metodo}")


if __name__ == "__main__":
    main()
