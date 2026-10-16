// Cliente HTTP del cerebro de Orión (http://127.0.0.1:8787 por defecto).
// Sin dependencias: usa fetch de Node >= 20.

import { contenidoDeRespuesta } from './logica.js';

export class CerebroApagado extends Error {}
export class CerebroDemorado extends Error {}
export class SinTranscripcion extends Error {}   // el cerebro aún no tiene /pedro/transcribir (404)
export class ErrorCerebro extends Error {
  constructor(mensaje, status) {
    super(mensaje);
    this.status = status;
  }
}

function clasificarFallo(e) {
  if (e?.name === 'TimeoutError' || e?.name === 'AbortError') return new CerebroDemorado('el cerebro no respondió a tiempo');
  const codigo = e?.cause?.code || e?.code;
  if (e instanceof TypeError || ['ECONNREFUSED', 'ECONNRESET', 'EHOSTUNREACH', 'ENOTFOUND', 'UND_ERR_SOCKET'].includes(codigo)) {
    return new CerebroApagado(`no hay conexión con el cerebro (${codigo || e.message})`);
  }
  return e;
}

async function leerError(resp) {
  try {
    const t = await resp.text();
    try {
      const j = JSON.parse(t);
      return j?.error?.message || j?.error || t;
    } catch {
      return t;
    }
  } catch {
    return '';
  }
}

/** GET /health -> true/false (nunca lanza). */
export async function cerebroEnLinea(api, { timeoutMs = 3000, fetchImpl = fetch } = {}) {
  try {
    const r = await fetchImpl(`${api}/health`, { signal: AbortSignal.timeout(timeoutMs) });
    return r.ok;
  } catch {
    return false;
  }
}

/** POST /v1/chat/completions con stream:false. Devuelve el texto de la respuesta. */
export async function preguntar(api, mensajes, { timeoutMs = 180_000, fetchImpl = fetch } = {}) {
  let resp;
  try {
    resp = await fetchImpl(`${api}/v1/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Pedro-Origen': 'whatsapp' },
      body: JSON.stringify({ model: 'pedro', stream: false, messages: mensajes }),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (e) {
    throw clasificarFallo(e);
  }
  if (!resp.ok) throw new ErrorCerebro(`el cerebro respondió HTTP ${resp.status}: ${String(await leerError(resp)).slice(0, 200)}`, resp.status);
  let json;
  try {
    json = await resp.json();
  } catch (e) {
    throw clasificarFallo(e);
  }
  return contenidoDeRespuesta(json);
}

/**
 * POST /pedro/transcribir con los bytes crudos del audio.
 * Contrato esperado: 200 {"texto": "..."}; 404 = el cerebro aún no lo tiene.
 */
export async function transcribir(api, audio, { mimetype = 'audio/ogg; codecs=opus', segundos = 0, timeoutMs = 120_000, fetchImpl = fetch } = {}) {
  let resp;
  try {
    resp = await fetchImpl(`${api}/pedro/transcribir`, {
      method: 'POST',
      headers: {
        'Content-Type': mimetype,
        'X-Pedro-Origen': 'whatsapp',
        'X-Pedro-Duracion': String(segundos || 0),
      },
      body: audio,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (e) {
    throw clasificarFallo(e);
  }
  if (resp.status === 404 || resp.status === 405) throw new SinTranscripcion('el cerebro no tiene /pedro/transcribir');
  if (!resp.ok) throw new ErrorCerebro(`transcripción falló HTTP ${resp.status}: ${String(await leerError(resp)).slice(0, 200)}`, resp.status);
  let json;
  try {
    json = await resp.json();
  } catch (e) {
    throw clasificarFallo(e);
  }
  return typeof json?.texto === 'string' ? json.texto.trim() : '';
}
