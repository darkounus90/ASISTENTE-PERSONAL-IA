// Pedro por WhatsApp
// Se vincula como "dispositivo" (WhatsApp Web) a TU cuenta y SOLO atiende el chat
// "Mensajes para ti mismo". Nunca lee, responde ni marca como leído ningún otro chat,
// grupo, estado o difusión: no le escribe a terceros, jamás.
//
//   node pedro-whatsapp.js
//
// Variables opcionales: JARVIS_API (por defecto http://127.0.0.1:8787)

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import pino from 'pino';
import qrcode from 'qrcode-terminal';
import makeWASocket, {
  Browsers,
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestWaWebVersion,
  generateMessageIDV2,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';

import {
  ConjuntoLimitado,
  INTERVALO_ENVIO_MS,
  MAX_EDAD_MS,
  MAX_TURNOS,
  MENSAJES,
  armarMensajes,
  armarRespuesta,
  clasificar,
  esperaNecesaria,
  esperaReconexion,
  jidsPropios,
  normalizarJid,
  puedeEnviarA,
  recortarHistorial,
  textoParaCerebro,
} from './logica.js';
import {
  CerebroApagado,
  CerebroDemorado,
  SinTranscripcion,
  cerebroEnLinea,
  preguntar,
  transcribir,
} from './cerebro.js';

// ------------------------------------------------------------------ rutas y ajustes
const API = (process.env.JARVIS_API || 'http://127.0.0.1:8787').replace(/\/+$/, '');
const CARPETA = path.join(os.homedir(), '.pedro');
const CARPETA_AUTH = path.join(CARPETA, 'whatsapp-auth');
const ARCHIVO_HISTORIAL = path.join(CARPETA, 'whatsapp-historial.json');
const TIMEOUT_CEREBRO_MS = 180_000;
const MAX_SEGUNDOS_AUDIO = 5 * 60;

const logger = pino({ level: 'warn' });

function info(msg) {
  const hora = new Date().toLocaleTimeString('es-CO', { hour12: false });
  console.log(`[${hora}] ${msg}`);
}

// ------------------------------------------------------------------ historial (en memoria + disco)
function cargarHistorial() {
  try {
    return recortarHistorial(JSON.parse(fs.readFileSync(ARCHIVO_HISTORIAL, 'utf8')), MAX_TURNOS);
  } catch {
    return [];
  }
}

let historial = cargarHistorial();

function guardarHistorial() {
  historial = recortarHistorial(historial, MAX_TURNOS);
  try {
    const tmp = `${ARCHIVO_HISTORIAL}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(historial, null, 1), 'utf8');
    fs.renameSync(tmp, ARCHIVO_HISTORIAL);
  } catch (e) {
    info(`no pude guardar el historial: ${e.message}`);
  }
}

// ------------------------------------------------------------------ estado
let sock = null;
let propios = new Set();
let intentosReconexion = 0;
let temporizadorReconexion = null;
let cerrando = false;
let avisoCerebroApagado = false;     // "mi cerebro está apagado" se manda una sola vez por caída
let ultimoEnvio = 0;
const enviados = new ConjuntoLimitado(500);    // ids de mensajes que mandó Pedro
const cacheEnviados = new Map();               // id -> contenido, para reintentos de cifrado del teléfono
let cola = Promise.resolve();                  // procesa de a un mensaje, en orden

const dormir = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ envío (solo al chat propio)
async function enviar(chatJid, texto) {
  // Candado final: jamás se envía a un chat que no sea el tuyo contigo mismo.
  if (!puedeEnviarA(chatJid, propios)) {
    info(`BLOQUEADO: intento de enviar a ${chatJid}, que no es tu chat contigo mismo.`);
    return;
  }
  const espera = esperaNecesaria(ultimoEnvio, Date.now(), INTERVALO_ENVIO_MS);
  if (espera > 0) await dormir(espera);
  ultimoEnvio = Date.now();
  // El id se registra ANTES de enviar: el eco de este mensaje (llega por messages.upsert) se ignora.
  const id = generateMessageIDV2(sock.user?.id);
  enviados.add(id);
  const r = await sock.sendMessage(chatJid, { text: texto }, { messageId: id });
  if (r?.message) {
    cacheEnviados.set(id, r.message);
    while (cacheEnviados.size > 200) cacheEnviados.delete(cacheEnviados.keys().next().value);
  }
}

async function enviarRespuesta(chatJid, texto) {
  for (const trozo of armarRespuesta(texto)) await enviar(chatJid, trozo);
}

/** Mantiene "escribiendo..." mientras corre `tarea` (WhatsApp lo apaga solo a los ~25 s). */
async function conEscribiendo(chatJid, tarea) {
  const pulso = () => sock?.sendPresenceUpdate('composing', chatJid).catch(() => {});
  pulso();
  const t = setInterval(pulso, 10_000);
  try {
    return await tarea();
  } finally {
    clearInterval(t);
    sock?.sendPresenceUpdate('paused', chatJid).catch(() => {});
  }
}

// ------------------------------------------------------------------ cerebro
async function hablarConCerebro(chatJid, textoUsuario) {
  const mensajes = armarMensajes(historial, textoUsuario, MAX_TURNOS);
  try {
    const respuesta = await conEscribiendo(chatJid, () => preguntar(API, mensajes, { timeoutMs: TIMEOUT_CEREBRO_MS }));
    avisoCerebroApagado = false;
    historial.push({ role: 'user', content: textoUsuario });
    historial.push({ role: 'assistant', content: respuesta || '(sin respuesta)' });
    guardarHistorial();
    await enviarRespuesta(chatJid, respuesta || 'No tengo respuesta para eso.');
  } catch (e) {
    await manejarFalloCerebro(chatJid, e, textoUsuario);
  }
}

async function manejarFalloCerebro(chatJid, e, textoUsuario = null) {
  if (e instanceof CerebroApagado) {
    info(`cerebro apagado (${e.message})`);
    if (!avisoCerebroApagado) {
      avisoCerebroApagado = true;
      await enviar(chatJid, MENSAJES.cerebroApagado);
    }
    return;
  }
  if (e instanceof CerebroDemorado) {
    info('el cerebro se demoró más de 3 minutos');
    if (textoUsuario) {
      historial.push({ role: 'user', content: textoUsuario });
      guardarHistorial();
    }
    await enviar(chatJid, MENSAJES.demora);
    return;
  }
  info(`error con el cerebro: ${e?.message || e}`);
  await enviar(chatJid, `🤖 Pedro: tuve un problema con mi cerebro (${String(e?.message || e).slice(0, 120)}).`);
}

async function atenderAudio(msg, c) {
  const chatJid = msg.key.remoteJid;
  if (c.segundos > MAX_SEGUNDOS_AUDIO) {
    await enviar(chatJid, MENSAJES.notaLarga);
    return;
  }
  let texto;
  try {
    const audio = await downloadMediaMessage(msg, 'buffer', {}, { logger, reuploadRequest: sock.updateMediaMessage });
    texto = await conEscribiendo(chatJid, () => transcribir(API, audio, { mimetype: c.mimetype, segundos: c.segundos }));
  } catch (e) {
    if (e instanceof SinTranscripcion) {
      await enviar(chatJid, MENSAJES.sinNotasDeVoz);
      return;
    }
    if (e instanceof CerebroApagado || e instanceof CerebroDemorado) {
      await manejarFalloCerebro(chatJid, e);
      return;
    }
    info(`no pude procesar la nota de voz: ${e?.message || e}`);
    await enviar(chatJid, MENSAJES.noEntendiAudio);
    return;
  }
  if (!texto) {
    await enviar(chatJid, MENSAJES.noEntendiAudio);
    return;
  }
  info(`nota de voz (${c.segundos}s): ${texto.slice(0, 80)}`);
  await enviar(chatJid, `🤖 Pedro: 🎙️ entendí: «${texto.length > 300 ? `${texto.slice(0, 300)}…` : texto}»`);
  await hablarConCerebro(chatJid, textoParaCerebro(texto, { notaDeVoz: true }));
}

async function atender(msg, c) {
  const chatJid = msg.key.remoteJid;
  // Si el cerebro estaba caído y ya avisé, no repito el aviso mientras siga caído.
  if (avisoCerebroApagado && (c.accion === 'texto' || c.accion === 'audio')) {
    if (!(await cerebroEnLinea(API))) {
      info('mensaje ignorado: el cerebro sigue apagado (ya avisé).');
      return;
    }
    avisoCerebroApagado = false;
  }
  switch (c.accion) {
    case 'texto':
      info(`mensaje: ${c.texto.slice(0, 80)}${c.reenviado ? ' (reenviado)' : ''}`);
      await hablarConCerebro(chatJid, textoParaCerebro(c.texto, { reenviado: c.reenviado }));
      break;
    case 'audio':
      await atenderAudio(msg, c);
      break;
    case 'imagen':
      await enviar(chatJid, MENSAJES.sinImagenes);
      break;
    case 'no_soportado':
      await enviar(chatJid, MENSAJES.noSoportado);
      break;
    default:
      break;
  }
}

function alLlegarMensajes({ messages }) {
  for (const msg of messages || []) {
    // clasificar() mira primero SOLO la llave: los mensajes de otros chats se descartan sin leerlos.
    const c = clasificar(msg, { propios, enviados, ahoraMs: Date.now(), maxEdadMs: MAX_EDAD_MS });
    if (c.accion === 'ignorar') continue;
    if (msg.key.id) enviados.add(msg.key.id);   // no atender dos veces el mismo mensaje
    cola = cola
      .then(() => (cerrando ? null : atender(msg, c)))
      .catch((e) => info(`error atendiendo un mensaje: ${e?.stack || e}`));
  }
}

// ------------------------------------------------------------------ conexión
async function actualizarPropios() {
  const me = sock?.authState?.creds?.me;
  const extras = [];
  try {
    const pn = normalizarJid(me?.id || sock?.user?.id);
    const lid = pn ? await sock.signalRepository?.lidMapping?.getLIDForPN?.(pn) : null;
    if (lid) extras.push(lid);
  } catch {
    // sin mapeo LID: seguimos con lo que trae la sesión
  }
  propios = jidsPropios(sock?.user, me, extras);
  info(`atendiendo solo tu chat contigo mismo: ${[...propios].join(' y ') || '(desconocido)'}`);
}

async function versionWhatsapp() {
  try {
    const { version, isLatest } = await fetchLatestWaWebVersion({ signal: AbortSignal.timeout(10_000) });
    if (isLatest) return version;
  } catch {
    // sin internet o cambió la página: usamos la versión que trae Baileys
  }
  return undefined;
}

function programarReconexion(motivo) {
  if (cerrando || temporizadorReconexion) return;
  const espera = esperaReconexion(intentosReconexion++);
  info(`conexión cerrada (${motivo}); reintento en ${Math.round(espera / 1000)} s`);
  temporizadorReconexion = setTimeout(() => {
    temporizadorReconexion = null;
    conectar().catch((e) => {
      info(`no pude conectar: ${e?.message || e}`);
      programarReconexion('error al conectar');
    });
  }, espera);
}

function salir(codigo, lineas) {
  cerrando = true;
  console.log('');
  for (const l of lineas) console.log(l);
  console.log('');
  guardarHistorial();
  try {
    sock?.end(undefined);
  } catch {
    // nada
  }
  setTimeout(() => process.exit(codigo), 300);
}

async function conectar() {
  fs.mkdirSync(CARPETA_AUTH, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(CARPETA_AUTH);
  const version = await versionWhatsapp();

  sock = makeWASocket({
    ...(version ? { version } : {}),
    auth: state,
    logger,
    browser: Browsers.windows('Chrome'),
    markOnlineOnConnect: false,          // tu teléfono sigue recibiendo notificaciones normal
    syncFullHistory: false,                // no pide el historial completo (Pedro no lo usa ni lo guarda)
    generateHighQualityLinkPreview: false,
    getMessage: async (key) => cacheEnviados.get(key.id),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (qr) {
      console.log('');
      console.log('  Vincula a Pedro con tu WhatsApp:');
      console.log('  En tu celular: WhatsApp > Ajustes (o los tres puntos) > Dispositivos vinculados > Vincular un dispositivo');
      console.log('  y escanea este código (se renueva solo cada ~20 s):');
      console.log('');
      qrcode.generate(qr, { small: true });
    }
    if (connection === 'open') {
      intentosReconexion = 0;
      await actualizarPropios();
      info('Pedro conectado a WhatsApp. Escríbele en "Mensajes para ti mismo" (tu propio chat).');
      return;
    }
    if (connection !== 'close' || cerrando) return;

    const codigo = lastDisconnect?.error?.output?.statusCode;
    if (codigo === DisconnectReason.loggedOut) {
      salir(2, [
        'WhatsApp cerró la sesión de Pedro (lo desvinculaste o expiró).',
        `Para volver a vincularlo: borra la carpeta ${CARPETA_AUTH}`,
        'y vuelve a iniciar Pedro por WhatsApp para escanear un código nuevo.',
      ]);
      return;
    }
    if (codigo === DisconnectReason.connectionReplaced) {
      salir(3, ['Otra copia de Pedro por WhatsApp abrió esta misma sesión. Cierra una de las dos ventanas.']);
      return;
    }
    if (codigo === DisconnectReason.forbidden) {
      salir(4, ['WhatsApp rechazó la conexión (403). Revisa tu cuenta en el teléfono antes de reintentar.']);
      return;
    }
    if (codigo === DisconnectReason.restartRequired) {
      // Normal justo después de escanear el QR: reconecta de una.
      info('vinculado; reconectando...');
      conectar().catch((e) => programarReconexion(e?.message || 'error'));
      return;
    }
    programarReconexion(`código ${codigo ?? 'desconocido'}`);
  });

  sock.ev.on('messages.upsert', alLlegarMensajes);
}

// ------------------------------------------------------------------ arranque y cierre
process.on('SIGINT', () => {
  if (cerrando) process.exit(0);
  info('cerrando Pedro por WhatsApp (la sesión queda vinculada)...');
  salir(0, ['Hasta luego.']);
});
process.on('SIGTERM', () => salir(0, ['Pedro por WhatsApp detenido.']));
process.on('unhandledRejection', (e) => info(`error no manejado: ${e?.stack || e}`));

const [mayor] = process.versions.node.split('.').map(Number);
if (mayor < 20) {
  console.error(`Pedro por WhatsApp necesita Node.js 20 o más nuevo (tienes ${process.versions.node}).`);
  process.exit(1);
}

fs.mkdirSync(CARPETA, { recursive: true });
info(`Pedro por WhatsApp · cerebro en ${API}`);
if (!(await cerebroEnLinea(API))) info('ojo: el cerebro no responde ahora; enciéndelo con Jarvis.bat (opción 1).');
await conectar();
