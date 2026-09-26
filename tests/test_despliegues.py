import io

import pytest

from aire import config as c, despliegues


def _d(i, fecha):
    return {"id": i, "created_on": fecha}


# Desordenadas, con los formatos de fecha que da la API.
DESPLIEGUES = [
    _d("b", "2026-09-27T10:31:05.123456Z"),
    _d("a", "2026-09-26T19:20:30.5Z"),
    _d("d", "2026-09-29T10:30:00Z"),
    _d("c", "2026-09-28T10:30:00Z"),
    _d("x", "2026-09-26T18:55:00Z"),
]


def test_se_quedan_las_mas_recientes():
    assert despliegues.a_borrar(DESPLIEGUES, vivo="d", conservar=3) == ["a", "x"]


def test_la_que_esta_en_marcha_no_se_borra_aunque_sea_vieja():
    # Tras volver atrás a mano en Cloudflare, la que se sirve es una vieja.
    assert despliegues.a_borrar(DESPLIEGUES, vivo="x", conservar=3) == ["a"]


def test_nunca_se_borran_todas():
    assert despliegues.a_borrar(DESPLIEGUES, vivo=None, conservar=0) == ["c", "b", "a", "x"]


def test_recorre_las_paginas_y_sigue_si_una_no_se_puede_borrar():
    llamadas = []
    paginas = {1: DESPLIEGUES[:3], 2: DESPLIEGUES[3:]}

    def pedir(metodo, ruta, token):
        llamadas.append((metodo, ruta, token))
        if metodo == "GET" and ruta.endswith("/projects/aire"):
            return {"result": {"canonical_deployment": {"id": "d"}}}
        if metodo == "GET":
            pagina = int(ruta.split("?page=")[1].split("&")[0])
            return {"result": paginas[pagina], "result_info": {"total_pages": 2}}
        if ruta.endswith("/a?force=true"):
            raise RuntimeError("HTTP 400")
        return {"result": None}

    total, borradas, fallos = despliegues.limpiar("cuenta", "aire", "secreto", 3, pedir=pedir)
    assert (total, borradas, fallos) == (5, ["x"], ["a: HTTP 400"])
    assert [r for m, r, _ in llamadas if m == "DELETE"] == [
        "/accounts/cuenta/pages/projects/aire/deployments/a?force=true",
        "/accounts/cuenta/pages/projects/aire/deployments/x?force=true",
    ]


def test_se_pide_con_el_token_y_un_user_agent_propio(monkeypatch):
    pedidas = []

    class Respuesta(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def falso(peticion, timeout):
        pedidas.append(peticion)
        return Respuesta(b'{"success": true, "result": []}')

    monkeypatch.setattr(despliegues.urllib.request, "urlopen", falso)
    despliegues._pedir("GET", "/accounts/x/pages/projects/y", "secreto")
    assert pedidas[0].get_header("Authorization") == "Bearer secreto"
    assert pedidas[0].get_header("User-agent") == c.AGENTE


def test_sin_las_variables_no_hace_nada(monkeypatch):
    for v in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "PROYECTO_PAGES"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(SystemExit):
        despliegues.main([])
