#!/usr/bin/env bash
# Arranca el cerebro de Jarvis (lo llama Jarvis.bat).
# El registro va a ~/.openjarvis/cerebro.log (Windows puede bloquear escribir en Documentos)
# y, si se puede, también se copia a la carpeta de Jarvis en Windows.
PORT="${1:-8787}"
AGENTE="${2:-simple}"
LOG="$HOME/.openjarvis/cerebro.log"
WINLOG=/mnt/c/Users/PC/Documents/jarvis/cerebro.log
mkdir -p "$HOME/.openjarvis"
touch "$WINLOG" 2>/dev/null || WINLOG=/dev/null
exec > >(tee -a "$LOG" "$WINLOG" 2>/dev/null) 2>&1
echo ""
echo "===== $(date '+%Y-%m-%d %H:%M:%S') arrancando en puerto $PORT ====="
# La persona se edita en Documentos\jarvis y se copia aquí en cada arranque
W=/mnt/c/Users/PC/Documents/jarvis
for f in SOUL.md USER.md; do
  [ -f "$W/$f" ] && cp "$W/$f" "$HOME/.openjarvis/$f" && echo "persona: $f actualizado"
done
# MEMORY.md: gana el más reciente (Pedro escribe aquí con "recuerda que…"; tú lo editas en Windows)
if [ -f "$W/MEMORY.md" ] && [ -f "$HOME/.openjarvis/MEMORY.md" ]; then
  if [ "$HOME/.openjarvis/MEMORY.md" -nt "$W/MEMORY.md" ]; then
    cp "$HOME/.openjarvis/MEMORY.md" "$W/MEMORY.md" && echo "memoria: MEMORY.md de Pedro -> Windows"
  else
    cp "$W/MEMORY.md" "$HOME/.openjarvis/MEMORY.md" && echo "memoria: MEMORY.md de Windows -> Pedro"
  fi
elif [ -f "$W/MEMORY.md" ]; then
  cp "$W/MEMORY.md" "$HOME/.openjarvis/MEMORY.md"
fi
[ -f "$HOME/.openjarvis/secrets.env" ] && source "$HOME/.openjarvis/secrets.env"
export PATH="$HOME/.local/bin:$PATH"
"$HOME/.openjarvis/.venv/bin/python" /mnt/c/Users/PC/Documents/jarvis/parches.py
"$HOME/.openjarvis/.venv/bin/python" /mnt/c/Users/PC/Documents/jarvis/herramientas.py "$AGENTE"
echo "jarvis: $(command -v jarvis || echo NO ENCONTRADO)"
echo "key: ${#ANTHROPIC_API_KEY} caracteres - base: ${ANTHROPIC_BASE_URL:-oficial}"
echo "registro de Windows: $WINLOG"
date > /mnt/c/Users/PC/Documents/jarvis/cerebro.iniciado 2>/dev/null
exec jarvis serve --port "$PORT" --agent "$AGENTE"
