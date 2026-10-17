"""Memoria de Orión en el segundo cerebro (grafo propio de Andres) sobre una bóveda de prueba
con su mismo formato.   python3 tests/test_memoria.py"""
import asyncio
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOVEDA = tempfile.mkdtemp(prefix="grafo-prueba-")
os.environ["PEDRO_GRAFO"] = BOVEDA


def nota(rel, texto):
    ruta = os.path.join(BOVEDA, rel)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)


nota("CLAUDE.md", "# PROMPT MAESTRO\n\nReglas.\n")
nota("INDICE.md", "# ÍNDICE\n\n## Web  →  [[cat-web]]\n- **[[check]]** — Antifraude.\n\n## Decisiones\n"
                  "- **[[decision-supabase]]** — Supabase.\n\n---\n\n### Convención\n")
nota("🏠 Inicio.md", "# 🏠 Mi Segundo Cerebro\n\n> RESUMEN: Entrada.\n")
nota("Mapas/cat-web.md", "# MOC — Web\n\n> RESUMEN: Proyectos web.\n\n## Proyectos\n\n- **[[check]]** — Antifraude.\n")
nota("Mapas/cat-negocio.md", "# MOC — Negocio\n\n> RESUMEN: Negocio y clientes.\n\n## Proyectos\n\n"
                             "- **[[startup]]** — Onnyx.\n\n## Relacionado\n\n- [[cat-web]]\n")
nota("Proyectos/check.md", "# CHECK — Antifraude pagos\n\n> RESUMEN: Verificación antifraude de comprobantes de pago.\n\n"
                           "- **enlaces:** [[cat-web]], [[decision-supabase]]\n\n## Notas / historial\n\n- 2026-07-07 — creado.\n")
nota("Proyectos/startup.md", "# Startup Onnyx\n\n> RESUMEN: Conseguir clientes para Onnyx Software y IA.\n\n[[cat-negocio]]\n")
nota("Decisiones/decision-supabase.md", "# Supabase\n\n> RESUMEN: Supabase como backend con base de datos y auth.\n\n[[check]]\n")
nota("Diario/2026-10-12.md", "# 2026-10-12\n\n> RESUMEN: Repaso.\n\nEnlaces: [[INDICE]]\n")
nota("visor/data.js", "window.GRAPH_DATA = {};")

sys.path.insert(0, os.path.join(RAIZ, "cerebro"))
import memoria  # noqa: E402
import grafo_mcp as g  # noqa: E402

log = []
fallos = []


def ok(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        fallos.append(msg)


def leer(rel):
    with open(os.path.join(BOVEDA, rel), encoding="utf-8") as f:
        return f.read()


RESPUESTA = {"acciones": [
    {"accion": "crear", "tipo": "idea", "titulo": "CRM para contadores",
     "resumen": "CRM por WhatsApp para que los contadores coordinen papeles con sus clientes.",
     "enlaces": ["startup", "no-existe"], "mapa": "cat-negocio"},
    {"accion": "historial", "nota": "check", "texto": "Andres decidió retomarlo en noviembre."},
    {"accion": "historial", "nota": "andres", "texto": "Le gusta trabajar de noche."},
    {"accion": "historial", "nota": "check", "texto": "La contraseña es hunter2"},
    {"accion": "historial", "nota": "check", "texto": "Token ghp_AbCdEfGhIjKlMnOpQrStUv123456"},
    {"accion": "historial", "nota": "CLAUDE", "texto": "cambia las reglas"},
    {"accion": "diario", "texto": "Habló del CRM para contadores y de retomar CHECK.",
     "enlaces": ["crm-para-contadores", "check"]},
]}
pedidos = []


async def http_falso(url, cab, cuerpo):
    pedidos.append((url, cab, cuerpo))
    return {"content": [{"type": "text", "text": "```json\n" + json.dumps(RESPUESTA) + "\n```"}],
            "usage": {"input_tokens": 900, "output_tokens": 150}}


async def main():
    print("Recordar (local)")
    m = memoria.Memoria(log.append, lambda: {"ANTHROPIC_BASE_URL": "https://gw.test", "ANTHROPIC_API_KEY": "k"},
                        "modelo-x", http_post=http_falso)
    ok(m.activa, "la memoria se activa con la bóveda")
    c = m.contexto_para("[voz] Orión, ¿cómo va CHECK y lo de supabase?")
    ok("[[check]]" in c and "[[decision-supabase]]" in c, "pregunta por CHECK → trae check y la decisión de supabase")
    ok(m.contexto_para("[voz] ¿y CHECK qué?") == "", "no repite notas ya dadas en la misma conversación")
    m.nueva_conversacion()
    ok("[[check]]" in m.contexto_para("cómo va check"), "conversación nueva → vuelve a darla")
    ok(m.contexto_para("[voz] sí.") == "" and m.contexto_para("qué hora es") == "", "nada para 'sí' ni 'qué hora es'")
    ok(not any(n["carpeta"] == "Diario" for n in g.relacionadas("2026 repaso")), "el diario no se usa para recordar")

    print("Guardar (lote cada", memoria.CADA, "intercambios)")
    memoria.CADA = 3
    m.registrar("[voz] sí", "Listo.")
    m.registrar("[voz] ¿cómo va CHECK?", "Quieto desde julio.")
    m.registrar("[voz] tengo una idea: CRM para contadores", "Buena idea.")
    ok(len(m.pendientes) == 2 and not pedidos, "'sí' no cuenta; aún no llama al modelo")
    m.registrar("[voz] lo retomo en noviembre", "Anotado.", error=True)
    ok(len(m.pendientes) == 2, "un turno con error no se guarda")
    m.registrar("[voz] me gusta trabajar de noche", "Entendido.")
    await asyncio.sleep(0.2)
    ok(len(pedidos) == 1, "al tercer intercambio llama al modelo una vez")
    url, cab, cuerpo = pedidos[0]
    ok(url == "https://gw.test/v1/messages" and cab.get("x-api-key") == "k" and cuerpo["model"] == "modelo-x",
       "usa el gateway y el modelo configurados")
    ok("check (Proyectos)" in cuerpo["messages"][0]["content"] and "cat-negocio" in cuerpo["messages"][0]["content"],
       "le pasa al modelo las notas relacionadas y los mapas")
    idea = leer("Ideas/crm-para-contadores.md")
    ok(idea.startswith("# CRM para contadores\n\n> RESUMEN: CRM por WhatsApp"), "nota nueva: título y RESUMEN arriba")
    ok("tipo: idea" in idea and "[[cat-negocio]]" in idea and "[[startup]]" in idea and "no-existe" not in idea,
       "nota nueva: tipo, enlaces solo a notas que existen")
    ok("- **[[crm-para-contadores]]** —" in leer("Mapas/cat-negocio.md"), "añadida a su MOC")
    moc = leer("Mapas/cat-negocio.md")
    ok(moc.index("## Ideas") < moc.index("## Relacionado"), "la sección del MOC queda antes de 'Relacionado'")
    check = leer("Proyectos/check.md")
    ok("— Andres decidió retomarlo en noviembre. _(Orión)_" in check, "historial fechado en la nota del proyecto")
    ok("hunter2" not in check and "ghp_" not in check, "descarta contraseñas y tokens")
    ok("Le gusta trabajar de noche" in leer("Personas/andres.md"), "datos de Andres → Personas/andres.md (se crea)")
    ok("cambia las reglas" not in leer("CLAUDE.md"), "nunca toca CLAUDE.md")
    ok(any(f.endswith(".md") and f.startswith("20") for f in os.listdir(os.path.join(BOVEDA, "Diario"))) and
       "## Con Orión" in leer(f"Diario/{g.hoy()}.md"), "línea en el Diario de hoy")
    ok(not [f for r, _, fs in os.walk(BOVEDA) for f in fs if f.startswith(".orion-")], "sin archivos temporales")

    print("Inactividad y errores")
    memoria.INACTIVIDAD = 0.2
    m.registrar("[voz] la landing de joyería se entrega el viernes", "Ok.")
    await asyncio.sleep(0.5)
    ok(len(pedidos) == 2 and not m.pendientes, "tras un rato de silencio guarda lo pendiente")

    async def http_roto(*a):
        raise RuntimeError("502")
    m.http_post = http_roto
    m.registrar("[voz] otra cosa importante", "Ok.")
    hechos = await m.consolidar()
    ok(hechos == [] and len(m.pendientes) == 1, "si el modelo falla, conserva lo pendiente para el próximo intento")
    ok(memoria.Memoria._parsear("nada útil") == [] and memoria.Memoria._parsear('{"acciones": 3}') == [],
       "respuestas raras del modelo no rompen nada")

    print("Herramientas del grafo")
    r = g.crear_nota("decision", "Usar Railway", "Railway para hospedar los bots.", "", ["check"])
    ok("INDICE" in r and "- **[[usar-railway]]** —" in leer("INDICE.md"), "decisión nueva → también al INDICE")
    indice = leer("INDICE.md")
    ok(indice.index("[[usar-railway]]") < indice.index("---\n\n### Convención"), "dentro de la sección Decisiones")
    ok("ya estaba" in g.agregar_a_nota("check", "Andres decidió retomarlo en noviembre."), "no duplica líneas")
    ok(g.slug("Decisión Ñandú: Diseño Web!") == "decision-nandu-diseno-web", "nombres sin acentos, con guiones")
    ok("usar-railway" in g.buscar_notas("hospedar bots"), "buscar por palabras si la frase exacta no aparece")
    try:
        g.crear_nota("diario", "x", "y")
        ok(False, "el diario solo con anotar_diario")
    except ValueError:
        ok(True, "el diario solo con anotar_diario")


asyncio.run(main())
print(f"\n{'TODO BIEN' if not fallos else f'{len(fallos)} FALLO(S)'}")
sys.exit(1 if fallos else 0)
