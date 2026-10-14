// Servidor intermedio de la previsión del tiempo de MET Norway.
//
// Las condiciones de MET Norway (https://api.met.no/doc/TermsOfService) piden
// que las apps no llamen a api.met.no desde cada móvil, sino a través de un
// servidor propio que:
//   - se identifique con un User-Agent con contacto;
//   - guarde cada respuesta hasta su Expires y no vuelva a pedir antes;
//   - al volver a pedir, mande If-Modified-Since con el Last-Modified que
//     tiene (MET contesta 304 si no ha cambiado);
//   - no mande coordenadas con más de cuatro decimales.
// Auditoría legal del 14/10/2026 (A-R1). Así, además, MET Norway deja de ver
// la IP de cada usuario: solo ve la de Cloudflare.
//
// Lo usa functions/met/complete.js (Cloudflare Pages Functions, en la misma
// web que los cuadros del aire). Aquí no hay nada de Cloudflare: la caché, el
// fetch y el reloj llegan como parámetros, para poder probarlo con Node
// (proxy/met.test.mjs).
//
// No se guarda nada de nadie: ni registros ni IP. Lo único que se guarda es la
// previsión de cada punto de la rejilla de 0,01°, que es la misma para todos.

export const MET_URL = 'https://api.met.no/weatherapi/locationforecast/2.0/complete';

export const AGENTE =
  'AireSinSustos/1.0 (+https://github.com/Demual/aire-sin-sustos-datos; demual@gmail.com)';

// La copia ya caducada se guarda un día más: para volver a pedir con
// If-Modified-Since y para tener algo que dar si MET no contesta.
const GUARDAR_DE_MAS_S = 24 * 3600;

// Si MET no manda Expires (no debería), media hora.
const CADUCIDAD_POR_DEFECTO_S = 30 * 60;

// Cuánto se da por buena una copia caducada cuando MET falla: la app no
// vuelve a pedir hasta entonces.
const REINTENTO_S = 10 * 60;

/** Grados con dos decimales (unos 1,1 km), o null si no vale. */
export function coordenada(texto, limite) {
  if (texto === null || texto === undefined || texto.trim() === '') return null;
  const n = Number(texto);
  if (!Number.isFinite(n) || Math.abs(n) > limite) return null;
  return (Math.round(n * 100) / 100 + 0).toFixed(2); // + 0: sin «-0.00»
}

function caducidad(respuesta, ahora) {
  const expira = Date.parse(respuesta.headers.get('Expires') ?? '');
  return Number.isFinite(expira) ? expira : ahora + CADUCIDAD_POR_DEFECTO_S * 1000;
}

/** La copia que se guarda en la caché, con su tiempo de vida. */
function copia(cuerpo, cabecerasMet, ahora) {
  const expira = caducidad({ headers: cabecerasMet }, ahora);
  const vida = Math.max(0, Math.round((expira - ahora) / 1000)) + GUARDAR_DE_MAS_S;
  const h = new Headers({
    'Content-Type': cabecerasMet.get('Content-Type') ?? 'application/json',
    Expires: new Date(expira).toUTCString(),
    'Cache-Control': `public, max-age=${vida}`,
  });
  const modificado = cabecerasMet.get('Last-Modified');
  if (modificado) h.set('Last-Modified', modificado);
  return new Response(cuerpo, { status: 200, headers: h });
}

function texto(estado, mensaje, extra = {}) {
  return new Response(mensaje, {
    status: estado,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store', ...extra },
  });
}

/**
 * Contesta a la app con la previsión de MET Norway del punto pedido.
 *
 * @param {Request} request GET /met/complete?lat=..&lon=..
 * @param {{cache: {match: Function, put: Function}, fetch: Function, ahora: () => number}} entorno
 */
export async function responderMet(request, { cache, fetch, ahora }) {
  const url = new URL(request.url);
  const lat = coordenada(url.searchParams.get('lat'), 90);
  const lon = coordenada(url.searchParams.get('lon'), 180);
  if (lat === null || lon === null) {
    return texto(400, 'Hacen falta lat y lon, en grados.');
  }

  const t = ahora();
  const clave = new Request(new URL(`/met/complete?lat=${lat}&lon=${lon}`, url).toString());
  let guardada = await cache.match(clave);

  if (!guardada || t >= caducidad(guardada, t)) {
    const cabeceras = { 'User-Agent': AGENTE, Accept: 'application/json' };
    const modificado = guardada?.headers.get('Last-Modified');
    if (modificado) cabeceras['If-Modified-Since'] = modificado;

    let r = null;
    try {
      r = await fetch(`${MET_URL}?lat=${lat}&lon=${lon}`, { headers: cabeceras });
    } catch {
      r = null; // sin red hacia MET: como un error suyo
    }

    if (r && (r.status === 200 || r.status === 203)) {
      // 203: la versión de la API va a dejar de existir, pero los datos valen.
      guardada = copia(await r.arrayBuffer(), r.headers, t);
      await cache.put(clave, guardada.clone());
    } else if (r && r.status === 304 && guardada) {
      // No ha cambiado: la misma previsión, con el Expires nuevo del 304.
      const h = new Headers(guardada.headers);
      const expira = r.headers.get('Expires');
      if (expira) h.set('Expires', expira);
      const nuevoModificado = r.headers.get('Last-Modified');
      if (nuevoModificado) h.set('Last-Modified', nuevoModificado);
      guardada = copia(await guardada.arrayBuffer(), h, t);
      await cache.put(clave, guardada.clone());
    } else if (guardada) {
      // MET falla (429, 5xx, sin red): la última buena, un rato.
      const h = new Headers(guardada.headers);
      h.set('Expires', new Date(t + REINTENTO_S * 1000).toUTCString());
      h.set('X-Aire-Copia', 'caducada');
      guardada = new Response(guardada.body, { status: 200, headers: h });
    } else {
      return texto(
        r?.status === 429 ? 503 : 502,
        'MET Norway no ha contestado. Vuelve a intentarlo en un rato.',
        { 'Retry-After': String(REINTENTO_S) },
      );
    }
  }

  // A la app: Expires y Last-Modified, para que no vuelva a pedir antes de
  // tiempo y mande su If-Modified-Since.
  const h = new Headers({
    'Content-Type': guardada.headers.get('Content-Type') ?? 'application/json',
    'Cache-Control': 'no-cache',
  });
  for (const nombre of ['Expires', 'Last-Modified', 'X-Aire-Copia']) {
    const valor = guardada.headers.get(nombre);
    if (valor) h.set(nombre, valor);
  }
  const suya = Date.parse(request.headers.get('If-Modified-Since') ?? '');
  const nuestra = Date.parse(guardada.headers.get('Last-Modified') ?? '');
  if (Number.isFinite(suya) && Number.isFinite(nuestra) && suya >= nuestra) {
    return new Response(null, { status: 304, headers: h });
  }
  return new Response(guardada.body, { status: 200, headers: h });
}
