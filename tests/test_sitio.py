"""Lo que se publica además de los cuadros."""

import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent / "sitio_base"


def test_un_cuadro_que_no_existe_da_404():
    # Sin 404.html, Cloudflare Pages devuelve la portada con un 200 para
    # cualquier ruta, y la app no sabría que ahí no hay datos.
    assert (BASE / "404.html").is_file()
