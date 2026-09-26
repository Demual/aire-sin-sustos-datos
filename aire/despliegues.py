"""Borra las publicaciones viejas del proyecto de Cloudflare Pages.

Cada día se sube la web entera, unos 280 MB, y Cloudflare guarda todas las
publicaciones. Basta con la que está en marcha y un par de las anteriores,
para volver atrás si una sale mal: los días pasados que usa la app van dentro
de cada publicación (`archivo/`), no hace falta guardar las viejas para eso.

    python -m aire.despliegues [--conservar 3]

Con CLOUDFLARE_API_TOKEN (permiso «Cloudflare Pages: Edit»),
CLOUDFLARE_ACCOUNT_ID y PROYECTO_PAGES, como la publicación.
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

from . import config as c

API = "https://api.cloudflare.com/client/v4"
CONSERVAR = 3


def a_borrar(despliegues: list[dict], vivo: str | None, conservar: int = CONSERVAR) -> list[str]:
    """Los ids que sobran: todos menos los [conservar] más recientes y el que
    está en marcha, que puede ser uno viejo si se ha vuelto atrás a mano."""
    orden = sorted(despliegues, key=lambda d: _fecha(d["created_on"]), reverse=True)
    return [d["id"] for d in orden[max(conservar, 1):] if d["id"] != vivo]


def _fecha(texto: str) -> dt.datetime:
    return dt.datetime.fromisoformat(texto.replace("Z", "+00:00"))


def _pedir(metodo: str, ruta: str, token: str) -> dict:
    peticion = urllib.request.Request(
        f"{API}{ruta}",
        method=metodo,
        headers={"Authorization": f"Bearer {token}", "User-Agent": c.AGENTE},
    )
    try:
        with urllib.request.urlopen(peticion, timeout=60) as r:
            respuesta = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{metodo} {ruta}: HTTP {e.code} {e.read()[:300]!r}") from None
    if not respuesta.get("success"):
        raise RuntimeError(f"{metodo} {ruta}: {respuesta.get('errors')}")
    return respuesta


def limpiar(cuenta: str, proyecto: str, token: str, conservar: int = CONSERVAR, pedir=_pedir) -> tuple[int, list[str], list[str]]:
    """Borra lo que sobra. Devuelve cuántas había, las borradas y las que no
    se han podido borrar (con el motivo)."""
    base = f"/accounts/{cuenta}/pages/projects/{proyecto}"
    vivo = (pedir("GET", base, token)["result"].get("canonical_deployment") or {}).get("id")
    despliegues, pagina = [], 1
    while True:
        r = pedir("GET", f"{base}/deployments?page={pagina}&per_page=25", token)
        despliegues += r["result"]
        if not r["result"] or pagina >= (r.get("result_info") or {}).get("total_pages", 1):
            break
        pagina += 1
    borradas, fallos = [], []
    for i in a_borrar(despliegues, vivo, conservar):
        try:
            pedir("DELETE", f"{base}/deployments/{i}?force=true", token)
            borradas.append(i)
        except RuntimeError as e:
            fallos.append(f"{i}: {e}")
    return len(despliegues), borradas, fallos


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aire.despliegues", description=__doc__.splitlines()[0])
    p.add_argument("--conservar", type=int, default=int(os.environ.get("CONSERVAR") or CONSERVAR),
                   help=f"publicaciones recientes que se guardan (por defecto, {CONSERVAR})")
    a = p.parse_args(argv)
    faltan = [v for v in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "PROYECTO_PAGES") if not os.environ.get(v)]
    if faltan:
        raise SystemExit(f"Faltan las variables {', '.join(faltan)}")
    total, borradas, fallos = limpiar(
        os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["PROYECTO_PAGES"],
        os.environ["CLOUDFLARE_API_TOKEN"], a.conservar,
    )
    print(f"{total} publicaciones; se conservan las {a.conservar} últimas y la que está en marcha.")
    print(f"Borradas: {len(borradas)}" + (f" ({', '.join(borradas)})" if borradas else ""))
    for f in fallos:
        print(f"No se pudo borrar {f}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
