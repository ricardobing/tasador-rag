"""`POST /v1/inventory/snapshot` — el panel empuja su inventario (doc 06 §2).

Elimina el riesgo de cuota de el CRM (doc 02 §2.3): el panel ya sincroniza
el CRM una vez por día; después de ese sync manda acá lo que descargó. El
Tasador nunca le pega a el CRM.

## Decisiones

- **Idempotente por `(org_id, source, source_id)`** — la unique constraint de
  la tabla. Mandar el mismo snapshot dos veces da `unchanged`, no duplicados.
- **`full_refresh: true` da de baja lo que NO vino** (`published=false`, no
  DELETE): el inventario es del tenant y "ya no está publicada" es un dato,
  no una ausencia. Con `full_refresh: false` el snapshot es incremental y no
  toca lo que no menciona.
- Hasta 1.000 propiedades por request. El inventario de la inmobiliaria son 285: si
  algún tenant necesita más, pagina el cliente con varios requests
  incrementales y un `full_refresh` final vacío no — se agrega cursor cuando
  exista el caso.
- El barrio NO se resuelve acá: la ingesta del corpus tiene su propia
  resolución con alias y este endpoint tiene que ser barato y predecible para
  un cron nocturno. `neighborhood_label` guarda lo que el panel diga.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.base import get_session
from tasador.db.models import InventoryProperty, Organization
from tasador.v1.auth import resolve_org

router = APIRouter()
log = structlog.get_logger()


class PropiedadIn(BaseModel):
    source_id: str = Field(min_length=1, max_length=100)
    address_raw: str | None = Field(default=None, max_length=300)
    neighborhood_label: str | None = Field(default=None, max_length=120)
    property_type: str | None = Field(default=None, max_length=40)
    operation: Literal["SALE", "RENT"] | None = None
    rooms: int | None = Field(default=None, ge=0, le=30)
    surface_total: Decimal | None = Field(default=None, gt=0, le=100000)
    surface_covered: Decimal | None = Field(default=None, gt=0, le=100000)
    price: Decimal | None = Field(default=None, gt=0)
    currency: Literal["USD", "ARS"] | None = None
    description: str | None = Field(default=None, max_length=20000)
    published: bool | None = None


class SnapshotIn(BaseModel):
    source: str = Field(default="CRM", min_length=1, max_length=40)
    full_refresh: bool = False
    properties: list[PropiedadIn] = Field(max_length=1000)


class SnapshotOut(BaseModel):
    received: int
    created: int
    updated: int
    unchanged: int
    delisted: int


@router.post(
    "/inventory/snapshot", response_model=SnapshotOut, summary="El panel empuja su inventario"
)
async def snapshot(
    body: SnapshotIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
) -> SnapshotOut:
    ahora = datetime.now(UTC)
    existentes: dict[str, InventoryProperty] = {
        f.source_id: f
        for f in (
            await session.execute(
                select(InventoryProperty).where(
                    InventoryProperty.org_id == org.id,
                    InventoryProperty.source == body.source,
                )
            )
        )
        .scalars()
        .all()
    }

    created = updated = unchanged = delisted = 0
    vistos: set[str] = set()

    for p in body.properties:
        vistos.add(p.source_id)
        datos: dict[str, Any] = p.model_dump(exclude={"source_id"})
        fila = existentes.get(p.source_id)
        if fila is None:
            session.add(
                InventoryProperty(
                    org_id=org.id,
                    source=body.source,
                    source_id=p.source_id,
                    raw=p.model_dump(mode="json"),
                    **datos,
                )
            )
            created += 1
            continue

        fila.last_seen_at = ahora
        if all(getattr(fila, campo) == valor for campo, valor in datos.items()):
            unchanged += 1
            continue
        for campo, valor in datos.items():
            setattr(fila, campo, valor)
        fila.raw = p.model_dump(mode="json")
        updated += 1

    if body.full_refresh:
        for source_id, fila in existentes.items():
            if source_id not in vistos and fila.published is not False:
                fila.published = False
                fila.last_seen_at = ahora
                delisted += 1

    await session.commit()
    log.info(
        "snapshot de inventario",
        org=org.slug,
        source=body.source,
        received=len(body.properties),
        created=created,
        updated=updated,
        unchanged=unchanged,
        delisted=delisted,
    )
    return SnapshotOut(
        received=len(body.properties),
        created=created,
        updated=updated,
        unchanged=unchanged,
        delisted=delisted,
    )
