# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""Chat por texto con Orión (usa el mismo cerebro que la voz)."""
import json
import os
import sys

import httpx

API = os.environ.get("JARVIS_API", "http://127.0.0.1:8787")
historial = []
print("── Orión · chat por texto ── (escribe 'salir' para terminar)\n")
while True:
    try:
        texto = input("Tú: ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not texto:
        continue
    if texto.lower() in ("salir", "exit", "quit"):
        break
    historial.append({"role": "user", "content": texto})
    print("Orión: ", end="", flush=True)
    respuesta = ""
    try:
        with httpx.stream("POST", f"{API}/v1/chat/completions",
                          json={"messages": historial[-20:], "stream": True},
                          timeout=httpx.Timeout(300, connect=10)) as r:
            for linea in r.iter_lines():
                if not linea.startswith("data:") or linea[5:].strip() == "[DONE]":
                    continue
                d = json.loads(linea[5:])["choices"][0]["delta"].get("content")
                if d:
                    respuesta += d
                    print(d, end="", flush=True)
    except KeyboardInterrupt:
        print(" (cortado)")
    except httpx.HTTPError as e:
        print(f"\n[No pude hablar con el cerebro: {e}]")
    print("\n")
    historial.append({"role": "assistant", "content": respuesta})
sys.exit(0)
