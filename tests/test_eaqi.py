import numpy as np
import pytest

from aire import eaqi


@pytest.mark.parametrize(
    "contaminante, conc, esperado",
    [
        ("pm2_5", 0, 0),
        ("pm2_5", 5, 20),  # límite bueno/aceptable
        ("pm2_5", 10, 30),  # mitad del tramo aceptable (5-15)
        ("pm2_5", 140, 100),
        ("pm10", 45, 40),
        ("nitrogen_dioxide", 60, 60),
        ("ozone", 110, 50),
        ("sulphur_dioxide", 275, 100),
    ],
)
def test_interpola_dentro_de_cada_tramo(contaminante, conc, esperado):
    r = eaqi.subindice(np.array([conc], dtype=np.float32), eaqi.TRAMOS[contaminante])
    assert r[0] == pytest.approx(esperado)


def test_pasa_de_100_con_la_recta_del_ultimo_tramo():
    # PM2,5: de 90 a 140 µg/m³ el índice sube 20 puntos; a 190, otros 20.
    r = eaqi.subindice(np.array([190.0]), eaqi.TRAMOS["pm2_5"])
    assert r[0] == pytest.approx(120)


def test_negativos_del_modelo_cuentan_como_cero():
    r = eaqi.subindice(np.array([-0.3]), eaqi.TRAMOS["ozone"])
    assert r[0] == 0


def test_el_indice_es_el_peor_contaminante_y_salta_huecos():
    r = eaqi.indice({
        "pm2_5": np.array([10.0, np.nan, np.nan]),  # 30, -, -
        "ozone": np.array([130.0, 50.0, np.nan]),  # 65, 16,7, -
        "nitrogen_dioxide": np.array([5.0, np.nan, np.nan]),  # 10, -, -
    })
    assert r[0] == pytest.approx(65)
    assert r[1] == pytest.approx(50 / 60 * 20)
    assert np.isnan(r[2])


def test_una_hora_normal_de_ciudad():
    # PM2,5 7,5 → 25; PM10 16,5 → 21; NO2 18 → 30,7; O3 96 → 38; SO2 2 → 2.
    # Manda el ozono. (Comparado el 25/09/2026 con Open-Meteo en Madrid, 72
    # horas: la mayor diferencia fue 0,5, su redondeo a enteros.)
    r = eaqi.indice({
        "pm2_5": np.array([7.5]),
        "pm10": np.array([16.5]),
        "nitrogen_dioxide": np.array([18.0]),
        "ozone": np.array([96.0]),
        "sulphur_dioxide": np.array([2.0]),
    })
    assert r[0] == pytest.approx(38)
