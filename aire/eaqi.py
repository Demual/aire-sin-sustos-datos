"""Índice europeo de calidad del aire, en la escala de 0 a 100 que usaba Open-Meteo.

La Agencia Europea de Medio Ambiente da seis categorías por contaminante,
con los tramos revisados en 2024. Open-Meteo las convertía en una escala
continua (0-20 bueno, 20-40 aceptable, 40-60 moderado, 60-80 malo, 80-100
muy malo, más de 100 extremadamente malo) interpolando dentro de cada tramo,
con concentraciones horarias, y el índice de la hora es el peor de los cinco.
La app está hecha para esa escala (`AirQualityScale` en `allergen.dart`), así
que se calcula igual.
"""

import numpy as np

# Límites de cada categoría en µg/m³: bueno, aceptable, moderado, malo, muy malo.
# Por encima del último, extremadamente malo.
TRAMOS = {
    "pm2_5": (0, 5, 15, 50, 90, 140),
    "pm10": (0, 15, 45, 120, 195, 270),
    "nitrogen_dioxide": (0, 10, 25, 60, 100, 150),
    "ozone": (0, 60, 100, 120, 160, 180),
    "sulphur_dioxide": (0, 20, 40, 125, 190, 275),
}
_ESCALA = (0, 20, 40, 60, 80, 100)


def subindice(concentracion: np.ndarray, tramos: tuple) -> np.ndarray:
    """Índice de un contaminante. Por encima del último tramo sigue la recta
    del tramo «muy malo», así que pasa de 100. Los huecos siguen siendo huecos."""
    c = np.asarray(concentracion, dtype=np.float32)
    dentro = np.interp(np.clip(c, 0, None), tramos, _ESCALA)
    pendiente = (_ESCALA[-1] - _ESCALA[-2]) / (tramos[-1] - tramos[-2])
    encima = _ESCALA[-1] + (c - tramos[-1]) * pendiente
    r = np.where(c > tramos[-1], encima, dentro)
    return np.where(np.isnan(c), np.nan, r).astype(np.float32)


def indice(concentraciones: dict) -> np.ndarray:
    """El peor subíndice de los contaminantes que haya. Si falta uno, se usa
    el resto; si faltan todos, no hay índice."""
    subs = [subindice(concentraciones[k], t) for k, t in TRAMOS.items() if k in concentraciones]
    if not subs:
        raise ValueError("sin contaminantes para el índice")
    pila = np.stack(subs)
    todos_nan = np.all(np.isnan(pila), axis=0)
    maximo = np.max(np.where(np.isnan(pila), -np.inf, pila), axis=0)
    return np.where(todos_nan, np.nan, maximo).astype(np.float32)
