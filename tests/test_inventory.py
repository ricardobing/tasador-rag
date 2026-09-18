"""`POST /v1/inventory/snapshot` — doc 06 §2.

El contrato que importa: idempotente por `(org, source, source_id)`, el
`full_refresh` da de baja sin borrar, y un tenant no puede tocar el inventario
de otro.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import InventoryProperty


@pytest.fixture
def cliente(db: AsyncSession):
    from fastapi.testclient import TestClient

    from tasador.db.base import get_session
    from tasador.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c


async def _org(db: AsyncSession, slug: str = "inmo-demo"):
    from tasador.db.models import Organization

    o = Organization(name=slug.title(), slug=slug)
    db.add(o)
    await db.flush()
    return o


def _prop(source_id: str, **extra) -> dict:
    return {
        "source_id": source_id,
        "address_raw": "Ciudad de la Paz 2100",
        "property_type": "departamento",
        "operation": "SALE",
        "rooms": 3,
        "surface_total": 82.0,
        "price": 189000,
        "currency": "USD",
        "published": True,
        **extra,
    }


H = {"X-Org-Slug": "inmo-demo"}


async def test_el_snapshot_es_idempotente(db: AsyncSession, cliente):
    await _org(db)
    await db.commit()
    cuerpo = {"source": "CRM", "properties": [_prop("t-1"), _prop("t-2")]}

    r1 = cliente.post("/v1/inventory/snapshot", json=cuerpo, headers=H)
    assert r1.status_code == 200
    assert r1.json() == {"received": 2, "created": 2, "updated": 0, "unchanged": 0, "delisted": 0}

    # El MISMO snapshot otra vez: nada se duplica, nada se actualiza.
    r2 = cliente.post("/v1/inventory/snapshot", json=cuerpo, headers=H)
    assert r2.json() == {"received": 2, "created": 0, "updated": 0, "unchanged": 2, "delisted": 0}

    filas = (await db.execute(select(InventoryProperty))).scalars().all()
    assert len(filas) == 2


async def test_un_cambio_de_precio_cuenta_como_updated(db: AsyncSession, cliente):
    await _org(db)
    await db.commit()
    cliente.post("/v1/inventory/snapshot", json={"properties": [_prop("t-1")]}, headers=H)

    r = cliente.post(
        "/v1/inventory/snapshot",
        json={"properties": [_prop("t-1", price=179000)]},
        headers=H,
    )
    assert r.json()["updated"] == 1
    fila = (await db.execute(select(InventoryProperty))).scalars().one()
    await db.refresh(fila)
    assert float(fila.price) == 179000


async def test_full_refresh_da_de_baja_lo_que_no_vino_sin_borrarlo(db: AsyncSession, cliente):
    """`published=false`, no DELETE: "ya no está publicada" es un dato."""
    await _org(db)
    await db.commit()
    cliente.post(
        "/v1/inventory/snapshot",
        json={"properties": [_prop("t-1"), _prop("t-2")]},
        headers=H,
    )

    r = cliente.post(
        "/v1/inventory/snapshot",
        json={"full_refresh": True, "properties": [_prop("t-1")]},
        headers=H,
    )
    assert r.json()["delisted"] == 1

    filas = {f.source_id: f for f in (await db.execute(select(InventoryProperty))).scalars()}
    assert len(filas) == 2, "no se borra nada"
    await db.refresh(filas["t-2"])
    assert filas["t-2"].published is False
    # Y un segundo full_refresh igual no la vuelve a contar.
    r2 = cliente.post(
        "/v1/inventory/snapshot",
        json={"full_refresh": True, "properties": [_prop("t-1")]},
        headers=H,
    )
    assert r2.json()["delisted"] == 0


async def test_sin_full_refresh_no_se_da_de_baja_nada(db: AsyncSession, cliente):
    await _org(db)
    await db.commit()
    cliente.post(
        "/v1/inventory/snapshot",
        json={"properties": [_prop("t-1"), _prop("t-2")]},
        headers=H,
    )
    r = cliente.post("/v1/inventory/snapshot", json={"properties": [_prop("t-1")]}, headers=H)
    assert r.json()["delisted"] == 0


async def test_el_inventario_de_un_tenant_no_toca_al_de_otro(db: AsyncSession, cliente):
    await _org(db, "inmobiliaria-a")
    await _org(db, "inmobiliaria-b")
    await db.commit()

    cliente.post(
        "/v1/inventory/snapshot",
        json={"properties": [_prop("t-1")]},
        headers={"X-Org-Slug": "inmobiliaria-a"},
    )
    # B manda un full_refresh VACÍO: si el filtro de org faltara, esto daría
    # de baja el inventario de A.
    r = cliente.post(
        "/v1/inventory/snapshot",
        json={"full_refresh": True, "properties": []},
        headers={"X-Org-Slug": "inmobiliaria-b"},
    )
    assert r.json()["delisted"] == 0

    fila = (await db.execute(select(InventoryProperty))).scalars().one()
    await db.refresh(fila)
    assert fila.published is True


async def test_mas_de_mil_propiedades_da_422(db: AsyncSession, cliente):
    await _org(db)
    await db.commit()
    r = cliente.post(
        "/v1/inventory/snapshot",
        json={"properties": [_prop(f"t-{i}") for i in range(1001)]},
        headers=H,
    )
    assert r.status_code == 422
