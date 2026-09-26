"""Lo que no cambia de un día para otro: la rejilla, los cuadros y las variables."""

# Versión del formato de los ficheros. Va en la ruta (/v1/…): si un día cambia
# el formato, se publica /v2/ al lado y las versiones viejas de la app siguen
# leyendo lo suyo.
VERSION = 1

# Rejilla común: celdas de 0,1° con el centro en x,x5, la de CAMS Europa.
# La fila 0 es la del sur y la columna 0 la del oeste.
PASO = 0.1
SUR, OESTE = 27.5, -25.0
FILAS, COLUMNAS = 445, 700  # hasta 72° N y 45° E

# El modelo europeo cubre de 30° N hacia arriba. Canarias queda fuera y sale
# del modelo global (0,4°), repartido en la misma rejilla.
FILA_EUROPA = 25  # 30,05° N
CANARIAS = dict(sur=27.5, norte=30.0, oeste=-18.5, este=-13.0)

# Cuadros de 0,5° de lado (5 × 5 celdas), uno por fichero. Solo se publican
# los que tienen algo de tierra: unos 6.600, lejos del límite de 20.000
# ficheros de Cloudflare Pages.
CUADRO = 5

# Horas de previsión: de 0 a 96 desde la pasada de las 00 UTC. La previsión
# europea se publica en dos partes: hasta 48 h a las 06:45 UTC y hasta 96 h a
# las 08:30 UTC.
HORAS = 97
DIAS_PASADOS = 7

# Variables de salida, con los nombres de Open-Meteo, que son los que usa la
# app, y el factor por el que se multiplican antes de redondear a entero.
ESCALAS = {
    "alder_pollen": 10,
    "birch_pollen": 10,
    "grass_pollen": 10,
    "mugwort_pollen": 10,
    "olive_pollen": 10,
    "ragweed_pollen": 10,
    "dust": 10,
    "pm2_5": 10,
    "pm10": 10,
    "aerosol_optical_depth": 100,
    "european_aqi": 1,
}
POLENES = [v for v in ESCALAS if v.endswith("_pollen")]

# Variables del modelo europeo: nombre en el ADS → nombre en esta rejilla.
EUROPA_ADS = {
    "alder_pollen": "alder_pollen",
    "birch_pollen": "birch_pollen",
    "grass_pollen": "grass_pollen",
    "mugwort_pollen": "mugwort_pollen",
    "olive_pollen": "olive_pollen",
    "ragweed_pollen": "ragweed_pollen",
    "dust": "dust",
    "particulate_matter_2.5um": "pm2_5",
    "particulate_matter_10um": "pm10",
    "ozone": "ozone",
    "nitrogen_dioxide": "nitrogen_dioxide",
    "sulphur_dioxide": "sulphur_dioxide",
}

ATRIBUCION = (
    "Contains modified Copernicus Atmosphere Monitoring Service information {año}. "
    "Neither the European Commission nor ECMWF is responsible for any use that may "
    "be made of the Copernicus information or data it contains."
)
LICENCIA = "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"

# Para pedirle cosas a la web publicada. Con el User-Agent de Python,
# Cloudflare contesta 403 («error code: 1010»: firma de navegador vetada).
AGENTE = "AireSinSustos-datos/1 (+https://github.com/Demual/aire-sin-sustos-datos)"
