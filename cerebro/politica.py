"""Política de permisos de Pedro: decide POR CÓDIGO qué hace solo, qué necesita el
"sí" de Andres y qué nunca hace. El modelo no tiene que recordarlo: si intenta algo
que requiere confirmación, la herramienta se bloquea con un mensaje que le dice que
pregunte; cuando Andres responde "sí", esa acción queda aprobada para el turno.

Sin dependencias: se prueba sola con `python politica.py`.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

LIBRE, CONFIRMAR, PROHIBIDO, SERVIDOR = "libre", "confirmar", "prohibido", "servidor"

# ------------------------------------------------------------------ secretos
_SECRETO_RUTA = re.compile(
    r"(^|[\\/])(\.env(?!\.(example|sample|template))(\.[\w-]+)?|secrets?\.env|google_pedro\.json|"
    r"id_rsa[\w.]*|id_ed25519[\w.]*|[\w-]*\.pem|[\w-]*\.p12|[\w-]*\.pfx|credentials?\.json|"
    r"\.npmrc|\.netrc)$|[\\/]\.ssh([\\/]|$)|[\\/]\.pedro([\\/]|$)", re.I)

# ------------------------------------------------------------------ Bash
_B_PROHIBIDO = re.compile(
    r"rm\s+-[a-z]*r[a-z]*\s+(/|~|\$home|c:|/mnt/c)\s*($|[;&|])|\bformat\s+[a-z]:|\bmkfs|\bdiskpart|"
    r"\bshutdown\b|restart-computer|stop-computer|:\(\)\s*\{|\breg\s+delete|vssadmin|"
    r"git\s+push\b[^;&|]*(--force\b|-f\b|--mirror)|curl[^|;]*\|\s*(sh|bash|iex|powershell)|"
    r"iwr[^|;]*\|\s*iex|invoke-expression|set-executionpolicy|\bnet\s+user\b|"
    r"^\s*(env|printenv|set|get-childitem\s+env:|gci\s+env:|dir\s+env:)\s*$|"
    r"\$\{?[a-z_]*(key|token|secret|password|passwd)[a-z_]*\}?|"
    r"\b(cat|type|less|more|head|tail|get-content|gc|grep|rg|findstr|sed|awk|cp|copy|scp|base64|xxd|od)\b[^;&|]*"
    r"((^|[\s'\"\\/])\.env(?!\.(example|sample|template))\b|secrets?\.env|google_pedro|id_rsa|\.pem\b|\.ssh[\\/]|\.pedro[\\/])",
    re.I)
_B_SERVIDOR = re.compile(
    r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(dev|start|serve|watch|preview)\b|\bnext\s+(dev|start)\b|"
    r"\bvite(\s|$)|\bnodemon\b|\bnest\s+start\b|\btsx\s+watch\b|\bnpx\s+(serve|http-server)\b|"
    r"\bprisma\s+studio\b", re.I)
_B_CONFIRMAR = re.compile(
    r"\bgit\s+(commit|push|pull|merge|rebase|reset|restore|clean|cherry-pick|revert|rm|mv|tag|am|apply|"
    r"checkout\s+(--|-f|\.)|switch\s+-[cC]|stash\s+(drop|clear|pop)|branch\s+-[dDmM])\b|"
    r"(^|[;&|]\s*|\s)(rm|del|erase|rmdir|rd|mv|move|ren|rename|remove-item|ri|move-item|rename-item|unlink|shred|truncate)\s|"
    r"\b(npm|pnpm|yarn|bun)\s+(publish|unpublish|deprecate|uninstall|remove|rm|un)\b|"
    r"\b(npm|pnpm|yarn|bun)\s+(add|install|i)\s+(?!-)[^\s;&|]|"
    r"\b(npm|pnpm)\s+(install|i)\s+-g|\b(winget|choco|scoop)\s+(install|uninstall)|\bpip3?\s+(install|uninstall)|"
    r"\b(vercel|railway|netlify|flyctl|fly|firebase|heroku|wrangler|amplify)\b[^;&|]*\b(deploy|up|--prod|publish|promote|rollback|delete|remove|destroy)\b|"
    r"\bvercel(\s+--prod|\s*$)|\bsupabase\s+(db\s+(push|reset)|migration\s+(repair|squash)|functions\s+deploy|secrets\s+set|projects\s+delete)|"
    r"\bprisma\s+(migrate\s+(deploy|reset|dev|resolve)|db\s+(push|execute|seed))|"
    r"\bdocker\s+(rm|rmi|kill|stop|system\s+prune|volume\s+(rm|prune)|compose\s+down)|"
    r"\b(kill|taskkill|pkill|killall|stop-process)\b|\bsed\s+(-[a-z]*i|--in-place)|\bchmod\b|\bchown\b|"
    r"\bset-content\b|\bout-file\b|\badd-content\b|\bnew-item\b|\bmkdir\b|\bmd\s|\btouch\b|\bcp\s|\bcopy\s|\bcopy-item\b|"
    r"\bgh\s+(pr\s+(create|merge|close)|issue\s+(create|close)|release|repo\s+(create|delete)|api\b)|"
    r"\bcurl\b[^;&|]*\s-(x\s*(post|put|patch|delete)|d\b|-data)|"
    r"(?<![0-9&>])>{1,2}(?!\s*(&|/dev/null|nul\b|\$null))", re.I)
_B_LECTURA = re.compile(
    r"^\s*(git\s+(status|log|diff|show|branch|remote\s+-v|rev-parse|ls-files|blame)|ls|dir|pwd|cat|type|head|tail|"
    r"wc|find|tree|grep|rg|echo|which|where|node\s+(-v|--version)|npm\s+(-v|--version|ls|list|outdated|view)|"
    r"pnpm\s+(-v|ls|list|outdated)|get-childitem|gci|get-content|gc|stat|du|df|date|whoami)\b", re.I)

# ------------------------------------------------------------------ MCP / otras
_LECTURA = {
    "Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "BashOutput", "TodoWrite",
    "listar_proyectos", "listar_archivos", "leer_archivo", "buscar_en_proyecto", "listar_terminales",
    "leer_terminal", "ver_pantalla", "listar_recordatorios", "correos_recientes", "leer_correo", "agenda",
    "estado_tareas", "resultado_tarea", "buscar_notas", "leer_nota", "vecinos", "notas_recientes",
    "browser_snapshot", "browser_take_screenshot", "browser_wait_for", "browser_tabs",
    "browser_console_messages", "browser_network_requests",
}
_LIBRE_MCP = _LECTURA | {
    "abrir_proyecto", "abrir_app", "abrir_claude_code", "abrir_terminal", "crear_recordatorio",
    "borrar_recordatorio", "cancelar_tarea", "crear_nota", "agregar_a_nota", "abrir_en_comet",
    "abrir_comet_pedro", "browser_navigate", "browser_navigate_back", "browser_hover",
    "browser_press_key", "browser_resize", "browser_close",
}
_CONFIRMAR_MCP = {"crear_evento", "lanzar_claude_code"}
_BROWSER_ACCION = {"browser_click", "browser_type", "browser_fill_form", "browser_select_option",
                   "browser_file_upload", "browser_evaluate", "browser_drag", "browser_handle_dialog"}
_WEB_RIESGO = re.compile(
    r"enviar|send|submit|comprar|buy|pagar|pay|checkout|publicar|post\b|publish|eliminar|borrar|delete|"
    r"remove|confirmar|confirm|aceptar|accept|suscrib|subscribe|transfer|ordenar|order|reservar|book|"
    r"registr|sign\s?up|log\s?out|cerrar sesi", re.I)
_AFIRMATIVO = re.compile(
    r"^\s*(s[ií]|sip|sí señor|dale|h[aá]zlo|h[aá]gale|ok|okay|okey|listo|claro|de una|adelante|"
    r"confirmo|confirmado|correcto|va|vale|procede|proceda|hazle|métele|metele|aprobado|autorizo|"
    r"te autorizo|te doy permiso|tienes permiso|por favor|perfecto|exacto|eso)\b", re.I)
_NEGATIVO = re.compile(r"\b(no|nunca|espera|cancela|todav[ií]a no|mejor no)\b|^\s*para\b", re.I)


def _base(nombre: str) -> str:
    return nombre.split("__")[-1]


def es_afirmativo(texto: str) -> bool:
    t = re.sub(r"^\s*(\[voz\]\s*)?(\([^)]*\)\s*)?(\(ahora:[^)]*\)\s*)?", "", texto or "")
    t = re.sub(r"^(pedro|oye pedro)[\s,.:!]*", "", t.strip(), flags=re.I)
    if not _AFIRMATIVO.search(t):
        return False
    return not _NEGATIVO.search(t.split(",")[0]) or bool(re.match(r"\s*s[ií]\b", t, re.I))


def clasificar(herramienta: str, entrada: dict) -> tuple[str, str]:
    """Devuelve (categoría, motivo)."""
    nombre = _base(herramienta)
    entrada = entrada or {}
    if nombre in ("Bash", "PowerShell"):
        cmd = str(entrada.get("command", ""))
        if _B_PROHIBIDO.search(cmd):
            return PROHIBIDO, "comando destructivo o que expone secretos"
        if entrada.get("run_in_background") or _B_SERVIDOR.search(cmd):
            return SERVIDOR, "servidor o proceso largo"
        if _B_CONFIRMAR.search(cmd):
            return CONFIRMAR, f"el comando «{cmd[:120]}» cambia cosas"
        return LIBRE, ""
    if nombre in ("Read", "Grep", "Glob", "LS"):
        ruta = str(entrada.get("file_path") or entrada.get("path") or "")
        if _SECRETO_RUTA.search(ruta.rstrip("\\/")):
            return PROHIBIDO, "archivo de secretos"
        return LIBRE, ""
    if nombre in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        ruta = str(entrada.get("file_path") or entrada.get("notebook_path") or "").replace("/", "\\").lower()
        if _SECRETO_RUTA.search(ruta):
            return PROHIBIDO, "archivo de secretos"
        if ruta.endswith("\\documents\\jarvis\\memory.md"):
            return LIBRE, ""
        return CONFIRMAR, f"editar el archivo {ruta[-80:]}"
    if nombre == "escribir_en_terminal":
        if str(entrada.get("ventana", "")).strip().lower().startswith("pedro-"):
            return LIBRE, ""
        return CONFIRMAR, f"escribir en la terminal «{entrada.get('ventana', '')}», que no abriste tú"
    if nombre in _BROWSER_ACCION:
        if nombre in ("browser_evaluate", "browser_file_upload") or _WEB_RIESGO.search(json.dumps(entrada, ensure_ascii=False)):
            return CONFIRMAR, "una acción en la web que envía, compra, publica o borra"
        return LIBRE, ""
    if nombre in _CONFIRMAR_MCP:
        return CONFIRMAR, {"crear_evento": "crear un evento en el calendario",
                           "lanzar_claude_code": "poner a Claude Code a cambiar código en segundo plano"}[nombre]
    if nombre in _LIBRE_MCP:
        return LIBRE, ""
    if nombre in ("Task", "Agent", "ExitPlanMode", "KillShell", "KillBash"):
        return LIBRE, ""
    return CONFIRMAR, f"usar la herramienta {nombre}"


def es_lectura(herramienta: str, entrada: dict) -> bool:
    nombre = _base(herramienta)
    if nombre in ("Bash", "PowerShell"):
        return bool(_B_LECTURA.match(str((entrada or {}).get("command", ""))))
    return nombre in _LECTURA


def _clave(herramienta: str, entrada: dict) -> str:
    nombre = _base(herramienta)
    if nombre in ("Bash", "PowerShell"):
        return f"{nombre}:{' '.join(str(entrada.get('command', '')).split())[:60]}"
    return nombre


@dataclass
class Politica:
    prueba: bool = False
    pendientes: Counter = field(default_factory=Counter)
    aprobados: Counter = field(default_factory=Counter)
    traza: list = field(default_factory=list)

    def nuevo_turno(self, texto: str, prueba: bool = False) -> None:
        """Un "sí" de Andres aprueba lo que quedó pendiente en el turno anterior."""
        self.aprobados = Counter(self.pendientes) if es_afirmativo(texto) else Counter()
        if self.aprobados:   # aprueba por herramienta (el modelo puede reformular el comando)
            for clave, n in list(self.aprobados.items()):
                self.aprobados[clave.split(":")[0]] += n
        self.pendientes = Counter()
        self.prueba = prueba
        self.traza = []

    def decidir(self, herramienta: str, entrada: dict) -> tuple[bool, str]:
        """(permitido, mensaje para el modelo si se bloquea)."""
        cat, motivo = clasificar(herramienta, entrada)
        nombre = _base(herramienta)
        resumen = json.dumps(entrada, ensure_ascii=False)[:200]
        if cat == PROHIBIDO:
            self.traza.append({"herramienta": nombre, "entrada": resumen, "decision": "prohibido"})
            return False, (f"BLOQUEADO por seguridad ({motivo}). No lo intentes de otra forma. "
                           "Dile a Andres en una frase que eso no lo haces y, si aplica, que lo haga él.")
        if cat == SERVIDOR:
            self.traza.append({"herramienta": nombre, "entrada": resumen, "decision": "servidor"})
            return False, ("No corras servidores ni procesos largos con Bash: se quedan colgados. Usa "
                           "abrir_terminal(proyecto, comando) para abrirlo en una ventana que Andres ve.")
        if cat == CONFIRMAR:
            clave = _clave(herramienta, entrada)
            if self.aprobados[clave] > 0 or self.aprobados[nombre] > 0:
                self.aprobados[clave] -= 1
                self.aprobados[nombre] -= 1
            else:
                self.pendientes[clave] += 1
                self.traza.append({"herramienta": nombre, "entrada": resumen, "decision": "pedir_si"})
                return False, (f"NECESITA EL SÍ DE ANDRES: vas a {motivo}. No lo ejecutes todavía. "
                               "Pregúntale en UNA frase qué vas a hacer exactamente y termina tu respuesta "
                               "ahí. Cuando él diga que sí, vuelve a ejecutarlo igual.")
        if self.prueba and not es_lectura(herramienta, entrada):
            self.traza.append({"herramienta": nombre, "entrada": resumen, "decision": "simulado"})
            return False, ("[MODO PRUEBA] Esta acción no se ejecutó de verdad, pero cuenta como hecha con "
                           "éxito: responde a Andres exactamente como si hubiera funcionado. No menciones la prueba.")
        self.traza.append({"herramienta": nombre, "entrada": resumen, "decision": "permitido"})
        return True, ""


# ------------------------------------------------------------------ autoprueba
if __name__ == "__main__":
    casos = [
        ("Bash", {"command": "git status"}, LIBRE),
        ("Bash", {"command": "git log --oneline -5"}, LIBRE),
        ("Bash", {"command": "ls -la /c/dev/prueba2"}, LIBRE),
        ("Bash", {"command": "cat package.json"}, LIBRE),
        ("Bash", {"command": "cat .env"}, PROHIBIDO),
        ("Bash", {"command": "cat .env.example"}, LIBRE),
        ("Bash", {"command": "grep -r KEY .env.local"}, PROHIBIDO),
        ("Bash", {"command": "echo $ANTHROPIC_API_KEY"}, PROHIBIDO),
        ("Bash", {"command": "env"}, PROHIBIDO),
        ("Bash", {"command": "npm run dev"}, SERVIDOR),
        ("Bash", {"command": "cd /c/dev/x && pnpm dev"}, SERVIDOR),
        ("Bash", {"command": "npm run build"}, LIBRE),
        ("Bash", {"command": "npm install"}, LIBRE),
        ("Bash", {"command": "npm install zod"}, CONFIRMAR),
        ("Bash", {"command": "npm test", "run_in_background": True}, SERVIDOR),
        ("Bash", {"command": "git commit -am 'x'"}, CONFIRMAR),
        ("Bash", {"command": "git push"}, CONFIRMAR),
        ("Bash", {"command": "git push --force"}, PROHIBIDO),
        ("Bash", {"command": "rm -rf node_modules"}, CONFIRMAR),
        ("Bash", {"command": "rm -rf /"}, PROHIBIDO),
        ("Bash", {"command": "vercel --prod"}, CONFIRMAR),
        ("Bash", {"command": "vercel deploy"}, CONFIRMAR),
        ("Bash", {"command": "echo hola > a.txt"}, CONFIRMAR),
        ("Bash", {"command": "npm run lint 2>/dev/null"}, LIBRE),
        ("Bash", {"command": "node -v 2>&1"}, LIBRE),
        ("Bash", {"command": "npx prisma migrate dev"}, CONFIRMAR),
        ("Read", {"file_path": "C:\\dev\\x\\.env"}, PROHIBIDO),
        ("Read", {"file_path": "C:\\dev\\x\\.env.example"}, LIBRE),
        ("Read", {"file_path": "C:\\dev\\x\\src\\env.ts"}, LIBRE),
        ("Read", {"file_path": "C:\\Users\\PC\\.pedro\\secrets.env"}, PROHIBIDO),
        ("Edit", {"file_path": "C:\\Users\\PC\\Documents\\jarvis\\MEMORY.md"}, LIBRE),
        ("Edit", {"file_path": "C:\\dev\\x\\src\\app.tsx"}, CONFIRMAR),
        ("mcp__pc__escribir_en_terminal", {"ventana": "Pedro-prueba2", "texto": "x"}, LIBRE),
        ("mcp__pc__escribir_en_terminal", {"ventana": "PowerShell", "texto": "x"}, CONFIRMAR),
        ("mcp__pc__abrir_terminal", {"proyecto": "x", "comando": "npm run dev"}, LIBRE),
        ("mcp__google__crear_evento", {}, CONFIRMAR),
        ("mcp__comet-control__browser_click", {"element": "Botón Enviar"}, CONFIRMAR),
        ("mcp__comet-control__browser_click", {"element": "enlace Servicios"}, LIBRE),
        ("mcp__grafo__crear_nota", {"titulo": "x"}, LIBRE),
        ("mcp__otro__algo_raro", {}, CONFIRMAR),
    ]
    mal = 0
    for h, e, esperado in casos:
        cat, _ = clasificar(h, e)
        if cat != esperado:
            mal += 1
            print(f"FALLA {h} {e} -> {cat} (esperado {esperado})")
    for t, esp in [("[voz] sí, dale", True), ("[voz] Pedro, hazlo", True), ("[voz] no, espera", False),
                   ("[voz] sí", True), ("[voz] te doy permiso para que hagas eso", True),
                   ("[voz] ¿qué hora es?", False), ("[voz] (te interrumpí mientras hablabas) dale", True),
                   ("[voz] no sé", False)]:
        if es_afirmativo(t) != esp:
            mal += 1
            print(f"FALLA afirmativo {t!r}")
    p = Politica()
    p.nuevo_turno("[voz] haz commit")
    assert p.decidir("Bash", {"command": "git commit -m a"})[0] is False
    p.nuevo_turno("[voz] sí, dale")
    assert p.decidir("Bash", {"command": "git commit -m 'a b'"})[0] is True
    assert p.decidir("Bash", {"command": "git commit -m c"})[0] is False   # solo una vez
    p.nuevo_turno("[voz] otra cosa")
    assert p.decidir("Bash", {"command": "git commit -m c"})[0] is False   # sin sí, sigue bloqueado
    p.nuevo_turno("[voz] ok", prueba=True)
    assert p.decidir("Read", {"file_path": "a.ts"})[0] is True
    assert p.decidir("mcp__pc__abrir_terminal", {"proyecto": "x"})[0] is False   # simulado
    print("politica: OK" if not mal else f"politica: {mal} fallas")
