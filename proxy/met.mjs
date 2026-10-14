// Servidor intermedio de la previsión del tiempo de MET Norway.
//
// Las condiciones de MET Norway (https://api.met.no/doc/TermsOfService) piden
// que las apps no llamen a api.met.no desde cada móvil, sino a través de un
// servidor propio que:
//   - se identifique con un User-Agent con contacto;
//   - guarde cada respuesta hasta su Expires y no vuelva a pedir antes;
//   - al volver a pedir, mande If-Modified-Since con el Last-Modified que
//     tiene (MET contesta 304 si no ha cambiado);
//   - frene en el acto si MET contesta 429 (demasiadas peticiones);
//   - no mande coordenadas con más de cuatro decimales.
// Auditoría legal del 14/10/2026 (A-R1). Así, además, MET Norway deja de ver
// la IP de cada usuario: solo ve la de Cloudflare.
//
// Lo usa functions/met/complete.js (Cloudflare Pages Functions, en la misma
// web que los cuadros del aire). Aquí no hay nada de Cloudflare: la caché, el
// fetch, el reloj y la IP llegan como parámetros, para poder probarlo con Node
// (proxy/met.test.mjs).
//
// La caché es la de cada centro de datos de Cloudflare, no una global (Cache
// API): cada centro (Madrid, París…) guarda su copia de cada punto y pide a
// MET la primera vez. El freno, los límites y las marcas también son de cada
// centro. Sigue cumpliendo las condiciones de MET; si algún día hiciera falta
// una caché común, serían Tiered Cache o KV.
//
// Lo que se guarda: la previsión de cada punto de la rejilla de 0,01°, que es
// la misma para todos, y, para frenar abusos, cuántas peticiones ha hecho cada
// IP en el último minuto. La IP no se guarda en claro: solo un resumen SHA-256
// con una sal aleatoria de cada centro y cada día, que no permite recuperarla
// y caduca a los dos minutos. Ni registros ni nada más.
//
// Peticiones simultáneas del mismo punto caducado: cada una puede llamar a
// MET (no se unen). Es poco frecuente y lo acotan el presupuesto por minuto y
// el freno; unirlas pediría estado compartido entre isolates.

export const MET_URL = 'https://api.met.no/weatherapi/locationforecast/2.0/complete';

// Quién pide, con contacto: JR Soft y las apps que usan este servidor (Aire
// sin sustos y, desde su próxima versión, Plic; decisión del Cuartel General
// del 14/10/2026). Si se suma otra app, se añade aquí.
export const AGENTE =
  'JRSoft/1.0 (+https://github.com/Demual/aire-sin-sustos-datos; demual@gmail.com) Aire sin sustos, Plic';

/**
 * Los límites (revisión de la 1.0.3, S1-S4). Se pueden cambiar en los tests.
 *
 * - esperaMetMs: lo que se espera a MET antes de darlo por caído (S1). La app
 *   corta a los 15 s; con 8 aún le llega la copia de reserva.
 * - copiaViejaMaxS: cuánto tiempo, desde la última vez que MET la confirmó, se
 *   sigue dando una copia caducada cuando MET falla (S4). Después, 503.
 * Los de S3 no pueden molestar nunca a un usuario normal (Cuartel General,
 * 14/10/2026). Solo cuentan las peticiones que obligan a llamar a MET: lo que
 * sale de la caché no gasta nada. Por encima, la copia que haya o un 503.
 * Las cifras, para miles de usuarios activos al día de Aire y Plic juntos:
 *   - Aire pide el tiempo al abrirse, una vez por lugar y por Expires (media
 *     hora o más): unas 6 llamadas a MET por usuario y día (un lugar y medio,
 *     tres o cuatro aperturas con el Expires ya pasado).
 *   - Plic, una vez al día en segundo plano y otra al abrirse: unas 2.
 *   - Con 10.000 usuarios activos al día en cada app, unas 80.000 llamadas a
 *     MET al día. Casi todo Aire cae en un mismo centro de datos (Madrid).
 * - metPorDia: presupuesto diario de llamadas a MET por centro de datos (día
 *   UTC). 150.000: casi el doble de lo de arriba en un solo centro. Hoy manda
 *   antes la cuota del plan gratuito (unas 100.000 invocaciones al día para
 *   toda la cuenta, también las que salen de la caché); este presupuesto
 *   importa si algún día se pasa al plan de pago. Es la cifra que hay que
 *   tocar si las apps crecen.
 * - metPorMinuto: freno de ráfagas por centro de datos. 600 (10 por
 *   segundo): la mitad del límite de MET para todo nuestro User-Agent (20
 *   por segundo), y unas 30 veces el pico normal de la mañana de arriba.
 * - ipPorMinuto: por IP. 300 (5 por segundo), del orden de cientos, porque
 *   detrás de una IP de operadora (CGNAT) puede haber miles de móviles; con
 *   la caché, un usuario normal hace llamar a MET una o dos veces por hora.
 *   Solo frena a quien barre coordenadas desde una IP.
 *
 * Lo que ninguno de los dos puede proteger es la cuota del plan gratuito
 * (unas 100.000 invocaciones al día para la cuenta): cuando la función cuenta,
 * ya se ha ejecutado. Eso solo se frena antes, con una regla de límite de
 * peticiones de Cloudflare, que en *.pages.dev no existe: hace falta un
 * dominio propio.
 */
export const LIMITES = {
  esperaMetMs: 8000,
  copiaViejaMaxS: 6 * 3600,
  metPorDia: 150_000,
  metPorMinuto: 600,
  ipPorMinuto: 300,
};

// La copia ya caducada se guarda un día más: para volver a pedir con
// If-Modified-Since y para tener algo que dar si MET no contesta.
const GUARDAR_DE_MAS_S = 24 * 3600;

// Si MET no manda Expires (no debería), media hora.
const CADUCIDAD_POR_DEFECTO_S = 30 * 60;

// Cuando MET falla, en este tiempo nadie vuelve a llamarle por ese punto: se
// da la última copia, o un 503 si no la hay. Con un 429 o un 403 se frena todo
// el centro de datos (S2), lo que diga su Retry-After y al menos esto. La app,
// por ahora, no hace caso de Retry-After y vuelve a pedir al abrirse: le
// contesta la marca, sin llamar a MET (A2, pendiente para la 1.0.4).
const REINTENTO_S = 10 * 60;

const RAIZ = 'https://aire-sin-sustos.pages.dev';

/** Grados con dos decimales (unos 1,1 km), o null si no vale. */
export function coordenada(texto, limite) {
  if (texto === null || texto === undefined || texto.trim() === '') return null;
  const n = Number(texto);
  if (!Number.isFinite(n) || Math.abs(n) > limite) return null;
  return (Math.round(n * 100) / 100 + 0).toFixed(2); // + 0: sin «-0.00»
}

/** La Cache API, sin que un fallo suyo acabe en un 500 (S6): sin caché, se sigue. */
function cacheSegura(cache) {
  return {
    async match(clave) {
      try {
        return await cache.match(clave);
      } catch {
        return undefined;
      }
    },
    async put(clave, respuesta) {
      try {
        await cache.put(clave, respuesta);
      } catch {
        // Sin caché: esta vez no se guarda.
      }
    },
  };
}

const clave = (ruta) => new Request(`${RAIZ}/met/${ruta}`);

function caducidad(respuesta, ahora) {
  const expira = Date.parse(respuesta.headers.get('Expires') ?? '');
  return Number.isFinite(expira) ? expira : ahora + CADUCIDAD_POR_DEFECTO_S * 1000;
}

/** Cuándo confirmó MET por última vez esta copia (ms). */
function comprobada(respuesta) {
  const t = Number(respuesta.headers.get('X-Aire-Comprobada'));
  return Number.isFinite(t) ? t : 0;
}

/** La copia que se guarda en la caché, con su tiempo de vida. */
function copia(cuerpo, cabeceras, ahora, { confirmada = ahora, caducada = false } = {}) {
  const expira = caducidad({ headers: cabeceras }, ahora);
  const vida = Math.max(0, Math.round((expira - ahora) / 1000)) + GUARDAR_DE_MAS_S;
  const h = new Headers({
    'Content-Type': cabeceras.get('Content-Type') ?? 'application/json',
    Expires: new Date(expira).toUTCString(),
    'Cache-Control': `public, max-age=${vida}`,
    'X-Aire-Comprobada': String(confirmada),
  });
  const modificado = cabeceras.get('Last-Modified');
  if (modificado) h.set('Last-Modified', modificado);
  if (caducada) h.set('X-Aire-Copia', 'caducada');
  return new Response(cuerpo, { status: 200, headers: h });
}

/** Una marca que vale hasta `hasta` (ms). */
function marca(hasta, ahora) {
  const vida = Math.max(1, Math.ceil((hasta - ahora) / 1000));
  return new Response('', {
    headers: { Expires: new Date(hasta).toUTCString(), 'Cache-Control': `public, max-age=${vida}` },
  });
}

async function marcaVigente(c, k, ahora) {
  const m = await c.match(k);
  const hasta = Date.parse(m?.headers.get('Expires') ?? '');
  return Number.isFinite(hasta) && ahora < hasta ? hasta : null;
}

/**
 * Suma uno al contador de este periodo (un minuto, o un día) y dice si aún cabía. Es aproximado: la
 * Cache API no tiene operaciones atómicas, y dos peticiones a la vez pueden
 * contar una sola vez. Para frenar un abuso basta.
 */
async function cabe(c, ruta, ahora, limite, periodoMs = 60000) {
  const periodo = Math.floor(ahora / periodoMs);
  const k = clave(`_cuenta/${ruta}/${periodo}`);
  const m = await c.match(k);
  const n = Number(m ? await m.text() : 0) || 0;
  if (n >= limite) return false;
  const vida = Math.ceil((2 * periodoMs) / 1000);
  await c.put(k, new Response(String(n + 1), { headers: { 'Cache-Control': `public, max-age=${vida}` } }));
  return true;
}

// Sal de reserva si la caché falla: la de este isolate.
let salDeReserva = null;

function aleatorio() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}

/** El resumen de la IP: SHA-256 con la sal del día de este centro de datos. */
async function resumenIp(c, ip, ahora) {
  const dia = Math.floor(ahora / 86400000);
  const k = clave(`_sal/${dia}`);
  const m = await c.match(k);
  let sal = m ? await m.text() : '';
  if (!sal) {
    sal = aleatorio();
    salDeReserva ??= sal;
    await c.put(k, new Response(sal, { headers: { 'Cache-Control': 'public, max-age=90000' } }));
  }
  const datos = new TextEncoder().encode(`${sal || salDeReserva}:${ip}`);
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', datos));
  return [...hash.slice(0, 12)].map((x) => x.toString(16).padStart(2, '0')).join('');
}

function texto(estado, mensaje, extra = {}) {
  return new Response(mensaje, {
    status: estado,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store', ...extra },
  });
}

function sinPrevision(estado, hasta, ahora) {
  const segundos = Math.max(1, Math.ceil(((hasta ?? ahora + REINTENTO_S * 1000) - ahora) / 1000));
  return texto(estado, 'MET Norway no ha contestado. Vuelve a intentarlo en un rato.', {
    'Retry-After': String(segundos),
  });
}

/** Retry-After de MET, en segundos o como fecha. */
function esperaPedida(r, ahora) {
  const v = r.headers.get('Retry-After');
  if (!v) return 0;
  const s = Number(v);
  if (Number.isFinite(s)) return s * 1000;
  const fecha = Date.parse(v);
  return Number.isFinite(fecha) ? fecha - ahora : 0;
}

/**
 * Contesta a la app con la previsión de MET Norway del punto pedido.
 *
 * @param {Request} request GET /met/complete?lat=..&lon=..
 * @param {{cache: {match: Function, put: Function}, fetch: Function, ahora: () => number,
 *          ip?: string|null, limites?: typeof LIMITES}} entorno
 */
export async function responderMet(request, { cache, fetch, ahora, ip = null, limites = LIMITES }) {
  const url = new URL(request.url);
  const lat = coordenada(url.searchParams.get('lat'), 90);
  const lon = coordenada(url.searchParams.get('lon'), 180);
  if (lat === null || lon === null) {
    return texto(400, 'Hacen falta lat y lon, en grados.');
  }

  const c = cacheSegura(cache);
  const t = ahora();

  const punto = `complete?lat=${lat}&lon=${lon}`;
  const kPunto = clave(punto);
  const kSinCopia = clave(`_sin-copia/${punto}`);
  const kFreno = clave('_freno');

  let guardada = await c.match(kPunto);
  // Una copia de reserva demasiado vieja ya no se da (S4).
  const sirve = (r) => r && t - comprobada(r) <= limites.copiaViejaMaxS * 1000;
  if (guardada && guardada.headers.get('X-Aire-Copia') && !sirve(guardada)) guardada = undefined;

  if (!guardada || t >= caducidad(guardada, t)) {
    // Sin MET: la copia que haya, si no es demasiado vieja, otros diez minutos;
    // si no, una marca para no volver a llamarle por este punto en ese rato.
    const sinMet = async (estado, hasta = null) => {
      if (sirve(guardada)) {
        const h = new Headers(guardada.headers);
        h.set('Expires', new Date(Math.max(hasta ?? 0, t + REINTENTO_S * 1000)).toUTCString());
        guardada = copia(await guardada.arrayBuffer(), h, t, {
          confirmada: comprobada(guardada),
          caducada: true,
        });
        await c.put(kPunto, guardada.clone());
        return null;
      }
      const fin = Math.max(hasta ?? 0, t + REINTENTO_S * 1000);
      await c.put(kSinCopia, marca(fin, t));
      return sinPrevision(estado, fin, t);
    };

    const frenoHasta = await marcaVigente(c, kFreno, t); // S2
    const sinCopiaHasta = await marcaVigente(c, kSinCopia, t);
    let respuesta = null;
    if (frenoHasta) {
      respuesta = await sinMet(503, frenoHasta);
    } else if (sinCopiaHasta && !sirve(guardada)) {
      respuesta = sinPrevision(503, sinCopiaHasta, t);
    } else if (ip && !(await cabe(c, `ip/${await resumenIp(c, ip, t)}`, t, limites.ipPorMinuto))) {
      // S3: esta IP ya ha hecho llamar a MET demasiadas veces este minuto.
      const finDelMinuto = (Math.floor(t / 60000) + 1) * 60000;
      respuesta = texto(429, 'Demasiadas peticiones. Vuelve a intentarlo en un minuto.', {
        'Retry-After': String(Math.ceil((finDelMinuto - t) / 1000)),
      });
    } else if (
      !(await cabe(c, 'met', t, limites.metPorMinuto)) ||
      !(await cabe(c, 'met-dia', t, limites.metPorDia, 86400000))
    ) {
      respuesta = await sinMet(503); // S3: presupuesto de llamadas a MET
    } else {
      const cabeceras = { 'User-Agent': AGENTE, Accept: 'application/json' };
      const modificado = guardada?.headers.get('Last-Modified');
      if (modificado) cabeceras['If-Modified-Since'] = modificado;

      let r = null;
      let cuerpo = null;
      try {
        r = await fetch(`${MET_URL}?lat=${lat}&lon=${lon}`, {
          headers: cabeceras,
          signal: AbortSignal.timeout(limites.esperaMetMs), // S1
        });
        if (r.status === 200 || r.status === 203) cuerpo = await r.arrayBuffer();
      } catch {
        r = null; // sin red, tiempo agotado o cuerpo cortado: como un error suyo
      }

      if (r && cuerpo !== null) {
        // 203: la versión de la API va a dejar de existir, pero los datos valen.
        guardada = copia(cuerpo, r.headers, t);
        await c.put(kPunto, guardada.clone());
      } else if (r && r.status === 304 && guardada) {
        // No ha cambiado: la misma previsión, con el Expires nuevo del 304.
        const h = new Headers(guardada.headers);
        const expira = r.headers.get('Expires');
        if (expira) h.set('Expires', expira);
        const nuevoModificado = r.headers.get('Last-Modified');
        if (nuevoModificado) h.set('Last-Modified', nuevoModificado);
        guardada = copia(await guardada.arrayBuffer(), h, t);
        await c.put(kPunto, guardada.clone());
      } else if (r && (r.status === 429 || r.status === 403)) {
        // MET nos frena (o nos bloquea): todo el centro de datos para (S2).
        const fin = t + Math.max(REINTENTO_S * 1000, esperaPedida(r, t));
        await c.put(kFreno, marca(fin, t));
        respuesta = await sinMet(503, fin);
      } else {
        respuesta = await sinMet(502);
      }
    }
    if (respuesta) return respuesta;
  }

  // A la app: Expires y Last-Modified, para que no vuelva a pedir antes de
  // tiempo y mande su If-Modified-Since, y la hora de la previsión (S4).
  const h = new Headers({
    'Content-Type': guardada.headers.get('Content-Type') ?? 'application/json',
    'Cache-Control': 'no-cache',
  });
  for (const nombre of ['Expires', 'Last-Modified', 'X-Aire-Copia']) {
    const valor = guardada.headers.get(nombre);
    if (valor) h.set(nombre, valor);
  }
  const hora = Date.parse(guardada.headers.get('Last-Modified') ?? '');
  h.set('X-Aire-Prevision', new Date(Number.isFinite(hora) ? hora : comprobada(guardada) || t).toISOString());

  const suya = Date.parse(request.headers.get('If-Modified-Since') ?? '');
  if (Number.isFinite(suya) && Number.isFinite(hora) && suya >= hora) {
    return new Response(null, { status: 304, headers: h });
  }
  return new Response(guardada.body, { status: 200, headers: h });
}
