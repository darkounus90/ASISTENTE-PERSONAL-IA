"""Configura las herramientas de Orión (lo llama cerebro.sh en cada arranque).

- Lectura: buscar en internet, consultar URLs/APIs, calcular.
- Comet (etapa 1): abrir páginas en el Comet de Andres.
- Comet (etapa 2): controlar el Comet con perfil propio de Orión vía Playwright MCP
  (necesita Node.js en Windows; se usa con npx).
Nada que ejecute comandos ni escriba/borre archivos.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import tomlkit

agente = sys.argv[1] if len(sys.argv) > 1 else "simple"
home = Path.home() / ".openjarvis"
cfg = home / "config.toml"
doc = tomlkit.parse(cfg.read_text()) if cfg.exists() else tomlkit.document()

herramientas = ["web_search", "http_request", "calculator", "think", "memory_manage"]
servidores = [{
    "name": "comet",
    "command": str(home / ".venv" / "bin" / "python"),
    "args": ["/mnt/c/Users/PC/Documents/jarvis/comet_mcp.py"],
}]
herramientas += ["abrir_en_comet", "abrir_comet_pedro"]

# --- PC, recordatorios, Gmail/Calendar y Claude Code (carpeta herramientas\)
PY = str(home / ".venv" / "bin" / "python")
H = "/mnt/c/Users/PC/Documents/jarvis/herramientas"
servidores += [
    {"name": "pc", "command": PY, "args": [f"{H}/pc_mcp.py"]},
    {"name": "google", "command": PY, "args": [f"{H}/google_mcp.py"]},
    {"name": "claude-code", "command": PY, "args": [f"{H}/claude_code_mcp.py"]},
    {"name": "grafo", "command": PY, "args": [f"{H}/grafo_mcp.py"]},
]
herramientas += [
    "listar_proyectos", "abrir_proyecto", "abrir_claude_code", "abrir_terminal", "abrir_app",
    "listar_terminales", "escribir_en_terminal",
    "listar_archivos", "leer_archivo", "buscar_en_proyecto",
    "crear_recordatorio", "listar_recordatorios", "borrar_recordatorio",
    "correos_recientes", "leer_correo", "agenda", "crear_evento",
    "lanzar_claude_code", "estado_tareas", "resultado_tarea", "cancelar_tarea",
    "buscar_notas", "leer_nota", "vecinos", "notas_recientes", "crear_nota", "agregar_a_nota",
]
google_ok = (home / "google_pedro.json").exists()


def hay_npx_windows() -> bool:
    try:
        r = subprocess.run(["cmd.exe", "/c", "where npx"], capture_output=True, text=True,
                           cwd="/mnt/c", timeout=20)
        return r.returncode == 0 and "npx" in r.stdout.lower()
    except Exception:  # noqa: BLE001
        return False


control_comet = agente != "simple" and hay_npx_windows()
if control_comet:
    servidores.append({
        "name": "comet-control",
        "command": "cmd.exe",
        "args": ["/c", "npx", "-y", "@playwright/mcp@latest",
                 "--cdp-endpoint", "http://127.0.0.1:9222"],
    })
    herramientas += ["browser_navigate", "browser_navigate_back", "browser_snapshot",
                     "browser_click", "browser_type", "browser_press_key", "browser_hover",
                     "browser_select_option", "browser_fill_form", "browser_wait_for",
                     "browser_tabs", "browser_take_screenshot"]
elif importlib.util.find_spec("playwright") is not None:
    # sin Node en Windows: navegador interno (headless) de OpenJarvis, solo lectura
    herramientas += ["browser_navigate", "browser_extract", "browser_axtree"]

(home / "mcp-servers.json").write_text(json.dumps(servidores, indent=2))

for seccion in ("tools", "agent"):
    if seccion not in doc:
        doc[seccion] = tomlkit.table()
if "mcp" not in doc["tools"]:
    doc["tools"]["mcp"] = tomlkit.table()
doc["tools"]["enabled"] = herramientas
doc["tools"]["mcp"]["enabled"] = agente != "simple"
# `jarvis serve` solo acepta el JSON en línea (no un nombre de archivo)
doc["tools"]["mcp"]["servers"] = json.dumps(servidores)
doc["agent"]["max_turns"] = 8
doc["agent"]["context_from_memory"] = True
# Personalidad + protocolo de respuesta de Andres caben completos
if "system_prompt" not in doc:
    doc["system_prompt"] = tomlkit.table()
doc["system_prompt"]["soul_max_chars"] = 8000
doc["system_prompt"]["memory_max_chars"] = 4000   # inyectar recuerdos relevantes en cada pregunta
# Memoria automática: extrae hechos duraderos de cada conversación (archivo local en ~/.openjarvis)
if "memory" not in doc:
    doc["memory"] = tomlkit.table()
doc["memory"]["enabled"] = True
doc["memory"]["backend"] = "local"
doc["memory"]["extraction_model"] = ""      # "" = el mismo modelo de Orión
doc["memory"]["max_facts"] = 1000
cfg.write_text(tomlkit.dumps(doc))

print("memoria: automática ACTIVADA (hechos en ~/.openjarvis, MEMORY.md sincronizado con Windows)")
if agente == "simple":
    print("herramientas: DESACTIVADAS (modo conversación rápida)")
else:
    print(f"herramientas: ACTIVADAS ({agente}): {', '.join(herramientas)}")
    print("herramientas: grafo de Obsidian " + ("CONECTADO (C:\\dev_grafo)" if Path("/mnt/c/dev_grafo").is_dir() else "NO encontrado en C:\\dev_grafo"))
    print("herramientas: Google " + ("CONECTADO" if google_ok else "sin conectar (menú opción 9)"))
    print("herramientas: control de Comet " + ("LISTO (Playwright MCP vía npx)" if control_comet
          else "NO disponible: falta Node.js en Windows (instálalo desde nodejs.org)"))
