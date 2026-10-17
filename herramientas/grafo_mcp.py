"""Servidor MCP mínimo (stdio) para que Orión use el segundo cerebro de Andres (C:\\dev\\_grafo).

Es su grafo propio: notas .md por carpetas según el tipo, con las reglas de su CLAUDE.md
(título "# …", "> RESUMEN: …", tipo/tags/fecha, enlaces [[id]], MOC en Mapas/) y su visor web
(visor/), que se regenera solo cuando cambian las notas.

Herramientas:
  buscar_notas(texto)                     -> notas por nombre, resumen o contenido
  leer_nota(nota)                         -> contenido + enlaces salientes + quién la enlaza
  vecinos(nota, saltos)                   -> notas conectadas (1-2 saltos)
  notas_recientes(n)                      -> últimas notas modificadas
  crear_nota(tipo, titulo, resumen, ...)  -> nota nueva en su carpeta, enlazada y añadida a su MOC
  agregar_a_nota(nota, texto)             -> línea fechada en el historial de una nota
  anotar_diario(texto)                    -> línea en Diario/AAAA-MM-DD.md (sección "Con Orión")

Nunca borra ni sobrescribe contenido. No toca CLAUDE.md, README, Plantillas/, visor/ ni ocultas.
Lo usa también cerebro/memoria.py (desde Windows) para la memoria automática.
Bóveda: env PEDRO_GRAFO (por defecto C:\\dev\\_grafo  ·  /mnt/c/dev/_grafo en WSL). Sin dependencias.
"""
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("America/Bogota")
except Exception:  # noqa: BLE001  (Windows sin base de zonas horarias)
    from datetime import timedelta, timezone
    ZONA = timezone(timedelta(hours=-5), "America/Bogota")


def _ruta_grafo() -> str:
    crudo = os.environ.get("PEDRO_GRAFO") or (r"C:\dev\_grafo" if os.name == "nt" else "/mnt/c/dev/_grafo")
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", crudo)
    if m and os.name != "nt":   # ruta de Windows vista desde WSL
        crudo = f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
    return os.path.realpath(crudo)


GRAFO = _ruta_grafo()
_OCULTAS = {".obsidian", ".trash", ".git", "node_modules", "visor", "Plantillas", "nodos", "__pycache__"}
_INTOCABLES = {"claude", "readme"}            # notas raíz que Orión no modifica
CARPETAS = {   # tipo -> carpeta (las mismas que lee su visor)
    "proyecto": "Proyectos", "estudio": "Estudios", "idea": "Ideas", "aprendizaje": "Aprendizajes",
    "persona": "Personas", "recurso": "Recursos", "decision": "Decisiones", "diario": "Diario",
}
_SECCION_MOC = {"proyecto": "Proyectos", "estudio": "Estudios", "idea": "Ideas", "aprendizaje": "Aprendizajes",
                "persona": "Personas", "recurso": "Recursos", "decision": "Decisiones"}
_ENLACE = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
_RESUMEN = re.compile(r"^>\s*RESUMEN:\s*(.+)$", re.M)
_TITULO_MD = re.compile(r"^#\s+(.+)$", re.M)
_MAX_LECTURA = 12000
_STOP = set("""
para como pero este esta esto estos estas ese esa eso esos esas todo toda todos todas porque cuando donde
quiero quieres puedes puede podemos podrias hacer hace hago hice haga tengo tiene tienes tenemos sobre entre
desde hasta favor orion oye dime abre abrir busca buscar mira mirar ahora bien gracias listo cosa cosas algo
nada mismo misma otra otro otros otras tambien solo hola bueno vale dale necesito vamos esta estan fue ser
son tus mis sus nuestro nuestra archivo archivos carpeta nota notas grafo cual cuales quien quienes que
del las los una uno unos unas con sin por mas menos muy aqui alli asi luego antes despues hoy ayer manana
dos tres ano dia mes vez veces hora horas pues aja sea
voy vas va van creo crees sabes saber digo dices dijo decir ver veo hay haber habia sea sean seria
estoy estas estamos estaba tenia queda quedo poner pon pongo dame dar damos tu mi su le les lo la el
""".split())


def log(msg: str) -> None:
    print(f"[grafo_mcp] {msg}", file=sys.stderr, flush=True)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def slug(texto: str) -> str:
    """Nombre de archivo según sus reglas: minúsculas-con-guiones, sin acentos ni espacios."""
    s = re.sub(r"[^a-z0-9]+", "-", _norm(texto).replace("ñ", "n")).strip("-")
    return s[:70].rstrip("-")


def hoy() -> str:
    return f"{datetime.now(ZONA):%Y-%m-%d}"


def _comprobar() -> None:
    if not os.path.isdir(GRAFO):
        raise RuntimeError(f"No encuentro el segundo cerebro en {GRAFO}.")


def _notas() -> list:
    """Rutas relativas (con /) de todas las .md del grafo."""
    _comprobar()
    out = []
    for raiz, dirs, archivos in os.walk(GRAFO):
        dirs[:] = [d for d in dirs if d not in _OCULTAS and not d.startswith((".", "_"))]
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


def _escribir(rel: str, texto: str) -> None:
    """Reemplazo atómico (el visor nunca lee un archivo a medias)."""
    ruta = os.path.join(GRAFO, rel)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(ruta), prefix=".orion-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto)
        for intento in range(5):   # en Windows falla si otro programa (el visor) la tiene abierta justo ahora
            try:
                os.replace(tmp, ruta)
                break
            except PermissionError:
                if intento == 4:
                    raise
                time.sleep(0.2)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- índice (con caché)
_CACHE = {"firma": None, "notas": []}


def indice() -> list:
    """[{id, rel, carpeta, titulo, resumen}] de todas las notas. Se recalcula solo si algo cambió."""
    notas = _notas()
    firma = (len(notas), round(sum(os.path.getmtime(os.path.join(GRAFO, r)) for r in notas), 3))
    if firma == _CACHE["firma"]:
        return _CACHE["notas"]
    out = []
    for rel in notas:
        try:
            txt = _leer(rel)[:4000]
        except OSError:
            continue
        t, r = _TITULO_MD.search(txt), _RESUMEN.search(txt)
        out.append({"id": _titulo(rel), "rel": rel, "carpeta": rel.split("/")[0] if "/" in rel else "",
                    "titulo": t.group(1).strip() if t else _titulo(rel),
                    "resumen": r.group(1).strip() if r else ""})
    _CACHE.update(firma=firma, notas=out)
    return out


def _palabras(texto: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9ñ]+", _norm(texto)):
        if len(w) >= 3 and w not in _STOP and not w.isdigit():
            out.add(w[:-2] if w.endswith("es") and len(w) > 5 else w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def relacionadas(texto: str, n: int = 3, excluir=(), minimo: int = 3) -> list:
    """Notas que tienen que ver con `texto` (nombre/título pesan 3, resumen 1). Local y gratis."""
    q = _palabras(texto)
    if not q:
        return []
    puntos = []
    for nota in indice():
        if nota["id"] in excluir or nota["carpeta"] in ("", "Diario") or nota["id"].lower() in _INTOCABLES:
            continue
        cab = _palabras(nota["id"].replace("-", " ") + " " + nota["titulo"])
        res = _palabras(nota["resumen"])
        p = 3 * len(q & cab) + len(q & res)
        if p >= minimo:
            puntos.append((p, nota["id"], nota))
    puntos.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in puntos[:n]]


def _resolver(nota: str, notas: list = None) -> str:
    """Nombre aproximado, [[enlace]] o ruta -> ruta relativa de una única nota."""
    notas = notas if notas is not None else _notas()
    q = _norm(nota.strip().strip("[]").removesuffix(".md").removesuffix(".MD"))
    if not q:
        raise ValueError("Dime qué nota.")
    por_ruta = [n for n in notas if _norm(n.removesuffix(".md")) == q]
    exactas = por_ruta or [n for n in notas if _norm(_titulo(n)) == q] or \
        [n for n in notas if _norm(_titulo(n)) == slug(q)]
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


def _escribible(rel: str) -> None:
    if _titulo(rel).lower() in _INTOCABLES or rel.split("/")[0] in _OCULTAS:
        raise ValueError(f"{rel} no lo modifico (reglas/plantillas).")


def _insertar_en_seccion(rel: str, es_encabezado, linea: str, nuevo_encabezado: str = "") -> bool:
    """Mete `linea` al final de la sección cuyo encabezado '## …' cumple `es_encabezado`.
    Si no existe y hay `nuevo_encabezado`, crea la sección (antes de '## Relacionado'/'## Enlaces' si los hay)."""
    lineas = _leer(rel).rstrip("\n").split("\n")
    if any(l.strip() == linea.strip() for l in lineas):
        return False
    i = next((k for k, l in enumerate(lineas) if l.startswith("## ") and es_encabezado(l)), None)
    if i is None:
        if not nuevo_encabezado:
            return False
        fin = next((k for k, l in enumerate(lineas) if re.match(r"^## (Relacionado|Enlaces)\b", l)), len(lineas))
        while fin > 0 and not lineas[fin - 1].strip():
            fin -= 1
        nuevo = ["", f"## {nuevo_encabezado}", "", linea]
        lineas[fin:fin] = nuevo + ([""] if fin < len(lineas) and lineas[fin].strip() else [])
    else:
        fin = next((k for k in range(i + 1, len(lineas)) if lineas[k].startswith("## ") or lineas[k].strip() == "---"),
                   len(lineas))
        while fin > i + 1 and not lineas[fin - 1].strip():
            fin -= 1
        if fin == i + 1:
            lineas[fin:fin] = ["", linea]
        else:
            lineas.insert(fin, linea)
    _escribir(rel, "\n".join(lineas).rstrip("\n") + "\n")
    return True


def _ids_existentes(nombres, notas_idx=None) -> list:
    idx = {_norm(n["id"]): n["id"] for n in (notas_idx or indice())}
    out = []
    for e in nombres or []:
        k = _norm(str(e).strip().strip("[]").removesuffix(".md"))
        real = idx.get(k) or idx.get(slug(k))
        if real and real not in out:
            out.append(real)
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
    if not res:   # la frase exacta no aparece: por palabras
        res = [f"- {n['id']}  ({n['rel']}): {n['resumen']}" for n in relacionadas(texto, max_resultados, minimo=1)]
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
        for r in notas) or "El grafo está vacío."


def agregar_a_nota(nota: str, texto: str) -> str:
    """Añade '- AAAA-MM-DD — texto' al historial de la nota (nunca borra ni reescribe lo que hay)."""
    texto = " ".join((texto or "").split())
    if not texto:
        raise ValueError("No hay texto para agregar.")
    rel = _resolver(nota)
    _escribible(rel)
    linea = f"- {hoy()} — {texto} _(Orión)_"
    if not _insertar_en_seccion(rel, lambda l: re.match(r"^## (Notas / )?[Hh]istorial", l) is not None,
                                linea, "Notas / historial"):
        return f"Eso ya estaba en {rel}."
    return f"Agregado al historial de [[{_titulo(rel)}]] ({rel})."


def crear_nota(tipo: str, titulo: str, resumen: str, contenido: str = "", enlaces: list = None,
               mapa: str = "", etiquetas: list = None) -> str:
    """Nota nueva con el formato de sus reglas, en la carpeta de su tipo. Si ya existe, va a su historial."""
    _comprobar()
    tipo = slug(tipo).replace("-", "")
    tipo = {"decisión": "decision", "ideas": "idea", "personas": "persona", "recursos": "recurso"}.get(tipo, tipo)
    if tipo not in CARPETAS or tipo == "diario":
        raise ValueError("tipo debe ser: proyecto, estudio, idea, aprendizaje, persona, recurso o decision "
                         "(para el diario usa anotar_diario).")
    titulo = " ".join((titulo or "").split())[:120]
    resumen = " ".join((resumen or "").split())
    if not titulo or not resumen:
        raise ValueError("Falta el título o el resumen de una frase.")
    ident = slug(titulo)
    if not ident:
        raise ValueError("Título inválido.")
    notas_idx = indice()
    if any(_norm(n["id"]) == ident for n in notas_idx):
        return agregar_a_nota(ident, resumen + (f" {contenido.strip()}" if contenido.strip() else ""))
    ids = _ids_existentes(enlaces, notas_idx)
    mapas = {n["id"] for n in notas_idx if n["carpeta"] == "Mapas"}
    mapa = next((m for m in _ids_existentes([mapa], notas_idx) if m in mapas), "")
    if not mapa:   # su MOC por los enlaces (p. ej. un proyecto que ya está en cat-web)
        mapa = next((i for i in ids if i in mapas), "")
    conexiones = ([mapa] if mapa else []) + [i for i in ids if i != mapa]
    if not conexiones:
        conexiones = ["INDICE"]    # sus reglas: ninguna nota queda suelta
    tags = [slug(t) for t in (etiquetas or []) if slug(t)]
    tags = ["orion"] + [t for t in tags if t != "orion"][:6]
    cuerpo = (contenido or "").strip()
    texto = (f"# {titulo}\n\n> RESUMEN: {resumen}\n\n---\n"
             f"tipo: {tipo}\ntags: [{', '.join(tags)}]\nfecha: {hoy()}\nestado: revisar\nfuente: orion\n---\n\n"
             + (f"## Contenido\n\n{cuerpo}\n\n" if cuerpo else "")
             + "## Enlaces\n" + "".join(f"- [[{c}]]\n" for c in conexiones)
             + f"\n## Notas / historial\n- {hoy()} — creada por Orión a partir de una conversación.\n")
    rel = f"{CARPETAS[tipo]}/{ident}.md"
    os.makedirs(os.path.join(GRAFO, CARPETAS[tipo]), exist_ok=True)
    with open(os.path.join(GRAFO, rel), "x", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    extra = []
    linea = f"- **[[{ident}]]** — {resumen}"
    if mapa:
        sec = _SECCION_MOC[tipo]
        if _insertar_en_seccion(f"Mapas/{mapa}.md", lambda l: l[3:].strip().lower().startswith(sec.lower()), linea, sec):
            extra.append(f"añadida a [[{mapa}]]")
    if os.path.exists(os.path.join(GRAFO, "INDICE.md")) and tipo in ("decision", "proyecto"):
        if tipo == "decision":
            ok = _insertar_en_seccion("INDICE.md", lambda l: l.strip() == "## Decisiones", linea, "Decisiones")
        else:
            ok = bool(mapa) and _insertar_en_seccion("INDICE.md", lambda l: f"[[{mapa}]]" in l, linea)
        if ok:
            extra.append("añadida al INDICE")
    return f"Nota creada: {rel}" + (f" ({', '.join(extra)})" if extra else "")


def anotar_diario(texto: str, enlaces: list = None) -> str:
    """Línea con hora en Diario/AAAA-MM-DD.md, sección 'Con Orión' (crea el día si no existe)."""
    texto = " ".join((texto or "").split())
    if not texto:
        raise ValueError("No hay nada que anotar.")
    _comprobar()
    ids = _ids_existentes(enlaces)
    linea = f"- {datetime.now(ZONA):%H:%M} — {texto}" + (" · " + " ".join(f"[[{i}]]" for i in ids) if ids else "")
    rel = f"Diario/{hoy()}.md"
    ruta = os.path.join(GRAFO, rel)
    if not os.path.exists(ruta):
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "x", encoding="utf-8", newline="\n") as f:
            f.write(f"# {hoy()}\n\n> RESUMEN: Lo que hablé y decidí hoy con Orión.\n\n---\n"
                    f"tipo: diario\ntags: [orion]\nfecha: {hoy()}\n---\n\n## Con Orión\n\n{linea}\n\n"
                    "## Enlaces\n- [[INDICE]]\n")
        return f"Anotado en {rel}"
    _insertar_en_seccion(rel, lambda l: l.strip() == "## Con Orión", linea, "Con Orión")
    return f"Anotado en {rel}"


# ---------------------------------------------------------------- MCP
def _obj(props: dict = None, req: list = None) -> dict:
    return {"type": "object", "properties": props or {}, **({"required": req} if req else {})}


_S = {"type": "string"}
_LISTA = {"type": "array", "items": _S}
HERRAMIENTAS = {
    "buscar_notas": (buscar_notas, {
        "description": "Busca en el segundo cerebro de Andres (su grafo propio, C:\\dev\\_grafo) por nombre, "
                       "resumen o contenido. Úsala antes de responder sobre sus proyectos, ideas, personas o decisiones.",
        "inputSchema": _obj({"texto": _S, "max_resultados": {"type": "integer"}}, ["texto"])}),
    "leer_nota": (leer_nota, {
        "description": "Lee una nota del grafo por nombre o [[enlace]]: contenido, a qué enlaza y quién la enlaza.",
        "inputSchema": _obj({"nota": _S}, ["nota"])}),
    "vecinos": (vecinos, {
        "description": "Notas conectadas a una nota en el grafo (enlaces en ambos sentidos), a 1 o 2 saltos.",
        "inputSchema": _obj({"nota": _S, "saltos": {"type": "integer", "description": "1 o 2"}}, ["nota"])}),
    "notas_recientes": (notas_recientes, {
        "description": "Las notas del grafo modificadas más recientemente (también sirve para ver qué guardaste).",
        "inputSchema": _obj({"n": {"type": "integer"}})}),
    "crear_nota": (crear_nota, {
        "description": "Crea una nota NUEVA en el grafo siguiendo las reglas de Andres: va a la carpeta de su tipo, "
                       "con título, '> RESUMEN:' de una frase, enlaces [[id]] a notas existentes (búscalas antes) y "
                       "se añade a su MOC (mapa cat-…). Si ya existe, agrega al historial. Nunca sobrescribe. "
                       "No guardes claves, contraseñas ni datos de cuentas.",
        "inputSchema": _obj({"tipo": {"type": "string", "description": "proyecto|estudio|idea|aprendizaje|persona|recurso|decision"},
                             "titulo": _S, "resumen": {"type": "string", "description": "Una frase que se entienda sola"},
                             "contenido": {"type": "string", "description": "Markdown (opcional)"},
                             "enlaces": {**_LISTA, "description": "ids de notas existentes: check, startup…"},
                             "mapa": {"type": "string", "description": "MOC: cat-web, cat-ia, cat-negocio…"},
                             "etiquetas": _LISTA}, ["tipo", "titulo", "resumen"])}),
    "agregar_a_nota": (agregar_a_nota, {
        "description": "Añade una línea fechada al historial de una nota existente (nunca borra ni reescribe).",
        "inputSchema": _obj({"nota": _S, "texto": _S}, ["nota", "texto"])}),
    "anotar_diario": (anotar_diario, {
        "description": "Anota una línea con la hora en el Diario de hoy (sección 'Con Orión').",
        "inputSchema": _obj({"texto": _S, "enlaces": _LISTA}, ["texto"])}),
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
                            "serverInfo": {"name": "grafo", "version": "2.0"}})
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
