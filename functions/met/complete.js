// GET /met/complete?lat=..&lon=.. → la previsión de MET Norway de ese punto,
// a través de nuestro servidor (ver proxy/met.mjs).
//
// Es una función de Cloudflare Pages: se publica con la web de datos, en el
// mismo `wrangler pages deploy` de la tarea diaria, y solo se ejecuta en esta
// ruta; los cuadros del aire siguen siendo ficheros estáticos. La caché es la
// de Cloudflare (Cache API) de cada centro de datos, que en las funciones de
// Pages funciona también en *.pages.dev.
import { responderMet } from '../../proxy/met.mjs';

export function onRequestGet(context) {
  return responderMet(context.request, {
    cache: caches.default,
    fetch: (url, opciones) => fetch(url, opciones),
    ahora: () => Date.now(),
    // Solo para el límite por IP, y nunca en claro (ver proxy/met.mjs).
    ip: context.request.headers.get('CF-Connecting-IP'),
  });
}
