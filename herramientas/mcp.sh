#!/usr/bin/env bash
# Lanza un servidor MCP de Orión dentro de WSL (lo llama el cerebro desde Windows).
# Uso: bash mcp.sh herramientas/pc_mcp.py
set -e
PY="$HOME/.openjarvis/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" "/mnt/c/Users/PC/Documents/jarvis/$1"
