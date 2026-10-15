"""Conecta Pedro con tu Gmail y Google Calendar (se ejecuta UNA vez, en la terminal de WSL).

Uso:  python3 google_auth_pedro.py

Pide el Client ID / Client secret de tu cliente OAuth "App de escritorio" (o la ruta del JSON
que descargaste de Google Cloud Console), abre Google en tu navegador de Windows, y guarda el
token en ~/.openjarvis/google_pedro.json (permisos 600).

Variables opcionales (sobre todo para pruebas):
  PEDRO_GOOGLE_TOKEN       ruta del archivo de token
  PEDRO_OAUTH_PORT         puerto local (8789)
  PEDRO_OAUTH_BIND         dirección donde escuchar (127.0.0.1; usa 0.0.0.0 si Windows no llega)
  PEDRO_OAUTH_TIMEOUT      segundos de espera (300)
  PEDRO_GOOGLE_AUTH_URL / PEDRO_GOOGLE_TOKEN_URL / PEDRO_GOOGLE_API_BASE   endpoints alternativos
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTH_URL = os.environ.get("PEDRO_GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
TOKEN_URL = os.environ.get("PEDRO_GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")
API_BASE = os.environ.get("PEDRO_GOOGLE_API_BASE", "").rstrip("/") or "https://gmail.googleapis.com"
PUERTO = int(os.environ.get("PEDRO_OAUTH_PORT", "8789"))
BIND = os.environ.get("PEDRO_OAUTH_BIND", "127.0.0.1")
ESPERA = int(os.environ.get("PEDRO_OAUTH_TIMEOUT", "300"))
REDIRECT_URI = f"http://localhost:{PUERTO}/callback"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/calendar.events"]
RUTA_TOKEN = os.path.expanduser(os.environ.get("PEDRO_GOOGLE_TOKEN", "~/.openjarvis/google_pedro.json"))


class Fallo(Exception):
    """Error con mensaje en español para el usuario."""


# --------------------------------------------------------------------------- credenciales

def ruta_windows_a_wsl(ruta: str) -> str:
    """C:\\Users\\PC\\Downloads\\x.json -> /mnt/c/Users/PC/Downloads/x.json (otras rutas, igual)."""
    ruta = ruta.strip().strip('"').strip("'").strip()
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", ruta)
    if m:
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
    return os.path.expanduser(ruta)


def leer_json_cliente(ruta: str) -> tuple[str, str]:
    ruta_linux = ruta_windows_a_wsl(ruta)
    try:
        with open(ruta_linux, encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError:
        raise Fallo(f"No encontré el archivo: {ruta_linux}")
    except (OSError, ValueError) as e:
        raise Fallo(f"No pude leer el JSON {ruta_linux}: {e}")
    bloque = datos.get("installed") or datos.get("web") or datos
    if "web" in datos and "installed" not in datos:
        print("Aviso: ese JSON es de tipo 'Aplicación web'. Lo correcto es 'App de escritorio'; "
              "puede fallar con redirect_uri_mismatch.")
    cid, secreto = bloque.get("client_id", ""), bloque.get("client_secret", "")
    if not cid or not secreto:
        raise Fallo("El JSON no tiene client_id y client_secret. Descarga el JSON del cliente "
                    "OAuth (tipo 'App de escritorio') en Google Cloud Console > Credenciales.")
    return cid, secreto


def pedir_credenciales() -> tuple[str, str]:
    print("Necesito el cliente OAuth 'App de escritorio' que creaste en Google Cloud Console.")
    entrada = input("Pega el Client ID, o la ruta del JSON descargado (client_secret_....json):\n> ").strip()
    if not entrada:
        raise Fallo("No escribiste nada.")
    limpio = entrada.strip('"').strip("'")
    if limpio.lower().endswith(".json") or "\\" in limpio or limpio.startswith(("/", "~")):
        cid, secreto = leer_json_cliente(limpio)
        print(f"Leí el cliente {cid[:24]}… del JSON.")
        return cid, secreto
    if not limpio.endswith(".apps.googleusercontent.com"):
        print("Aviso: el Client ID normalmente termina en .apps.googleusercontent.com")
    secreto = input("Pega el Client secret:\n> ").strip().strip('"').strip("'")
    if not secreto:
        raise Fallo("Falta el Client secret.")
    return limpio, secreto


# --------------------------------------------------------------------------- PKCE / URL

def nuevo_pkce() -> tuple[str, str]:
    verificador = secrets.token_urlsafe(64)[:96]  # 43..128 caracteres permitidos
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, reto


def url_autorizacion(client_id: str, estado: str, reto: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": estado,
        "code_challenge": reto,
        "code_challenge_method": "S256",
    })


def abrir_navegador(url: str) -> bool:
    """Abre la URL en el navegador predeterminado de Windows desde WSL.

    Se usa `rundll32.exe url.dll,FileProtocolHandler <url>` porque:
      - Se lanza con una lista de argumentos (sin shell). La interop de WSL arma la línea de
        comandos de Windows citando cada argumento; ningún shell vuelve a interpretarla.
      - cmd.exe trataría `&` (hay varios en la URL) como separador de comandos y PowerShell
        también parsea `&`/`;`: ambos romperían la URL salvo escapes frágiles.
      - explorer.exe recibe la URL pero tiene su propio parser (las comas son separadores de
        opciones, p. ej. `/select,`) y siempre devuelve código 1, así que no sabemos si funcionó.
      - FileProtocolHandler entrega el resto de la línea tal cual a ShellExecute, que abre el
        navegador predeterminado.
    Si no está, se intenta explorer.exe y luego wslview. Si todo falla, el usuario copia la URL.
    """
    candidatos = []
    rundll = shutil.which("rundll32.exe") or (
        "/mnt/c/Windows/System32/rundll32.exe" if os.path.exists("/mnt/c/Windows/System32/rundll32.exe") else None)
    if rundll:
        candidatos.append([rundll, "url.dll,FileProtocolHandler", url])
    explorer = shutil.which("explorer.exe") or (
        "/mnt/c/Windows/explorer.exe" if os.path.exists("/mnt/c/Windows/explorer.exe") else None)
    if explorer:
        candidatos.append([explorer, url])
    if shutil.which("wslview"):
        candidatos.append(["wslview", url])
    cwd = "/mnt/c" if os.path.isdir("/mnt/c") else None  # evita el aviso de ruta UNC de Windows
    for cmd in candidatos:
        try:
            subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- callback local

PAGINA = """<!doctype html><html lang="es"><meta charset="utf-8"><title>Pedro</title>
<body style="font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">
<h2>{titulo}</h2><p>{texto}</p></body></html>"""


def esperar_callback(servidor: HTTPServer, estado: str) -> str:
    """Atiende peticiones hasta recibir un code válido (devuelve el code) o fallar."""
    resultado: dict = {}

    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencio
            pass

        def _pagina(self, codigo: int, titulo: str, texto: str):
            cuerpo = PAGINA.format(titulo=html.escape(titulo), texto=html.escape(texto)).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path != "/callback":
                self._pagina(404, "No encontrado", "")
                return
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if q.get("state") != estado:
                # Pestaña vieja o petición ajena: se ignora y se sigue esperando.
                print("Aviso: llegó una respuesta con 'state' que no coincide; la ignoro.")
                self._pagina(400, "Enlace no válido",
                             "Esta respuesta no corresponde al intento actual. Usa el enlace "
                             "que muestra la terminal.")
                return
            if "error" in q:
                resultado["error"] = q["error"]
                self._pagina(200, "Conexión cancelada", "Puedes cerrar esta pestaña y volver a la terminal.")
                return
            if not q.get("code"):
                self._pagina(400, "Falta el código", "Google no envió el código de autorización.")
                return
            resultado["code"] = q["code"]
            self._pagina(200, "¡Listo! Pedro ya está conectado",
                         "Puedes cerrar esta pestaña y volver a la terminal.")

    servidor.RequestHandlerClass = Manejador
    servidor.timeout = 1.0
    limite = time.monotonic() + ESPERA
    while not resultado and time.monotonic() < limite:
        servidor.handle_request()
    if "error" in resultado:
        if resultado["error"] == "access_denied":
            raise Fallo("Cancelaste el permiso en Google (access_denied). Si fue un error, vuelve a "
                        "ejecutar este script y pulsa 'Continuar'/'Permitir'. Si Google dice que la app "
                        "no está verificada o que no tienes acceso, revisa que tu correo esté como "
                        "'usuario de prueba' en la pantalla de consentimiento.")
        raise Fallo(f"Google devolvió un error: {resultado['error']}")
    if "code" not in resultado:
        plazo = f"{ESPERA // 60} minutos" if ESPERA >= 60 else f"{ESPERA} segundos"
        raise Fallo(f"Pasaron {plazo} sin respuesta del navegador. Vuelve a ejecutar "
                    "el script y abre el enlace de inmediato.")
    return resultado["code"]


# --------------------------------------------------------------------------- token

def canjear_codigo(client_id: str, secreto: str, code: str, verificador: str) -> dict:
    try:
        r = httpx.post(TOKEN_URL, data={
            "client_id": client_id, "client_secret": secreto, "code": code,
            "code_verifier": verificador, "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI}, timeout=30)
    except httpx.HTTPError as e:
        raise Fallo(f"No pude conectar con Google para obtener el token: {e}")
    if r.status_code != 200:
        try:
            j = r.json()
            detalle = j.get("error", "") + (f": {j['error_description']}" if j.get("error_description") else "")
        except ValueError:
            detalle = r.text[:200]
        pista = ""
        if "invalid_grant" in detalle:
            pista = " El código caducó o el Client secret no corresponde; vuelve a intentarlo."
        elif "invalid_client" in detalle:
            pista = " Revisa el Client ID y el Client secret."
        elif "redirect_uri_mismatch" in detalle:
            pista = " El cliente OAuth debe ser de tipo 'App de escritorio'."
        raise Fallo(f"Google rechazó el canje del código (HTTP {r.status_code}: {detalle}).{pista}")
    tok = r.json()
    if not tok.get("refresh_token"):
        raise Fallo("Google no entregó un refresh_token. Quita el acceso de la app en "
                    "https://myaccount.google.com/permissions y vuelve a ejecutar el script.")
    return tok


def guardar(datos: dict) -> None:
    carpeta = os.path.dirname(RUTA_TOKEN)
    os.makedirs(carpeta, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".google_pedro.", suffix=".tmp", dir=carpeta)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, RUTA_TOKEN)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(RUTA_TOKEN, 0o600)


def correo_conectado(access_token: str) -> str | None:
    try:
        r = httpx.get(f"{API_BASE}/gmail/v1/users/me/profile",
                      headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
        return r.json().get("emailAddress") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


# --------------------------------------------------------------------------- main

def main() -> int:
    print("=== Conectar Pedro con Gmail y Google Calendar ===\n")
    try:
        client_id, secreto = pedir_credenciales()
        try:
            servidor = HTTPServer((BIND, PUERTO), BaseHTTPRequestHandler)
        except OSError as e:
            raise Fallo(f"No pude abrir el puerto {PUERTO} ({e}). ¿Hay otro intento abierto? "
                        "Ciérralo y vuelve a intentar.")
        try:
            verificador, reto = nuevo_pkce()
            estado = secrets.token_urlsafe(24)
            url = url_autorizacion(client_id, estado, reto)
            print("\nAbre este enlace en tu navegador e inicia sesión con tu cuenta de Google:\n")
            print(url + "\n")
            if abrir_navegador(url):
                print("(Intenté abrirlo automáticamente en tu navegador de Windows.)")
            print("Si Google avisa 'Google no verificó esta app', pulsa 'Continuar' (la app es tuya).")
            print("Marca las DOS casillas de permisos (Gmail y Calendar).")
            print(f"Esperando la respuesta del navegador (máximo {ESPERA // 60} min)...")
            code = esperar_callback(servidor, estado)
        finally:
            servidor.server_close()

        tok = canjear_codigo(client_id, secreto, code, verificador)
        concedidos = (tok.get("scope") or " ".join(SCOPES)).split()
        faltan = [s for s in SCOPES if s not in concedidos]
        expira = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 3600)))
        guardar({
            "client_id": client_id,
            "client_secret": secreto,
            "refresh_token": tok["refresh_token"],
            "access_token": tok["access_token"],
            "expiry": expira.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scopes": concedidos,
        })
        quien = correo_conectado(tok["access_token"]) if "https://www.googleapis.com/auth/gmail.readonly" in concedidos else None
        print("\n✔ ¡Listo! Pedro ya puede leer tu Gmail y usar tu Google Calendar.")
        if quien:
            print(f"  Cuenta conectada: {quien}")
        print(f"  Token guardado en {RUTA_TOKEN} (permisos 600).")
        if faltan:
            nombres = {"https://www.googleapis.com/auth/gmail.readonly": "Gmail",
                       "https://www.googleapis.com/auth/calendar.events": "Calendar"}
            print("\n⚠ No marcaste el permiso de: " + ", ".join(nombres[s] for s in faltan) +
                  ". Esa parte no funcionará. Vuelve a ejecutar el script y marca ambas casillas.")
        return 0
    except Fallo as e:
        print(f"\n✘ {e}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n✘ Cancelado.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
