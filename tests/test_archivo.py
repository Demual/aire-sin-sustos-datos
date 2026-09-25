import datetime as dt

import numpy as np
import pytest

from aire import archivo, config as c

nan = np.nan


def _horario(valor, horas=c.HORAS):
    return {"olive_pollen": np.full((horas, c.FILAS, c.COLUMNAS), valor, dtype=np.float32)}


def test_media_de_las_primeras_24_horas():
    h = _horario(0.0)
    h["olive_pollen"][:24, 10, 20] = np.arange(24)  # media 11,5
    h["olive_pollen"][24:, 10, 20] = 1000  # otro día: no cuenta
    m = archivo.medias_del_dia(h)["olive_pollen"]
    assert m[10, 20] == pytest.approx(11.5)
    assert m[0, 0] == 0


def test_sin_12_horas_con_dato_no_hay_media():
    h = _horario(nan)
    h["olive_pollen"][:11, 3, 3] = 5.0
    h["olive_pollen"][:12, 4, 4] = 5.0
    m = archivo.medias_del_dia(h)["olive_pollen"]
    assert np.isnan(m[3, 3])
    assert m[4, 4] == pytest.approx(5.0)


def test_ida_y_vuelta_con_la_escala_y_los_huecos():
    m = {"olive_pollen": np.full((c.FILAS, c.COLUMNAS), 3.14159, dtype=np.float32)}
    m["olive_pollen"][0, 0] = nan
    vuelta = archivo.de_bytes(archivo.a_bytes(m))["olive_pollen"]
    assert np.isnan(vuelta[0, 0])
    assert vuelta[1, 1] == pytest.approx(3.1)  # escala 10


def test_la_semana_anterior_con_un_dia_perdido(tmp_path):
    hoy = dt.date(2026, 9, 25)
    for n, valor in [(1, 1.0), (2, 2.0), (7, 7.0)]:  # faltan del 3 al 6
        m = {"olive_pollen": np.full((c.FILAS, c.COLUMNAS), valor, dtype=np.float32)}
        (tmp_path / archivo.nombre(hoy - dt.timedelta(days=n))).write_bytes(archivo.a_bytes(m))
    crudos = {}
    for n in range(1, 8):
        d = hoy - dt.timedelta(days=n)
        if (b := archivo.leer(d, str(tmp_path))) is not None:
            crudos[d] = b
    inicio, pilas = archivo.pasados(hoy, crudos)
    assert inicio == dt.date(2026, 9, 18)
    serie = pilas["olive_pollen"][:, 50, 50]
    assert serie[0] == pytest.approx(7.0)
    assert np.isnan(serie[1:5]).all()
    assert list(serie[5:]) == pytest.approx([2.0, 1.0])
    assert "dust" not in pilas  # no estaba en ningún día


def test_sin_archivo_no_hay_dias():
    inicio, pilas = archivo.pasados(dt.date(2026, 9, 25), {})
    assert pilas == {}
