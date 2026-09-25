"""Los cuadros que se publican: cuáles, en qué huso está cada celda y su fichero."""

from collections import Counter

import numpy as np
from global_land_mask import globe
from timezonefinder import TimezoneFinder

from . import config as c

_LADO = c.PASO * c.CUADRO


def centros() -> tuple[np.ndarray, np.ndarray]:
    """Latitudes (de sur a norte) y longitudes (de oeste a este) de las celdas."""
    lat = np.round(c.SUR + c.PASO * (np.arange(c.FILAS) + 0.5), 2)
    lon = np.round(c.OESTE + c.PASO * (np.arange(c.COLUMNAS) + 0.5), 2)
    return lat, lon


def esquina(ti: int, tj: int) -> tuple[float, float]:
    """Esquina suroeste del cuadro."""
    return round(c.SUR + ti * _LADO, 1), round(c.OESTE + tj * _LADO, 1)


def nombre(ti: int, tj: int) -> str:
    """«37.0_-4.0.json»: la app lo calcula redondeando hacia abajo a medio grado."""
    lat0, lon0 = esquina(ti, tj)
    return f"{lat0:.1f}_{lon0:.1f}.json"


def _con_datos(ti: int, tj: int) -> bool:
    """Por debajo de 30° N solo hay datos en la caja de Canarias."""
    if ti * c.CUADRO >= c.FILA_EUROPA:
        return True
    _, lon0 = esquina(ti, tj)
    return c.CANARIAS["oeste"] <= lon0 < c.CANARIAS["este"]


def con_tierra(fino: float = 0.02) -> dict[tuple[int, int], tuple[float, float]]:
    """Cuadros con algo de tierra, con un punto de tierra de cada uno.

    Se mira una malla de 0,02° (unos 2 km) y no solo los centros de las
    celdas, para no perder islas pequeñas ni cabos."""
    sub = np.arange(fino / 2, _LADO, fino)
    dy, dx = np.meshgrid(sub, sub, indexing="ij")
    cuadros = {}
    for ti in range(c.FILAS // c.CUADRO):
        for tj in range(c.COLUMNAS // c.CUADRO):
            if not _con_datos(ti, tj):
                continue
            lat0, lon0 = esquina(ti, tj)
            la, lo = (lat0 + dy).ravel(), (lon0 + dx).ravel()
            tierra = globe.is_land(la, lo)
            if tierra.any():
                i = int(np.argmax(tierra))
                cuadros[(ti, tj)] = (float(la[i]), float(lo[i]))
    return cuadros


def husos(cuadros: dict[tuple[int, int], tuple[float, float]]) -> dict[tuple[int, int], list[str]]:
    """Huso horario de cada celda de cada cuadro.

    En el mar, timezonefinder da husos náuticos («Etc/GMT+1»). Esas celdas
    toman el huso de tierra más repetido del cuadro: quien está en la costa
    de Huelva cae a veces en una celda que es casi toda agua."""
    tf = TimezoneFinder()
    lat, lon = centros()
    res = {}
    for (ti, tj), (la_tierra, lo_tierra) in cuadros.items():
        zonas = [
            tf.timezone_at(lat=float(lat[f]), lng=float(lon[k]))
            for f in range(ti * c.CUADRO, (ti + 1) * c.CUADRO)
            for k in range(tj * c.CUADRO, (tj + 1) * c.CUADRO)
        ]
        tierra = [z for z in zonas if z and not z.startswith("Etc/")]
        comun = (
            Counter(tierra).most_common(1)[0][0]
            if tierra
            else tf.timezone_at(lat=la_tierra, lng=lo_tierra)
        )
        res[(ti, tj)] = [z if z and not z.startswith("Etc/") else comun for z in zonas]
    return res


def codificar(serie: np.ndarray, escala: int):
    """Serie de una celda: nada si no hay datos, un número si no cambia, o la
    lista con huecos. Las series constantes son casi todas ceros de polen
    fuera de temporada, y así el fichero no carga con 97 ceros por celda."""
    validos = ~np.isnan(serie)
    if not validos.any():
        return None
    q = np.round(np.where(validos, serie, 0).astype(np.float64) * escala).astype(np.int64)
    if validos.all() and (q == q[0]).all():
        return int(q[0])
    return [int(v) if ok else None for v, ok in zip(q, validos)]


def contenido(ti: int, tj: int, horario: dict, diario: dict, zonas: list[str], meta: dict) -> dict:
    """El JSON de un cuadro.

    `horario[v]` es [hora, fila, columna] y `diario[v]`, [día, fila, columna].
    Las celdas van de suroeste a noreste, fila a fila: la celda de (lat, lon)
    es `fila * cols + col`, con fila = (lat - lat0) / 0,1 hacia abajo."""
    f0, k0 = ti * c.CUADRO, tj * c.CUADRO
    lat0, lon0 = esquina(ti, tj)

    def bloque(datos: dict) -> dict:
        salida = {}
        for v, escala in c.ESCALAS.items():
            if v not in datos:
                continue
            trozo = datos[v][:, f0 : f0 + c.CUADRO, k0 : k0 + c.CUADRO]
            celdas = [codificar(trozo[:, i, j], escala) for i in range(c.CUADRO) for j in range(c.CUADRO)]
            # Una variable sin ningún dato en el cuadro (el polen en Canarias)
            # no se escribe: para la app, «no hay modelo aquí».
            if any(x is not None for x in celdas):
                salida[v] = {"scale": escala, "cells": celdas}
        return salida

    return {
        "version": c.VERSION,
        "run": meta["run"],
        "generated": meta["generated"],
        "lat0": lat0,
        "lon0": lon0,
        "step": c.PASO,
        "rows": c.CUADRO,
        "cols": c.CUADRO,
        "tz": zonas if len(set(zonas)) > 1 else zonas[:1],
        "start": meta["run"],
        "hours": c.HORAS,
        "hourly": bloque(horario),
        "dailyStart": meta["dailyStart"],
        "days": meta["days"],
        "daily": bloque(diario) if meta["days"] else {},
    }
