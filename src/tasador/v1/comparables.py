"""`GET /v1/comparables` — el explorador del corpus (doc 07 §8).

Para que el agente busque avisos sin generar un informe, y —lo que más vale
hoy— **para auditar la calidad de la extracción**. Cada aviso viene con su
descripción original Y con lo que el nodo 4 sacó de ella, para poder ponerlas
una al lado de la otra.

## Por qué esta pantalla es la que más rinde ahora

El nodo 4 está en ~76% de exactitud contra un objetivo de 92%, y la varianza
del eval —17,6 pp medidos— es ruido de muestreo de un golden set de 24 avisos.
Lo que falta no es prompt: es anotación. Y anotar leyendo YAML es lento.

Acá el agente ve el texto y la extracción juntos, marca lo que está mal
(`needs_review`), y esos avisos entran como candidatos al golden set. Es el
circuito que doc 09 §3.3 describe cuando dice que el set "crece con los errores
reales".

## `org_id` y el corpus compartido

`corpus.listings` es compartido entre tenants a propósito (`org_id` NULL =
aviso público, doc 17 §4.1). Por eso este endpoint NO filtra por `org_id` sobre
los avisos públicos — sería el bug opuesto — pero sí excluye los privados de
otros tenants, que son los cargados a mano y que solo ve quien los cargó.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.base import get_session
from tasador.db.models import Listing, ListingFeatures, Neighborhood, Organization
from tasador.v1.auth import resolve_org

router = APIRouter()

# Los cinco que mueven el precio (doc 05 §4.1) y que por eso exigen cita
# verificada en el nodo 4. Son los que hay que auditar; el resto del extracto
# es informativo.
CAMPOS_QUE_AJUSTAN = ("condition", "orientation", "floor_number", "has_elevator", "age_years")


class FeaturesOut(BaseModel):
    condition: str | None = None
    orientation: str | None = None
    floor_number: int | None = None
    has_elevator: bool | None = None
    age_years: int | None = None
    parking_spaces: int | None = None
    rooms: int | None = None
    balcony: bool | None = None
    credit_eligible: bool | None = None
    extractor_model: str | None = None
    extractor_version: str | None = None
    confidence: float | None = None
    needs_review: bool = False
    extracted_at: datetime | None = None


class ComparableOut(BaseModel):
    id: uuid.UUID
    source: str
    source_id: str
    url: str | None
    address: str | None
    neighborhood: str | None
    price: float | None
    currency: str | None
    surface_weighted: float | None
    usd_per_m2: float | None
    rooms: int | None
    published_at: str | None
    active: bool
    cluster_id: uuid.UUID | None
    # El texto crudo del aviso. Es la mitad izquierda de la comparación: sin
    # esto la pantalla mostraría la extracción sin nada contra qué contrastarla,
    # que es exactamente lo que no sirve.
    description: str | None
    features: FeaturesOut | None
    # Cuáles de los campos que ajustan quedaron SIN dato. Se calcula acá y no
    # en el front porque es la definición de "qué falta" y tiene que ser una
    # sola: el eval mide contra esta misma lista.
    faltantes: list[str]


class ListadoComparables(BaseModel):
    items: list[ComparableOut]
    next_cursor: str | None
    total_aprox: int


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def _cursor(fila: Listing) -> str:
    crudo = f"{fila.first_seen_at.isoformat()}|{fila.id}"
    return base64.urlsafe_b64encode(crudo.encode()).decode().rstrip("=")


def _decodificar(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        relleno = "=" * (-len(cursor) % 4)
        fecha, ident = base64.urlsafe_b64decode(cursor + relleno).decode().split("|", 1)
        return datetime.fromisoformat(fecha), uuid.UUID(ident)
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=422, detail="Cursor inválido.") from e


@router.get("/comparables", response_model=ListadoComparables, summary="Explorar el corpus")
async def listar_comparables(
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
    barrio: str | None = None,
    fuente: str | None = None,
    q: str | None = Query(default=None, description="busca en dirección y descripción"),
    solo_sin_extraer: bool = False,
    solo_para_revisar: bool = False,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
) -> ListadoComparables:
    base = (
        select(Listing, ListingFeatures, Neighborhood.name)
        # LEFT y no INNER: los avisos sin features son los que MÁS interesan en
        # esta pantalla. Un INNER los escondería justo a ellos — es el mismo
        # error que el nodo 2 tenía y que dejaba todo informe en cero
        # (informe Etapa 3 §5.1).
        .outerjoin(ListingFeatures, ListingFeatures.listing_id == Listing.id)
        .outerjoin(Neighborhood, Neighborhood.id == Listing.neighborhood_id)
        .where(
            Listing.active.is_(True),
            # Corpus compartido: los públicos son de todos, los privados solo
            # de quien los cargó.
            or_(Listing.org_id.is_(None), Listing.org_id == org.id),
        )
    )

    if barrio:
        base = base.where(Neighborhood.name == barrio)
    if fuente:
        base = base.where(Listing.source == fuente)
    if q:
        patron = f"%{q}%"
        base = base.where(or_(Listing.address_raw.ilike(patron), Listing.description.ilike(patron)))
    if solo_sin_extraer:
        base = base.where(ListingFeatures.listing_id.is_(None))
    if solo_para_revisar:
        base = base.where(ListingFeatures.needs_review.is_(True))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    q_pag = base.order_by(Listing.first_seen_at.desc(), Listing.id.desc()).limit(limit + 1)
    if cursor:
        desde, desde_id = _decodificar(cursor)
        from sqlalchemy import tuple_

        q_pag = q_pag.where(
            tuple_(Listing.first_seen_at, Listing.id) < tuple_(desde, desde_id)  # type: ignore[arg-type]
        )

    filas = (await session.execute(q_pag)).all()
    hay_mas = len(filas) > limit
    filas = filas[:limit]

    items: list[ComparableOut] = []
    for li, feat, nombre_barrio in filas:
        raw: dict[str, Any] = li.raw or {}
        sup = raw.get("surface_weighted")
        sup_f = float(sup) if sup else None
        precio = _f(li.price)
        faltantes = (
            [c for c in CAMPOS_QUE_AJUSTAN if getattr(feat, c, None) is None]
            if feat is not None
            else list(CAMPOS_QUE_AJUSTAN)
        )
        items.append(
            ComparableOut(
                id=li.id,
                source=li.source,
                source_id=li.source_id,
                url=li.url,
                address=li.address_raw,
                neighborhood=nombre_barrio,
                price=precio,
                currency=li.currency,
                surface_weighted=sup_f,
                usd_per_m2=round(precio / sup_f) if precio and sup_f else None,
                rooms=raw.get("rooms"),
                published_at=li.published_at.isoformat() if li.published_at else None,
                active=li.active,
                cluster_id=li.cluster_id,
                description=li.description,
                features=FeaturesOut(
                    condition=feat.condition,
                    orientation=feat.orientation,
                    floor_number=feat.floor_number,
                    has_elevator=feat.has_elevator,
                    age_years=feat.age_years,
                    parking_spaces=feat.parking_spaces,
                    rooms=feat.rooms,
                    balcony=feat.balcony,
                    credit_eligible=feat.credit_eligible,
                    extractor_model=feat.extractor_model,
                    extractor_version=feat.extractor_version,
                    confidence=_f(feat.confidence),
                    needs_review=feat.needs_review,
                    extracted_at=feat.extracted_at,
                )
                if feat is not None
                else None,
                faltantes=faltantes,
            )
        )

    return ListadoComparables(
        items=items,
        next_cursor=_cursor(filas[-1][0]) if hay_mas and filas else None,
        total_aprox=total,
    )


class MarcarIn(BaseModel):
    needs_review: bool = True


@router.post("/comparables/{listing_id}/revisar", summary="Marcar una extracción incorrecta")
async def marcar_para_revisar(
    listing_id: uuid.UUID,
    body: MarcarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
) -> dict[str, Any]:
    """El botón "reportar extracción incorrecta" de doc 07 §8.

    No corrige nada: MARCA. La corrección es una anotación humana y va al
    golden set, que es el único lugar donde una respuesta cuenta como correcta
    (doc 09 §3.3). Dejar que la UI escribiera la feature "arreglada"
    convertiría el corpus en la anotación, y entonces el eval mediría el
    acuerdo del modelo con lo último que alguien tocó.
    """
    fila = (
        await session.execute(
            select(ListingFeatures).where(ListingFeatures.listing_id == listing_id)
        )
    ).scalar_one_or_none()
    if fila is None:
        raise HTTPException(
            status_code=404, detail="Ese aviso todavía no tiene features extraídas."
        )

    fila.needs_review = body.needs_review
    await session.commit()
    return {"listing_id": str(listing_id), "needs_review": fila.needs_review, "org": org.slug}
