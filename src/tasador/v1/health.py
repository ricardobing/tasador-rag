"""Liveness y readiness.

Diferencia que importa en producción:
  /health -> ¿el proceso está vivo?  (no toca dependencias, nunca falla por
             culpa de otro servicio; si esto falla, reiniciar el contenedor)
  /ready  -> ¿puede atender tráfico? (verifica Postgres, Redis y LiteLLM)

Ninguno de los dos revela detalles internos: un 503 con el stack trace de
Postgres es información gratis para un atacante.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tasador.settings import get_settings

router = APIRouter()
log = structlog.get_logger()

Status = Literal["ok", "degraded", "down"]


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    s = get_settings()
    return {"status": "ok", "version": s.engine_version}


async def _check_postgres() -> Status:
    s = get_settings()
    try:
        engine = create_async_engine(
            s.database_url.replace("postgresql+psycopg", "postgresql+psycopg"),
            pool_pre_ping=True,
        )
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        await engine.dispose()
    except Exception:
        log.warning("readiness: postgres inaccesible", exc_info=True)
        return "down"
    return "ok"


async def _check_redis() -> Status:
    from redis.asyncio import Redis

    s = get_settings()
    try:
        client: Redis = Redis.from_url(s.redis_url)
        await client.ping()
        await client.aclose()
    except Exception:
        log.warning("readiness: redis inaccesible", exc_info=True)
        return "down"
    return "ok"


async def _check_litellm() -> Status:
    """El gateway caído degrada, no tumba: la API sigue sirviendo informes ya
    generados y encolando trabajo."""
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{s.litellm_base_url}/health/liveliness")
            return "ok" if r.status_code == 200 else "degraded"
    except Exception:
        log.warning("readiness: litellm inaccesible", exc_info=True)
        return "degraded"


@router.get("/ready", summary="Readiness")
async def ready(response: Response) -> dict[str, Any]:
    pg, rd, llm = await asyncio.gather(_check_postgres(), _check_redis(), _check_litellm())
    checks = {"postgres": pg, "redis": rd, "litellm": llm}

    # Postgres o Redis caídos = no puede atender. LiteLLM caído = degradado.
    if "down" in (pg, rd):
        overall: Status = "down"
        response.status_code = 503
    elif "degraded" in checks.values():
        overall = "degraded"
    else:
        overall = "ok"

    return {"status": overall, "checks": checks}
