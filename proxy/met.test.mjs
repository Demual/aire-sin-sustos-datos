// Tests del servidor intermedio de MET Norway: node --test proxy
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { AGENTE, MET_URL, coordenada, responderMet } from './met.mjs';

const T0 = Date.parse('2026-10-14T10:00:00Z');
const MIN = 60 * 1000;
const CUERPO = JSON.stringify({ properties: { timeseries: [] } });

/** Una caché como la de Cloudflare, en memoria. */
function cacheFalsa() {
  const datos = new Map();
  return {
    datos,
    async match(req) {
      const r = datos.get(req.url);
      return r ? r.clone() : undefined;
    },
    async put(req, res) {
      datos.set(req.url, res.clone());
    },
  };
}

/** Un MET falso que contesta lo que se le diga y apunta cada petición. */
function metFalso(...respuestas) {
  const llamadas = [];
  const fetch = async (url, opciones) => {
    llamadas.push({ url, headers: opciones.headers });
    const r = respuestas.shift();
    if (r instanceof Error) throw r;
    return r();
  };
  return { fetch, llamadas };
}

const ok = (expira, modificado = 'Tue, 14 Oct 2026 09:30:00 GMT') => () =>
  new Response(CUERPO, {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      Expires: new Date(expira).toUTCString(),
      'Last-Modified': modificado,
    },
  });

const pedir = (query, cabeceras = {}) =>
  new Request(`https://aire-sin-sustos.pages.dev/met/complete?${query}`, { headers: cabeceras });

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
  const r = await responderMet(pedir('lat=40.4'), { cache: cacheFalsa(), fetch: met.fetch, ahora: () => T0 });
  assert.equal(r.status, 400);
  assert.equal(met.llamadas.length, 0);
});

test('la primera vez pide a MET con su User-Agent y las coordenadas redondeadas', async () => {
  const met = metFalso(ok(T0 + 30 * MIN));
  const r = await responderMet(pedir('lat=40.41678&lon=-3.70379'), {
    cache: cacheFalsa(),
    fetch: met.fetch,
    ahora: () => T0,
  });
  assert.equal(r.status, 200);
  assert.equal(await r.text(), CUERPO);
  assert.equal(met.llamadas.length, 1);
  assert.equal(met.llamadas[0].url, `${MET_URL}?lat=40.42&lon=-3.70`);
  assert.equal(met.llamadas[0].headers['User-Agent'], AGENTE);
  assert.match(AGENTE, /@/); // con contacto
  assert.equal(met.llamadas[0].headers['If-Modified-Since'], undefined);
  assert.equal(r.headers.get('Last-Modified'), 'Tue, 14 Oct 2026 09:30:00 GMT');
  assert.equal(Date.parse(r.headers.get('Expires')), T0 + 30 * MIN);
});

test('antes del Expires no vuelve a pedir, aunque pregunten otros móviles', async () => {
  const cache = cacheFalsa();
  const met = metFalso(ok(T0 + 30 * MIN));
  const entorno = (t) => ({ cache, fetch: met.fetch, ahora: () => t });
  await responderMet(pedir('lat=40.42&lon=-3.70'), entorno(T0));
  // Otro punto que redondea al mismo: la misma copia.
  const r = await responderMet(pedir('lat=40.4249&lon=-3.7012'), entorno(T0 + 20 * MIN));
  assert.equal(r.status, 200);
  assert.equal(await r.text(), CUERPO);
  assert.equal(met.llamadas.length, 1);
  // La copia guardada vive hasta el Expires y un día más.
  const guardada = [...cache.datos.values()][0];
  assert.equal(guardada.headers.get('Cache-Control'), `public, max-age=${30 * 60 + 24 * 3600}`);
});

test('caducada, pide con If-Modified-Since y un 304 alarga la copia', async () => {
  const cache = cacheFalsa();
  const met = metFalso(ok(T0 + 30 * MIN), () =>
    new Response(null, { status: 304, headers: { Expires: new Date(T0 + 90 * MIN).toUTCString() } }),
  );
  await responderMet(pedir('lat=40.42&lon=-3.70'), { cache, fetch: met.fetch, ahora: () => T0 });
  const r = await responderMet(pedir('lat=40.42&lon=-3.70'), {
    cache,
    fetch: met.fetch,
    ahora: () => T0 + 31 * MIN,
  });
  assert.equal(met.llamadas.length, 2);
  assert.equal(met.llamadas[1].headers['If-Modified-Since'], 'Tue, 14 Oct 2026 09:30:00 GMT');
  assert.equal(r.status, 200);
  assert.equal(await r.text(), CUERPO);
  assert.equal(Date.parse(r.headers.get('Expires')), T0 + 90 * MIN);
  // Y hasta el Expires nuevo, sin preguntar más.
  await responderMet(pedir('lat=40.42&lon=-3.70'), { cache, fetch: met.fetch, ahora: () => T0 + 60 * MIN });
  assert.equal(met.llamadas.length, 2);
});

test('la app recibe un 304 si ya tiene esa previsión', async () => {
  const met = metFalso(ok(T0 + 30 * MIN));
  const r = await responderMet(
    pedir('lat=40.42&lon=-3.70', { 'If-Modified-Since': 'Tue, 14 Oct 2026 09:30:00 GMT' }),
    { cache: cacheFalsa(), fetch: met.fetch, ahora: () => T0 },
  );
  assert.equal(r.status, 304);
  assert.equal(r.headers.get('Last-Modified'), 'Tue, 14 Oct 2026 09:30:00 GMT');
  assert.ok(r.headers.get('Expires'));
});

test('si MET falla, da la última copia y no le vuelve a llamar en diez minutos', async () => {
  const cache = cacheFalsa();
  const met = metFalso(
    ok(T0 + 30 * MIN),
    () => new Response('lento', { status: 429 }),
    ok(T0 + 120 * MIN),
  );
  const pedirA = (t) => responderMet(pedir('lat=40.42&lon=-3.70'), { cache, fetch: met.fetch, ahora: () => t });
  await pedirA(T0);
  const caducada = await pedirA(T0 + 40 * MIN);
  assert.equal(caducada.status, 200);
  assert.equal(caducada.headers.get('X-Aire-Copia'), 'caducada');
  assert.equal(Date.parse(caducada.headers.get('Expires')), T0 + 50 * MIN);
  assert.equal(await caducada.text(), CUERPO);
  assert.equal(met.llamadas.length, 2);

  // Otro móvil a los cinco minutos: la misma copia, sin llamar a MET.
  const otra = await pedirA(T0 + 45 * MIN);
  assert.equal(otra.status, 200);
  assert.equal(await otra.text(), CUERPO);
  assert.equal(met.llamadas.length, 2);

  // Pasados los diez minutos, vuelve a probar, con su If-Modified-Since.
  const despues = await pedirA(T0 + 51 * MIN);
  assert.equal(despues.status, 200);
  assert.equal(despues.headers.get('X-Aire-Copia'), null);
  assert.equal(met.llamadas.length, 3);
  assert.equal(met.llamadas[2].headers['If-Modified-Since'], 'Tue, 14 Oct 2026 09:30:00 GMT');
});

test('sin copia, 502 o 503, y diez minutos sin llamar a MET por ese punto', async () => {
  const cache = cacheFalsa();
  const met = metFalso(
    new TypeError('sin red'),
    () => new Response('lento', { status: 429 }),
    ok(T0 + 60 * MIN),
  );
  const pedirA = (query, t) => responderMet(pedir(query), { cache, fetch: met.fetch, ahora: () => t });

  const sinRed = await pedirA('lat=10.00&lon=10.00', T0);
  assert.equal(sinRed.status, 502);
  const limitado = await pedirA('lat=11.00&lon=11.00', T0);
  assert.equal(limitado.status, 503);
  assert.equal(limitado.headers.get('Retry-After'), '600');
  assert.equal(met.llamadas.length, 2);

  // Mientras dura la marca, ni se le llama.
  const otraVez = await pedirA('lat=11.00&lon=11.00', T0 + 5 * MIN);
  assert.equal(otraVez.status, 503);
  assert.equal(met.llamadas.length, 2);

  // Después, sí.
  const luego = await pedirA('lat=11.00&lon=11.00', T0 + 11 * MIN);
  assert.equal(luego.status, 200);
  assert.equal(met.llamadas.length, 3);
});
