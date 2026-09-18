"""`GET /v1/calidad` — la serie histórica de calidad (doc 07 §9).

Lee `eval.backtest_runs` y `eval.component_runs` y las devuelve como series
para la pantalla `/calidad`. No calcula nada: los números los produjeron
`tasador.eval.run` y los evals de componente, con sus versiones. Acá solo se
ordenan para que un humano los compare.

**No hay botón "correr backtest" en esta versión, a propósito.** El backtest
tarda minutos y ya tiene CLI con historia; un botón que dispara un job largo
merece cola y notificación propias, y hacerlo a medias sería un gate que puede
pasar sin medir nada (decisión en el análisis del 14/08).

Solo admin: los números de calidad del motor son del que lo opera, no de cada
agente inmobiliario. Una API key también entra (es del tenant y puede todo lo
del tenant).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.base import get_session
from tasador.db.models import BacktestRun, ComponentRun
from tasador.v1.auth import Principal, resolve_principal

router = APIRouter()


async def require_admin(p: Annotated[Principal, Depends(resolve_principal)]) -> Principal:
    """403 y no 401: la identidad es válida, el rol no alcanza.

    `header_dev` pasa porque ese camino solo existe fuera de producción
    (apagado por código en `_por_header_de_desarrollo`) y es el que usan los
    tests y los scripts, que no tienen usuario.
    """
    if p.via == "header_dev":
        return p
    if not p.es_admin:
        raise HTTPException(status_code=403, detail="Solo un administrador puede ver esto.")
    return p


class BacktestOut(BaseModel):
    id: uuid.UUID
    dataset: str
    sample: int
    seed: int
    n_cases: int
    n_evaluated: int
    coverage: float | None
    mdape: float | None
    ppe20: float | None
    hit_rate: float | None
    bias: float | None
    baseline_mdape: float | None
    baseline_ppe20: float | None
    por_barrio: dict[str, Any]
    por_confianza: dict[str, Any]
    engine_version: str
    method_version: str
    prompt_bundle_version: str
    notes: str | None
    created_at: datetime


class ComponenteOut(BaseModel):
    componente: str
    metrica: str
    valor: float
    n: int
    objetivo: float | None
    engine_version: str
    prompt_bundle_version: str
    created_at: datetime


class CalidadOut(BaseModel):
    backtests: list[BacktestOut]
    componentes: list[ComponenteOut]


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


@router.get("/calidad", response_model=CalidadOut, summary="Serie histórica de calidad")
async def calidad(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[Principal, Depends(require_admin)],
    limit: int = Query(default=30, ge=1, le=200),
) -> CalidadOut:
    backtests = (
        (
            await session.execute(
                select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    # Las corridas de componente vienen todas juntas y el front las agrupa por
    # (componente, metrica): la mediana de las últimas 5 —que es como se leen
    # estos números, ver ESTADO §5.1— se calcula sobre la serie, no acá.
    componentes = (
        (
            await session.execute(
                select(ComponentRun).order_by(ComponentRun.created_at.desc()).limit(limit * 3)
            )
        )
        .scalars()
        .all()
    )

    return CalidadOut(
        backtests=[
            BacktestOut(
                id=b.id,
                dataset=b.dataset,
                sample=b.sample,
                seed=b.seed,
                n_cases=b.n_cases,
                n_evaluated=b.n_evaluated,
                coverage=_f(b.coverage),
                mdape=_f(b.mdape),
                ppe20=_f(b.ppe20),
                hit_rate=_f(b.hit_rate),
                bias=_f(b.bias),
                baseline_mdape=_f(b.baseline_mdape),
                baseline_ppe20=_f(b.baseline_ppe20),
                por_barrio=b.por_barrio,
                por_confianza=b.por_confianza,
                engine_version=b.engine_version,
                method_version=b.method_version,
                prompt_bundle_version=b.prompt_bundle_version,
                notes=b.notes,
                created_at=b.created_at,
            )
            for b in backtests
        ],
        componentes=[
            ComponenteOut(
                componente=c.componente,
                metrica=c.metrica,
                valor=float(c.valor),
                n=c.n,
                objetivo=_f(c.objetivo),
                engine_version=c.engine_version,
                prompt_bundle_version=c.prompt_bundle_version,
                created_at=c.created_at,
            )
            for c in componentes
        ],
    )
