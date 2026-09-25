"""Las medias de los días pasados.

La app pide los últimos siete días para que el diario pueda guardar el nivel
de un día que se apunta con retraso. Cada pasada solo trae el futuro, así que
cada día se guarda la media de sus primeras 24 horas en un fichero
`archivo/AAAA-MM-DD.npz`, que se publica con lo demás. Al día siguiente se
recogen de la web los de la semana anterior: el sitio publicado es el
almacén, y no hace falta ningún otro.

Las medias son de días UTC. Para el diario da igual: en España el día local
empieza una o dos horas antes y la media de 24 horas apenas cambia.
"""

import datetime as dt
import io
import pathlib
import urllib.error
import urllib.request

import numpy as np

from . import config as c

_HUECO = -1


def medias_del_dia(horario: dict) -> dict:
    """Media de las horas 0 a 23 de cada celda, si al menos 12 tienen dato."""
    medias = {}
    for v, datos in horario.items():
        dia = datos[:24]
        n = np.sum(~np.isnan(dia), axis=0)
        suma = np.nansum(dia, axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            medias[v] = np.where(n >= 12, suma / n, np.nan).astype(np.float32)
    return medias


def a_bytes(medias: dict) -> bytes:
    """Enteros con la escala de cada variable y -1 en los huecos: se comprimen
    mucho mejor que los decimales."""
    enteros = {
        v: np.where(np.isnan(m), _HUECO, np.round(np.nan_to_num(m) * c.ESCALAS[v])).astype(np.int32)
        for v, m in medias.items()
    }
    buf = io.BytesIO()
    np.savez_compressed(buf, **enteros)
    return buf.getvalue()


def de_bytes(datos: bytes) -> dict:
    with np.load(io.BytesIO(datos)) as npz:
        return {
            v: np.where(npz[v] == _HUECO, np.nan, npz[v] / c.ESCALAS[v]).astype(np.float32)
            for v in npz.files
            if v in c.ESCALAS
        }


def nombre(dia: dt.date) -> str:
    return f"{dia.isoformat()}.npz"


def leer(dia: dt.date, fuente: str | None) -> bytes | None:
    """El fichero de un día, de una carpeta o de la web publicada. None si no está."""
    if not fuente:
        return None
    if fuente.startswith(("http://", "https://")):
        url = f"{fuente.rstrip('/')}/archivo/{nombre(dia)}"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    ruta = pathlib.Path(fuente) / nombre(dia)
    return ruta.read_bytes() if ruta.exists() else None


def pasados(hoy: dt.date, crudos: dict[dt.date, bytes]) -> tuple[dt.date, dict]:
    """Los días anteriores a hoy apilados: [día, fila, columna] por variable.

    Un día que falta queda en blanco; las variables que no estén en ningún
    día no se devuelven."""
    dias = [hoy - dt.timedelta(days=n) for n in range(c.DIAS_PASADOS, 0, -1)]
    leidos = {d: de_bytes(crudos[d]) for d in dias if d in crudos}
    pilas = {}
    for v in c.ESCALAS:
        if not any(v in m for m in leidos.values()):
            continue
        vacio = np.full((c.FILAS, c.COLUMNAS), np.nan, dtype=np.float32)
        pilas[v] = np.stack([leidos.get(d, {}).get(v, vacio) for d in dias])
    return dias[0], pilas
