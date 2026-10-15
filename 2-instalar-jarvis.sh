#!/usr/bin/env bash
# Jarvis - Parte 2: instalar y configurar OpenJarvis dentro de WSL2 (Ubuntu)
# Uso (desde la carpeta donde estan SOUL.md, USER.md y MEMORY.md):
#   bash 2-instalar-jarvis.sh
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OJ_HOME="${OPENJARVIS_HOME:-$HOME/.openjarvis}"
SECRETS="$OJ_HOME/secrets.env"
MODELO_PRINCIPAL="claude-sonnet-5-5"
MODELO_LOCAL="qwen3.5:9b"

paso() { printf "\n\033[36m==> %s\033[0m\n" "$1"; }
ok()   { printf "    \033[32mOK\033[0m  %s\n" "$1"; }
ojo()  { printf "    \033[33m!!\033[0m  %s\n" "$1"; }
fallo(){ printf "\n\033[31mERROR:\033[0m %s\n" "$1"; exit 1; }

# ---------------------------------------------------------------- 0. Chequeos
paso "Revisando requisitos"
for f in SOUL.md USER.md MEMORY.md; do
  [[ -f "$AQUI/$f" ]] || fallo "No encuentro $f junto a este script. Ponlos en la misma carpeta."
done
ok "Archivos de persona encontrados"

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  ok "GPU visible desde WSL: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
else
  ojo "WSL no ve la GPU. Jarvis funcionara, pero el modelo local ira por CPU (lento)."
  ojo "Revisa que el driver NVIDIA de Windows este actualizado y corre 'wsl --update' en PowerShell."
  read -rp "    ¿Continuar igual? [s/N] " r; [[ "$r" =~ ^[sS]$ ]] || exit 1
fi

sudo apt-get update -qq && sudo apt-get install -y -qq curl git python3 zstd >/dev/null
ok "Paquetes base listos"

# ---------------------------------------------------------------- 1. OpenJarvis
paso "Instalando OpenJarvis (instalador oficial, sin analytics) — tarda unos minutos"
# El instalador oficial recuerda qué pasos ya completó, así que re-ejecutarlo
# solo retoma lo que haya quedado pendiente (p. ej. si falló instalando Ollama).
curl -fsSL https://open-jarvis.github.io/OpenJarvis/install.sh | bash -s -- --no-analytics
export PATH="$HOME/.local/bin:$PATH"
PY="$OJ_HOME/.venv/bin/python"
[[ -x "$PY" ]] || fallo "El instalador no dejo el entorno en $OJ_HOME/.venv. Revisa los mensajes de arriba."
ok "OpenJarvis instalado en $OJ_HOME"

# ---------------------------------------------------------------- 2. Persona
paso "Copiando la persona de Jarvis"
mkdir -p "$OJ_HOME"
for f in SOUL.md USER.md MEMORY.md; do
  [[ -f "$OJ_HOME/$f" ]] && cp "$OJ_HOME/$f" "$OJ_HOME/$f.bak"
  cp "$AQUI/$f" "$OJ_HOME/$f"
done
ok "SOUL.md, USER.md y MEMORY.md instalados (respaldos .bak si existian)"

# ---------------------------------------------------------------- 3. Claves
paso "Claves (se guardan solo en tu PC, en $SECRETS con permisos 600)"
touch "$SECRETS"; chmod 600 "$SECRETS"
# shellcheck disable=SC1090
source "$SECRETS"

guardar() {   # guardar NOMBRE VALOR  -> reemplaza la línea si ya existía
  sed -i "/^export $1=/d" "$SECRETS"
  printf "export %s='%s'\n" "$1" "$2" >> "$SECRETS"
  chmod 600 "$SECRETS"
  export "$1=$2"
}
borrar() { sed -i "/^export $1=/d" "$SECRETS"; unset "$1"; }
limpiar() { printf '%s' "$1" | tr -d '[:space:][:cntrl:]'; }

anthropic_ok() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' https://api.anthropic.com/v1/models \
          -H "x-api-key: $1" -H "anthropic-version: 2023-06-01")" || true
  [[ "$code" == "200" ]]
}
telegram_ok() { curl -fsS "https://api.telegram.org/bot$1/getMe" >/dev/null 2>&1; }

PEGAR="(en esta terminal se pega con CLIC DERECHO o Ctrl+Shift+V; Ctrl+V NO funciona)"

# --- Anthropic (obligatoria) ---
if [[ -n "${ANTHROPIC_API_KEY:-}" ]] && anthropic_ok "$ANTHROPIC_API_KEY"; then
  ok "API key de Anthropic válida"
else
  [[ -n "${ANTHROPIC_API_KEY:-}" ]] && { ojo "La API key guardada no es válida; la borro."; borrar ANTHROPIC_API_KEY; }
  echo "    Crea tu API key en https://console.anthropic.com/settings/keys"
  echo "    $PEGAR"
  while true; do
    read -rsp "    Pega tu ANTHROPIC_API_KEY (no se verá al escribir): " k; echo
    k="$(limpiar "$k")"
    if [[ "$k" != sk-ant-* ]]; then
      ojo "Eso no parece una API key (debe empezar por sk-ant-). Intenta de nuevo."
    elif anthropic_ok "$k"; then
      guardar ANTHROPIC_API_KEY "$k"; ok "API key de Anthropic verificada y guardada"; break
    else
      ojo "Anthropic rechazó la key (o no hay saldo en la cuenta). Revísala e intenta de nuevo."
    fi
  done
fi

# --- Telegram (opcional) ---
USAR_TELEGRAM=0
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] && telegram_ok "$TELEGRAM_BOT_TOKEN"; then
  USAR_TELEGRAM=1; ok "Token de Telegram válido"
else
  [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] && { ojo "El token de Telegram guardado no es válido; lo borro."; borrar TELEGRAM_BOT_TOKEN; borrar TELEGRAM_CHAT_ID; }
  read -rp "    ¿Quieres conectar Jarvis a Telegram ahora? (puedes hacerlo después) [s/N] " r
  if [[ "$r" =~ ^[sS]$ ]]; then
    cat <<'EOF'
    Crea tu bot de Telegram (2 minutos):
      1. En Telegram abre @BotFather y envía /newbot
      2. Nombre: Jarvis   ·   usuario: algo que termine en "bot" (ej. andres_jarvis_bot)
      3. Copia el token que te da (se ve como 123456789:ABC...)
EOF
    echo "    $PEGAR"
    while true; do
      read -rsp "    Pega el token del bot (Enter vacío para omitir): " t; echo
      t="$(limpiar "$t")"
      [[ -z "$t" ]] && { ojo "Telegram omitido."; break; }
      if telegram_ok "$t"; then
        guardar TELEGRAM_BOT_TOKEN "$t"; USAR_TELEGRAM=1; ok "Token de Telegram verificado y guardado"; break
      else
        ojo "Telegram rechazó ese token. Cópialo completo de @BotFather e intenta de nuevo."
      fi
    done
  else
    ok "Sin Telegram por ahora: usarás Jarvis desde la terminal"
  fi
fi

grep -q "openjarvis/secrets.env" "$HOME/.bashrc" 2>/dev/null || \
  echo '[ -f "$HOME/.openjarvis/secrets.env" ] && source "$HOME/.openjarvis/secrets.env"' >> "$HOME/.bashrc"

# ---------------------------------------------------------------- 4. Chat ID (solo tú le hablas)
CHAT_ID=""
if [[ "$USAR_TELEGRAM" == "1" ]]; then
  paso "Enlazando el bot SOLO a tu chat"
  CHAT_ID="${TELEGRAM_CHAT_ID:-}"
  if [[ -z "$CHAT_ID" ]]; then
    read -rp "    Abre tu bot en Telegram, envíale /start y luego presiona Enter aquí... " _
    for _intento in 1 2 3 4 5; do
      CHAT_ID="$(curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
ids = [u["message"]["chat"]["id"] for u in d.get("result", []) if "message" in u]
print(ids[-1] if ids else "")
' 2>/dev/null)" || true
      [[ -n "$CHAT_ID" ]] && break
      sleep 3
    done
    if [[ -n "$CHAT_ID" ]]; then
      guardar TELEGRAM_CHAT_ID "$CHAT_ID"
    else
      ojo "No vi tu /start. Sigo sin Telegram; vuelve a correr el script cuando quieras conectarlo."
      USAR_TELEGRAM=0
    fi
  fi
  [[ -n "$CHAT_ID" ]] && ok "Tu chat ID es $CHAT_ID — nadie más podrá darle órdenes a Jarvis"
fi

# ---------------------------------------------------------------- 5. Modelo local
paso "Descargando el modelo local $MODELO_LOCAL (~6 GB)"
if command -v ollama >/dev/null 2>&1; then
  ollama pull "$MODELO_LOCAL" && ok "Modelo local listo" || ojo "No pude bajar $MODELO_LOCAL; sigue funcionando con Claude."
else
  ojo "Ollama no quedó instalado; Jarvis funcionará solo con Claude por ahora."
fi

# ---------------------------------------------------------------- 6. config.toml
paso "Configurando Jarvis (config.toml)"
CFG="$OJ_HOME/config.toml"
[[ -f "$CFG" ]] && cp "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)"
CFG="$CFG" CHAT_ID="$CHAT_ID" M1="$MODELO_PRINCIPAL" M2="$MODELO_LOCAL" "$PY" - <<'PYEOF'
import os
from pathlib import Path
import tomlkit

path = Path(os.environ["CFG"])
doc = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()

def table(*keys):
    t = doc
    for k in keys:
        if k not in t:
            t[k] = tomlkit.table()
        t = t[k]
    return t

intel = table("intelligence")
intel["default_model"] = os.environ["M1"]
intel["fallback_model"] = os.environ["M2"]
intel["provider"] = "cloud"

eng = table("engine")
eng["default"] = "cloud"
table("engine", "ollama")["host"] = "http://localhost:11434"

table("learning", "routing")["policy"] = "heuristic"
table("traces")["enabled"] = True

srv = table("server")
srv["host"] = "127.0.0.1"
srv["port"] = 8000

ch = table("channel")
ch["enabled"] = True
ch["default_channel"] = "telegram"
tg = table("channel", "telegram")
tg["allowed_chat_ids"] = os.environ["CHAT_ID"]

path.write_text(tomlkit.dumps(doc))
print("    config.toml actualizado")
PYEOF
ok "Cerebro: $MODELO_PRINCIPAL · respaldo local: $MODELO_LOCAL · canal: Telegram"

# ---------------------------------------------------------------- 7. Prueba
paso "Probando"
jarvis doctor || ojo "jarvis doctor reportó avisos (revísalos arriba)."

echo
echo "    Pregunta de prueba a Jarvis:"
jarvis ask "Preséntate en una frase y dime cuál es mi prioridad número uno." || \
  ojo "La prueba falló. Revisa la API key y el modelo en $OJ_HOME/config.toml"

curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="$CHAT_ID" -d text="Jarvis en línea. Instalación completa ✅" >/dev/null \
  && ok "Te envié un mensaje de prueba a Telegram"

cat <<EOF

$(printf '\033[32m')Listo.$(printf '\033[0m')
  · Para hablarle por Telegram, deja corriendo:   jarvis serve
  · Para hablarle en la terminal:                 jarvis
  · Tus claves están en $SECRETS (no compartas ese archivo)
EOF
