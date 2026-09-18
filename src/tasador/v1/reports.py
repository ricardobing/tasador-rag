"""`POST /v1/reports` y `GET /v1/reports/{id}` — doc 06 §2.

La API **no corre el grafo**: crea la propiedad sujeto, crea el informe en
`QUEUED`, lo encola y devuelve 202. El progreso se lee de `core.report_events`,
que el worker escribe nodo a nodo mientras corre.

`INSUFFICIENT_DATA` se devuelve con **200**, no con 404 ni 500: el sistema
funcionó correctamente y su respuesta correcta es "no hay datos con qué".
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import load_agents_config
from tasador.db.base import get_session
from tasador.db.models import (
    CONDITIONS,
    ORIENTATIONS,
    SUBJECT_PROPERTY_TYPES,
    Listing,
    Organization,
    Report,
    ReportArtifact,
    ReportComparable,
    ReportEvent,
    SubjectProperty,
)
from tasador.settings import get_settings
from tasador.v1.auth import resolve_org

router = APIRouter()
log = structlog.get_logger()

# Ventana de idempotencia: dos clicks del botón no cuestan el doble.
IDEMPOTENCY_TTL = timedelta(hours=24)


# ── Contrato de entrada ──────────────────────────────────────────────────
class PropertyIn(BaseModel):
    """Solo `address_raw` y `property_type` son obligatorios.

    Todo lo demás mejora el informe pero no lo bloquea, porque el flujo real es
    generar ANTES de la visita con poco dato y regenerar después con todo
    (doc 06 §2).
    """

    address_raw: str = Field(min_length=3, max_length=300)
    property_type: Literal[SUBJECT_PROPERTY_TYPES] = "departamento"  # type: ignore[valid-type]
    city: str = "CABA"
    province: str = "CABA"
    rooms: int | None = Field(default=None, ge=1, le=15)
    bedrooms: int | None = Field(default=None, ge=0, le=12)
    bathrooms: int | None = Field(default=None, ge=0, le=10)
    surface_total: Decimal | None = Field(default=None, gt=0, le=10000)
    surface_covered: Decimal | None = Field(default=None, gt=0, le=10000)
    age_years: int | None = Field(default=None, ge=0, le=200)
    floor_number: int | None = Field(default=None, ge=-5, le=200)
    has_elevator: bool | None = None
    condition: Literal[CONDITIONS] | None = None  # type: ignore[valid-type]
    orientation: Literal[ORIENTATIONS] | None = None  # type: ignore[valid-type]
    amenities: list[str] = Field(default_factory=list, max_length=30)
    parking_spaces: int = Field(default=0, ge=0, le=10)
    expenses_ars: Decimal | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)


class ReportIn(BaseModel):
    external_ref: str | None = Field(default=None, max_length=200)
    property: PropertyIn


class ReportQueued(BaseModel):
    report_id: uuid.UUID
    status: str
    poll_url: str
    estimated_seconds: int


# ── Tenant ───────────────────────────────────────────────────────────────
# `resolve_org` vive en `v1/auth.py` desde el 14/08. Lo que cambió es de DÓNDE
# sale el tenant —Bearer o cookie de sesión, ya no un header sin secreto—; el
# contrato con este módulo es el mismo, y por eso acá no hubo que tocar nada
# más que este import.
#
# Lo que nunca fue provisorio y sigue igual: **`org_id` siempre es explícito**.
# Ninguna consulta del sistema corre sin tenant, y hay un gate estático que lo
# verifica (tests/architecture/test_aislamiento.py).


# ── POST /v1/reports ─────────────────────────────────────────────────────
@router.post(
    "/reports",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReportQueued,
    summary="Solicitar un informe",
)
async def crear_informe(
    body: ReportIn,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> ReportQueued:
    s = get_settings()

    if idempotency_key:
        desde = datetime.now(UTC) - IDEMPOTENCY_TTL
        previo = (
            await session.execute(
                select(Report).where(
                    Report.org_id == org.id,
                    Report.idempotency_key == idempotency_key,
                    Report.created_at >= desde,
                )
            )
        ).scalar_one_or_none()
        if previo is not None:
            # 200, no 202: no se creó nada nuevo.
            response.status_code = status.HTTP_200_OK
            return ReportQueued(
                report_id=previo.id,
                status=previo.status,
                poll_url=f"/v1/reports/{previo.id}",
                estimated_seconds=0,
            )

    p = body.property
    if p.surface_covered and p.surface_total and p.surface_covered > p.surface_total:
        raise HTTPException(
            status_code=422,
            detail="La superficie cubierta no puede ser mayor que la total.",
        )

    sujeto = SubjectProperty(
        org_id=org.id,
        external_ref=body.external_ref,
        **p.model_dump(exclude_none=True, exclude={"amenities", "parking_spaces"}),
        amenities=p.amenities,
        parking_spaces=p.parking_spaces,
    )
    session.add(sujeto)
    await session.flush()

    informe = Report(
        org_id=org.id,
        subject_property_id=sujeto.id,
        status="QUEUED",
        idempotency_key=idempotency_key,
        requested_via="API",
        engine_version=s.engine_version,
        method_version=s.method_version,
        prompt_bundle_version=load_agents_config().bundle_hash,
    )
    session.add(informe)
    await session.commit()

    await _encolar(str(informe.id))
    log.info("informe encolado", report_id=str(informe.id), org=org.slug)

    return ReportQueued(
        report_id=informe.id,
        status="QUEUED",
        poll_url=f"/v1/reports/{informe.id}",
        estimated_seconds=90,
    )


async def _encolar(report_id: str) -> None:
    """Encola en arq. Si Redis está caído, el informe queda en QUEUED y un
    barrido posterior lo levanta — no se pierde."""
    from arq import create_pool

    from tasador.worker import redis_settings

    try:
        pool = await create_pool(redis_settings())
        await pool.enqueue_job("generar_informe", report_id, _job_id=f"report:{report_id}")
        await pool.aclose()
    except Exception:
        log.exception("no se pudo encolar; queda en QUEUED", report_id=report_id)


# ── GET /v1/reports/{id} ─────────────────────────────────────────────────
def _dinero(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


# ── GET /v1/reports ──────────────────────────────────────────────────────
class ItemDeListado(BaseModel):
    report_id: uuid.UUID
    status: str
    address: str | None
    neighborhood: str | None
    property_type: str | None
    rooms: int | None
    surface_total: float | None
    value_mid: float | None
    value_low: float | None
    value_high: float | None
    price_per_m2: float | None
    confidence: str | None
    comparables_found: int | None
    comparables_used: int | None
    created_at: datetime


class ListadoDeInformes(BaseModel):
    items: list[ItemDeListado]
    next_cursor: str | None


def _cursor(fila: Report) -> str:
    """`created_at|id` en base64url, que es el orden del índice.

    Paginación por keyset y no por OFFSET: con OFFSET, un informe nuevo entre
    dos pedidos corre todas las filas un lugar y el usuario ve repetido lo que
    ya vio, o —peor— nunca ve lo que quedó del otro lado del borde. Con keyset
    el resultado es estable aunque se creen informes mientras se pagina.

    **En base64url y no en texto plano.** El ISO-8601 con zona trae un `+`
    (`2026-08-14T04:44:07+00:00`), y en un query string el `+` se decodifica
    como ESPACIO: el cursor viajaba de ida y volvía roto. Lo encontró un test.
    Codificarlo tiene además el efecto sano de que se lea como opaco: nadie va a
    construir uno a mano, y el formato interno puede cambiar sin romper clientes.
    """
    crudo = f"{fila.created_at.isoformat()}|{fila.id}"
    return base64.urlsafe_b64encode(crudo.encode()).decode().rstrip("=")


def _decodificar_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        relleno = "=" * (-len(cursor) % 4)
        crudo = base64.urlsafe_b64decode(cursor + relleno).decode()
        fecha, ident = crudo.split("|", 1)
        return datetime.fromisoformat(fecha), uuid.UUID(ident)
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        # 422 y no 500: un cursor corrupto es un pedido mal formado del cliente,
        # no una falla del servidor.
        raise HTTPException(status_code=422, detail="Cursor inválido.") from e


@router.get("/reports", response_model=ListadoDeInformes, summary="Listado de informes")
async def listar_informes(
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
    cursor: str | None = None,
    estado: str | None = None,
) -> ListadoDeInformes:
    """La pantalla `/informes` de doc 07 §3, que es donde vive el usuario.

    Trae la dirección y el barrio del JOIN con `subject_properties`: sin eso el
    listado sería una columna de UUIDs, y el agente no reconoce sus informes por
    id.
    """
    from tasador.db.models import Neighborhood

    q = (
        select(Report, SubjectProperty, Neighborhood.name)
        .join(SubjectProperty, SubjectProperty.id == Report.subject_property_id)
        .outerjoin(Neighborhood, Neighborhood.id == SubjectProperty.neighborhood_id)
        # El filtro de tenant es del informe, no de la propiedad: es la fila que
        # se está listando y la que tiene el índice.
        .where(Report.org_id == org.id)
        .order_by(Report.created_at.desc(), Report.id.desc())
        # Uno de más para saber si hay página siguiente sin un COUNT(*), que
        # sobre una tabla que crece es la consulta cara del listado.
        .limit(limit + 1)
    )

    if estado:
        q = q.where(Report.status == estado)

    if cursor:
        desde, desde_id = _decodificar_cursor(cursor)
        # Tupla y no `created_at <`: dos informes con el mismo instante existen
        # —el worker crea varios en el mismo milisegundo— y con `<` a secas uno
        # de los dos desaparece del listado para siempre.
        q = q.where(
            tuple_(Report.created_at, Report.id) < tuple_(desde, desde_id)  # type: ignore[arg-type]
        )

    filas = (await session.execute(q)).all()
    hay_mas = len(filas) > limit
    filas = filas[:limit]

    items = [
        ItemDeListado(
            report_id=r.id,
            status=r.status,
            address=s.address_raw,
            neighborhood=barrio,
            property_type=s.property_type,
            rooms=s.rooms,
            surface_total=_dinero(s.surface_total),
            value_mid=_dinero(r.value_mid),
            value_low=_dinero(r.value_low),
            value_high=_dinero(r.value_high),
            price_per_m2=_dinero(r.price_per_m2),
            confidence=r.confidence,
            comparables_found=r.comparables_found,
            comparables_used=r.comparables_used,
            created_at=r.created_at,
        )
        for r, s, barrio in filas
    ]
    return ListadoDeInformes(
        items=items,
        next_cursor=_cursor(filas[-1][0]) if hay_mas and filas else None,
    )


@router.get("/reports/{report_id}/pdf", summary="Descargar el PDF del informe")
async def descargar_pdf(
    report_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
) -> Response:
    """`404` si el informe todavía no terminó o no tiene PDF (doc 06 §2).

    Se sirve el archivo guardado y no se regenera al vuelo: un informe
    entregado a un cliente en septiembre tiene que seguir siendo byte por byte
    el mismo en diciembre. Por eso existe el `sha256`.
    """
    from fastapi.responses import FileResponse

    from tasador.db.models import ReportArtifact

    fila = (
        await session.execute(
            select(Report, ReportArtifact)
            .join(ReportArtifact, ReportArtifact.report_id == Report.id)
            .where(Report.id == report_id, Report.org_id == org.id)
        )
    ).first()
    # 404 y no 403 para un informe de otro tenant: que exista o no es
    # información gratis para quien está probando ids.
    if fila is None:
        raise HTTPException(status_code=404, detail="No hay un PDF para ese informe.")

    _, artefacto = fila
    ruta = Path(artefacto.pdf_path)
    # `exists()` bloquea el event loop. Es un stat local de microsegundos, pero
    # la alternativa —servir un 200 apuntando a un archivo que no está— es un
    # error mudo del lado del cliente, así que se paga.
    if not await asyncio.to_thread(ruta.exists):
        log.error("el PDF está registrado pero el archivo no existe", ruta=str(ruta))
        raise HTTPException(status_code=404, detail="No hay un PDF para ese informe.")

    return FileResponse(
        ruta,
        media_type="application/pdf",
        filename=f"informe-{report_id}.pdf",
        headers={"X-Content-SHA256": artefacto.sha256 or ""},
    )


async def _sugerencias(
    session: AsyncSession, informe: Report, eventos: Sequence[ReportEvent]
) -> list[str]:
    """Qué hacer cuando el sistema dice que no hay datos.

    Se arma con lo que el corpus SÍ tiene cerca, no con frases genéricas. La
    diferencia entre "no hay datos suficientes" y "hay 24 avisos en la zona
    pero ninguno de 2 ambientes; capturá esa tipología" es la diferencia entre
    un callejón sin salida y una tarea.
    """
    from tasador.db.models import Listing, Neighborhood, SubjectProperty

    # El `org_id` es redundante hoy —a esta función solo se llega con un
    # informe que ya se filtró por tenant— y va igual. Es la única consulta de
    # la API que buscaba por PK sin acotar el tenant, y esa es exactamente la
    # forma que tiene una fuga de datos de entrar: no por una consulta mal
    # escrita, sino por una bien escrita que después alguien reusa desde otro
    # camino donde la garantía de arriba ya no está.
    sujeto = (
        await session.execute(
            select(SubjectProperty).where(
                SubjectProperty.id == informe.subject_property_id,
                SubjectProperty.org_id == informe.org_id,
            )
        )
    ).scalar_one_or_none()
    if sujeto is None:
        return []

    sugerencias: list[str] = []
    escalera = next(
        (e.detail.get("escalera") for e in eventos if e.node == "retrieve_candidates"), None
    )
    barrio = next(
        (e.detail.get("barrio") for e in eventos if e.node == "retrieve_candidates"), None
    )

    # ¿Se agotó la escalera sin encontrar nada, o encontró y se cayeron después?
    if escalera and all(p.get("encontrados", 0) == 0 for p in escalera):
        # No hay NADA en la zona: el problema es de cobertura, no de curaduría.
        filas = (
            await session.execute(
                select(Neighborhood.name, func.count(Listing.id))
                .select_from(Listing)
                .join(Neighborhood, Neighborhood.id == Listing.neighborhood_id)
                .where(Listing.active.is_(True))
                .group_by(Neighborhood.name)
                .order_by(func.count(Listing.id).desc())
                .limit(3)
            )
        ).all()

        if not filas:
            sugerencias.append(
                "El corpus no tiene ningún aviso vigente. Hay que correr una captura "
                "antes de poder tasar."
            )
        else:
            cobertura = ", ".join(f"{n} ({c})" for n, c in filas)
            sugerencias.append(
                f"No hay avisos vigentes que sirvan para esta propiedad. Hoy el corpus "
                f"solo cubre: {cobertura}."
            )
            sugerencias.append(
                f"Incorporá avisos de {barrio or 'ese barrio'} con "
                f"{sujeto.rooms or '?'} ambientes al corpus: "
                "scripts/ingest_csv.py --carpeta <carpeta con los avisos>"
            )
    elif informe.comparables_found:
        sugerencias.append(
            f"Se encontraron {informe.comparables_found} avisos pero no sobrevivieron a la "
            f"curaduría. Revisá los motivos de descarte en el detalle."
        )

    if not sujeto.surface_total and not sujeto.surface_covered:
        sugerencias.append("Agregá la superficie: sin ella no se puede calcular el USD/m².")

    return sugerencias


async def _items_de_comparables(
    session: AsyncSession, report_id: uuid.UUID, org_id: uuid.UUID, *, incluir_descartados: bool
) -> list[dict[str, Any]]:
    """La tabla que justifica el número — doc 06 §2, `comparables.items[]`.

    Sale de `report_comparables`, no de `reports.methodology`: esa tabla guarda
    el SNAPSHOT del precio y la superficie a la hora del informe. Un aviso que
    después bajó de precio no puede cambiar un informe ya entregado (doc 03).
    Lo que se lee del `listings` de hoy es solo lo que identifica al aviso
    —url, dirección, ambientes—, que no cambia el número.

    Por defecto vienen solo los INCLUIDOS: con 60 comparables la respuesta pesa
    ~40 KB y quien mira el informe quiere ver los que se usaron. Los excluidos,
    con su motivo, están detrás de `?incluir=descartados`.
    """
    # El join contra `Report` con el `org_id` no es decorativo: esta función
    # recibe un `report_id` y `report_comparables` no tiene tenant propio. Sin
    # el join, quien la llame mañana sin verificar el dueño lee los comparables
    # de otra inmobiliaria — y la función se vería bien. El gate estático de
    # aislamiento marcó exactamente esto (H-31).
    q = (
        select(ReportComparable, Listing)
        .join(Listing, Listing.id == ReportComparable.listing_id)
        .join(Report, Report.id == ReportComparable.report_id)
        .where(ReportComparable.report_id == report_id, Report.org_id == org_id)
        .order_by(ReportComparable.included.desc(), ReportComparable.adjusted_price_per_m2)
    )
    if not incluir_descartados:
        q = q.where(ReportComparable.included.is_(True))

    hoy = datetime.now(UTC).date()
    items: list[dict[str, Any]] = []
    for rc, li in (await session.execute(q)).all():
        crudo = li.raw or {}
        items.append(
            {
                "source": li.source,
                "url": li.url,
                "address": li.address_raw,
                "price": _dinero(rc.snapshot_price),
                "currency": rc.snapshot_currency,
                "surface_weighted": _dinero(rc.snapshot_surface),
                "rooms": int(crudo["rooms"]) if str(crudo.get("rooms", "")).isdigit() else None,
                "raw_price_per_m2": _dinero(rc.raw_price_per_m2),
                "adjusted_price_per_m2": _dinero(rc.adjusted_price_per_m2),
                "adjustments": rc.adjustments,
                "distance_m": rc.distance_m,
                "days_published": (hoy - li.published_at).days if li.published_at else None,
                "included": rc.included,
                "exclusion_reason": rc.exclusion_reason,
            }
        )
    return items


@router.get("/reports/{report_id}", summary="Estado y resultado de un informe")
async def obtener_informe(
    report_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
    incluir: Annotated[
        str | None,
        Query(description="`descartados` agrega los comparables excluidos, con su motivo"),
    ] = None,
) -> dict[str, Any]:
    informe = (
        await session.execute(select(Report).where(Report.id == report_id, Report.org_id == org.id))
    ).scalar_one_or_none()
    # 404 y no 403 para un informe de otro tenant: que exista o no es
    # información gratis para quien está probando ids.
    if informe is None:
        raise HTTPException(status_code=404, detail="No existe ese informe.")

    eventos = (
        (
            await session.execute(
                select(ReportEvent)
                .where(ReportEvent.report_id == report_id)
                .order_by(ReportEvent.seq, ReportEvent.id)
            )
        )
        .scalars()
        .all()
    )

    cfg = load_agents_config()
    total = len(cfg.enabled)
    pasos = [
        {
            "node": e.node,
            "status": e.status,
            "duration_ms": e.duration_ms,
            "cost_usd": _dinero(e.cost_usd),
            "detail": e.detail,
        }
        for e in eventos
    ]
    hechos = {e.node for e in eventos if e.status in ("OK", "RETRIED", "SKIPPED")}
    pendientes = [n.id for n in cfg.enabled if n.id not in hechos]

    # `external_ref` es del CRM del cliente y vive en `subject_properties`: es
    # cómo el panel de la inmobiliaria vuelve de un informe a su propiedad. Se devolvía
    # `None` fijo, así que ese camino de vuelta no existía (H-27).
    # El `org_id` es redundante —`informe` ya vino filtrado— y va igual: es la
    # regla del proyecto y el gate estático la exige sin excepciones. Una regla
    # con excepciones "porque acá se sabe" es una regla que no se puede
    # verificar leyendo una línea.
    sujeto = (
        await session.execute(
            select(SubjectProperty).where(
                SubjectProperty.id == informe.subject_property_id,
                SubjectProperty.org_id == org.id,
            )
        )
    ).scalar_one_or_none()

    salida: dict[str, Any] = {
        "report_id": str(informe.id),
        "status": informe.status,
        "external_ref": sujeto.external_ref if sujeto else None,
        "progress": {
            "current_node": pendientes[0] if pendientes else None,
            "completed": len(hechos),
            "total": total,
            "steps": pasos,
        },
        "cost_usd": _dinero(informe.cost_usd),
        "methodology_version": informe.method_version,
        "prompt_bundle_version": informe.prompt_bundle_version,
    }

    if informe.status == "INSUFFICIENT_DATA":
        v = informe.methodology or {}
        excluidos: dict[str, int] = {}
        for d in v.get("detail", []):
            if not d.get("included"):
                motivo = d.get("exclusion_reason") or "sin_motivo"
                excluidos[motivo] = excluidos.get(motivo, 0) + 1
        salida["insufficient_reason"] = informe.insufficient_reason
        salida["detail"] = {
            "candidates_found": informe.comparables_found,
            "excluded": [{"reason": k, "count": v_} for k, v_ in sorted(excluidos.items())],
            # "No hay datos" sin decir QUÉ falta es un callejón sin salida para
            # quien lo recibe. Esto convierte el "no sé" en una acción.
            "suggestions": await _sugerencias(session, informe, eventos),
        }
        return salida

    if informe.status == "SUCCEEDED":
        salida["generated_at"] = informe.finished_at.isoformat() if informe.finished_at else None
        salida["valuation"] = {
            "currency": informe.currency,
            "suggested_listing_price": {
                "low": _dinero(informe.value_low),
                "mid": _dinero(informe.value_mid),
                "high": _dinero(informe.value_high),
            },
            "expected_closing_range": {
                "low": _dinero(informe.closing_low),
                "high": _dinero(informe.closing_high),
            },
            "price_per_m2": _dinero(informe.price_per_m2),
            "weighted_surface": _dinero(informe.weighted_surface),
        }
        metodo = informe.methodology or {}
        salida["confidence"] = {
            "level": informe.confidence,
            "score": _dinero(informe.confidence_score),
            # Por qué la confianza es la que es. El motor ya las calcula y las
            # guarda en `methodology`; hasta el 15/08 no salían de la base.
            "notes": metodo.get("notes") or [],
        }
        usados = informe.comparables_used or 0
        encontrados = informe.comparables_found or 0
        salida["comparables"] = {
            "found": informe.comparables_found,
            "used": informe.comparables_used,
            "excluded": max(encontrados - usados, 0),
            "items": await _items_de_comparables(
                session, report_id, org.id, incluir_descartados=incluir == "descartados"
            ),
        }
        # Doc 06 §2. Se calcula en el nodo 8, se paga, y hasta el 15/08 solo
        # llegaba al PDF: por API no salía (H-28 lo persistió, esto lo expone).
        if (contexto := metodo.get("market_context")) is not None:
            salida["market_context"] = contexto
        salida["narrative_md"] = informe.narrative_md
        # El MISMO renderer que usa el PDF, no otro.
        #
        # La pantalla del informe mostraba el markdown crudo —`## Resumen
        # ejecutivo` y `**USD 134.667**` con los asteriscos a la vista— porque
        # el front lo pintaba como texto plano. Y lo hacía por un motivo bueno:
        # el markdown lo escribió un LLM sobre texto de terceros, y renderizarlo
        # como HTML sin escapar fue el bug 16, un `<script>` que llegaba entero
        # al PDF.
        #
        # `markdown_a_html` es la solución que ya existía para el PDF:
        # CommonMark con `html: False`, así que el HTML crudo del origen se
        # escapa en vez de pasar. Una sola implementación para las dos salidas.
        if informe.narrative_md:
            from tasador.agents.nodes.render import markdown_a_html

            salida["narrative_html"] = markdown_a_html(informe.narrative_md)
        # Cómo el cliente sabe que hay PDF. Antes tenía que adivinar la URL y
        # comerse un 404 si el nodo 11 no había corrido.
        artefacto = (
            await session.execute(
                select(ReportArtifact.report_id).where(ReportArtifact.report_id == report_id)
            )
        ).scalar_one_or_none()
        salida["pdf_url"] = f"/v1/reports/{report_id}/pdf" if artefacto else None
        salida["limitations"] = [
            "Los valores corresponden a precios de publicación, no de escrituración.",
            "Este informe no constituye una tasación con validez legal.",
        ]

    if informe.status == "FAILED":
        salida["error_code"] = informe.error_code

    return salida


# ── POST /v1/reports/{id}/regenerate ─────────────────────────────────────
class PropertyPatch(BaseModel):
    """Lo que se aprende EN la visita. Todo opcional: solo pisa lo que viene.

    La dirección y el tipo no se tocan — si están mal, es otra propiedad y
    corresponde un informe nuevo, no una regeneración.
    """

    rooms: int | None = Field(default=None, ge=1, le=15)
    bedrooms: int | None = Field(default=None, ge=0, le=12)
    bathrooms: int | None = Field(default=None, ge=0, le=10)
    surface_total: Decimal | None = Field(default=None, gt=0, le=10000)
    surface_covered: Decimal | None = Field(default=None, gt=0, le=10000)
    age_years: int | None = Field(default=None, ge=0, le=200)
    floor_number: int | None = Field(default=None, ge=-5, le=200)
    has_elevator: bool | None = None
    condition: Literal[CONDITIONS] | None = None  # type: ignore[valid-type]
    orientation: Literal[ORIENTATIONS] | None = None  # type: ignore[valid-type]
    amenities: list[str] | None = Field(default=None, max_length=30)
    parking_spaces: int | None = Field(default=None, ge=0, le=10)
    expenses_ars: Decimal | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)


class RegenerateIn(BaseModel):
    property: PropertyPatch = PropertyPatch()


@router.post(
    "/reports/{report_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReportQueued,
    summary="Regenerar el informe (flujo post-visita)",
)
async def regenerar_informe(
    report_id: uuid.UUID,
    body: RegenerateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
) -> ReportQueued:
    """Crea un informe NUEVO sobre la misma propiedad, actualizada.

    El flujo real (doc 06 §2): antes de la visita se sabe poco, después se sabe
    todo. El informe anterior NO se muta —un informe es un documento, no una
    vista— y queda en el historial con los números que mostró.
    """
    s = get_settings()
    previo = (
        await session.execute(select(Report).where(Report.id == report_id, Report.org_id == org.id))
    ).scalar_one_or_none()
    if previo is None:
        raise HTTPException(status_code=404, detail="No existe ese informe.")
    if previo.status in ("QUEUED", "RUNNING"):
        raise HTTPException(
            status_code=409, detail="Ese informe todavía está corriendo; esperá a que termine."
        )

    sujeto = (
        await session.execute(
            select(SubjectProperty).where(
                SubjectProperty.id == previo.subject_property_id,
                SubjectProperty.org_id == org.id,
            )
        )
    ).scalar_one_or_none()
    if sujeto is None:
        raise HTTPException(status_code=404, detail="No existe ese informe.")

    cambios = body.property.model_dump(exclude_none=True)
    if (
        "surface_covered" in cambios
        and (total := cambios.get("surface_total", sujeto.surface_total))
        and cambios["surface_covered"] > total
    ):
        raise HTTPException(
            status_code=422,
            detail="La superficie cubierta no puede ser mayor que la total.",
        )
    for campo, valor in cambios.items():
        setattr(sujeto, campo, valor)

    nuevo = Report(
        org_id=org.id,
        subject_property_id=sujeto.id,
        status="QUEUED",
        requested_via="API",
        engine_version=s.engine_version,
        method_version=s.method_version,
        prompt_bundle_version=load_agents_config().bundle_hash,
    )
    session.add(nuevo)
    await session.commit()

    await _encolar(str(nuevo.id))
    log.info(
        "informe regenerado",
        report_id=str(nuevo.id),
        anterior=str(report_id),
        campos=sorted(cambios),
        org=org.slug,
    )
    return ReportQueued(
        report_id=nuevo.id,
        status="QUEUED",
        poll_url=f"/v1/reports/{nuevo.id}",
        estimated_seconds=90,
    )


# ── Compartir con el propietario ─────────────────────────────────────────
class ShareOut(BaseModel):
    url: str
    expires_at: datetime


@router.post(
    "/reports/{report_id}/share",
    response_model=ShareOut,
    summary="Link para compartir con el propietario",
)
async def compartir_informe(
    report_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
) -> ShareOut:
    """URL firmada de solo lectura, sin cuenta. Vence a los 30 días.

    Solo un informe TERMINADO se comparte: el propietario no tiene por qué ver
    el stepper, y un link a un informe que después falla es una mala promesa.
    """
    from tasador.security import DURACION_SHARE, emitir_share

    informe = (
        await session.execute(select(Report).where(Report.id == report_id, Report.org_id == org.id))
    ).scalar_one_or_none()
    if informe is None:
        raise HTTPException(status_code=404, detail="No existe ese informe.")
    if informe.status != "SUCCEEDED":
        raise HTTPException(status_code=409, detail="Solo se puede compartir un informe terminado.")

    token = emitir_share(str(informe.id), str(org.id))
    return ShareOut(
        url=f"/compartido/{token}",
        expires_at=datetime.now(UTC) + DURACION_SHARE,
    )


@router.get("/shared/{token}", summary="Informe compartido (público)")
async def informe_compartido(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Lo que ve el propietario. **Sin autenticación: el token ES la credencial.**

    Devuelve MENOS que `GET /v1/reports/{id}`: ni traza, ni costos, ni
    versiones, ni sugerencias operativas. El propietario recibe el resultado,
    no la cocina.
    """
    from tasador.db.models import Neighborhood
    from tasador.security import leer_share

    datos = leer_share(token)
    if datos is None:
        raise HTTPException(status_code=404, detail="El link no existe o venció.")
    rid, org_id = datos

    fila = (
        await session.execute(
            select(Report, SubjectProperty, Neighborhood.name)
            .join(SubjectProperty, SubjectProperty.id == Report.subject_property_id)
            .outerjoin(Neighborhood, Neighborhood.id == SubjectProperty.neighborhood_id)
            .where(
                Report.id == uuid.UUID(rid),
                Report.org_id == uuid.UUID(org_id),
                Report.status == "SUCCEEDED",
            )
        )
    ).first()
    if fila is None:
        raise HTTPException(status_code=404, detail="El link no existe o venció.")
    informe, sujeto, barrio = fila

    return {
        "address": sujeto.address_raw,
        "neighborhood": barrio,
        "property_type": sujeto.property_type,
        "rooms": sujeto.rooms,
        "surface_total": _dinero(sujeto.surface_total),
        "generated_at": informe.finished_at.isoformat() if informe.finished_at else None,
        "valuation": {
            "currency": informe.currency,
            "suggested_listing_price": {
                "low": _dinero(informe.value_low),
                "mid": _dinero(informe.value_mid),
                "high": _dinero(informe.value_high),
            },
            "expected_closing_range": {
                "low": _dinero(informe.closing_low),
                "high": _dinero(informe.closing_high),
            },
            "price_per_m2": _dinero(informe.price_per_m2),
        },
        "confidence": {"level": informe.confidence},
        "narrative_md": informe.narrative_md,
        "limitations": [
            "Los valores corresponden a precios de publicación, no de escrituración.",
            "Este informe no constituye una tasación con validez legal.",
        ],
    }


@router.get("/shared/{token}/pdf", summary="PDF del informe compartido (público)")
async def pdf_compartido(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    from fastapi.responses import FileResponse

    from tasador.db.models import ReportArtifact
    from tasador.security import leer_share

    datos = leer_share(token)
    if datos is None:
        raise HTTPException(status_code=404, detail="El link no existe o venció.")
    rid, org_id = datos

    fila = (
        await session.execute(
            select(Report, ReportArtifact)
            .join(ReportArtifact, ReportArtifact.report_id == Report.id)
            .where(Report.id == uuid.UUID(rid), Report.org_id == uuid.UUID(org_id))
        )
    ).first()
    if fila is None:
        raise HTTPException(status_code=404, detail="No hay un PDF para ese informe.")
    _, artefacto = fila
    ruta = Path(artefacto.pdf_path)
    if not await asyncio.to_thread(ruta.exists):
        raise HTTPException(status_code=404, detail="No hay un PDF para ese informe.")
    return FileResponse(ruta, media_type="application/pdf", filename="informe.pdf")
