"""Corre el grafo para un informe y persiste el resultado.

Separa dos responsabilidades a propósito:

  · el **grafo** trabaja sobre un dict serializable y no sabe de SQLAlchemy;
  · el **runner** carga de la base, lo invoca y guarda lo que salió.

Así el grafo se puede correr en un test sin base de datos, y el backtest puede
llamar al nodo 7 sin arrastrar el resto.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import load_agents_config
from tasador.agents.graph import checkpointer_dsn, compile_graph
from tasador.agents.state import ReportState, estado_inicial
from tasador.db.base import get_session_factory
from tasador.db.models import Report, ReportComparable, SubjectProperty
from tasador.settings import get_settings

log = structlog.get_logger()


def _subject_dict(sp: SubjectProperty) -> dict[str, Any]:
    """Fila de `subject_properties` → dict del estado.

    El nodo 1 (`normalize_subject`) enriquece esto con la dirección desglosada,
    lat/lng y el barrio resuelto. Mientras ese nodo sea un stub, el informe
    arranca con lo que cargó el usuario y nada más — que es exactamente lo que
    hay, sin inventar.
    """
    return {
        "address_raw": sp.address_raw,
        "city": sp.city,
        "province": sp.province,
        "neighborhood_id": str(sp.neighborhood_id) if sp.neighborhood_id else None,
        "lat": str(sp.lat) if sp.lat is not None else None,
        "lng": str(sp.lng) if sp.lng is not None else None,
        "property_type": sp.property_type,
        "rooms": sp.rooms,
        "bedrooms": sp.bedrooms,
        "bathrooms": sp.bathrooms,
        "surface_total": str(sp.surface_total) if sp.surface_total is not None else None,
        "surface_covered": str(sp.surface_covered) if sp.surface_covered is not None else None,
        "age_years": sp.age_years,
        "floor_number": sp.floor_number,
        "has_elevator": sp.has_elevator,
        "condition": sp.condition,
        "orientation": sp.orientation,
        "amenities": list(sp.amenities or []),
        "parking_spaces": sp.parking_spaces or 0,
        "expenses_ars": str(sp.expenses_ars) if sp.expenses_ars is not None else None,
        # `notes` NO va al estado: puede tener datos del propietario y no tiene
        # por qué llegar a un proveedor de LLM (doc 10 §3).
    }


def _d(v: Any) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


async def _persist_comparables(
    session: AsyncSession, report_id: Any, valuation: dict[str, Any]
) -> int:
    """Guarda el SNAPSHOT de cada comparable, incluidos los excluidos.

    Snapshot y no FK sola: un informe entregado en septiembre tiene que seguir
    mostrando en diciembre los mismos números, aunque el aviso haya bajado de
    precio o desaparecido. Un informe es un documento, no una vista.
    """
    from sqlalchemy import delete

    # Reintentar el mismo informe volvía a insertar TODOS los comparables: la
    # tabla es el snapshot del informe, no un log. Sin esto, un informe que el
    # worker retoma muestra cada comparable dos veces.
    await session.execute(delete(ReportComparable).where(ReportComparable.report_id == report_id))

    filas = 0
    for d in valuation.get("detail", []):
        precio = _d(d.get("snapshot_price"))
        if precio is None:
            continue
        session.add(
            ReportComparable(
                report_id=report_id,
                listing_id=d["listing_id"],
                included=bool(d.get("included")),
                exclusion_reason=d.get("exclusion_reason")
                or (None if d.get("included") else "sin_motivo_declarado"),
                distance_m=d.get("distance_m"),
                snapshot_price=precio,
                snapshot_currency=d.get("snapshot_currency") or "USD",
                snapshot_surface=_d(d.get("snapshot_surface")),
                raw_price_per_m2=_d(d.get("raw_price_per_m2")),
                adjustments=d.get("adjustments") or {},
                adjusted_price_per_m2=_d(d.get("adjusted_price_per_m2")),
            )
        )
        filas += 1
    return filas


async def run_report(report_id: str) -> ReportState:
    """Corre el grafo completo para un informe ya encolado."""
    factory = get_session_factory()
    s = get_settings()
    cfg = load_agents_config()

    async with factory() as session:
        report = (
            await session.execute(select(Report).where(Report.id == report_id))
        ).scalar_one_or_none()
        if report is None:
            raise ValueError(f"no existe el informe {report_id}")
        subject = (
            await session.execute(
                select(SubjectProperty).where(SubjectProperty.id == report.subject_property_id)
            )
        ).scalar_one()

        report.status = "RUNNING"
        report.started_at = datetime.now(UTC)
        report.prompt_bundle_version = cfg.bundle_hash
        org_id, sp_id = str(report.org_id), str(report.subject_property_id)
        datos_sujeto = _subject_dict(subject)
        await session.commit()

    estado = estado_inicial(str(report_id), org_id, sp_id)
    estado["subject"] = datos_sujeto

    t0 = time.perf_counter()
    final: ReportState
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        dsn = checkpointer_dsn(s.database_url)
        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            await saver.setup()
            grafo = compile_graph(checkpointer=saver, cfg=cfg)
            final = await grafo.ainvoke(
                estado,
                # `thread_id` = el informe. Reintentar el mismo informe retoma
                # desde el último checkpoint en vez de rehacer los nodos caros.
                config={"configurable": {"thread_id": str(report_id)}},
            )
    except Exception as e:
        log.exception("el grafo se cayó", report_id=str(report_id))
        final = {
            **estado,
            "status": "FAILED",
            "error_code": type(e).__name__,
            "error_detail": str(e)[:1000],
        }

    ms = int((time.perf_counter() - t0) * 1000)
    await _persist_result(report_id, final, ms)
    return final


async def _persist_result(report_id: str, final: ReportState, duration_ms: int) -> None:
    from sqlalchemy import func as sqlfunc

    from tasador.db.models import ReportEvent

    factory = get_session_factory()
    async with factory() as session:
        report = (await session.execute(select(Report).where(Report.id == report_id))).scalar_one()

        v = final.get("valuation") or {}
        estado = final.get("status") or "RUNNING"
        # Si ningún nodo declaró un estado terminal y hay valor, terminó bien.
        if estado == "RUNNING":
            estado = "SUCCEEDED" if v.get("value_mid") else "FAILED"
        if estado == "SUCCEEDED" and not v.get("value_mid"):
            # El CHECK de la base lo rechazaría igual; mejor un error nuestro
            # con contexto que un IntegrityError críptico.
            estado = "FAILED"
            report.error_code = "SIN_VALOR"
            report.error_detail = "El grafo terminó sin producir un valor."

        report.status = estado
        report.currency = v.get("currency") or "USD"
        report.value_low = _d(v.get("value_low"))
        report.value_mid = _d(v.get("value_mid"))
        report.value_high = _d(v.get("value_high"))
        report.closing_low = _d(v.get("closing_low"))
        report.closing_high = _d(v.get("closing_high"))
        report.price_per_m2 = _d(v.get("price_per_m2"))
        report.weighted_surface = _d(v.get("weighted_surface"))
        report.comparables_found = v.get("comparables_found")
        report.comparables_used = v.get("comparables_used")
        report.dispersion = _d(v.get("dispersion"))
        report.confidence = v.get("confidence")
        report.confidence_score = _d(v.get("confidence_score"))
        # ⚠️ `methodology` lleva TRES cosas, no solo la valuación.
        #
        # Guardaba `v` pelado y se perdían dos que el grafo sí calcula:
        #
        #  · `degraded_nodes` — un informe al que le faltó el contexto de
        #    mercado era indistinguible de uno completo a nivel de informe.
        #    Había que hacer un JOIN con `report_events` filtrando por FAILED
        #    para enterarse. Doc 04 §3 dice "degradar antes que fallar"; la
        #    contraparte de esa política es que la degradación se vea.
        #
        #  · `market_context` — el nodo 8 corre cuatro consultas SQL y una crew
        #    de CrewAI, y el resultado vivía en memoria hasta que el redactor lo
        #    usaba. No quedaba en ningún lado: doc 06 §2 lo documenta como parte
        #    de la respuesta, se paga, y un informe entregado no se podía
        #    reconstruir porque el corpus cambia y el snapshot no existía.
        report.methodology = {
            **v,
            "degraded_nodes": list(final.get("degraded_nodes") or []),
            "market_context": final.get("market_context") or {},
        }
        report.narrative_md = final.get("draft_md") or None
        report.critic_rejections = final.get("critic_rejections", 0)
        report.insufficient_reason = final.get("insufficient_reason") or v.get(
            "insufficient_reason"
        )
        if final.get("error_code"):
            report.error_code = final["error_code"]
            report.error_detail = final.get("error_detail")
        report.duration_ms = duration_ms
        report.finished_at = datetime.now(UTC)

        # El costo del informe se SUMA de la traza, no se estima. Es la misma
        # tabla que ve el usuario en el desglose: si no coinciden, mentimos.
        agregado = (
            await session.execute(
                select(
                    sqlfunc.coalesce(sqlfunc.sum(ReportEvent.cost_usd), 0),
                    sqlfunc.coalesce(sqlfunc.sum(ReportEvent.tokens_in), 0),
                    sqlfunc.coalesce(sqlfunc.sum(ReportEvent.tokens_out), 0),
                ).where(ReportEvent.report_id == report_id)
            )
        ).one()
        report.cost_usd, report.tokens_in, report.tokens_out = (
            Decimal(str(agregado[0])),
            int(agregado[1]),
            int(agregado[2]),
        )

        if v.get("detail"):
            await _persist_comparables(session, report.id, v)

        await session.commit()

    log.info(
        "informe terminado",
        report_id=str(report_id),
        status=final.get("status"),
        duration_ms=duration_ms,
    )
