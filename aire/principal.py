"""La tarea del día: bajar, calcular y escribir el sitio.

    python -m aire                          # hoy, a ./sitio
    python -m aire --fecha 2026-09-25 --datos descargas --archivo sitio_viejo/archivo

`--archivo` es de dónde salen las medias de los días pasados: una carpeta o la
web publicada (por defecto, la variable SITIO_URL).
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import archivo, config as c, cuadros, descarga, lectura

_BASE = pathlib.Path(__file__).resolve().parent.parent / "sitio_base"


def _ya_publicado(dia: dt.date, sitio: str | None) -> bool:
    """Si la web ya tiene la pasada de hoy (para la ejecución de repuesto)."""
    if not sitio:
        return False
    url = f"{sitio.rstrip('/')}/v{c.VERSION}/info.json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": c.AGENTE}), timeout=30) as r:
            return json.load(r).get("run", "").startswith(dia.isoformat())
    except Exception:
        return False


def _escribir_json(ruta: pathlib.Path, datos: dict) -> None:
    ruta.write_text(json.dumps(datos, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def generar(dia: dt.date, salida: pathlib.Path, datos: pathlib.Path, fuente_archivo: str | None) -> dict:
    t0 = time.time()
    print(f"Descargando la pasada del {dia}…", flush=True)
    rutas = descarga.bajar(dia, datos)
    print(f"Leyendo ({time.time() - t0:.0f} s)…", flush=True)
    horario, pasada_global = lectura.todo(rutas, dia)

    print("Días pasados…", flush=True)
    dias = [dia - dt.timedelta(days=n) for n in range(c.DIAS_PASADOS, 0, -1)]
    crudos = {d: b for d in dias if (b := archivo.leer(d, fuente_archivo)) is not None}
    inicio_pasado, diario = archivo.pasados(dia, crudos)
    hoy = archivo.a_bytes(archivo.medias_del_dia(horario))
    print(f"  {len(crudos)} de {len(dias)} días en el archivo", flush=True)

    print("Cuadros…", flush=True)
    lista = cuadros.con_tierra()
    husos = cuadros.husos(lista)

    if salida.exists():
        shutil.rmtree(salida)
    carpeta = salida / f"v{c.VERSION}"
    carpeta.mkdir(parents=True)
    (salida / "archivo").mkdir()
    for d, b in crudos.items():
        (salida / "archivo" / archivo.nombre(d)).write_bytes(b)
    (salida / "archivo" / archivo.nombre(dia)).write_bytes(hoy)

    ahora = dt.datetime.now(dt.timezone.utc)
    meta = {
        "run": f"{dia.isoformat()}T00:00Z",
        "generated": ahora.strftime("%Y-%m-%dT%H:%MZ"),
        "dailyStart": inicio_pasado.isoformat(),
        "days": c.DIAS_PASADOS if diario else 0,
    }

    def uno(clave):
        ti, tj = clave
        _escribir_json(carpeta / cuadros.nombre(ti, tj), cuadros.contenido(ti, tj, horario, diario, husos[clave], meta))

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(uno, lista))

    info = {
        "version": c.VERSION,
        "run": meta["run"],
        "globalRun": pasada_global,
        "generated": meta["generated"],
        "tile": c.PASO * c.CUADRO,
        "step": c.PASO,
        "tiles": len(lista),
        "hours": c.HORAS,
        "days": meta["days"],
        "scales": c.ESCALAS,
        "attribution": c.ATRIBUCION.format(año=dia.year),
        "license": c.LICENCIA,
    }
    _escribir_json(carpeta / "info.json", info)
    for f in _BASE.iterdir():
        shutil.copy(f, salida / f.name)
    tam = sum(f.stat().st_size for f in carpeta.iterdir())
    print(f"Hecho en {time.time() - t0:.0f} s: {len(lista)} cuadros, {tam / 1e6:.0f} MB", flush=True)
    return info


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aire", description=__doc__.splitlines()[0])
    p.add_argument("--fecha", type=dt.date.fromisoformat, help="día de la pasada (por defecto, hoy en UTC)")
    p.add_argument("--salida", type=pathlib.Path, default=pathlib.Path("sitio"))
    p.add_argument("--datos", type=pathlib.Path, help="carpeta para las descargas (por defecto, una temporal)")
    p.add_argument("--archivo", default=os.environ.get("SITIO_URL"), help="carpeta o web con los días pasados")
    p.add_argument("--si-falta", action="store_true", help="no hacer nada si la web ya tiene la pasada del día")
    a = p.parse_args(argv)

    dia = a.fecha or dt.datetime.now(dt.timezone.utc).date()
    if a.si_falta and _ya_publicado(dia, os.environ.get("SITIO_URL")):
        print(f"La web ya tiene la pasada del {dia}.")
        _salida_action("publicar", "false")
        return 0
    if a.datos:
        generar(dia, a.salida, a.datos, a.archivo)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            generar(dia, a.salida, pathlib.Path(tmp), a.archivo)
    _salida_action("publicar", "true")
    return 0


def _salida_action(nombre: str, valor: str) -> None:
    """Deja un valor para los pasos siguientes de la GitHub Action."""
    if f := os.environ.get("GITHUB_OUTPUT"):
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"{nombre}={valor}\n")


if __name__ == "__main__":
    sys.exit(main())
