// Prueba el cliente HTTP contra un cerebro falso local (no toca WhatsApp ni el cerebro real).
import assert from 'node:assert/strict';
import http from 'node:http';
import { after, before, describe, it } from 'node:test';

import {
  CerebroApagado,
  CerebroDemorado,
  ErrorCerebro,
  SinTranscripcion,
  cerebroEnLinea,
  preguntar,
  transcribir,
} from '../cerebro.js';

let servidor;
let api;
let ultimo = null;
let transcribirExiste = false;

function leerCuerpo(req) {
  return new Promise((res) => {
    const partes = [];
    req.on('data', (p) => partes.push(p));
    req.on('end', () => res(Buffer.concat(partes)));
  });
}

before(async () => {
  servidor = http.createServer(async (req, res) => {
    const cuerpo = await leerCuerpo(req);
    ultimo = { url: req.url, headers: req.headers, cuerpo };
    const json = (status, obj) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(obj));
    };
    if (req.url === '/health') return json(200, { status: 'ok' });
    if (req.url === '/v1/chat/completions') {
      const b = JSON.parse(cuerpo.toString('utf8'));
      if (b.messages.at(-1).content.includes('lento')) return setTimeout(() => json(200, {}), 1000);
      if (b.messages.at(-1).content.includes('falla')) return json(500, { error: { message: 'se rompió' } });
      return json(200, { choices: [{ message: { role: 'assistant', content: `eco: ${b.messages.at(-1).content}` } }] });
    }
    if (req.url === '/pedro/transcribir' && transcribirExiste) return json(200, { texto: ' hola desde audio ' });
    return json(404, { error: 'no existe' });
  });
  await new Promise((r) => servidor.listen(0, '127.0.0.1', r));
  api = `http://127.0.0.1:${servidor.address().port}`;
});

after(() => servidor.close());

describe('cliente del cerebro', () => {
  it('health', async () => {
    assert.equal(await cerebroEnLinea(api), true);
    assert.equal(await cerebroEnLinea('http://127.0.0.1:9'), false);
  });

  it('chat con stream:false y los mensajes tal cual', async () => {
    const mensajes = [{ role: 'user', content: '[whatsapp] a' }, { role: 'assistant', content: 'b' }, { role: 'user', content: '[whatsapp] c' }];
    const r = await preguntar(api, mensajes);
    assert.equal(r, 'eco: [whatsapp] c');
    const enviado = JSON.parse(ultimo.cuerpo.toString('utf8'));
    assert.equal(enviado.stream, false);
    assert.deepEqual(enviado.messages, mensajes);
    assert.equal(ultimo.headers['x-pedro-nueva'], undefined);
  });

  it('cerebro apagado', async () => {
    await assert.rejects(preguntar('http://127.0.0.1:9', [{ role: 'user', content: 'x' }]), CerebroApagado);
  });

  it('cerebro demorado (timeout)', async () => {
    await assert.rejects(preguntar(api, [{ role: 'user', content: 'lento' }], { timeoutMs: 100 }), CerebroDemorado);
  });

  it('error HTTP', async () => {
    await assert.rejects(preguntar(api, [{ role: 'user', content: 'falla' }]), (e) => e instanceof ErrorCerebro && e.status === 500);
  });

  it('transcribir: 404 => SinTranscripcion', async () => {
    transcribirExiste = false;
    await assert.rejects(transcribir(api, Buffer.from('OggS')), SinTranscripcion);
  });

  it('transcribir: manda bytes crudos con content-type de audio y lee {texto}', async () => {
    transcribirExiste = true;
    const t = await transcribir(api, Buffer.from('OggS-datos'), { mimetype: 'audio/ogg; codecs=opus', segundos: 4 });
    assert.equal(t, 'hola desde audio');
    assert.equal(ultimo.headers['content-type'], 'audio/ogg; codecs=opus');
    assert.equal(ultimo.headers['x-pedro-duracion'], '4');
    assert.equal(ultimo.cuerpo.toString(), 'OggS-datos');
  });
});
