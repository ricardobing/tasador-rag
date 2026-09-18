"""Nodo 11: el armado del documento.

Se testea `informe_html()`, que es una función pura. La conversión a PDF
necesita las librerías nativas de GTK y solo corre en el contenedor Linux —
está partida en dos justamente para que el armado del documento sí se pueda
verificar en cualquier lado.
"""

from __future__ import annotations

from typing import Any

import pytest

from tasador.agents.nodes.render import _plata, informe_html, markdown_a_html
from tasador.agents.state import ReportState, estado_inicial


def _estado(**kw: Any) -> ReportState:
    e = estado_inicial("11111111-1111-1111-1111-111111111111", "org", "sub")
    e["subject"] = {"address_raw": "Av. Cabildo 2530", "neighborhood_name": "Belgrano"}
    e["draft_md"] = "## Resumen\n\nSugerimos publicar en USD 283.385."
    e["valuation"] = {
        "currency": "USD",
        "value_low": "239899",
        "value_mid": "283385",
        "value_high": "373326",
        "closing_low": "240877",
        "closing_high": "269216",
        "confidence": "MEDIA",
        "comparables_used": 12,
        "comparables_found": 16,
        "detail": [
            {
                "listing_id": "abc12345",
                "source": "PORTAL_B",
                "included": True,
                "snapshot_price": "198000",
                "snapshot_surface": "96",
                "raw_price_per_m2": "2063",
                "adjusted_price_per_m2": "2100",
            },
            {
                "listing_id": "def67890",
                "source": "PORTAL_B",
                "included": False,
                "exclusion_reason": "en_pozo_o_construccion",
                "snapshot_price": "610400",
                "snapshot_surface": "105",
                "raw_price_per_m2": "5813",
                "adjusted_price_per_m2": None,
            },
        ],
    }
    e.update(kw)  # type: ignore[typeddict-item]
    return e


def test_arma_el_documento_completo():
    html = informe_html(_estado(), org="la inmobiliaria")
    assert "Informe de Mercado Comparativo" in html
    assert "Av. Cabildo 2530" in html
    assert "Belgrano" in html
    assert "la inmobiliaria" in html


def test_los_importes_van_con_separador_de_miles():
    """Es un documento que lee una persona: "283385" no se lee."""
    html = informe_html(_estado())
    assert "283.385" in html
    assert ">283385<" not in html


def test_los_descartados_aparecen_con_su_motivo():
    """Doc 03 §3.6: es lo que permite responder "¿por qué no usaste el de
    Cabildo 2500?". Si no están impresos, el descarte es invisible."""
    html = informe_html(_estado())
    # En castellano llano: el PDF lo lee el propietario, no el motor.
    assert "en pozo o en construcción" in html
    assert "610.400" in html


def test_los_usados_van_primero():
    html = informe_html(_estado())
    assert html.index("198.000") < html.index("610.400")


def test_la_trazabilidad_va_impresa():
    """Las tres versiones y el id del informe van en el PDF, no escondidas en
    la base: es lo que permite auditarlo seis meses después."""
    html = informe_html(_estado())
    assert "11111111-1111-1111-1111-111111111111" in html
    assert "2026.08.1" in html
    assert "no constituye una tasación con validez legal" in html


def test_la_advertencia_de_confianza_baja_es_visible():
    """Doc 05 §7: recuadro visible en la primera página, no un footer de 6pt."""
    normal = informe_html(_estado())
    baja = _estado()
    baja["valuation"] = {**baja["valuation"], "confidence": "BAJA"}
    con_aviso = informe_html(baja)

    assert "Confianza baja" not in normal
    assert "Confianza baja" in con_aviso
    assert 'class="aviso"' in con_aviso


def test_la_narrativa_se_convierte_a_html():
    html = informe_html(_estado())
    assert "<h2>Resumen</h2>" in html
    assert "283.385" in html


def test_no_se_cuela_html_desde_el_markdown():
    """El markdown lo escribió un LLM sobre texto de terceros. CommonMark sin
    HTML embebido: el crítico ya verificó las cifras, pero eso no vuelve
    confiable al HTML crudo (doc 10 §4.2)."""
    peligroso = "Texto normal.\n\n<script>alert(1)</script>\n\n<img src=x onerror=y>"
    salida = markdown_a_html(peligroso)
    # Lo que importa no es que la PALABRA no aparezca —queda como texto
    # inerte— sino que no quede ningún tag ejecutable.
    assert "<script" not in salida
    assert "<img" not in salida
    assert "&lt;script&gt;" in salida
    assert "Texto normal." in salida


def test_los_datos_del_sujeto_se_escapan():
    """La dirección la escribe el usuario y va a un HTML."""
    e = _estado()
    e["subject"] = {"address_raw": "<script>alert(1)</script>", "neighborhood_name": None}
    html = informe_html(e)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_sin_narrativa_el_documento_sale_igual():
    """La degradación del crítico: al tercer rechazo el informe sale con la
    tabla y el rango, sin prosa. El PDF tiene que armarse igual."""
    e = _estado()
    e["draft_md"] = ""
    html = informe_html(e)
    assert "283.385" in html
    assert "PORTAL_B" in html


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [("283385", "283.385"), (1000, "1.000"), ("0", "0"), (None, "—"), ("no-es-numero", "—")],
)
def test_formato_de_plata(entrada: Any, esperado: str):
    assert _plata(entrada) == esperado


# ── Fase A del crítico: falsos positivos que cuestan la narrativa ─────────
def test_la_altura_de_la_direccion_no_es_una_cifra_no_trazable():
    """El redactor nombra la dirección; la altura es parte de la dirección.

    Medido el 14/08 con el primer informe de Palermo: la fase A rechazaba
    «Thames 1800» porque `1800` no aparecía como número suelto en los datos —
    aparecía adentro del string de la dirección. Tres rechazos y el propietario
    recibe el informe SIN NARRATIVA.

    El costo del falso positivo es asimétrico y concreto, igual que el de
    §10.3 de la Etapa 3: rechazar de más no protege de nada y borra el texto.
    """
    from tasador.agents.nodes.critic import cifras_no_trazables

    datos = {
        "sujeto": {"direccion": "Thames 1800, Palermo"},
        "valuacion": {"precio_publicacion_sugerido": {"medio": 230564}},
    }
    md = "La propiedad de Thames 1800 se sugiere publicar en USD 230.564."
    assert cifras_no_trazables(md, datos) == []


def test_una_cifra_inventada_sigue_siendo_rechazo():
    """El contrapeso del test de arriba: aflojar el falso positivo no puede
    aflojar la regla. Es la única defensa real contra la alucinación de cifras
    y no depende de que ningún modelo se dé cuenta (ADR-002)."""
    from tasador.agents.nodes.critic import cifras_no_trazables

    datos = {
        "sujeto": {"direccion": "Thames 1800, Palermo"},
        "valuacion": {"precio_publicacion_sugerido": {"medio": 230564}},
    }
    md = "La propiedad se sugiere publicar en USD 312.000."
    assert cifras_no_trazables(md, datos) == ["312.000"]
