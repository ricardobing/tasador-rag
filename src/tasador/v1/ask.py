"""`POST /v1/reports/{id}/ask` — «Preguntale al informe» (doc 18 §5).

El índice de un informe (sus hechos + la metodología) se construye la
primera vez que alguien le pregunta y se cachea en memoria por proceso; la
metodología se embebe una sola vez. La primera pregunta a un informe paga el
embedding de ~70 pasajes en CPU; las siguientes, solo el de la pregunta.

Cada pregunta queda en `core.report_events` como un evento `ask`, con su
costo: el gasto de un informe tiene que seguir siendo una suma y no una
estimación (ADR sobre contabilidad, `llm.py`).
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.base import get_session
from tasador.db.models import Organization, Report, ReportEvent
from tasador.llm import LlmClient
from tasador.rag import qa
from tasador.rag.embedder import get_embedder
from tasador.settings import get_settings
from tasador.v1.auth import resolve_org
from tasador.v1.reports import obtener_informe

log = structlog.get_logger()
router = APIRouter()


class Pregunta(BaseModel):
    pregunta: str = Field(min_length=3, max_length=500)


class Cita(BaseModel):
    id: str
    texto: str
    fuente: str


class Respuesta(BaseModel):
    report_id: str
    pregunta: str
    respuesta: str
    citas: list[Cita]
    rechazada: bool
    motivo: str | None
    cost_usd: float
    duration_ms: int


# Índices por informe, acotados: 32 informes x ~70 vectores de 1024 floats.
_INDICES: OrderedDict[str, qa.Indice] = OrderedDict()
_METODOLOGIA: qa.Indice | None = None
_MAX_INDICES = 32


async def _indice_de(session: AsyncSession, report_id: uuid.UUID, org: Organization) -> qa.Indice:
    global _METODOLOGIA
    clave = str(report_id)
    if clave in _INDICES:
        _INDICES.move_to_end(clave)
        return _INDICES[clave]

    embedder = get_embedder()
    if _METODOLOGIA is None:
        _METODOLOGIA = await qa.Indice.construir(qa.fragmentos_de_metodologia(), embedder)

    informe = await obtener_informe(report_id, session, org, incluir="descartados")
    if informe.get("status") != "SUCCEEDED":
        raise HTTPException(status_code=409, detail="El informe todavía no tiene resultado.")
    hechos = await qa.Indice.construir(qa.fragmentos_del_informe(informe), embedder)
    indice = qa.Indice(
        [*hechos.fragmentos, *_METODOLOGIA.fragmentos],
        [*hechos.vectores, *_METODOLOGIA.vectores],
    )
    _INDICES[clave] = indice
    if len(_INDICES) > _MAX_INDICES:
        _INDICES.popitem(last=False)
    return indice


@router.post(
    "/reports/{report_id}/ask",
    response_model=Respuesta,
    summary="Preguntar sobre un informe, con citas verificadas",
)
async def preguntar(
    report_id: uuid.UUID,
    cuerpo: Pregunta,
    session: Annotated[AsyncSession, Depends(get_session)],
    org: Annotated[Organization, Depends(resolve_org)],
) -> Respuesta:
    existe = (
        await session.execute(
            select(Report.id).where(Report.id == report_id, Report.org_id == org.id)
        )
    ).scalar_one_or_none()
    if existe is None:
        raise HTTPException(status_code=404, detail="No existe ese informe.")

    s = get_settings()
    t0 = time.perf_counter()
    indice = await _indice_de(session, report_id, org)
    cliente = LlmClient()
    try:
        r = await qa.responder(
            cuerpo.pregunta,
            indice,
            embedder=get_embedder(),
            cliente=cliente,
            task=s.qa_task,
            umbral=s.qa_umbral,
            umbral_lexico=s.qa_umbral_lexico,
            k=s.qa_k,
        )
    finally:
        await cliente.close()
    ms = int((time.perf_counter() - t0) * 1000)

    await _registrar(session, report_id, org, cuerpo.pregunta, r, ms)
    return Respuesta(
        report_id=str(report_id),
        pregunta=cuerpo.pregunta,
        respuesta=r.respuesta,
        citas=[Cita(id=f.id, texto=f.texto, fuente=f.fuente) for f in r.citas],
        rechazada=r.rechazada,
        motivo=r.motivo,
        cost_usd=r.cost_usd,
        duration_ms=ms,
    )


async def _registrar(
    session: AsyncSession,
    report_id: uuid.UUID,
    org: Organization,
    pregunta: str,
    r: qa.ResultadoQA,
    ms: int,
) -> None:
    """Un evento `ask` por pregunta, con costo, y el costo sumado al informe."""
    ultimo = (
        await session.execute(
            select(func.coalesce(func.max(ReportEvent.seq), 0)).where(
                ReportEvent.report_id == report_id
            )
        )
    ).scalar_one()
    detalle: dict[str, Any] = {
        "pregunta": pregunta[:200],
        "rechazada": r.rechazada,
        "motivo": r.motivo,
        "citas": [f.id for f in r.citas],
        "mejor_coseno": round(r.mejor_coseno, 3),
        "intentos": r.intentos,
    }
    session.add(
        ReportEvent(
            report_id=report_id,
            seq=int(ultimo) + 1,
            node="ask",
            status="OK" if not r.rechazada else "REJECTED",
            model=r.usos[-1].provider_model if r.usos else None,
            tokens_in=sum(u.tokens_in for u in r.usos) or None,
            tokens_out=sum(u.tokens_out for u in r.usos) or None,
            cost_usd=Decimal(str(r.cost_usd)),
            duration_ms=ms,
            detail=detalle,
            trace_id=None,
        )
    )
    informe = (
        await session.execute(select(Report).where(Report.id == report_id, Report.org_id == org.id))
    ).scalar_one()
    informe.cost_usd = (informe.cost_usd or Decimal(0)) + Decimal(str(r.cost_usd))
    await session.commit()
