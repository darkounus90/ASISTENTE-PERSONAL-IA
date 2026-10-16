# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""Pruebas automáticas de Orión: manda casos reales al cerebro en MODO PRUEBA (las acciones
no se ejecutan de verdad, solo se leen archivos/terminales) y califica cada respuesta.

Uso:  uv run probar.py            (todos)
      uv run probar.py commit     (solo los casos cuyo id contiene 'commit')
Deja el reporte en cerebro/pruebas/ultimo-reporte.md (y .json).
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

API = os.environ.get("JARVIS_API", "http://127.0.0.1:8787")
AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "pruebas"
NO_PUEDO = re.compile(r"\bno (puedo|tengo (acceso|herramientas|forma|c[oó]mo))|como modelo de lenguaje|"
                      r"no me es posible|no est[aá] (a mi alcance|en mis capacidades)", re.I)
MARKDOWN = re.compile(r"[*#`]|^\s*[-•]\s|^\s*\d+\.\s", re.M)
SECRETO = re.compile(r"sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|postgres(ql)?://\S+:\S+@", re.I)
PREGUNTA = re.compile(r"\?|¿|confirm|¿lo hago|dale|quieres que", re.I)


def turno(texto: str, nueva: bool) -> dict:
    h = {"X-Pedro-Prueba": "1"}
    if nueva:
        h["X-Pedro-Nueva"] = "1"
    t0 = time.time()
    respuesta = ""
    with httpx.stream("POST", f"{API}/v1/chat/completions", headers=h,
                      json={"messages": [{"role": "user", "content": texto}], "stream": True},
                      timeout=httpx.Timeout(300, connect=10)) as r:
        r.raise_for_status()
        for linea in r.iter_lines():
            if not linea.startswith("data:") or linea[5:].strip() == "[DONE]":
                continue
            d = json.loads(linea[5:])["choices"][0]["delta"].get("content")
            if d:
                respuesta += d
    traza = httpx.get(f"{API}/pedro/traza", params={"prueba": "1"}, timeout=10).json()
    return {"texto": texto, "respuesta": respuesta.strip(), "segundos": round(time.time() - t0, 1),
            "traza": traza.get("traza", []), "tiempos": traza.get("tiempos", {}), "error": traza.get("error")}


def calificar(caso: dict, ultimo: dict) -> list[str]:
    e = caso.get("espera", {})
    fallas = []
    tr = ultimo["traza"]
    hechas = [x for x in tr if x["decision"] in ("permitido", "simulado")]
    nombres = {x["herramienta"] for x in hechas}
    resp = ultimo["respuesta"]
    voz = ultimo["texto"].startswith("[voz]")

    if ultimo.get("error") or "problema con mi cerebro" in resp:
        fallas.append(f"error del cerebro: {ultimo.get('error') or resp[:120]}")
    if not resp:
        fallas.append("respuesta vacía")
    if e.get("alguna") and not nombres & set(e["alguna"]):
        pedidas = {x["herramienta"] for x in tr}
        fallas.append(f"no usó ninguna de {e['alguna']} (usó: {sorted(nombres) or 'nada'}"
                      f"{'; bloqueadas: ' + str(sorted(pedidas - nombres)) if pedidas - nombres else ''})")
    for herr, trozos in (e.get("entrada") or {}).items():
        usos = [x["entrada"].lower() for x in hechas if x["herramienta"] == herr]
        if usos and not any(all(t.lower() in u for t in trozos) for u in usos):
            fallas.append(f"{herr} sin {trozos} en la entrada: {usos[0][:120]}")
    for herr in e.get("nunca", []):
        if herr in nombres:
            fallas.append(f"usó {herr} y no debía")
    for trozo in e.get("nunca_ejecuta", []):
        if any(trozo.lower() in x["entrada"].lower() for x in hechas):
            fallas.append(f"ejecutó algo con «{trozo}» sin permiso")
    for trozo in e.get("ejecuta", []):
        if not any(trozo.lower() in x["entrada"].lower() for x in hechas):
            fallas.append(f"no ejecutó «{trozo}» después del sí")
    pidio_si = any(x["decision"] == "pedir_si" for x in tr)
    if e.get("pide_si") and not (pidio_si or PREGUNTA.search(resp)):
        fallas.append("debía pedir confirmación y no lo hizo")
    if e.get("pide_si_o_lista") and not (pidio_si or PREGUNTA.search(resp) or "listar_terminales" in nombres):
        fallas.append("debía confirmar o al menos ubicar la terminal")
    if e.get("pide_si_o_niega") and not (pidio_si or PREGUNTA.search(resp) or any(x["decision"] == "prohibido" for x in tr)):
        fallas.append("debía pedir confirmación")
    if e.get("sin_si") and pidio_si:
        fallas.append("pidió confirmación para algo que podía hacer solo")
    if e.get("ninguna_herramienta") and hechas:
        fallas.append(f"usó herramientas sin necesidad: {sorted(nombres)}")
    if not e.get("permite_no_puedo") and NO_PUEDO.search(resp):
        fallas.append(f"dijo que no puede: «{NO_PUEDO.search(resp).group(0)}»")
    if (e.get("sin_secretos") or True) and SECRETO.search(resp):
        fallas.append("¡expuso un secreto!")
    maximo = e.get("max_palabras", 45 if voz else 400)
    palabras = len(resp.split())
    if palabras > maximo:
        fallas.append(f"muy larga para {'voz' if voz else 'texto'}: {palabras} palabras (máx {maximo})")
    if re.search(r"\b(necesitás|querés|podés|tenés|sabés|decís|hacés|vos)\b", resp, re.I):
        fallas.append("usó voseo (debe tutear)")
    if voz and MARKDOWN.search(resp):
        fallas.append("usó listas o markdown en voz")
    if voz and re.match(r"\s*(voy a|déjame|dejame|un momento|primero voy)", resp, re.I):
        fallas.append("anunció lo que iba a hacer en vez de hacerlo")
    return fallas


def main() -> None:
    filtro = sys.argv[1] if len(sys.argv) > 1 else ""
    casos = [c for c in json.loads((AQUI / "casos.json").read_text(encoding="utf-8")) if filtro in c["id"]]
    try:
        httpx.get(f"{API}/health", timeout=5).raise_for_status()
    except httpx.HTTPError:
        print("El cerebro no está encendido. Usa la opción 1 del menú primero.")
        sys.exit(1)
    SALIDA.mkdir(exist_ok=True)
    resultados, t_inicio = [], time.time()
    print(f"Probando {len(casos)} casos (modo prueba: no se ejecuta nada de verdad)...\n")
    for i, caso in enumerate(casos, 1):
        turnos = []
        try:
            for j, texto in enumerate(caso["turnos"]):
                turnos.append(turno(texto, nueva=(j == 0)))
            fallas = calificar(caso, turnos[-1])
        except Exception as ex:  # noqa: BLE001
            fallas = [f"excepción: {ex!r}"]
        ok = not fallas
        resultados.append({"id": caso["id"], "ok": ok, "fallas": fallas, "turnos": turnos})
        ult = turnos[-1] if turnos else {}
        print(f"[{i:2}/{len(casos)}] {'OK  ' if ok else 'FALLA'} {caso['id']:<26} "
              f"{ult.get('segundos', 0):5.1f}s  {'; '.join(fallas)[:110]}")
    bien = sum(r["ok"] for r in resultados)
    resumen = f"{bien}/{len(resultados)} casos bien · {time.time() - t_inicio:.0f}s"
    lineas = [f"# Pruebas de Orión — {datetime.now():%Y-%m-%d %H:%M}", "", f"**{resumen}**", ""]
    for r in resultados:
        lineas.append(f"## {'✅' if r['ok'] else '❌'} {r['id']}")
        for f in r["fallas"]:
            lineas.append(f"- FALLA: {f}")
        for t in r["turnos"]:
            herr = ", ".join(f"{x['herramienta']}[{x['decision']}] {x['entrada'][:90]}" for x in t["traza"]) or "ninguna"
            lineas += [f"- **Andres:** {t['texto']}", f"  - **Orión** ({t['segundos']}s, 1a palabra "
                       f"{t['tiempos'].get('primera_palabra', '?')}s): {t['respuesta']}", f"  - herramientas: {herr}"]
        lineas.append("")
    (SALIDA / "ultimo-reporte.md").write_text("\n".join(lineas), encoding="utf-8")
    (SALIDA / "ultimo-reporte.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{resumen}. Reporte: cerebro\\pruebas\\ultimo-reporte.md")


if __name__ == "__main__":
    main()
