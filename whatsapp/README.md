# Orión por WhatsApp

Le escribes a Orión desde el celular, en tu chat **"Mensajes para ti mismo"** (el chat contigo mismo), y él te responde ahí mismo usando el mismo cerebro del PC (el de `Jarvis.bat`). Sirve para pedirle cosas cuando no estás frente al computador: "¿cómo va el build?", "recuérdame…", "revisa el repo de CHECK".

## Qué hace (y qué no)

- Se vincula como un **dispositivo más** de tu WhatsApp (igual que WhatsApp Web), usando la librería no oficial [Baileys](https://github.com/WhiskeySockets/Baileys).
- **Solo atiende tu chat contigo mismo** y solo lo que escribes tú. Todo lo demás (chats con otras personas, grupos, estados, difusiones, canales) lo descarta sin leer el contenido: no responde, no marca como leído, no guarda nada.
- **Nunca le escribe a otra persona.** Además del filtro, hay un candado final: antes de enviar comprueba que el destino sea tu propio chat; si no lo es, bloquea el envío.
- Sus respuestas empiezan con `🤖 Orión:` para que las distingas y para que él nunca se responda a sí mismo.
- Cada mensaje tuyo se le pasa al cerebro con la marca `[whatsapp]` (para que sepa que es chat y no voz) junto con los últimos 10 mensajes de esta conversación, así continúa la misma sesión.
- Mientras piensa ves "escribiendo…". Si el cerebro está apagado te lo dice una vez: `🤖 Orión: mi cerebro está apagado en el PC.`
- **Mensajes reenviados**: se le pasan al cerebro marcados como contenido de otra persona (datos, nunca órdenes).
- **Notas de voz**: las descarga y se las manda al cerebro para transcribir (`/pedro/transcribir`). Mientras el cerebro no tenga esa función, te responde que le escribas.
- **Imágenes**: todavía no (v1); te avisa.
- Al arrancar no repasa mensajes viejos: ignora todo lo que tenga más de 2 minutos. Responde como máximo 1 mensaje cada 2 segundos.
- Las acciones peligrosas siguen pasando por la política del cerebro (`cerebro/politica.py`): lo que necesita tu "sí", te lo pregunta y le contestas en el mismo chat.

## Riesgo de bloqueo (léelo)

WhatsApp **no permite oficialmente** clientes no oficiales como Baileys. Cualquier cuenta que use uno puede, en teoría, ser suspendida.

En este caso el riesgo es **bajo** porque Orión se comporta como un WhatsApp Web normal que solo escribe en tu propio chat: no manda mensajes a terceros, no hace envíos masivos, no entra a grupos, no agrega contactos y responde despacio (máximo 1 mensaje cada 2 s). Lo que WhatsApp suele castigar es el spam y los envíos masivos a otros números, y aquí no ocurre nada de eso.

Pero **el riesgo no es cero**: WhatsApp puede detectar el cliente no oficial en sí, o cambiar el protocolo y romper Baileys de un día para otro. Si tu número es crítico para tu negocio, tenlo en cuenta (o usa un número secundario).

Otra cosa: quien tenga tu celular desbloqueado (o un WhatsApp Web abierto de tu cuenta) puede darle órdenes a Orión desde tu chat contigo mismo. Protege tu teléfono.

## Requisitos

- Node.js **20 o más nuevo** (opción **8** de `Jarvis.bat` instala Node.js LTS).
- El cerebro de Orión encendido en el PC (opción **1** de `Jarvis.bat`) en `http://127.0.0.1:8787` (se cambia con la variable `JARVIS_API`).

## Vincular (la primera vez)

1. Abre una PowerShell en la carpeta de Orión y ejecuta:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File whatsapp\iniciar-whatsapp.ps1
   ```
   La primera vez instala las dependencias (1-2 minutos).
2. Aparece un código QR en la ventana. En tu celular: **WhatsApp > Ajustes (o los tres puntos) > Dispositivos vinculados > Vincular un dispositivo** y escanéalo. Si el QR se ve deformado, maximiza la ventana.
3. Cuando diga *"Orión conectado a WhatsApp"*, abre tu chat contigo mismo (búscate en "Nuevo chat": aparece tu nombre con "(Tú)") y escríbele.
4. Deja la ventana **"Orión - WhatsApp (no cerrar)"** abierta mientras lo quieras usar. Para apagarlo: `Ctrl+C` o cierra la ventana (la vinculación se conserva y la próxima vez no pide QR).

La sesión se guarda en `%USERPROFILE%\.pedro\whatsapp-auth` (fuera del repositorio; **nunca la subas ni la compartas**: con esos archivos alguien podría usar tu WhatsApp). El historial corto del chat queda en `%USERPROFILE%\.pedro\whatsapp-historial.json`.

## Desvincular

1. En el celular: **WhatsApp > Dispositivos vinculados**, toca el dispositivo (aparece como *Chrome (Windows)*) y **Cerrar sesión**.
2. En el PC, borra la carpeta `%USERPROFILE%\.pedro\whatsapp-auth` (y si quieres `whatsapp-historial.json`).

Si cierras la sesión desde el celular con Orión encendido, él lo detecta, te explica cómo volver a vincular y se apaga solo.

## Problemas comunes

| Síntoma | Qué hacer |
|---|---|
| "mi cerebro está apagado en el PC" | Enciende el cerebro con `Jarvis.bat` (opción 1). |
| "Otra copia de Orión por WhatsApp abrió esta misma sesión" | Tienes dos ventanas de Orión por WhatsApp; cierra una. |
| Reintenta conectar una y otra vez | Revisa el internet del PC. Si sigue, puede que WhatsApp cambió algo y haya que actualizar Baileys en `package.json`. |
| No responde a un mensaje | Debe ser en tu chat contigo mismo, escrito por ti, de menos de 2 minutos y sin empezar con 🤖. |

## Para desarrolladores

| Archivo | Qué es |
|---|---|
| `pedro-whatsapp.js` | Conexión con WhatsApp (Baileys), cola de mensajes, envío con límite de ritmo, reconexión |
| `logica.js` | Lógica pura: filtro del chat propio (número y LID), prefijo, edad, historial, trozos de ≤ 3500 caracteres |
| `cerebro.js` | Cliente HTTP del cerebro (`/v1/chat/completions`, `/pedro/transcribir`, `/health`) |
| `test/` | Pruebas: `npm test` (o `node --test`), no necesitan WhatsApp |
| `iniciar-whatsapp.ps1` | Lanzador para Windows |

Contrato que el cerebro debe implementar para las notas de voz:

```
POST /pedro/transcribir
Content-Type: audio/ogg; codecs=opus      (el mimetype que trae WhatsApp)
X-Pedro-Origen: whatsapp
X-Pedro-Duracion: <segundos>
<cuerpo: bytes crudos del audio>

200 {"texto": "lo que dijo Andres"}       ("" si no entendió nada)
4xx/5xx {"error": {"message": "..."}}
404 = todavía no existe (Orión le pide a Andres que escriba)
```
