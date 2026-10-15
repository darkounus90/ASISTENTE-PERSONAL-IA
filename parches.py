"""Parches locales para OpenJarvis (los aplica cerebro.sh en cada arranque).

1. Agrega los modelos Claude 5.5 a la lista fija del motor cloud.
2. Hace que el streaming de Anthropic respete ANTHROPIC_BASE_URL (para usar
   un gateway como nyxos) y no envíe temperature a modelos de muestreo fijo.
Es idempotente: si ya están aplicados, no hace nada.
"""
from pathlib import Path

SRC = Path.home() / ".openjarvis" / "src" / "src" / "openjarvis"


def parche_modelos() -> str:
    f = SRC / "engine" / "cloud.py"
    s = f.read_text()
    if '"claude-sonnet-5-5"' in s:
        return "modelos 5.5: ya estaba"
    s = s.replace('_ANTHROPIC_MODELS = [\n',
                  '_ANTHROPIC_MODELS = [\n    "claude-sonnet-5-5",\n    "claude-opus-5-5",\n    "claude-haiku-5-5",\n', 1)
    f.write_text(s)
    return "modelos 5.5: aplicado"


def parche_streaming() -> str:
    f = SRC / "server" / "cloud_router.py"
    s = f.read_text()
    if "_base}/v1/messages" in s:
        return "streaming por gateway: ya estaba"
    viejo = '''        "temperature": temperature,
        "stream": True,
    }
    if system_text:
        payload["system"] = system_text

    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",'''
    nuevo = '''        "temperature": temperature,
        "stream": True,
    }
    # Modelos con muestreo fijo (Claude 5.x) no aceptan temperature.
    if model.startswith(("claude-opus-4-7", "claude-opus-4-8", "claude-opus-5",
                         "claude-sonnet-5", "claude-fable-5", "claude-haiku-5")):
        payload.pop("temperature", None)
    if system_text:
        payload["system"] = system_text

    import os as _os

    _base = (_os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    if _base.endswith("/v1"):
        _base = _base[:-3]
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            f"{_base}/v1/messages",'''
    if viejo not in s:
        return "streaming por gateway: NO encontré el código esperado (¿cambió OpenJarvis?)"
    f.write_text(s.replace(viejo, nuevo, 1))
    return "streaming por gateway: aplicado"


def parche_prioridad_mcp() -> str:
    """Si una herramienta MCP (ej. Playwright sobre Comet) se llama igual que una
    interna (browser_navigate...), usar la MCP en vez de la interna."""
    f = SRC / "cli" / "serve.py"
    s = f.read_text()
    if "_mcp_names = " in s:
        return "prioridad MCP: ya estaba"
    viejo = """                    tools = []
                    for name in ToolRegistry.keys():
                        if name not in allowed:
                            continue"""
    nuevo = """                    tools = []
                    _mcp_names = {t.spec.name for t in managed_mcp_tools}
                    for name in ToolRegistry.keys():
                        if name not in allowed or name in _mcp_names:
                            continue"""
    if viejo not in s:
        return "prioridad MCP: NO encontré el código esperado (¿cambió OpenJarvis?)"
    f.write_text(s.replace(viejo, nuevo, 1))
    return "prioridad MCP: aplicado"


if __name__ == "__main__":
    for fn in (parche_modelos, parche_streaming, parche_prioridad_mcp):
        try:
            print("parche:", fn())
        except Exception as e:  # noqa: BLE001
            print("parche: ERROR", fn.__name__, e)
