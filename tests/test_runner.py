"""`runner._persist_result`: la frontera entre "el grafo calculó" y "el cliente lo ve".

Estaba al **0% de cobertura**, y tres hallazgos de la auditoría del 15/08 vivían
en esta única función:

  · el costo de un informe que no termina quedaba en cero (7,5% de subestimación
    sobre el total del tenant),
  · `degraded_nodes` se calculaba y no se guardaba,
  · `market_context` se calculaba, se pagaba y se tiraba.

Los tres son invisibles desde los tests del grafo —que miran el estado— y desde
los de la API —que miran filas que alguien tuvo que escribir—. Este archivo mira
justo el medio.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.state import ReportState, estado_inicial


@pytest.fixture(autouse=True)
def _runner_contra_la_base_de_test(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """`_persist_result` y `_persist_event` abren su PROPIA sesión.

    Y con razón: la traza de un nodo que falló tiene que sobrevivir aunque la
    transacción del nodo se haya abortado. El costo es que no se les puede
    inyectar la sesión del test, así que se apunta la fábrica a la base
    descartable que creó la fixture `db`.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import tasador.agents.runner as runner_mod
    import tasador.db.base as base_mod

    maker = async_sessionmaker(db.info["engine"], expire_on_commit=False)
    monkeypatch.setattr(base_mod, "get_session_factory", lambda: maker)
    monkeypatch.setattr(runner_mod, "get_session_factory", lambda: maker)
    return maker


async def _listing(session: AsyncSession) -> str:
    """Un aviso real: `report_comparables.listing_id` tiene FK a `corpus.listings`."""
    from tasador.db.models import Listing

    li = Listing(
        source="PORTAL_A",
        source_id=f"t{uuid.uuid4().hex[:12]}",
        url="https://ejemplo.test/1",
        operation="SALE",
        price=Decimal("175000"),
        currency="USD",
        address_raw="Gorriti 5000",
        content_hash=uuid.uuid4().hex,
    )
    session.add(li)
    # COMMIT y no flush: `_persist_result` usa otra sesión y no vería la fila.
    await session.commit()
    return str(li.id)


async def _informe(session: AsyncSession) -> tuple[str, str]:
    from tasador.db.models import Organization, Report, SubjectProperty

    org = Organization(name="Test", slug=f"t{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    sujeto = SubjectProperty(
        org_id=org.id, address_raw="Gorriti 5000", property_type="departamento"
    )
    session.add(sujeto)
    await session.flush()
    r = Report(
        org_id=org.id,
        subject_property_id=sujeto.id,
        status="RUNNING",
        engine_version="test",
        method_version="test",
        prompt_bundle_version="test",
    )
    session.add(r)
    await session.flush()
    await session.commit()
    return str(r.id), str(org.id)


def _estado(report_id: str, org_id: str, listing_id: str, **extra: Any) -> ReportState:
    e = estado_inicial(report_id, org_id, str(uuid.uuid4()))
    e["valuation"] = {
        "currency": "USD",
        "value_low": "191424",
        "value_mid": "239280",
        "value_high": "287136",
        "closing_low": "203388",
        "closing_high": "227316",
        "price_per_m2": "3988",
        "weighted_surface": "60.0",
        "comparables_found": 10,
        "comparables_used": 8,
        "confidence": "ALTA",
        "confidence_score": "0.9",
        "detail": [
            {
                "listing_id": listing_id,
                "included": True,
                "snapshot_price": "175000",
                "snapshot_currency": "USD",
                "snapshot_surface": "70",
                "raw_price_per_m2": "2500",
                "adjusted_price_per_m2": "2500",
                "adjustments": {"total": "1.0000"},
            }
        ],
    }
    e.update(extra)  # type: ignore[typeddict-item]
    return e


async def _persistir(report_id: str, estado: ReportState) -> None:
    from tasador.agents.runner import _persist_result

    await _persist_result(report_id, estado, 1234)


# ── El estado derivado ───────────────────────────────────────────────────
async def test_con_valor_termina_succeeded(db: AsyncSession):
    from tasador.db.models import Report

    rid, oid = await _informe(db)
    lid = await _listing(db)
    await _persistir(rid, _estado(rid, oid, lid))
    r = (await db.execute(select(Report).where(Report.id == uuid.UUID(rid)))).scalar_one()
    await db.refresh(r)
    assert r.status == "SUCCEEDED"
    assert r.value_mid == Decimal("239280.00")
    assert r.duration_ms == 1234


async def test_sin_valor_no_puede_terminar_succeeded(db: AsyncSession):
    """Sin `value_mid` el informe termina FAILED, no SUCCEEDED.

    El CHECK `valores_coherentes` de la base lo rechazaría igual; el runner lo
    deriva antes para que el error tenga contexto en vez de ser un
    IntegrityError críptico.
    """
    from tasador.db.models import Report

    rid, oid = await _informe(db)
    lid = await _listing(db)
    estado = _estado(rid, oid, lid)
    estado["valuation"] = {}
    await _persistir(rid, estado)
    r = (await db.execute(select(Report).where(Report.id == uuid.UUID(rid)))).scalar_one()
    await db.refresh(r)
    assert r.status == "FAILED"


# ── Lo que se perdía ─────────────────────────────────────────────────────
async def test_los_nodos_degradados_quedan_en_el_informe(db: AsyncSession):
    """Un informe al que le faltó el contexto de mercado no puede ser
    indistinguible de uno completo (doc 04 §3)."""
    from tasador.db.models import Report

    rid, oid = await _informe(db)
    lid = await _listing(db)
    await _persistir(rid, _estado(rid, oid, lid, degraded_nodes=["market_context"]))
    r = (await db.execute(select(Report).where(Report.id == uuid.UUID(rid)))).scalar_one()
    await db.refresh(r)
    assert r.methodology["degraded_nodes"] == ["market_context"]


async def test_el_contexto_de_mercado_se_persiste(db: AsyncSession):
    """El nodo 8 corre cuatro consultas y una crew: si no se guarda, se paga por
    un dato que se tira, doc 06 §2 promete algo que no se puede entregar, y un
    informe entregado no se puede reconstruir."""
    from tasador.db.models import Report

    rid, oid = await _informe(db)
    lid = await _listing(db)
    contexto = {"barrio": "Palermo", "stock": {"stock_activo": 8497, "usd_m2_mediano": 2831}}
    await _persistir(rid, _estado(rid, oid, lid, market_context=contexto))
    r = (await db.execute(select(Report).where(Report.id == uuid.UUID(rid)))).scalar_one()
    await db.refresh(r)
    assert r.methodology["market_context"]["stock"]["usd_m2_mediano"] == 2831


# ── El costo ─────────────────────────────────────────────────────────────
async def test_el_costo_sale_de_la_traza(db: AsyncSession):
    from tasador.db.models import Report, ReportEvent

    rid, oid = await _informe(db)
    lid = await _listing(db)
    for i, costo in enumerate([Decimal("0.001"), Decimal("0.019"), Decimal("0.0005")]):
        db.add(
            ReportEvent(
                report_id=uuid.UUID(rid),
                seq=i + 1,
                node=f"n{i}",
                status="OK",
                cost_usd=costo,
                tokens_in=100,
                tokens_out=50,
            )
        )
    await db.commit()

    await _persistir(rid, _estado(rid, oid, lid))
    r = (await db.execute(select(Report).where(Report.id == uuid.UUID(rid)))).scalar_one()
    await db.refresh(r)
    assert r.cost_usd == Decimal("0.020500")
    assert r.tokens_in == 300 and r.tokens_out == 150


async def test_reintentar_no_duplica_los_comparables(db: AsyncSession):
    """`report_comparables` es el SNAPSHOT del informe, no un log.

    Si el worker retoma el mismo informe, cada comparable aparecía dos veces y
    el detalle que ve el cliente mostraba el doble.
    """
    from tasador.db.models import ReportComparable

    rid, oid = await _informe(db)
    lid = await _listing(db)
    estado = _estado(rid, oid, lid)
    await _persistir(rid, estado)
    await _persistir(rid, estado)  # el reintento

    n = len(
        (
            await db.execute(
                select(ReportComparable).where(ReportComparable.report_id == uuid.UUID(rid))
            )
        )
        .scalars()
        .all()
    )
    assert n == 1, f"el reintento duplicó los comparables: hay {n}"


# ── El cosechador ────────────────────────────────────────────────────────
async def test_el_cosechador_no_toca_un_informe_que_recien_arranco(db: AsyncSession):
    """`job_timeout` de arq es 1.800 s y el primer informe de un barrio nuevo
    tarda ~5 min: un umbral corto mata informes vivos."""
    from datetime import UTC, datetime

    from tasador.db.models import Report

    rid, _ = await _informe(db)
    r = (await db.execute(select(Report).where(Report.id == uuid.UUID(rid)))).scalar_one()
    r.started_at = datetime.now(UTC)
    await db.commit()

    import scripts.cosechar_colgados as cosechar

    assert cosechar.HORAS_RUNNING >= 1.0, "el umbral tiene que superar el job_timeout de arq"


@pytest.mark.parametrize("estado_previo", ["RUNNING", "QUEUED"])
async def test_el_cosechador_encuentra_los_colgados(db: AsyncSession, estado_previo: str):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import or_

    from tasador.db.models import Report

    rid, _ = await _informe(db)
    viejo = datetime.now(UTC) - timedelta(hours=9)
    r = (await db.execute(select(Report).where(Report.id == uuid.UUID(rid)))).scalar_one()
    r.status = estado_previo
    r.started_at = viejo if estado_previo == "RUNNING" else None
    r.created_at = viejo
    await db.commit()

    import scripts.cosechar_colgados as cosechar

    limite = datetime.now(UTC) - timedelta(hours=cosechar.HORAS_RUNNING)
    encontrados = (
        (
            await db.execute(
                select(Report).where(
                    or_(
                        (Report.status == "RUNNING") & (Report.started_at < limite),
                        (Report.status == "QUEUED") & (Report.created_at < limite),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(str(x.id) == rid for x in encontrados)
