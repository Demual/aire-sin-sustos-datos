"""Lo descargado, a la rejilla común: [hora, fila, columna] en float32.

Unidades de salida: granos/m³ el polen, µg/m³ el polvo, las partículas y los
gases, y sin unidad el espesor óptico. Los huecos son NaN.
"""

import datetime as dt
import pathlib
import zipfile

import numpy as np
import xarray as xr

from . import config as c, eaqi
from .cuadros import centros

# Nombres dentro del NetCDF del conjunto europeo → nombres de salida.
_EUROPA_NC = {
    "apg_conc": "alder_pollen",
    "bpg_conc": "birch_pollen",
    "gpg_conc": "grass_pollen",
    "mpg_conc": "mugwort_pollen",
    "opg_conc": "olive_pollen",
    "rwpg_conc": "ragweed_pollen",
    "dust": "dust",
    "pm2p5_conc": "pm2_5",
    "pm10_conc": "pm10",
    "o3_conc": "ozone",
    "no2_conc": "nitrogen_dioxide",
    "so2_conc": "sulphur_dioxide",
}
_GASES = ("ozone", "nitrogen_dioxide", "sulphur_dioxide")
_R_SECO = 287.05  # J/(kg·K)
_KG_A_UG = 1e9


def _abrir(ruta: pathlib.Path) -> dict[str, xr.Dataset]:
    """Cada NetCDF del zip, por su nombre."""
    carpeta = ruta.with_suffix("")
    with zipfile.ZipFile(ruta) as z:
        z.extractall(carpeta)
        nombres = z.namelist()
    return {n: xr.open_dataset(carpeta / n) for n in nombres}


def _ordenar(ds: xr.Dataset) -> xr.Dataset:
    """Longitudes de −180 a 180 (el global a veces las da de 0 a 360) y todo
    de sur a norte y de oeste a este, como la rejilla.

    Se redondean: 340,8 pasado a −180…180 da −19,199999999999989, y xarray
    no lo empareja con el −19,2 del otro fichero al multiplicarlos."""
    lon = np.round(((ds.longitude.values + 180) % 360) - 180, 4)
    ds = ds.assign_coords(longitude=lon, latitude=np.round(ds.latitude.values, 4))
    return ds.sortby("longitude").sortby("latitude")


def _vacio() -> np.ndarray:
    return np.full((c.HORAS, c.FILAS, c.COLUMNAS), np.nan, dtype=np.float32)


def europa(ruta: pathlib.Path) -> dict[str, np.ndarray]:
    """El modelo europeo, que ya viene en la rejilla de 0,1°: solo se coloca."""
    (ds,) = _abrir(ruta).values()
    ds = _ordenar(ds)
    lat, lon = centros()
    if not (np.allclose(ds.latitude, lat[c.FILA_EUROPA:], atol=1e-3) and np.allclose(ds.longitude, lon, atol=1e-3)):
        raise ValueError("la rejilla del modelo europeo no es la esperada")
    horas = ds["time"].values.astype(float)
    if len(horas) != c.HORAS or horas[0] != 0 or horas[-1] != c.HORAS - 1:
        raise ValueError(f"horas inesperadas en el modelo europeo: {horas[:3]}…{horas[-3:]}")
    faltan = set(_EUROPA_NC) - set(ds.data_vars)
    if faltan:
        raise ValueError(f"faltan variables en el modelo europeo: {sorted(faltan)}")
    salida = {}
    for nc, nombre in _EUROPA_NC.items():
        datos = ds[nc].squeeze("level", drop=True).transpose("time", "latitude", "longitude").values
        todo = _vacio()
        # Los modelos dan a veces negativos minúsculos: son ceros.
        todo[:, c.FILA_EUROPA:, :] = np.clip(datos, 0, None)
        salida[nombre] = todo
    return salida


def _global(ds: xr.Dataset, inicio: dt.datetime) -> xr.Dataset:
    """Quita las dimensiones sobrantes del global y comprueba sus horas."""
    ds = _ordenar(ds).squeeze("forecast_reference_time", drop=False)
    if "model_level" in ds.dims:
        ds = ds.squeeze("model_level", drop=True)
    validas = ds["valid_time"].values
    esperadas = np.array([np.datetime64(inicio.replace(tzinfo=None)) + np.timedelta64(h, "h") for h in range(c.HORAS)])
    if len(validas) != c.HORAS or not (validas == esperadas).all():
        raise ValueError(f"horas inesperadas en el global: {validas[:2]}…")
    return ds.transpose("forecast_period", "latitude", "longitude")


def _repartir(da: xr.DataArray, filas: slice, columnas: slice) -> np.ndarray:
    """Del global (0,4°) a las celdas de 0,1°: el punto del modelo más cercano.

    Sin interpolar, como Open-Meteo: en la costa, promediar con puntos de mar
    borraba media calima (Las Palmas, 27/09/2026: 23 µg/m³ en el punto más
    cercano y 10 interpolando)."""
    lat, lon = centros()
    try:
        r = da.sel(latitude=lat[filas], longitude=lon[columnas], method="nearest", tolerance=0.25)
    except KeyError as e:
        raise ValueError(f"{da.name}: el área descargada no cubre las celdas") from e
    return r.values.astype(np.float32)


def espesor_optico(ruta: pathlib.Path, inicio: dt.datetime) -> tuple[np.ndarray, str]:
    """La turbidez del cielo, y de qué pasada global sale."""
    (ds,) = _abrir(ruta).values()
    ds = _global(ds, inicio)
    todo = _vacio()
    todo[:] = np.clip(_repartir(ds["aod550"], slice(None), slice(None)), 0, None)
    pasada = np.datetime_as_string(ds["forecast_reference_time"].values, unit="m")
    return todo, f"{pasada}Z"


def _celdas_canarias() -> tuple[slice, slice]:
    lat, lon = centros()
    k = np.where((lon > c.CANARIAS["oeste"]) & (lon < c.CANARIAS["este"]))[0]
    return slice(0, c.FILA_EUROPA), slice(int(k[0]), int(k[-1]) + 1)


def canarias(ruta: pathlib.Path, inicio: dt.datetime) -> dict[str, np.ndarray]:
    """Polvo, partículas y gases a ras de suelo del modelo global.

    El polvo y los gases vienen como proporción de masa (kg por kg de aire) en
    el nivel 137 del modelo, el más bajo, a unos 10 m. Se pasan a µg/m³ con
    la densidad del aire: presión en superficie / (R · temperatura del nivel).
    El polvo es la suma de sus tres tamaños. Sale un 10 % por encima del de
    Open-Meteo, que no dice cómo lo calcula; las partículas coinciden."""
    ficheros = _abrir(ruta)
    sup = _global(next(d for n, d in ficheros.items() if "sfc" in n), inicio)
    niv = _global(next(d for n, d in ficheros.items() if "mlev" in n), inicio)
    densidad = sup["sp"] / (_R_SECO * niv["t"])
    magnitudes = {
        "dust": (niv["aermr04"] + niv["aermr05"] + niv["aermr06"]) * densidad,
        "pm2_5": sup["pm2p5"],
        "pm10": sup["pm10"],
        "ozone": niv["go3"] * densidad,
        "nitrogen_dioxide": niv["no2"] * densidad,
        "sulphur_dioxide": niv["so2"] * densidad,
    }
    filas, columnas = _celdas_canarias()
    salida = {}
    for nombre, da in magnitudes.items():
        salida[nombre] = np.clip(_repartir(da.rename(nombre) * _KG_A_UG, filas, columnas), 0, None)
    return salida


def todo(rutas: dict[str, pathlib.Path], dia: dt.date) -> tuple[dict[str, np.ndarray], str]:
    """Todas las variables de salida en la rejilla, con el índice europeo, y
    la pasada global que se ha usado."""
    inicio = dt.datetime(dia.year, dia.month, dia.day, tzinfo=dt.timezone.utc)
    horario = europa(rutas["europa.zip"])
    filas, columnas = _celdas_canarias()
    for nombre, datos in canarias(rutas["global_canarias.zip"], inicio).items():
        horario[nombre][:, filas, columnas] = datos
    horario["aerosol_optical_depth"], pasada_global = espesor_optico(rutas["global_aod.zip"], inicio)
    horario["european_aqi"] = eaqi.indice({k: horario[k] for k in eaqi.TRAMOS})
    for gas in _GASES:
        del horario[gas]
    return horario, pasada_global
