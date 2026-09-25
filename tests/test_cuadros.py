import numpy as np

from aire import config as c, cuadros

nan = np.nan


def test_nombre_y_esquina_de_granada():
    # Granada, 37,18 N −3,60: cuadro 37,0–37,5 N, −4,0 a −3,5.
    ti = int((37.18 - c.SUR) // 0.5)
    tj = int((-3.60 - c.OESTE) // 0.5)
    assert cuadros.esquina(ti, tj) == (37.0, -4.0)
    assert cuadros.nombre(ti, tj) == "37.0_-4.0.json"


def test_meridiano_cero():
    tj = int((0.2 - c.OESTE) // 0.5)
    assert cuadros.nombre(0, tj).endswith("_0.0.json")
    tj = int((-0.2 - c.OESTE) // 0.5)
    assert cuadros.nombre(0, tj).endswith("_-0.5.json")


def test_centros_de_la_rejilla_coinciden_con_cams_europa():
    lat, lon = cuadros.centros()
    assert lat[c.FILA_EUROPA] == 30.05
    assert lat[-1] == 71.95
    assert lon[0] == -24.95 and lon[-1] == 44.95


def test_codificar_constante_lista_y_vacio():
    assert cuadros.codificar(np.zeros(5), 10) == 0
    assert cuadros.codificar(np.array([nan, nan]), 10) is None
    assert cuadros.codificar(np.array([1.24, nan, 3.0]), 10) == [12, None, 30]
    # Una serie constante con huecos no se resume: los huecos importan.
    assert cuadros.codificar(np.array([0.0, nan]), 10) == [0, None]


def _vacio(horas):
    return np.full((horas, c.FILAS, c.COLUMNAS), nan, dtype=np.float32)


def test_contenido_de_un_cuadro():
    ti, tj = 19, 42  # 37,0 N −4,0
    horario = {v: _vacio(3) for v in ("olive_pollen", "dust", "european_aqi")}
    f0, k0 = ti * c.CUADRO, tj * c.CUADRO
    horario["dust"][:, f0:f0 + 5, k0:k0 + 5] = 12.34
    horario["olive_pollen"][:, f0:f0 + 5, k0:k0 + 5] = 0
    horario["olive_pollen"][1, f0 + 2, k0 + 3] = 7.0  # celda 13 (fila 2, col 3)
    meta = {"run": "2026-09-25T00:00Z", "generated": "x", "dailyStart": "2026-09-18", "days": 0}
    j = cuadros.contenido(ti, tj, horario, {}, ["Europe/Madrid"] * 25, meta)

    assert (j["lat0"], j["lon0"], j["rows"], j["cols"]) == (37.0, -4.0, 5, 5)
    assert j["tz"] == ["Europe/Madrid"]
    assert j["hourly"]["dust"] == {"scale": 10, "cells": [123] * 25}
    olivo = j["hourly"]["olive_pollen"]["cells"]
    assert olivo[13] == [0, 70, 0]
    assert olivo[12] == 0
    # Sin ningún dato en el cuadro, la variable no sale.
    assert "european_aqi" not in j["hourly"]
    assert j["daily"] == {}


def test_husos_mezclados_van_celda_a_celda():
    # Frontera de Badajoz y Elvas: 38,5–39,0 N, −7,5 a −7,0.
    ti = int((38.5 - c.SUR) / 0.5)
    tj = int((-7.5 - c.OESTE) / 0.5)
    zonas = cuadros.husos({(ti, tj): (38.88, -6.97)})[(ti, tj)]
    assert set(zonas) == {"Europe/Madrid", "Europe/Lisbon"}
    assert len(zonas) == 25
