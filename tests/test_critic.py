"""Nodo 10, fase A: la verificación determinística de cifras.

Es el control del que depende el argumento del producto —"el LLM no produce
cifras, y si inventa una la agarramos sin depender de otro LLM"— así que estos
tests no comprueban que la función corra: **miden cuánto discrimina**.

## Por qué se mide en vez de ejercitar

La auditoría del 15/08 encontró que la fase A dejaba pasar el **50,5%** de los
precios inventados entre USD 100.000 y 400.000 sobre un informe real de 23
comparables — incluido el `USD 312.000` que el docstring de `critic.py` pone
como ejemplo de lo que no puede pasar.

Y era invisible con tests de casos: cada caso individual daba lo esperado. Lo
que fallaba era la COBERTURA del conjunto aceptado, que solo se ve midiéndola.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest

from tasador.agents.nodes.critic import (
    _valores_permitidos,
    cifras_clave_equivocadas,
    cifras_no_trazables,
)

# La tolerancia real de `config/agents.yaml`.
TOL = 0.1


def datos_de_informe(n_comparables: int = 23) -> dict[str, Any]:
    """La forma de `datos_del_informe` con la cantidad de comparables que más
    duele: cada uno aporta 4 cifras del mismo orden que el valor buscado."""
    return {
        "valuacion": {
            "moneda": "USD",
            "precio_publicacion_sugerido": {"minimo": 191424, "medio": 239280, "maximo": 287136},
            "rango_de_cierre_esperado": {"minimo": 203388, "maximo": 227316},
            "usd_por_m2": 3988,
            "dispersion": "0.1234",
        },
        "comparables": {
            "encontrados": 60,
            "usados": n_comparables,
            "detalle_usados": [
                {
                    "precio": 180000 + i * 7000,
                    "superficie_m2": 55 + i,
                    "usd_m2_crudo": 3200 + i * 40,
                    "usd_m2_ajustado": 3100 + i * 38,
                }
                for i in range(n_comparables)
            ],
        },
        "propiedad": {"direccion": "Gorriti 5000, Palermo", "superficie_ponderada_m2": "60.0"},
    }


def cobertura(datos: dict[str, Any], *, tolerancia_pct: float = TOL, n: int = 6000) -> float:
    """Qué fracción de los precios plausibles deja pasar la fase A.

    Es la medición del hallazgo, convertida en aserción. Un precio "plausible"
    es cualquier entero entre USD 100.000 y 400.000: el rango de un
    departamento porteño, o sea el rango en el que una alucinación sería
    creíble.
    """
    rnd = random.Random(20260815)
    permitidos = _valores_permitidos(datos)
    tol = Decimal(str(tolerancia_pct)) / 100
    pasan = 0
    for _ in range(n):
        x = Decimal(rnd.randint(100_000, 400_000))
        if any(abs(x - p) <= max(abs(p) * tol, Decimal("0.5")) for p in permitidos):
            pasan += 1
    return pasan / n


# ── La medición ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("n_comparables", [7, 23, 53])
def test_la_fase_a_no_deja_pasar_mas_del_10_por_ciento_de_los_precios_inventados(
    n_comparables: int,
):
    """EL test de esta sección.

    Con la configuración anterior (`v*100` para todo y tolerancia 1%) esto daba
    27-50% según la cantidad de comparables. El umbral del 10% no es redondo por
    gusto: es el orden que da la tolerancia de 0,1% con 53 comparables, que es
    el peor caso del corpus de hoy.

    Si este test se pone en rojo, la pregunta no es "subamos el umbral" sino
    "qué se agregó al conjunto de cifras aceptadas".
    """
    c = cobertura(datos_de_informe(n_comparables))
    assert c <= 0.10, (
        f"con {n_comparables} comparables la fase A deja pasar el {c:.1%} de los "
        f"precios inventados: el control no discrimina"
    )


def test_la_medicion_detecta_una_regla_que_afloja_el_gate():
    """El test del test.

    Si la cobertura se midiera mal, el de arriba pasaría siempre. Con la
    tolerancia vieja (1%) tiene que dar sustancialmente peor.
    """
    datos = datos_de_informe(23)
    assert cobertura(datos, tolerancia_pct=1.0) > 3 * cobertura(datos, tolerancia_pct=TOL)


# ── Los casos concretos del docstring de critic.py ───────────────────────
def test_el_ejemplo_del_docstring_se_rechaza():
    """«si el redactor escribió "USD 312.000" y ese número no está en la
    valuación ni en la tabla, el informe no sale». Pasaba."""
    datos = datos_de_informe(23)
    md = "El valor estimado de la propiedad es de USD 312.000."
    assert cifras_no_trazables(md, datos, tolerancia_pct=TOL) or cifras_clave_equivocadas(
        md, datos, tolerancia_pct=TOL
    )


def test_la_cifra_correcta_pasa():
    datos = datos_de_informe(23)
    md = "El valor sugerido de publicación es de USD 239.280."
    assert not cifras_no_trazables(md, datos, tolerancia_pct=TOL)
    assert not cifras_clave_equivocadas(md, datos, tolerancia_pct=TOL)


def test_el_redondeo_del_redactor_sigue_pasando():
    """0,1% cubre "USD 182.000" por 181.950 (0,03%). Si esto falla, la
    tolerancia quedó demasiado dura y el crítico va a rechazar informes
    correctos — que cuesta el producto (Etapa 3 §10.3)."""
    datos = datos_de_informe(23)
    for texto in ("USD 239.280", "USD 239.300", "USD 3.988"):
        md = f"El precio sugerido es {texto}."
        assert not cifras_clave_equivocadas(md, datos, tolerancia_pct=TOL), texto


# ── La verificación posicional ───────────────────────────────────────────
def test_el_precio_de_un_comparable_no_puede_presentarse_como_el_valor():
    """La pregunta que `cifras_no_trazables` no puede hacer.

    `USD 306.000` es el precio de un comparable: está en los datos, así que es
    "trazable". Pero no es el valor de ESTA propiedad, y el informe no puede
    decir que lo es.
    """
    datos = datos_de_informe(23)
    precio_de_un_comparable = datos["comparables"]["detalle_usados"][18]["precio"]
    md = f"El valor de la propiedad es de USD {precio_de_un_comparable:,}".replace(",", ".")
    assert not cifras_no_trazables(md, datos, tolerancia_pct=TOL), "está en los datos"
    assert cifras_clave_equivocadas(md, datos, tolerancia_pct=TOL), (
        "pero no es el valor de la propiedad y el control posicional tiene que verlo"
    )


def test_mencionar_un_comparable_como_lo_que_es_no_se_rechaza():
    """El contrapeso: el informe SÍ puede citar el precio de un comparable
    mientras no lo presente como el valor del sujeto. Rechazar de más cuesta el
    producto."""
    datos = datos_de_informe(23)
    p = datos["comparables"]["detalle_usados"][18]["precio"]
    md = f"El comparable de la esquina se publica en USD {p:,}.".replace(",", ".")
    assert not cifras_no_trazables(md, datos, tolerancia_pct=TOL)
    assert not cifras_clave_equivocadas(md, datos, tolerancia_pct=TOL)


def test_no_rechaza_un_usd_m2_de_comparable_en_una_frase_sobre_precios():
    """El falso positivo REAL del primer informe con este control puesto.

    La narrativa decía algo como «los comparables van de USD 2.100 a USD 3.839
    por m²» y el chequeo posicional leyó `3.839` como si fuera el valor de la
    propiedad: rechazó tres veces y el informe salió SIN NARRATIVA.

    Es el costo asimétrico de la Etapa 3 §10.3 —rechazar de más cuesta el
    producto— con este control como causa.
    """
    datos = datos_de_informe(23)
    for md in (
        "El precio por m² de los comparables va de USD 2.100 a USD 3.839.",
        "Los avisos analizados tienen un precio de publicación de hasta USD 3.839 por m².",
        "El valor del m² en la zona ronda los USD 3.839.",
    ):
        assert not cifras_clave_equivocadas(md, datos, tolerancia_pct=TOL), md


def test_sigue_rechazando_el_valor_del_sujeto_inventado():
    """El contrapeso del anterior: acotar el control no puede apagarlo."""
    datos = datos_de_informe(23)
    for md in (
        "El valor de la propiedad es de USD 312.000.",
        "El precio sugerido de publicación es de USD 450.000.",
        "Nuestra estimación arroja USD 199.999 para esta unidad.",
    ):
        assert cifras_clave_equivocadas(md, datos, tolerancia_pct=TOL), md


def test_la_direccion_no_se_lee_como_una_cifra_inventada():
    """El bug 32: el crítico marcaba la altura de la dirección como no trazable
    y el propietario se quedaba sin narrativa. `1800` SÍ estaba en los datos,
    adentro de un string."""
    datos = datos_de_informe(23)
    md = "La propiedad está en Gorriti 5000, Palermo."
    assert not cifras_no_trazables(md, datos, tolerancia_pct=TOL)
