"""Tests de la fuente externa opcional.

El test que más importa es `test_apagada_no_rompe_nada`: la garantía #3 de
doc 01 §2.2 es que el sistema funciona sin esta fuente. Si algún día alguien
la vuelve obligatoria por descuido, esto falla.
"""

from __future__ import annotations

from decimal import Decimal

from tasador.settings import Settings
from tasador.sources.panel_externo import map_inventory_row


def test_apagada_no_rompe_nada() -> None:
    """Garantía #3: la fuente es opcional DE VERDAD.

    El default es false y la app tiene que poder construir su configuración
    sin DSN. Si alguien hace obligatorio el DSN, este test lo detecta.
    """
    s = Settings(
        database_url="postgresql+psycopg://t:t@localhost/t",
        redis_url="redis://localhost:6379/0",
        secret_key="x",
        encryption_key="x",
        litellm_master_key="x",
    )
    assert s.panel_source_enabled is False
    assert s.panel_readonly_dsn.get_secret_value() == ""


def test_mapeo_de_una_fila_real() -> None:
    row = {
        "source_id": "1234567",
        "address_raw": "Ciudad de la Paz 2100",
        "neighborhood_label": "Belgrano",
        "property_type": "Departamento",
        "operation": "Sale",
        "rooms": 3,
        "surface_total": "82.0",
        "surface_covered": "75.0",
        "price": "189000",
        "currency": "usd",
        "description": "Excelente 3 amb",
        "published": True,
    }
    m = map_inventory_row(row)
    assert m is not None
    assert m["source"] == "PANEL"
    assert m["property_type"] == "departamento"  # normalizado
    assert m["operation"] == "SALE"  # normalizado
    assert m["currency"] == "USD"  # normalizado
    assert m["price"] == Decimal("189000")
    assert m["surface_covered"] == Decimal("75.0")


def test_casing_mixto_del_panel_se_normaliza() -> None:
    """El panel tiene casing heredado inconsistente (doc 02 §3.1). El
    adaptador lo absorbe para que no contamine nuestro vocabulario."""
    for op_in, op_out in (("Sale", "SALE"), ("venta", "SALE"), ("RENT", "RENT")):
        m = map_inventory_row({"source_id": "1", "operation": op_in})
        assert m is not None and m["operation"] == op_out


def test_moneda_desconocida_queda_en_none() -> None:
    """Mejor sin dato que con un dato inventado: una moneda que no
    reconocemos no se fuerza a USD."""
    m = map_inventory_row({"source_id": "1", "currency": "EUR"})
    assert m is not None and m["currency"] is None


def test_fila_sin_identificador_se_descarta() -> None:
    assert map_inventory_row({"address_raw": "Falsa 123"}) is None
    assert map_inventory_row({"source_id": "  "}) is None


def test_no_se_filtra_pii() -> None:
    """Si la vista del otro lado se modificara y empezara a mandar datos
    personales, no deben llegar a nuestro modelo. El mapeo es una lista
    blanca, no un `**row`."""
    m = map_inventory_row(
        {
            "source_id": "1",
            "contact_name": "Juan Pérez",
            "contact_phone": "+5491155551234",
            "contact_email": "juan@example.com",
        }
    )
    assert m is not None
    assert "contact_name" not in m
    assert "contact_phone" not in m
    assert "contact_email" not in m
    # `raw` sí conserva lo recibido para auditoría, pero no se promueve a
    # columnas. Que quede explícito por si algún día se decide filtrarlo ahí.
    assert set(m) == {
        "source",
        "source_id",
        "address_raw",
        "neighborhood_label",
        "property_type",
        "operation",
        "rooms",
        "surface_total",
        "surface_covered",
        "price",
        "currency",
        "description",
        "published",
        "raw",
    }
