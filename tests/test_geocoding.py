"""Normalización de direcciones y elección de resultados de Nominatim.

Sin red: `normalizar_direccion` y `_elegir` son funciones puras, y son
justamente donde estuvieron las tres fallas del 13/08.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tasador.geocoding import (
    AMBA_BBOX,
    Geocoder,
    _elegir,
    dentro_del_amba,
    distancia_m,
    normalizar_direccion,
)


# ── Normalización ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("crudo", "calle", "altura", "aprox"),
    [
        ("Av. Cabildo 2530", "AVENIDA CABILDO", "2530", False),
        # La forma MÁS común en Portal B: 24 de 24 avisos de Belgrano la usan.
        ("Cabildo al 2200", "CABILDO", "2200", True),
        ("AV Balbín  al 2400", "AVENIDA BALBIN", "2400", True),
        ("Gral. Las Heras 3200", "GENERAL LAS HERAS", "3200", False),
        ("Dr. Ricardo Rojas 400", "DOCTOR RICARDO ROJAS", "400", False),
        # Sin altura: es una calle, no un portal. No se inventa un número.
        ("Ciudad de la Paz", "CIUDAD DE LA PAZ", None, False),
    ],
)
def test_desarma_direcciones_reales(crudo: str, calle: str, altura: str | None, aprox: bool):
    d = normalizar_direccion(crudo)
    assert d.calle == calle
    assert d.altura == altura
    assert d.altura_aproximada is aprox


def test_separa_la_unidad():
    d = normalizar_direccion("Av. Cabildo 2530 4°B")
    assert d.unidad == "4B"
    assert d.altura == "2530"
    # La unidad NO va a Nominatim: el 4°B no le dice nada y empeora el match.
    assert "4B" not in d.para_geocodificar


def test_saca_tildes_y_normaliza_espacios():
    assert normalizar_direccion("  Núñez   1200  ").normalizada == "NUNEZ 1200"


def test_direccion_vacia_no_explota():
    d = normalizar_direccion("")
    assert d.calle is None and d.altura is None


def test_la_altura_aproximada_queda_marcada():
    """ "al 2200" ubica la cuadra, no el portal. El informe tiene que saberlo."""
    assert normalizar_direccion("Cramer al 2200").altura_aproximada
    assert not normalizar_direccion("Cramer 2200").altura_aproximada


# ── Elección de resultados ───────────────────────────────────────────────
def _r(lat: float, lon: float, clase: str, tipo: str, imp: float) -> dict:
    return {
        "lat": str(lat),
        "lon": str(lon),
        "category": clase,
        "type": tipo,
        "importance": imp,
        "display_name": f"{clase}/{tipo}",
    }


def test_un_growshop_no_es_un_barrio():
    """Caso real del 13/08: buscar "Olivos" devolvía una clínica, un growshop
    y un faro. Y con limit=1, una calle de La Matanza a 20 km."""
    resultados = [
        _r(-34.7023, -58.4808, "highway", "residential", 0.053),  # calle en La Matanza
        _r(-34.5227, -58.4848, "amenity", "hospital", 0.0),  # Clínica Olivos
        _r(-34.5214, -58.4846, "shop", "cannabis", 0.0),  # Olivos Growshop
        _r(-34.5110, -58.4901, "boundary", "administrative", 0.460),  # el barrio
    ]
    elegido = _elegir(resultados, "lugar")
    assert elegido is not None
    assert float(elegido["lat"]) == pytest.approx(-34.5110)


def _dir(lat: float, lon: float, clase: str, calle: str, altura: str = "") -> dict:
    return {
        "lat": str(lat),
        "lon": str(lon),
        "category": clase,
        "type": "x",
        "importance": 0.1,
        "display_name": f"{calle} {altura}",
        "address": {"road": calle, "house_number": altura},
    }


def test_un_comercio_en_una_direccion_si_es_esa_direccion():
    """El criterio se INVIERTE respecto de los barrios, y por un motivo real:
    el único resultado de OSM para "Avenida Cabildo 2530" es un local de
    comidas rápidas. Su clase es `amenity`, pero está en Avenida Cabildo 2530.

    Filtrar por clase acá rechazaba toda dirección cuyo único registro en OSM
    sea un negocio — o sea, muchísimas."""
    mostaza = _dir(-34.5581, -58.4605, "amenity", "Avenida Cabildo", "2530")
    elegido = _elegir([mostaza], "direccion", calle="AVENIDA CABILDO", altura="2530")
    assert elegido is not None
    # Pero como BARRIO seguiría sin servir.
    assert _elegir([mostaza], "lugar") is None


def test_gana_el_que_tiene_la_altura_exacta():
    otro = _dir(-34.56, -58.46, "place", "Avenida Cabildo", "3000")
    exacto = _dir(-34.5581, -58.4605, "amenity", "Avenida Cabildo", "2530")
    elegido = _elegir([otro, exacto], "direccion", calle="AVENIDA CABILDO", altura="2530")
    assert elegido is not None
    assert elegido["address"]["house_number"] == "2530"


def test_se_descarta_un_resultado_de_otra_calle():
    """Preferimos no responder antes que responder cualquier cosa: un barrio
    equivocado produce un informe entero sobre la zona incorrecta."""
    otra = _dir(-34.56, -58.46, "place", "Avenida Santa Fe", "2530")
    assert _elegir([otra], "direccion", calle="AVENIDA CABILDO", altura="2530") is None


def test_los_acentos_de_la_calle_no_importan():
    cramer = _dir(-34.5633, -58.4624, "highway", "Avenida Crámer", "2200")
    assert _elegir([cramer], "direccion", calle="AVENIDA CRAMER", altura="2200") is not None


def test_un_homonimo_de_otra_provincia_se_descarta():
    """Casos reales: "San Isidro" resolvió a Salta y "San Fernando" a Córdoba."""
    salta = _r(-24.5740, -64.9336, "boundary", "administrative", 0.6)
    bsas = _r(-34.4739, -58.5264, "boundary", "administrative", 0.4)
    elegido = _elegir([salta, bsas], "lugar")
    assert elegido is not None
    # Gana el de menor `importance` porque el otro está fuera del AMBA.
    assert float(elegido["lat"]) == pytest.approx(-34.4739)


def test_si_no_queda_ningun_candidato_valido_devuelve_none():
    """Mejor sin resultado que con un faro. Un `None` se ve; una coordenada
    equivocada entra callada."""
    assert _elegir([_r(-34.5, -58.4, "amenity", "hospital", 0.9)], "lugar") is None
    assert _elegir([], "lugar") is None


def test_la_caja_del_amba():
    assert dentro_del_amba(-34.5614, -58.4560)  # Belgrano
    assert not dentro_del_amba(-24.5740, -64.9336)  # Salta
    assert not dentro_del_amba(-31.4487, -64.1905)  # Córdoba
    lat_min, lat_max, lng_min, lng_max = AMBA_BBOX
    assert lat_min < lat_max and lng_min < lng_max


# ── Distancia ────────────────────────────────────────────────────────────
def test_distancia_entre_barrios_conocidos():
    """Belgrano y Núñez se tocan: los centroides reales dan 1.847 m."""
    d = distancia_m(-34.5614, -58.4560, -34.5455, -58.4620)
    assert 1500 < d < 2500


def test_distancia_a_si_mismo_es_cero():
    assert distancia_m(-34.56, -58.45, -34.56, -58.45) == 0


# ── Caché ────────────────────────────────────────────────────────────────
async def test_offline_sin_cache_no_sale_a_la_red(tmp_path: Path):
    """Así corren los tests: sin depender de OSM ni gastarle el rate limit."""
    geo = Geocoder(cache_dir=tmp_path, offline=True)
    assert await geo.geocodificar("Calle Inexistente 9999") is None


async def test_el_cache_negativo_tambien_se_guarda(tmp_path: Path):
    """Sin esto, una dirección mal escrita se vuelve a consultar en cada
    reintento y se come el rate limit de OSM."""
    geo = Geocoder(cache_dir=tmp_path, offline=True)
    geo._escribir_cache("DIRECCION|CABA", None)
    assert geo._leer_cache("DIRECCION|CABA") == "NEGATIVO"
    assert geo._archivo("DIRECCION|CABA").exists()
