"""Fuente externa OPCIONAL: la Supabase del panel de la inmobiliaria.

Doc 01 §2.2. Tres garantías que no son declarativas:

  1. **Solo lectura, por permisos.** El rol `tasador_ro` no tiene INSERT ni
     UPDATE. Aunque este código tuviera un bug, no puede escribir.
  2. **Sin PII.** Se lee de dos vistas que no exponen contact_id, nombre,
     teléfono ni email.
  3. **Opcional de verdad.** Con `PANEL_SOURCE_ENABLED=false` el sistema
     arranca y funciona igual. Hay un test que lo verifica.

Dos modos, los dos soportados por el mismo mapeo:
  - `pull`   — conexión directa a las vistas (rol read-only)
  - `import` — un JSON exportado a mano y empujado por /v1/inventory/snapshot

El modo `import` existe porque el inventario cambia 2-3 propiedades por semana:
una sincronización manual cada par de días alcanza y no requiere tocar la base
de nadie.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import InventoryProperty, Neighborhood
from tasador.ingest.badata import _norm_key

log = structlog.get_logger()

SOURCE = "PANEL"

# Mapeo del vocabulario del panel al nuestro. Es el anti-corruption layer:
# si mañana cambian sus valores, se toca acá y nada más.
PROPERTY_TYPE_MAP = {
    "departamento": "departamento",
    "depto": "departamento",
    "casa": "casa",
    "ph": "ph",
    "local": "local",
    "oficina": "oficina",
    "cochera": "cochera",
    "terreno": "terreno",
    "galpon": "galpon",
    "galpón": "galpon",
}
OPERATION_MAP = {"sale": "SALE", "venta": "SALE", "rent": "RENT", "alquiler": "RENT"}


@dataclass(slots=True)
class SyncStats:
    received: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    delisted: int = 0


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _int(v: Any) -> int | None:
    d = _dec(v)
    return int(d) if d is not None else None


def map_inventory_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Traduce una fila de `v_tasador_inventory` a nuestro modelo.

    **Este es el único lugar del sistema que conoce el vocabulario del panel.**
    Si cambian su schema, se rompe esta función y nada más.

    Devuelve None si la fila no sirve (sin identificador).
    """
    source_id = str(row.get("source_id") or "").strip()
    if not source_id:
        return None

    ptype = (row.get("property_type") or "").strip().lower()
    op = (row.get("operation") or "").strip().lower()
    currency = (row.get("currency") or "").strip().upper() or None

    return {
        "source": SOURCE,
        "source_id": source_id,
        "address_raw": (row.get("address_raw") or "").strip() or None,
        "neighborhood_label": (row.get("neighborhood_label") or "").strip() or None,
        "property_type": PROPERTY_TYPE_MAP.get(ptype),
        "operation": OPERATION_MAP.get(op),
        "rooms": _int(row.get("rooms")),
        "surface_total": _dec(row.get("surface_total")),
        "surface_covered": _dec(row.get("surface_covered")),
        "price": _dec(row.get("price")),
        # El panel tiene casing mixto heredado; acá se normaliza a USD/ARS.
        "currency": currency if currency in ("USD", "ARS") else None,
        "description": row.get("description"),
        "published": bool(row.get("published")) if row.get("published") is not None else None,
        "raw": {k: str(v) for k, v in row.items() if v is not None},
    }


async def _neighborhood_ids(session: AsyncSession) -> dict[str, uuid.UUID]:
    rows = (await session.execute(select(Neighborhood))).scalars().all()
    out: dict[str, uuid.UUID] = {}
    for n in rows:
        out[_norm_key(n.name)] = n.id
        for alias in n.aliases or []:
            out.setdefault(_norm_key(alias), n.id)
    return out


async def sync_inventory(
    session: AsyncSession,
    org_id: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    full_refresh: bool = False,
) -> SyncStats:
    """Aplica un snapshot de inventario. Idempotente por (org, source, source_id).

    `full_refresh=True` marca como no publicado lo que no vino en el snapshot:
    es la forma de detectar propiedades dadas de baja sin borrar el historial.
    """
    stats = SyncStats(received=len(rows))
    barrios = await _neighborhood_ids(session)
    now = datetime.now(UTC)
    vistos: set[str] = set()

    for raw_row in rows:
        mapped = map_inventory_row(raw_row)
        if mapped is None:
            stats.skipped += 1
            continue

        vistos.add(mapped["source_id"])
        if label := mapped.get("neighborhood_label"):
            mapped["neighborhood_id"] = barrios.get(_norm_key(label))

        existing = (
            await session.execute(
                select(InventoryProperty).where(
                    InventoryProperty.org_id == org_id,
                    InventoryProperty.source == SOURCE,
                    InventoryProperty.source_id == mapped["source_id"],
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(InventoryProperty(org_id=org_id, **mapped, last_seen_at=now))
            stats.created += 1
            continue

        cambio = any(
            getattr(existing, k) != v
            for k, v in mapped.items()
            if k not in ("raw", "source", "source_id")
        )
        for k, v in mapped.items():
            setattr(existing, k, v)
        existing.last_seen_at = now
        if cambio:
            stats.updated += 1
        else:
            stats.unchanged += 1

    if full_refresh:
        todas = (
            (
                await session.execute(
                    select(InventoryProperty).where(
                        InventoryProperty.org_id == org_id, InventoryProperty.source == SOURCE
                    )
                )
            )
            .scalars()
            .all()
        )
        for prop in todas:
            if prop.source_id not in vistos and prop.published is not False:
                # No se borra: se marca. El historial de precios vale.
                prop.published = False
                stats.delisted += 1

    await session.commit()
    log.info("inventario inmo-demo sincronizado", org_id=str(org_id), **stats.__dict__)
    return stats


async def fetch_from_supabase(dsn: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Modo `pull`: lee las dos vistas con el rol de solo lectura.

    Conexión aparte, efímera y con su propio DSN: nunca comparte el pool de
    nuestra base. Si falla, el llamador degrada — no es una dependencia.
    """
    import psycopg
    from psycopg.rows import dict_row

    conn = await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)
    try:
        async with conn.cursor() as cur:
            await cur.execute("select * from public.v_tasador_inventory")
            inventory = list(await cur.fetchall())
            await cur.execute("select * from public.v_tasador_appraisals")
            appraisals = list(await cur.fetchall())
    finally:
        await conn.close()

    log.info("inmo-demo: leído", inventario=len(inventory), tasaciones=len(appraisals))
    return inventory, appraisals
