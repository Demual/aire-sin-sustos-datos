# Datos de Aire sin sustos

La tarea diaria que sustituye a la API de calidad del aire de Open-Meteo, que
no permite el uso comercial y la app llevará anuncios. Baja la previsión de
Copernicus (CAMS), la reparte en cuadros de medio grado y la publica como
ficheros estáticos en Cloudflare Pages, que los sirve gratis y sin límite de
tráfico. La app baja solo el cuadro donde está.

## Qué hace cada día

1. **Descarga** del Atmosphere Data Store (`aire/descarga.py`), en paralelo:
   - la previsión europea de las 00 UTC, de 0 a 96 h, a 0,1°: seis pólenes,
     polvo, PM2,5, PM10, ozono, NO₂ y SO₂. Se publica completa a las 08:30 UTC.
   - la global de las 00 UTC (0,4°), garantizada a las 10:00 UTC: el espesor
     óptico de toda Europa (la turbidez del cielo) y, en Canarias, que queda
     al sur del modelo europeo, el polvo, las partículas y los gases a ras de
     suelo. Si falla, la de las 12 UTC del día anterior, que cubre las mismas
     horas. Para la calima conviene la más reciente: el 25/09/2026, para dos
     días después, la de las 00 UTC veía 23 µg/m³ de polvo en Las Palmas y la
     de doce horas antes, 2.
2. **Lectura** (`aire/lectura.py`): todo a una rejilla común de 0,1° y en
   µg/m³. La europea va tal cual; de la global se toma el punto más cercano,
   sin interpolar, como Open-Meteo (en la costa, interpolar con puntos de mar
   borraba media calima).
3. **Índice europeo** de calidad del aire (`aire/eaqi.py`), en la escala de 0
   a 100 que usaba Open-Meteo y con los tramos de la Agencia Europea de Medio
   Ambiente revisados en 2024. Comparado con Open-Meteo en Madrid el
   25/09/2026: 72 horas, la mayor diferencia fue 0,5 (su redondeo).

Comparado el resultado con Open-Meteo el 25/09/2026 en Granada, Madrid,
Sevilla, Viena y Milán: pólenes, PM2,5, PM10 e índice, iguales; el polvo
difiere en décimas (Open-Meteo lo redondea a enteros). En Canarias, con la
misma pasada global, las PM10 coinciden y el polvo sale un 10 % más alto.
4. **Días pasados** (`aire/archivo.py`): la media de las primeras 24 horas de
   cada día se guarda en `archivo/AAAA-MM-DD.npz` y se publica con lo demás.
   Al día siguiente se recogen de la propia web los de la semana anterior.
5. **Cuadros** (`aire/cuadros.py`): un JSON por cuadro de 0,5° que tenga algo
   de tierra (unos 6.600; el límite gratis de Cloudflare Pages es 20.000
   ficheros), con el huso horario de cada celda.

## Probar en local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # en Linux: .venv/bin/pip
.venv/Scripts/python -m pytest -q tests
.venv/Scripts/python -m aire --fecha 2026-09-25 --datos descargas --salida sitio
```

La clave del ADS se lee de `~/.cdsapirc` o de la variable `ADS_KEY`. Con
`--datos`, lo ya descargado no se vuelve a pedir. `--archivo` dice de dónde
salen los días pasados: una carpeta con los `.npz` o la web publicada (por
defecto, la variable `SITIO_URL`).

## Los ficheros

`/v1/{lat0}_{lon0}.json`, con la esquina suroeste del cuadro redondeada hacia
abajo a medio grado: Granada (37,18, −3,60) está en `/v1/37.0_-4.0.json`. Si
no existe (mar, o fuera de Europa y Canarias), la respuesta es 404 gracias a
`sitio_base/404.html`: sin él, Cloudflare Pages toma la web por una aplicación
de una sola página y devuelve la portada con un 200, que la app lee como una
respuesta rota.

```jsonc
{
  "version": 1,
  "run": "2026-09-25T00:00Z",        // pasada del modelo europeo
  "generated": "2026-09-25T08:58Z",
  "lat0": 37.0, "lon0": -4.0, "step": 0.1, "rows": 5, "cols": 5,
  "tz": ["Europe/Madrid"],            // uno, o uno por celda si hay frontera
  "start": "2026-09-25T00:00Z", "hours": 97,
  "hourly": {
    "olive_pollen": {"scale": 10, "cells": [0, 0, [0, 12, null, …], …]},
    …
  },
  "dailyStart": "2026-09-18", "days": 7,
  "daily": { … }                      // medias de días UTC, mismo formato
}
```

- 25 celdas de 0,1°, de suroeste a noreste, fila a fila: la de (lat, lon) es
  `fila * cols + col`, con `fila = floor((lat − lat0) / 0,1)`.
- Cada celda es `null` (sin datos), un entero (la serie no cambia: casi
  siempre ceros de polen fuera de temporada) o la lista con huecos.
- El valor real es el entero dividido por `scale`.
- Variables con los nombres de Open-Meteo: `alder_pollen`, `birch_pollen`,
  `grass_pollen`, `mugwort_pollen`, `olive_pollen`, `ragweed_pollen`
  (granos/m³), `dust`, `pm2_5`, `pm10` (µg/m³), `aerosol_optical_depth` y
  `european_aqi`. Una variable sin ningún dato en el cuadro no sale: el polen
  en Canarias, donde no hay modelo.
- `/v1/info.json` resume la última pasada.

## Puesta en marcha

1. Un repositorio **público** en GitHub con esta carpeta como raíz (las
   Actions de un repositorio público no gastan minutos).
2. En Cloudflare, un proyecto de Pages de subida directa con rama de
   producción `main`. Su nombre va en la variable del repositorio
   `PROYECTO_PAGES` y su dirección (`https://<proyecto>.pages.dev`) en
   `SITIO_URL`. Al crearlo, súbele a mano lo de `sitio_base/`: la tarea lee
   de la web los días pasados y falla si la dirección todavía no existe.
3. Secretos del repositorio: `ADS_KEY` (la clave del ADS),
   `CLOUDFLARE_API_TOKEN` (con permiso «Cloudflare Pages: Edit») y
   `CLOUDFLARE_ACCOUNT_ID`.

`.github/workflows/diaria.yml` se ejecuta a las 10:15 UTC y, de repuesto, a
las 12:30 UTC, que no hace nada si la web ya tiene la pasada del día. Al
terminar vuelve a activarse a sí misma: GitHub apaga las tareas programadas de
los repositorios públicos tras 60 días sin actividad.

## Licencia y atribución

Los datos de Copernicus tienen licencia CC BY 4.0. Quien los use, la app
incluida, tiene que decir:

> Contains modified Copernicus Atmosphere Monitoring Service information 2026.
> Neither the European Commission nor ECMWF is responsible for any use that may
> be made of the Copernicus information or data it contains.

La portada del sitio (`sitio_base/index.html`) lo dice y explica los cambios.
