"""Peticiones al Atmosphere Data Store (ADS) de Copernicus."""

import datetime as dt
import os
import pathlib
from concurrent.futures import ThreadPoolExecutor

from ecmwf.datastores import Client

from . import config as c

URL = "https://ads.atmosphere.copernicus.eu/api"
EUROPA = "cams-europe-air-quality-forecasts"
GLOBAL = "cams-global-atmospheric-composition-forecasts"


def cliente() -> Client:
    """La clave sale de ADS_KEY (en la Action) o de ~/.cdsapirc (en local)."""
    url, clave = os.environ.get("ADS_URL", URL), os.environ.get("ADS_KEY", "").strip()
    rc = pathlib.Path.home() / ".cdsapirc"
    if not clave and rc.exists():
        cfg = dict(l.split(":", 1) for l in rc.read_text(encoding="utf-8").splitlines() if ":" in l)
        url, clave = cfg.get("url", url).strip(), cfg.get("key", "").strip()
    if not clave:
        raise SystemExit("Falta la clave del ADS: variable ADS_KEY o fichero ~/.cdsapirc")
    return Client(url=url, key=clave, progress=False)


def peticiones(dia: dt.date, global_de_ayer: bool = False) -> dict[str, tuple[str, dict]]:
    """Lo que se baja para un día.

    - Europa: la pasada de las 00 UTC del día, de 0 a 96 h. Completa a las
      08:30 UTC.
    - Global: también la de las 00 UTC, garantizada a las 10:00 UTC. Da la
      turbidez del cielo de toda Europa y, en Canarias, el polvo, las
      partículas y los gases a ras de suelo (nivel 137 del modelo, a unos
      10 m). Con `global_de_ayer`, la de las 12 UTC del día anterior, de 12 a
      108 h, que cubre las mismas horas: es el repuesto si la de hoy se
      retrasa. Para la calima importa que sea la más reciente: el 25/09/2026
      la de las 00 UTC veía 23 µg/m³ de polvo en Las Palmas dos días después
      y la de doce horas antes, 2.

    Las áreas globales llevan margen para que todas las celdas tengan vecino."""
    horas = [str(h) for h in range(c.HORAS)]
    ayer = dia - dt.timedelta(days=1)
    global_ = {
        "date": [f"{ayer}/{ayer}"] if global_de_ayer else [f"{dia}/{dia}"],
        "time": ["12:00"] if global_de_ayer else ["00:00"],
        "type": ["forecast"],
        "leadtime_hour": [str(h) for h in range(12, 12 + c.HORAS)] if global_de_ayer else horas,
        "data_format": "netcdf_zip",
    }
    return {
        "europa.zip": (EUROPA, {
            "variable": list(c.EUROPA_ADS),
            "model": ["ensemble"],
            "level": ["0"],
            "date": [f"{dia}/{dia}"],
            "type": ["forecast"],
            "time": ["00:00"],
            "leadtime_hour": horas,
            "data_format": "netcdf_zip",
        }),
        "global_aod.zip": (GLOBAL, {
            **global_,
            "variable": ["total_aerosol_optical_depth_550nm"],
            "area": [72.8, -25.6, 26.8, 45.6],
        }),
        "global_canarias.zip": (GLOBAL, {
            **global_,
            "variable": [
                "particulate_matter_2.5um",
                "particulate_matter_10um",
                "surface_pressure",
                "dust_aerosol_0.03-0.55um_mixing_ratio",
                "dust_aerosol_0.55-0.9um_mixing_ratio",
                "dust_aerosol_0.9-20um_mixing_ratio",
                "ozone",
                "nitrogen_dioxide",
                "sulphur_dioxide",
                "temperature",
            ],
            "model_level": ["137"],
            "area": [30.8, -19.2, 26.8, -12.4],
        }),
    }


def bajar(dia: dt.date, carpeta: pathlib.Path) -> dict[str, pathlib.Path]:
    """Pide las tres cosas a la vez. Lo que ya esté en la carpeta no se vuelve
    a pedir: sirve para repetir el proceso en local sin esperar colas.

    Si la global de hoy falla, se pide la de ayer a las 12 UTC: mejor calima
    de hace doce horas que quedarse sin publicar Europa."""
    carpeta.mkdir(parents=True, exist_ok=True)
    pendientes = {n: p for n, p in peticiones(dia).items() if not (carpeta / n).exists()}
    if pendientes:
        cl = cliente()
        de_ayer = peticiones(dia, global_de_ayer=True)

        def una(nombre: str) -> None:
            coleccion, peticion = pendientes[nombre]
            parcial = carpeta / f"{nombre}.parte"
            try:
                cl.retrieve(coleccion, peticion, target=str(parcial))
            except Exception as e:
                if coleccion != GLOBAL:
                    raise
                print(f"  {nombre}: la pasada global de hoy falla ({e}); uso la de ayer a las 12 UTC", flush=True)
                cl.retrieve(coleccion, de_ayer[nombre][1], target=str(parcial))
            parcial.replace(carpeta / nombre)
            print(f"  {nombre}: {(carpeta / nombre).stat().st_size / 1e6:.1f} MB", flush=True)

        with ThreadPoolExecutor(len(pendientes)) as ex:
            list(ex.map(una, pendientes))
    return {n: carpeta / n for n in peticiones(dia)}
