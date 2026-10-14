// Tests del servidor intermedio de MET Norway: node --test proxy/met.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { AGENTE, LIMITES, MET_URL, coordenada, responderMet } from './met.mjs';

const T0 = Date.parse('2026-10-14T10:00:30Z');
const MIN = 60 * 1000;
const HORA = 60 * MIN;
const CUERPO = JSON.stringify({ properties: { timeseries: [] } });
const MODIFICADO = 'Tue, 14 Oct 2026 09:30:00 GMT';
const PUNTO_MADRID = 'https://aire-sin-sustos.pages.dev/met/complete?lat=40.42&lon=-3.70';

/** Una caché como la de Cloudflare, en memoria. Con `rota`, cada operación falla. */
function cacheFalsa({ rota = false } = {}) {
  const datos = new Map();
  return {
    datos,
    async match(req) {
      if (rota) throw new Error('caché caída');
      const r = datos.get(req.url);
      return r ? r.clone() : undefined;
    },
    async put(req, res) {
      if (rota) throw new Error('caché caída');
      datos.set(req.url, res.clone());
    },
  };
}

/** Un MET falso que contesta lo que se le diga y apunta cada petición. */
function metFalso(...respuestas) {
  const llamadas = [];
  const fetch = async (url, opciones) => {
    llamadas.push({ url, headers: opciones.headers, signal: opciones.signal });
    const r = respuestas.shift();
    if (r instanceof Error) throw r;
    if (r === 'cuelga') {
      // No contesta nunca: solo acaba si se corta con la señal. El intervalo
      // mantiene vivo el proceso: el temporizador de AbortSignal.timeout no
      // lo hace (en Node 20, el de la tarea diaria, el test se quedaba sin
      // terminar).
      return new Promise((_, rechazar) => {
        const vivo = setInterval(() => {}, 1000);
        opciones.signal.addEventListener('abort', () => {
          clearInterval(vivo);
          rechazar(opciones.signal.reason);
        });
      });
    }
    return r();
  };
  return { fetch, llamadas };
}

const ok = (expira, modificado = MODIFICADO) => () =>
  new Response(CUERPO, {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      Expires: new Date(expira).toUTCString(),
      'Last-Modified': modificado,
    },
  });

const error = (estado, extra = {}) => () => new Response('no', { status: estado, headers: extra });

const pedir = (query, cabeceras = {}) =>
  new Request(`https://aire-sin-sustos.pages.dev/met/complete?${query}`, { headers: cabeceras });

/** Un servidor con su caché y su MET falsos, y un reloj que se mueve a mano. */
function servidor(met, { cache = cacheFalsa(), limites = {}, ip = null } = {}) {
  const reloj = { t: T0 };
  const responder = (query, { cabeceras = {}, desde = ip } = {}) =>
    responderMet(pedir(query, cabeceras), {
      cache,
      fetch: met.fetch,
      ahora: () => reloj.t,
      ip: desde,
      limites: { ...LIMITES, ...limites },
    });
  return { reloj, responder, cache };
}

test('redondea las coordenadas a dos decimales y rechaza las que no valen', () => {
  assert.equal(coordenada('40.41678', 90), '40.42');
  assert.equal(coordenada('-3.70379', 180), '-3.70');
  assert.equal(coordenada('-0.001', 180), '0.00');
  assert.equal(coordenada('91', 90), null);
  assert.equal(coordenada('abc', 90), null);
  assert.equal(coordenada('', 90), null);
  assert.equal(coordenada(null, 90), null);
});

test('sin lat o lon, 400 y no pregunta a MET', async () => {
  const met = metFalso();
  const r = await servidor(met).responder('lat=40.4');
  assert.equal(r.status, 400);
  assert.equal(met.llamadas.length, 0);
});

test('la primera vez pide a MET con su User-Agent, las coordenadas redondeadas y un límite de tiempo', async () => {
  const met = metFalso(ok(T0 + 30 * MIN));
  const r = await servidor(met).responder('lat=40.41678&lon=-3.70379');
  assert.equal(r.status, 200);
  assert.equal(await r.text(), CUERPO);
  assert.equal(met.llamadas.length, 1);
  assert.equal(met.llamadas[0].url, `${MET_URL}?lat=40.42&lon=-3.70`);
  assert.equal(met.llamadas[0].headers['User-Agent'], AGENTE);
  assert.match(AGENTE, /@/); // con contacto
  assert.match(AGENTE, /^JRSoft\/1\.0 /);
  assert.match(AGENTE, /Aire sin sustos, Plic$/);
  assert.equal(met.llamadas[0].headers['If-Modified-Since'], undefined);
  assert.ok(met.llamadas[0].signal instanceof AbortSignal);
  assert.equal(r.headers.get('Last-Modified'), MODIFICADO);
  assert.equal(r.headers.get('X-Aire-Prevision'), '2026-10-14T09:30:00.000Z');
  assert.equal(Date.parse(r.headers.get('Expires')), T0 + 30 * MIN);
});

test('antes del Expires no vuelve a pedir, aunque pregunten otros móviles', async () => {
  const met = metFalso(ok(T0 + 30 * MIN));
  const s = servidor(met);
  await s.responder('lat=40.42&lon=-3.70');
  s.reloj.t = T0 + 20 * MIN;
  // Otro punto que redondea al mismo: la misma copia.
  const r = await s.responder('lat=40.4249&lon=-3.7012');
  assert.equal(r.status, 200);
  assert.equal(await r.text(), CUERPO);
  assert.equal(met.llamadas.length, 1);
  // La copia guardada vive hasta el Expires y un día más.
  const guardada = s.cache.datos.get(PUNTO_MADRID);
  assert.equal(guardada.headers.get('Cache-Control'), `public, max-age=${30 * 60 + 24 * 3600}`);
});

test('caducada, pide con If-Modified-Since y un 304 alarga la copia', async () => {
  const met = metFalso(ok(T0 + 30 * MIN), () =>
    new Response(null, { status: 304, headers: { Expires: new Date(T0 + 90 * MIN).toUTCString() } }),
  );
  const s = servidor(met);
  await s.responder('lat=40.42&lon=-3.70');
  s.reloj.t = T0 + 31 * MIN;
  const r = await s.responder('lat=40.42&lon=-3.70');
  assert.equal(met.llamadas.length, 2);
  assert.equal(met.llamadas[1].headers['If-Modified-Since'], MODIFICADO);
  assert.equal(r.status, 200);
  assert.equal(await r.text(), CUERPO);
  assert.equal(Date.parse(r.headers.get('Expires')), T0 + 90 * MIN);
  s.reloj.t = T0 + 60 * MIN;
  await s.responder('lat=40.42&lon=-3.70');
  assert.equal(met.llamadas.length, 2);
});

test('la app recibe un 304 si ya tiene esa previsión', async () => {
  const met = metFalso(ok(T0 + 30 * MIN));
  const r = await servidor(met).responder('lat=40.42&lon=-3.70', {
    cabeceras: { 'If-Modified-Since': MODIFICADO },
  });
  assert.equal(r.status, 304);
  assert.equal(r.headers.get('Last-Modified'), MODIFICADO);
  assert.ok(r.headers.get('Expires'));
});

test('si MET falla, da la última copia y no le vuelve a llamar en diez minutos', async () => {
  const met = metFalso(ok(T0 + 30 * MIN), error(500), ok(T0 + 120 * MIN));
  const s = servidor(met);
  await s.responder('lat=40.42&lon=-3.70');
  s.reloj.t = T0 + 40 * MIN;
  const caducada = await s.responder('lat=40.42&lon=-3.70');
  assert.equal(caducada.status, 200);
  assert.equal(caducada.headers.get('X-Aire-Copia'), 'caducada');
  assert.equal(caducada.headers.get('X-Aire-Prevision'), '2026-10-14T09:30:00.000Z');
  assert.equal(Date.parse(caducada.headers.get('Expires')), T0 + 50 * MIN);
  assert.equal(await caducada.text(), CUERPO);
  assert.equal(met.llamadas.length, 2);

  s.reloj.t = T0 + 45 * MIN;
  const otra = await s.responder('lat=40.42&lon=-3.70');
  assert.equal(otra.status, 200);
  assert.equal(met.llamadas.length, 2);

  s.reloj.t = T0 + 51 * MIN;
  const despues = await s.responder('lat=40.42&lon=-3.70');
  assert.equal(despues.status, 200);
  assert.equal(despues.headers.get('X-Aire-Copia'), null);
  assert.equal(met.llamadas.length, 3);
  assert.equal(met.llamadas[2].headers['If-Modified-Since'], MODIFICADO);
});

test('sin copia, 502 y diez minutos sin llamar a MET por ese punto', async () => {
  const met = metFalso(new TypeError('sin red'), ok(T0 + 60 * MIN));
  const s = servidor(met);
  const sinRed = await s.responder('lat=10.00&lon=10.00');
  assert.equal(sinRed.status, 502);
  assert.equal(sinRed.headers.get('Retry-After'), '600');
  s.reloj.t = T0 + 5 * MIN;
  const otraVez = await s.responder('lat=10.00&lon=10.00');
  assert.equal(otraVez.status, 503);
  assert.equal(met.llamadas.length, 1);
  s.reloj.t = T0 + 11 * MIN;
  assert.equal((await s.responder('lat=10.00&lon=10.00')).status, 200);
  assert.equal(met.llamadas.length, 2);
});

test('S1: si MET no contesta, corta a tiempo y da la copia vieja', async () => {
  const met = metFalso(ok(T0 + 30 * MIN), 'cuelga', 'cuelga');
  const s = servidor(met, { limites: { esperaMetMs: 30 } });
  await s.responder('lat=40.42&lon=-3.70');
  s.reloj.t = T0 + 40 * MIN;
  const inicio = Date.now();
  const r = await s.responder('lat=40.42&lon=-3.70');
  assert.ok(Date.now() - inicio < 2000);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('X-Aire-Copia'), 'caducada');
  // Sin copia: 502, también a tiempo.
  assert.equal((await s.responder('lat=20.00&lon=20.00')).status, 502);
});

test('S2: un 429 frena todo el centro de datos, el tiempo que pida MET', async () => {
  const met = metFalso(error(429, { 'Retry-After': '1200' }), ok(T0 + 120 * MIN));
  const s = servidor(met);
  const primero = await s.responder('lat=40.42&lon=-3.70');
  assert.equal(primero.status, 503);
  assert.equal(primero.headers.get('Retry-After'), '1200');
  // Otro punto, a los diez minutos: aún frenado, sin llamar a MET.
  s.reloj.t = T0 + 10 * MIN;
  const otro = await s.responder('lat=41.39&lon=2.17');
  assert.equal(otro.status, 503);
  assert.equal(met.llamadas.length, 1);
  // Pasados los veinte minutos que pidió, vuelve a llamar.
  s.reloj.t = T0 + 21 * MIN;
  assert.equal((await s.responder('lat=41.39&lon=2.17')).status, 200);
  assert.equal(met.llamadas.length, 2);
});

test('S3: un presupuesto de llamadas a MET por minuto', async () => {
  const met = metFalso(ok(T0 + 60 * MIN), ok(T0 + 60 * MIN), ok(T0 + 60 * MIN));
  const s = servidor(met, { limites: { metPorMinuto: 2 } });
  assert.equal((await s.responder('lat=1.00&lon=1.00')).status, 200);
  assert.equal((await s.responder('lat=2.00&lon=2.00')).status, 200);
  const tercero = await s.responder('lat=3.00&lon=3.00');
  assert.equal(tercero.status, 503);
  assert.equal(met.llamadas.length, 2);
  // Lo que ya está en la caché se sigue dando.
  assert.equal((await s.responder('lat=1.00&lon=1.00')).status, 200);
  // Pasada la marca de ese punto, con presupuesto nuevo, otra vez.
  s.reloj.t = T0 + 11 * MIN;
  assert.equal((await s.responder('lat=3.00&lon=3.00')).status, 200);
  assert.equal(met.llamadas.length, 3);
});

test('S3: un presupuesto diario de llamadas a MET', async () => {
  const met = metFalso(ok(T0 + 60 * MIN), ok(T0 + 60 * MIN), ok(T0 + 25 * HORA));
  const s = servidor(met, { limites: { metPorDia: 2 } });
  assert.equal((await s.responder('lat=1.00&lon=1.00')).status, 200);
  s.reloj.t = T0 + 3 * HORA;
  assert.equal((await s.responder('lat=2.00&lon=2.00')).status, 200);
  s.reloj.t = T0 + 5 * HORA;
  assert.equal((await s.responder('lat=3.00&lon=3.00')).status, 503);
  assert.equal(met.llamadas.length, 2);
  // Al día siguiente (UTC), otra vez.
  s.reloj.t = T0 + 24 * HORA;
  assert.equal((await s.responder('lat=3.00&lon=3.00')).status, 200);
  assert.equal(met.llamadas.length, 3);
});

test('S3: los límites por defecto no frenan a un usuario normal', () => {
  assert.ok(LIMITES.ipPorMinuto >= 100, 'por IP, del orden de cientos (CGNAT)');
  assert.ok(LIMITES.metPorMinuto * 60 * 24 >= LIMITES.metPorDia);
  assert.ok(LIMITES.metPorDia >= 100_000, 'al menos lo que da el plan gratuito');
});

test('S3: un límite por IP de llamadas a MET, sin guardar la IP en claro', async () => {
  const met = metFalso(ok(T0 + 60 * MIN), ok(T0 + 60 * MIN), ok(T0 + 60 * MIN));
  const s = servidor(met, { limites: { ipPorMinuto: 2 }, ip: '203.0.113.7' });
  assert.equal((await s.responder('lat=1.00&lon=1.00')).status, 200);
  assert.equal((await s.responder('lat=2.00&lon=2.00')).status, 200);
  // Lo de la caché no cuenta.
  assert.equal((await s.responder('lat=1.00&lon=1.00')).status, 200);
  const tercero = await s.responder('lat=3.00&lon=3.00');
  assert.equal(tercero.status, 429);
  assert.ok(Number(tercero.headers.get('Retry-After')) > 0);
  // Otra IP, sí.
  assert.equal((await s.responder('lat=3.00&lon=3.00', { desde: '198.51.100.9' })).status, 200);
  assert.equal(met.llamadas.length, 3);
  for (const k of s.cache.datos.keys()) assert.ok(!k.includes('203.0.113.7'), k);
});

test('S4: la copia vieja deja de darse a las seis horas de la última confirmación', async () => {
  const met = metFalso(ok(T0 + 30 * MIN), error(500), error(500));
  const s = servidor(met);
  await s.responder('lat=40.42&lon=-3.70');
  s.reloj.t = T0 + 5 * HORA;
  assert.equal((await s.responder('lat=40.42&lon=-3.70')).status, 200);
  s.reloj.t = T0 + 7 * HORA;
  const r = await s.responder('lat=40.42&lon=-3.70');
  assert.equal(r.status, 502);
  assert.equal(met.llamadas.length, 3);
});

test('S6: si la caché falla, contesta igual y no da un 500', async () => {
  const met = metFalso(ok(T0 + 30 * MIN));
  const s = servidor(met, { cache: cacheFalsa({ rota: true }), ip: '203.0.113.7' });
  const r = await s.responder('lat=40.42&lon=-3.70');
  assert.equal(r.status, 200);
  assert.equal(await r.text(), CUERPO);
});
