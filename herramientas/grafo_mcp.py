"""Servidor MCP mínimo (stdio) para que Pedro use el grafo de Obsidian de Andres (C:\\dev_grafo).

Herramientas:
  buscar_notas(texto)                 -> notas cuyo título o contenido coinciden
  leer_nota(nota)                     -> contenido + enlaces salientes + backlinks
  vecinos(nota, saltos)               -> notas conectadas en el grafo (1-2 saltos)
  notas_recientes(n)                  -> últimas notas modificadas
  crear_nota(titulo, contenido, ...)  -> nota nueva (nunca sobrescribe)
  agregar_a_nota(nota, texto)         -> añade al final de una nota existente (nunca borra)

Nunca borra ni sobrescribe. No toca .obsidian/, .trash/ ni .git/.
Bóveda: env PEDRO_GRAFO (por defecto /mnt/c/dev_grafo). Sin dependencias.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

GRAFO = os.path.realpath(os.environ.get("PEDRO_GRAFO", "/mnt/c/dev_grafo"))
CARPETA_PEDRO = "Pedro"            # carpeta por defecto para notas nuevas
ZONA = ZoneInfo("America/Bogota")
_OCULTAS = {".obsidian", ".trash", ".git", "node_modules"}
_ENLACE = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
_TITULO_MALO = re.compile(r'[\\/:*?"<>|#^\[\]]')
_MAX_LECTURA = 12000


def log(msg: str) -> None:
    print(f"[grafo_mcp] {msg}", file=sys.stderr, flush=True)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _comprobar() -> None:
    if not os.path.isdir(GRAFO):
        raise RuntimeError("No encuentro la bóveda C:\\dev_grafo.")


def _notas() -> list:
    """Rutas relativas (con /) de todas las .md de la bóveda."""
    _comprobar()
    out = []
    for raiz, dirs, archivos in os.walk(GRAFO):
        dirs[:] = [d for d in dirs if d not in _OCULTAS and not d.startswith(".")]
        for a in archivos:
            if a.lower().endswith(".md"):
                out.append(os.path.relpath(os.path.join(raiz, a), GRAFO).replace(os.sep, "/"))
                if len(out) >= 20000:
                    return out
    return out


def _titulo(rel: str) -> str:
    return os.path.splitext(os.path.basename(rel))[0]


def _leer(rel: str) -> str:
    with open(os.path.join(GRAFO, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def _resolver(nota: str, notas: list = None) -> str:
    """Nombre aproximado, [[enlace]] o ruta -> ruta relativa de una única nota."""
    notas = notas if notas is not None else _notas()
    q = _norm(nota.strip().strip("[]").removesuffix(".md").removesuffix(".MD"))
    if not q:
        raise ValueError("Dime qué nota.")
    por_ruta = [n for n in notas if _norm(n.removesuffix(".md")) == q]
    exactas = por_ruta or [n for n in notas if _norm(_titulo(n)) == q]
    candidatas = exactas or [n for n in notas if q in _norm(_titulo(n))]
    if len(candidatas) == 1:
        return candidatas[0]
    if candidatas:
        muestra = ", ".join(_titulo(c) for c in candidatas[:10])
        raise ValueError(f"'{nota}' coincide con varias notas: {muestra}. ¿Cuál?")
    raise ValueError(f"No encontré la nota '{nota}'. Prueba buscar_notas.")


def _enlaces(texto: str) -> list:
    vistos, out = set(), []
    for m in _ENLACE.finditer(texto):
        t = m.group(1).strip()
        if t and _norm(t) not in vistos:
            vistos.add(_norm(t))
            out.append(t)
    return out


def _mapa_titulos(notas: list) -> dict:
    return {_norm(_titulo(n)): n for n in notas}


def _backlinks(rel: str, notas: list) -> list:
    objetivo = {_norm(_titulo(rel)), _norm(rel.removesuffix(".md"))}
    out = []
    for n in notas:
        if n == rel:
            continue
        try:
            if any(_norm(os.path.basename(e)) in objetivo or _norm(e) in objetivo
                   for e in _enlaces(_leer(n))):
                out.append(_titulo(n))
        except OSError:
            pass
    return out


# ---------------------------------------------------------------- herramientas
def buscar_notas(texto: str, max_resultados: int = 15) -> str:
    q = _norm(texto)
    if not q:
        raise ValueError("Dime qué buscar.")
    max_resultados = max(1, min(int(max_resultados), 40))
    en_titulo, en_texto = [], []
    for n in _notas():
        if q in _norm(_titulo(n)):
            en_titulo.append(f"- {_titulo(n)}  ({n})")
            continue
        try:
            contenido = _leer(n)
        except OSError:
            continue
        i = _norm(contenido).find(q)
        if i >= 0:
            frag = " ".join(contenido[max(0, i - 60): i + 100].split())
            en_texto.append(f"- {_titulo(n)}  ({n}): …{frag}…")
        if len(en_titulo) + len(en_texto) >= max_resultados * 3:
            break
    res = (en_titulo + en_texto)[:max_resultados]
    return "\n".join(res) if res else f"Ninguna nota menciona '{texto}'."


def leer_nota(nota: str) -> str:
    notas = _notas()
    rel = _resolver(nota, notas)
    contenido = _leer(rel)
    cortado = len(contenido) > _MAX_LECTURA
    salientes = _enlaces(contenido)
    entrantes = _backlinks(rel, notas)
    partes = [f"# {_titulo(rel)}  ({rel})", contenido[:_MAX_LECTURA]]
    if cortado:
        partes.append(f"[… nota recortada, {len(contenido)} caracteres en total]")
    partes.append("Enlaza a: " + (", ".join(salientes) or "nada"))
    partes.append("La enlazan: " + (", ".join(entrantes) or "nadie"))
    return "\n\n".join(partes)


def vecinos(nota: str, saltos: int = 1) -> str:
    notas = _notas()
    inicio = _resolver(nota, notas)
    saltos = 2 if int(saltos) >= 2 else 1
    titulos = _mapa_titulos(notas)
    # grafo no dirigido: salientes + entrantes
    salientes = {}
    for n in notas:
        try:
            salientes[n] = [titulos[_norm(os.path.basename(e))] for e in _enlaces(_leer(n))
                            if _norm(os.path.basename(e)) in titulos]
        except OSError:
            salientes[n] = []
    adyacentes = {n: set(v) for n, v in salientes.items()}
    for n, v in salientes.items():
        for m in v:
            adyacentes.setdefault(m, set()).add(n)
    nivel, vistos, lineas = {inicio}, {inicio}, []
    for s in range(1, saltos + 1):
        nuevo = set()
        for n in nivel:
            nuevo |= adyacentes.get(n, set()) - vistos
        if nuevo:
            lineas.append(f"A {s} salto(s): " + ", ".join(sorted(_titulo(n) for n in nuevo)))
        vistos |= nuevo
        nivel = nuevo
    return "\n".join(lineas) or f"'{_titulo(inicio)}' no tiene conexiones en el grafo."


def notas_recientes(n: int = 10) -> str:
    n = max(1, min(int(n), 30))
    notas = sorted(_notas(), key=lambda r: os.path.getmtime(os.path.join(GRAFO, r)), reverse=True)[:n]
    return "\n".join(
        f"- {_titulo(r)}  ({r}) — {datetime.fromtimestamp(os.path.getmtime(os.path.join(GRAFO, r)), ZONA):%Y-%m-%d %H:%M}"
        for r in notas) or "La bóveda está vacía."


def _carpeta_segura(carpeta: str) -> str:
    carpeta = (carpeta or CARPETA_PEDRO).strip().strip("/\\").replace("\\", "/")
    partes = [p for p in carpeta.split("/") if p]
    if any(p in (".", "..") or p.startswith(".") or _TITULO_MALO.search(p) for p in partes):
        raise ValueError("Carpeta no permitida.")
    destino = os.path.realpath(os.path.join(GRAFO, *partes))
    if not (destino == GRAFO or destino.startswith(GRAFO + os.sep)):
        raise ValueError("Carpeta fuera de la bóveda.")
    return destino


def crear_nota(titulo: str, contenido: str, carpeta: str = "", etiquetas: list = None) -> str:
    _comprobar()
    titulo = " ".join((titulo or "").split())
    if not titulo or _TITULO_MALO.search(titulo) or len(titulo) > 120:
        raise ValueError("Título inválido (sin \\ / : * ? \" < > | # ^ [ ]).")
    destino = _carpeta_segura(carpeta)
    ruta = os.path.join(destino, titulo + ".md")
    if os.path.exists(ruta):
        raise ValueError(f"Ya existe la nota '{titulo}'. Usa agregar_a_nota.")
    if any(_norm(_titulo(n)) == _norm(titulo) for n in _notas()):
        raise ValueError(f"Ya hay una nota llamada '{titulo}' en otra carpeta. Usa agregar_a_nota.")
    tags = [re.sub(r"[^\w/-]", "", t) for t in (etiquetas or []) if t]
    tags = ["pedro"] + [t for t in tags if t and t != "pedro"]
    cabecera = ("---\n"
                f"creado: {datetime.now(ZONA):%Y-%m-%d %H:%M}\n"
                f"tags: [{', '.join(tags)}]\n"
                "fuente: Pedro\n"
                "---\n\n")
    os.makedirs(destino, exist_ok=True)
    with open(ruta, "x", encoding="utf-8", newline="\n") as f:
        f.write(cabecera + (contenido or "").rstrip() + "\n")
    rel = os.path.relpath(ruta, GRAFO).replace(os.sep, "/")
    return f"Nota creada: {rel}"


def agregar_a_nota(nota: str, texto: str) -> str:
    if not (texto or "").strip():
        raise ValueError("No hay texto para agregar.")
    rel = _resolver(nota)
    with open(os.path.join(GRAFO, rel), "a", encoding="utf-8", newline="\n") as f:
        f.write(f"\n\n<!-- Pedro {datetime.now(ZONA):%Y-%m-%d %H:%M} -->\n{texto.rstrip()}\n")
    return f"Agregado al final de {rel}"


# ---------------------------------------------------------------- MCP
def _obj(props: dict = None, req: list = None) -> dict:
    return {"type": "object", "properties": props or {}, **({"required": req} if req else {})}


_S = {"type": "string"}
HERRAMIENTAS = {
    "buscar_notas": (buscar_notas, {
        "description": "Busca en el grafo de Obsidian de Andres (su segundo cerebro, C:\\dev_grafo) por "
                       "título o contenido. Úsala antes de responder sobre ideas, decisiones o notas suyas.",
        "inputSchema": _obj({"texto": _S, "max_resultados": {"type": "integer"}}, ["texto"])}),
    "leer_nota": (leer_nota, {
        "description": "Lee una nota del grafo por nombre aproximado o [[enlace]]; devuelve contenido, "
                       "a qué notas enlaza y qué notas la enlazan (backlinks).",
        "inputSchema": _obj({"nota": _S}, ["nota"])}),
    "vecinos": (vecinos, {
        "description": "Notas conectadas a una nota en el grafo (enlaces en ambos sentidos), a 1 o 2 saltos.",
        "inputSchema": _obj({"nota": _S, "saltos": {"type": "integer", "description": "1 o 2"}}, ["nota"])}),
    "notas_recientes": (notas_recientes, {
        "description": "Las notas del grafo modificadas más recientemente.",
        "inputSchema": _obj({"n": {"type": "integer"}})}),
    "crear_nota": (crear_nota, {
        "description": "Crea una nota NUEVA en el grafo (por defecto en la carpeta Pedro/). Nunca sobrescribe. "
                       "Conéctala con [[Nombre de nota]] a notas existentes (búscalas antes). Escribe en "
                       "Markdown. No guardes secretos ni contenido copiado de correos o webs sin que Andres lo pida.",
        "inputSchema": _obj({"titulo": _S, "contenido": _S,
                             "carpeta": {"type": "string", "description": "Subcarpeta (por defecto Pedro)"},
                             "etiquetas": {"type": "array", "items": _S}}, ["titulo", "contenido"])}),
    "agregar_a_nota": (agregar_a_nota, {
        "description": "Añade texto al FINAL de una nota existente del grafo (nunca borra ni reescribe).",
        "inputSchema": _obj({"nota": _S, "texto": _S}, ["nota", "texto"])}),
}


def responder(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error:
        msg["error"] = {"code": -32000, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
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
            continue
        if metodo == "initialize":
            responder(id_, {"protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "grafo", "version": "1.0"}})
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
                log(f"{p.get('name')} falló: {e!r}")
                responder(id_, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        elif metodo == "ping":
            responder(id_, {})
        else:
            responder(id_, error=f"Método no soportado: {metodo}")


if __name__ == "__main__":
    main()
