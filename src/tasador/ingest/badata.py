"""Carga de los datasets abiertos del GCBA al corpus.

Dos cosas distintas:

1. `departamentos-en-venta-<año>.csv` -> `corpus.listings` con `source='BADATA'`.
   156.259 filas solo en 2020, con dirección, superficie, precio en USD,
   ambientes y barrio. Es el DATASET DE BACKTEST: permite validar la
   metodología sin depender de ningún portal.

2. `precio-venta-deptos.csv` -> `corpus.market_index` con `source='BADATA'`.
   Serie oficial trimestral de USD/m² por barrio. Es el ANCLA: comparar el
   corpus contra ella es el chequeo de sesgo.

Licencia CC-BY-2.5-AR: uso libre citando la fuente. El informe cita
"DGEyC-GCBA" cuando usa estos datos.
"""

from __future__ import annotations

import csv
import hashlib
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import structlog
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Listing, ListingFeatures, MarketIndex, Neighborhood

log = structlog.get_logger()

SOURCE = "BADATA"
EXTRACTOR = "badata-csv"
EXTRACTOR_VERSION = "1"

# Guardarraíles: el dataset trae errores de carga del relevamiento original.
MIN_USD_M2, MAX_USD_M2 = Decimal("200"), Decimal("20000")
MIN_M2, MAX_M2 = Decimal("15"), Decimal("1000")


@dataclass(slots=True)
class LoadStats:
    rows: int = 0
    loaded: int = 0
    skipped_invalid: int = 0
    skipped_no_neighborhood: int = 0
    duplicates: int = 0


def _dec(v: str | None) -> Decimal | None:
    if not v or not v.strip():
        return None
    try:
        return Decimal(v.strip().replace(",", "."))
    except InvalidOperation:
        return None


def _int(v: str | None) -> int | None:
    d = _dec(v)
    return int(d) if d is not None else None


def _rooms(v: str | None) -> int | None:
    """El relevamiento del GCBA usa valores centinela para 'sin dato': aparecen
    ambientes negativos (`-7`). Sin sanear, el CHECK de la base rechaza la fila
    entera y se pierde un aviso que por lo demás es perfectamente válido.

    Encontrado al cargar 2020: el CHECK hizo su trabajo.
    """
    n = _int(v)
    return n if n is not None and 1 <= n <= 20 else None


TRIMESTRE_MES = {"PRIMERO": 1, "SEGUNDO": 4, "TERCERO": 7, "CUARTO": 10}


def _row_id(row: dict[str, str], year: int) -> str:
    """El dataset no trae identificador. Se sintetiza uno estable a partir del
    contenido, para que recargar el mismo archivo no duplique."""
    key = "|".join(
        str(row.get(k, "")) for k in ("Direccion", "PropiedadS", "Dolares", "Trimestre", "Barrio")
    )
    return f"{year}-{hashlib.sha256(key.encode()).hexdigest()[:20]}"


def _norm_key(s: str) -> str:
    """Clave de barrio normalizada: mayusculas, sin tildes, sin espacios de mas.

    Necesario por dos motivos reales encontrados el 13/08 al cargar 2020:
    el CSV del GCBA trae la N con virgulilla mal codificada (2.533 filas de
    NUNEZ perdidas), y usa sub-barrios propios del relevamiento
    ("FLORES NORTE", "BARRACAS ESTE", "VILLA DEVOTO SUR") que no existen en
    la nomenclatura oficial. Entre las dos cosas se perdia el 11% del dataset.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c) and (c.isalnum() or c.isspace()))
    return " ".join(s.upper().split())


async def _neighborhood_map(session: AsyncSession) -> dict[str, Neighborhood]:
    rows = (await session.execute(select(Neighborhood))).scalars().all()
    out: dict[str, Neighborhood] = {}
    for n in rows:
        if n.badata_key:
            out[_norm_key(n.badata_key)] = n
        out[_norm_key(n.name)] = n
        for alias in n.aliases or []:
            out.setdefault(_norm_key(alias), n)
    return out


async def load_listings_csv(session: AsyncSession, path: Path, year: int) -> LoadStats:
    """Carga un `departamentos-en-venta-<año>.csv`.

    Los avisos entran como `active=False` a propósito: son históricos, no
    mercado vigente. Nunca deben aparecer como comparables actuales de un
    informe. Solo se usan para el backtest.
    """
    stats = LoadStats()
    barrios = await _neighborhood_map(session)
    seen: set[str] = set()
    batch: list[tuple[dict[str, object], dict[str, object]]] = []

    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            stats.rows += 1

            surface = _dec(row.get("PropiedadS"))
            price = _dec(row.get("Dolares"))
            usd_m2 = _dec(row.get("DolaresM2"))

            if not (surface and price) or not (MIN_M2 <= surface <= MAX_M2):
                stats.skipped_invalid += 1
                continue
            if usd_m2 and not (MIN_USD_M2 <= usd_m2 <= MAX_USD_M2):
                stats.skipped_invalid += 1
                continue

            barrio = barrios.get(_norm_key(row.get("Barrio") or ""))
            if barrio is None:
                stats.skipped_no_neighborhood += 1
                continue

            sid = _row_id(row, year)
            if sid in seen:
                stats.duplicates += 1
                continue
            seen.add(sid)

            month = TRIMESTRE_MES.get((row.get("Trimestre") or "").strip().upper(), 1)
            listing_id = uuid.uuid4()
            listing = {
                "id": listing_id,
                "source": SOURCE,
                "source_id": sid,
                "operation": "SALE",
                "price": price,
                "currency": "USD",
                "address_raw": (row.get("Direccion") or "").strip() or None,
                "neighborhood_id": barrio.id,
                "neighborhood_label": barrio.name,
                "published_at": date(year, month, 1),
                # Histórico: NO es mercado vigente. Nunca es comparable actual.
                "active": False,
                "delisted_at": None,
                "content_hash": hashlib.sha256(sid.encode()).hexdigest(),
                "quality_flags": ["historico", "badata"],
                "raw": {
                    "surface_weighted": str(surface),
                    "surface_total": str(surface),
                    "usd_m2_declarado": str(usd_m2) if usd_m2 else None,
                    "trimestre": row.get("Trimestre"),
                    "comuna": row.get("Comunas"),
                    "cotizacion": row.get("Cotizacion"),
                },
                "price_on_request": False,
                "first_seen_at": datetime.now(UTC),
                "last_seen_at": datetime.now(UTC),
            }
            features = {
                "listing_id": listing_id,
                "property_type": "departamento",
                "rooms": _rooms(row.get("Ambientes")),
                "surface_total": surface,
                # El relevamiento no distingue cubierta de total.
                "surface_covered": surface,
                "extractor_model": EXTRACTOR,
                "extractor_version": EXTRACTOR_VERSION,
                "confidence": Decimal("1.00"),
                "amenities": [],
                "needs_review": False,
            }
            batch.append((listing, features))

            if len(batch) >= 2000:
                await _flush(session, batch, stats)
                batch.clear()

    if batch:
        await _flush(session, batch, stats)
    await session.commit()
    log.info("badata: listings cargados", year=year, **asdict(stats))
    return stats


async def _flush(
    session: AsyncSession,
    batch: list[tuple[dict[str, object], dict[str, object]]],
    stats: LoadStats,
) -> None:
    """Inserción masiva: dos sentencias por lote, no dos por fila.

    La primera versión hacía `session.add()` + `await session.flush()` por cada
    aviso para obtener el `id` antes de insertar sus features. Con 156.259
    filas eso son ~312.000 round-trips y la carga no terminaba en 10 minutos.

    La solución es generar el UUID del lado del cliente: sin necesidad de que
    la base devuelva el id, ambas tablas se insertan en bloque.
    """
    if not batch:
        return
    await session.execute(insert(Listing), [lst for lst, _ in batch])
    await session.execute(insert(ListingFeatures), [feat for _, feat in batch])
    stats.loaded += len(batch)


AMBIENTES_A_ROOMS = {"2 ambientes": 2, "3 ambientes": 3}
ESTADO_A_GRUPO = {"Usado": "usado", "A estrenar": "a_estrenar"}


async def load_market_index(session: AsyncSession, path: Path) -> int:
    """Carga `precio-venta-deptos.csv` -> `corpus.market_index`.

    Separador `;`. Ojo: hay filas con `precio_prom` vacío (Agronomía 2010) —
    la serie oficial tiene huecos y la carga debe tolerarlos en vez de asumir
    cobertura completa por barrio.
    """
    barrios = await _neighborhood_map(session)
    loaded = skipped = 0

    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            precio = _dec(row.get("precio_prom"))
            if precio is None or precio <= 0:
                skipped += 1
                continue

            barrio = barrios.get(_norm_key(row.get("barrio") or ""))
            if barrio is None:
                skipped += 1
                continue

            year, trim = _int(row.get("año")), _int(row.get("trimestre"))
            if not year or not trim:
                skipped += 1
                continue

            period = date(year, (trim - 1) * 3 + 1, 1)
            rooms = AMBIENTES_A_ROOMS.get((row.get("ambientes") or "").strip())
            grupo = ESTADO_A_GRUPO.get((row.get("estado") or "").strip(), "todos")

            exists = (
                await session.execute(
                    select(MarketIndex).where(
                        MarketIndex.neighborhood_id == barrio.id,
                        MarketIndex.period == period,
                        MarketIndex.property_type == "departamento",
                        MarketIndex.rooms == rooms,
                        MarketIndex.condition_group == grupo,
                        MarketIndex.source == SOURCE,
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue

            session.add(
                MarketIndex(
                    neighborhood_id=barrio.id,
                    period=period,
                    property_type="departamento",
                    rooms=rooms,
                    condition_group=grupo,
                    usd_per_m2=precio,
                    source=SOURCE,
                )
            )
            loaded += 1

    await session.commit()
    log.info("badata: serie oficial cargada", loaded=loaded, skipped=skipped)
    return loaded
