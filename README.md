# Pedro — asistente personal por voz

Asistente personal tipo JARVIS para Windows: lo activas diciendo **"Pedro, …"**, te responde con voz y trabaja en tu PC con el motor de **Claude Code** (Claude Agent SDK).

## Qué puede hacer
- **Voz**: escucha con Whisper (local), habla con voces de Microsoft (gratis, ej. `es-CO-GonzaloNeural`). Lo puedes interrumpir hablando.
- **Proyectos**: lee y busca en tus carpetas de código, revisa git, abre proyectos en VS Code, levanta servidores (`npm run dev`) en terminales.
- **Terminales**: las lista, **lee lo que muestran** y escribe en ellas.
- **Claude Code**: lo abre en un proyecto con un pedido inicial o lo pone a trabajar en segundo plano.
- **Visión**: captura la pantalla o una ventana y la ve ("¿qué error sale aquí?").
- **Grafo de Obsidian**: busca, lee y crea notas enlazadas.
- **Recordatorios**, **Gmail y Calendar**, **navegador Comet** (abrir páginas y manejarlo con Playwright).
- **Memoria**: recuerda lo que le cuentas en `MEMORY.md` y retoma la conversación.

## Seguridad (por código, no por prompt)
`cerebro/politica.py` decide cada acción:
- **Libre**: leer, buscar, abrir, comandos de desarrollo.
- **Con tu "sí"**: commits, push, deploys, borrar, editar código, crear eventos, escribir en terminales ajenas, enviar formularios.
- **Bloqueado**: leer `.env` y secretos, comandos destructivos.

Lo que lee en webs, correos, terminales o la pantalla lo trata como datos, nunca como órdenes.

## Estructura
| Carpeta / archivo | Qué es |
|---|---|
| `Jarvis.bat` | Menú para encender, hablar, probar y configurar |
| `cerebro/` | Servidor del cerebro (API compatible con OpenAI en `127.0.0.1:8787`), política de permisos, pruebas automáticas |
| `voz/` | Cliente de voz (micrófono, Whisper, TTS, interrupciones, recordatorios) |
| `herramientas/` | Servidores MCP: PC y terminales, visión, grafo, Google, Claude Code |
| `SOUL.md` | Personalidad y principios de Pedro |
| `*.example.*` | Plantillas de tu perfil, memoria y apodos de proyectos |
| `cerebro.sh`, `herramientas.py`, `parches.py`, `2-instalar-jarvis.sh` | Cerebro anterior (OpenJarvis en WSL), se conserva como respaldo |

## Instalación
1. Windows 10/11 con WSL (Ubuntu), [uv](https://docs.astral.sh/uv/), Git para Windows y, opcional, Node.js (para manejar el navegador).
2. Copia `USER.example.md` → `USER.md`, `MEMORY.example.md` → `MEMORY.md` y `apodos_proyectos.example.json` → `apodos_proyectos.json`, y llénalos.
3. Crea `%USERPROFILE%\.pedro\secrets.env` con tu clave:
   ```
   ANTHROPIC_API_KEY=tu_clave
   ANTHROPIC_BASE_URL=https://api.anthropic.com
   ```
4. Abre `Jarvis.bat` → opción **1** y di "Pedro, …".
5. Opción **7** corre las pruebas automáticas (simula las acciones; deja el reporte en `cerebro/pruebas/`).

> Las rutas por defecto asumen `C:\Users\PC\Documents\jarvis`, proyectos en `C:\dev` y el grafo en `C:\dev_grafo`; se cambian con variables de entorno (`PEDRO_DEV`, `PEDRO_GRAFO`, `PEDRO_MODELO`…).
