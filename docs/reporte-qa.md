# Reporte de QA — Pedro (15 oct 2026)

**Alcance:** política de permisos (`cerebro/politica.py`), servidor del cerebro (`cerebro/pedro_cerebro.py`), herramientas del PC (`herramientas/pc_mcp.py`, `escribir.ps1`), visión, grafo.
**Método:** revisión de código por un revisor independiente + pruebas automáticas (unitarias de la política y de punta a punta del cerebro contra un modelo simulado) + pruebas manuales por voz en el PC real.

## Resultado

| Severidad | Encontrados | Corregidos | Aceptados (riesgo conocido) |
|---|---|---|---|
| Crítica | 6 | 6 | 0 |
| Alta | 4 | 4 | 0 |
| Media | 8 | 6 | 2 |
| Baja | 6 | 3 | 3 |

## Hallazgos críticos (todos corregidos)

| # | Hallazgo | Ejemplo que pasaba sin permiso | Corrección |
|---|---|---|---|
| 1 | Código arbitrario con intérpretes | `node -e "fs.rmSync('src')"`, `python -c …` | Comandos por **lista blanca**: lo que no está, pide el sí |
| 2 | Envolturas y borrados indirectos | `bash -c 'rm -r src'`, `find -delete` | Ídem |
| 3 | Volcado de variables de entorno (clave de la API) | `printenv ANTHROPIC_API_KEY`, `env \| grep KEY` | **Prohibido** en cualquier parte del comando |
| 4 | Git con opciones delante | `git -C x push --force`, `git -C . reset --hard` | Lista blanca de git de solo lectura; force-push prohibido con cualquier opción |
| 5 | Un "sí" aprobaba otra acción distinta | sí a `git commit` → corría `rm -r node_modules` | Se aprueba solo la acción **exacta** pendiente, una vez |
| 6 | Frases normales contaban como "sí" | "Va a llover hoy", "Sí, pero no lo hagas todavía" | "Sí" corto y claro, sin dudas ni condiciones |

## Hallazgos altos (corregidos)

- `.env` legible con otros comandos o comodines (`nl .env`, `cat .e*`, `< .env`) → secretos prohibidos en cualquier parte del comando.
- Secretos con espacio final, flujos NTFS (`.env::$DATA`) y carpetas `~/.aws`, `~/.ssh`, `.git-credentials` → rutas normalizadas + lista ampliada.
- `npm run deploy`, `psql … DROP TABLE` → fuera de la lista blanca, piden el sí.
- Descargar y ejecutar (`wget … | bash`, `certutil -urlcache`) → prohibido.

## Medios y bajos

Corregidos: redirecciones a archivo (`1>`, `&>`, `tee`, `dd of=`), persistencia en Windows (`reg add …\Run`, `schtasks`), `pwsh`/`ri`/`docker-compose`, escritura en terminales ajenas por coincidencia parcial de título (ahora exacta para las ventanas `Pedro-…`), comandos escritos en las consolas `Pedro-dev-…` (siguen la misma lista blanca), portapapeles borrado al escribir, `.env.example` y `token.ts` bloqueados por error.

Aceptados (riesgo conocido):
- El permiso depende del hook `PreToolUse` del motor de Claude Code; verificado que se dispara para Bash, archivos, herramientas MCP y la visión.
- Límite de espera por mensaje (90 s sin noticias del modelo), no por turno completo.
- Al cortar a Pedro en medio de una herramienta, el turno se interrumpe; si queda inconsistente, el siguiente turno reinicia la sesión.

## Pruebas

- **Política:** 110+ casos automáticos (`python cerebro/politica.py`), incluidos todos los ataques del revisor y los comandos normales de trabajo (que deben seguir corriendo sin preguntar).
- **Punta a punta (modelo simulado):** pedir sí → "va a llover" (no aprueba) → "sí, dale" (aprueba), `.env` bloqueado, servidor en Bash redirigido a terminal, visión, interrupción por voz, límite de espera y recuperación.
- **Comportamiento con el modelo real:** opción 7 del menú (`cerebro/probar.py`, 39 casos de conversaciones reales en modo simulación).
