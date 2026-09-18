"""Instrumentación común a todos los nodos.

Cada nodo del grafo se envuelve acá para que, pase lo que pase, quede una fila
en `core.report_events` con qué corrió, cuánto tardó, qué modelo lo atendió y
cuánto costó. **Esa tabla se escribe durante la corrida, no al final**: es lo
que alimenta el stepper en vivo, y un proceso de 90 segundos sin feedback se
percibe como roto (doc 06 §2).

También es donde vive la política de errores. Un nodo declara en
`config/agents.yaml` qué pasa si se cae:

    fail    -> el informe entero falla
    degrade -> el informe sigue sin lo que aportaba ese nodo
    skip    -> no corre

"Degradar antes que fallar; fallar explícito antes que inventar" (doc 04 §3).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from tasador.agents.config import NodeConfig
from tasador.agents.state import ReportState
from tasador.llm import UsageLedger

log = structlog.get_logger()


@dataclass(slots=True)
class NodeResult:
    """Lo que devuelve un nodo: qué cambió del estado y qué costó."""

    updates: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    usage: UsageLedger | None = None


NodeFn = Callable[[ReportState, NodeConfig], Awaitable[NodeResult]]


class NodeError(RuntimeError):
    """Falla del nodo con un código estable para el informe."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


async def _persist_event(
    report_id: str,
    *,
    seq: int,
    node: str,
    status: str,
    model: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    cost_usd: Decimal | None,
    duration_ms: int,
    detail: dict[str, Any],
    trace_id: str | None,
) -> None:
    """Escribe la traza en su propia sesión, ya.

    Sesión aparte a propósito: si el nodo falló y su transacción se abortó, la
    traza de esa falla tiene que sobrevivir igual. Una traza que solo existe
    cuando todo salió bien no sirve para diagnosticar nada.
    """
    from sqlalchemy import func, select, update

    from tasador.db.base import get_session_factory
    from tasador.db.models import Report, ReportEvent

    try:
        async with get_session_factory()() as session:
            session.add(
                ReportEvent(
                    report_id=report_id,
                    seq=seq,
                    node=node,
                    status=status,
                    model=model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    detail=detail,
                    trace_id=trace_id,
                )
            )
            await session.flush()
            # ⚠️ El costo se actualiza ACÁ, evento por evento, y no solo al
            # final del informe.
            #
            # `runner._persist_result` lo sumaba al terminar. Un informe que no
            # termina —el worker muere, el job se pierde— deja sus eventos con
            # el costo real y `reports.cost_usd` en cero. Medido el 15/08:
            # `inmo-demo` tenía USD 1,455851 según `reports` y USD 1,565145 según
            # la traza, un 7,5% menos, y un informe en RUNNING con 22 eventos y
            # USD 0,109 gastados figuraba en 0.
            #
            # Es un SUM sobre todos los eventos, así que es idempotente ante un
            # reintento — a diferencia de incrementar.
            agregado = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(ReportEvent.cost_usd), 0),
                        func.coalesce(func.sum(ReportEvent.tokens_in), 0),
                        func.coalesce(func.sum(ReportEvent.tokens_out), 0),
                    ).where(ReportEvent.report_id == report_id)
                )
            ).one()
            await session.execute(
                update(Report)
                .where(Report.id == report_id)
                .values(
                    cost_usd=agregado[0], tokens_in=int(agregado[1]), tokens_out=int(agregado[2])
                )
            )
            await session.commit()
    except Exception:
        # Que falle la traza no puede tumbar el informe. Se loguea y sigue.
        log.exception("no se pudo persistir el report_event", node=node, report_id=report_id)


def instrument(cfg: NodeConfig, fn: NodeFn) -> Callable[[ReportState], Awaitable[dict[str, Any]]]:
    """Envuelve un nodo con traza, reintentos y política de error."""

    async def wrapped(state: ReportState) -> dict[str, Any]:
        report_id = state["report_id"]
        t0 = time.perf_counter()
        intentos = 0
        ultimo: Exception | None = None
        resultado: NodeResult | None = None
        status = "OK"

        while intentos < cfg.max_attempts:
            intentos += 1
            try:
                resultado = await fn(state, cfg)
                status = "OK" if intentos == 1 else "RETRIED"
                ultimo = None
                break
            # A propósito se atrapa TODO: un nodo que revienta con algo
            # inesperado tiene que dejar su fila en la traza igual. La política
            # de `on_error` decide después si eso tumba el informe o no.
            except Exception as e:
                ultimo = e
                log.warning(
                    "nodo falló", node=cfg.id, intento=intentos, error=f"{type(e).__name__}: {e}"
                )

        ms = int((time.perf_counter() - t0) * 1000)
        ledger = resultado.usage if resultado else None
        detalle: dict[str, Any] = dict(resultado.detail) if resultado else {}

        if ledger and ledger.usos:
            detalle["llm"] = {
                "llamadas": len(ledger.usos),
                "degradado": ledger.degraded,
                "modelos": sorted({u.provider_model for u in ledger.usos}),
            }

        if ultimo is not None:
            status = "FAILED"
            code = getattr(ultimo, "code", type(ultimo).__name__)
            detalle["error"] = f"{type(ultimo).__name__}: {ultimo}"[:500]
            detalle["on_error"] = cfg.on_error

            await _persist_event(
                report_id,
                seq=cfg.seq,
                node=cfg.id,
                status=status,
                model=cfg.task,
                tokens_in=ledger.tokens_in if ledger else None,
                tokens_out=ledger.tokens_out if ledger else None,
                cost_usd=ledger.cost_usd if ledger else None,
                duration_ms=ms,
                detail=detalle,
                trace_id=ledger.usos[0].call_id if ledger and ledger.usos else None,
            )

            if cfg.on_error == "fail":
                # No se sigue: un informe al que le faltó un nodo obligatorio
                # no es un informe degradado, es un informe mal hecho.
                return {
                    "status": "FAILED",
                    "error_code": code,
                    "error_detail": f"{cfg.id}: {ultimo}"[:1000],
                    "errors": [f"{cfg.id}: {ultimo}"],
                }
            return {
                "errors": [f"{cfg.id} (degradado): {ultimo}"],
                "degraded_nodes": [cfg.id],
            }

        assert resultado is not None
        await _persist_event(
            report_id,
            seq=cfg.seq,
            node=cfg.id,
            status=status,
            model=cfg.task,
            tokens_in=ledger.tokens_in if ledger else None,
            tokens_out=ledger.tokens_out if ledger else None,
            cost_usd=ledger.cost_usd if ledger else None,
            duration_ms=ms,
            detail=detalle,
            trace_id=ledger.usos[0].call_id if ledger and ledger.usos else None,
        )

        updates = dict(resultado.updates)
        if ledger and ledger.degraded:
            updates.setdefault("degraded_nodes", []).append(cfg.id)
        return updates

    wrapped.__name__ = f"node_{cfg.id}"
    return wrapped
