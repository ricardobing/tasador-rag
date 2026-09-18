"""Worker de la cola — `arq tasador.worker.WorkerSettings`.

El grafo corre acá y no en la API por una razón concreta: un informe tarda
~90 segundos. Sostener una request HTTP ese tiempo hace que cualquier proxy,
balanceador o pestaña que se cierra corte el trabajo a la mitad. La API encola
y devuelve 202; el estado se consulta con `GET /v1/reports/{id}`.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from typing import Any, ClassVar

import structlog
from arq.connections import RedisSettings

from tasador.settings import get_settings

log = structlog.get_logger()

# ⚠ Se fija ACÁ, al importar el módulo, y no dentro de una función.
#
# `arq` crea su propio event loop; para cuando corre el primer job ya es tarde
# para cambiar la política. En Windows el default es `ProactorEventLoop` y
# psycopg en modo async lo rechaza con `InterfaceError`. arq importa este
# módulo para resolver `WorkerSettings` ANTES de crear el loop, así que este
# es el único punto que funciona.
#
# Medido el 13/08: sin esto, el worker toma el job, revienta, y el informe
# queda en QUEUED para siempre. En Linux (los contenedores) es un no-op.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _marcar_fallado(report_id: str, error: Exception) -> None:
    """Un job que revienta no puede dejar el informe en QUEUED para siempre.

    Desde afuera, "encolado" y "roto" se ven igual: el usuario mira un spinner
    que no termina nunca. Es peor que un error.
    """
    from sqlalchemy import select

    from tasador.db.base import get_session_factory
    from tasador.db.models import Report

    try:
        async with get_session_factory()() as session:
            rep = (
                await session.execute(select(Report).where(Report.id == report_id))
            ).scalar_one_or_none()
            if rep is None or rep.status not in ("QUEUED", "RUNNING"):
                return
            rep.status = "FAILED"
            rep.error_code = type(error).__name__
            rep.error_detail = str(error)[:1000]
            rep.finished_at = datetime.now(UTC)
            await session.commit()
    except Exception:
        log.exception("tampoco se pudo marcar el informe como fallado", report_id=report_id)


async def generar_informe(ctx: dict[str, Any], report_id: str) -> str:
    """Job encolado por `POST /v1/reports`."""
    from tasador.agents.runner import run_report

    log.info("tomando informe de la cola", report_id=report_id, job_try=ctx.get("job_try"))
    try:
        final = await run_report(report_id)
    except Exception as e:
        log.exception("el informe reventó fuera del grafo", report_id=report_id)
        await _marcar_fallado(report_id, e)
        raise
    return str(final.get("status"))


async def startup(ctx: dict[str, Any]) -> None:
    s = get_settings()
    log.info("worker arriba", env=s.env, engine=s.engine_version)


async def shutdown(ctx: dict[str, Any]) -> None:
    log.info("worker abajo")


def redis_settings() -> RedisSettings:
    """La conexión a Redis del worker.

    ⚠️ **Los dos parámetros de abajo no son ajuste fino: sin ellos el worker
    deja de consumir la cola y el contenedor sigue diciendo "Up".**

    Los defaults de arq son `conn_timeout=1` segundo y `retry_on_timeout=False`.
    Un segundo para *conectar* alcanza con el loop libre; con `max_jobs=4`
    informes en paralelo no, porque el nodo 11 renderiza el PDF con WeasyPrint,
    que es CPU y **bloquea el event loop**. El poll de arq no llega a conectar,
    la conexión se cae, y como no hay reintento el worker queda vivo sin tomar
    trabajo.

    Medido el 15/08: dos informes estuvieron **95 minutos** en QUEUED con el
    worker "Up (2 hours)" y ocioso. Al reiniciarlo los tomó de inmediato
    (`delayed=5737.98s`). En los logs, `redis.exceptions.TimeoutError: Timeout
    connecting to server`, y cinco más en los doce minutos siguientes.

    Lo peor del síntoma es que no se ve: la API responde 202, el informe queda
    en QUEUED, `docker compose ps` muestra todo sano y nadie se entera.
    `scripts/cosechar_colgados.py` existe para rescatarlos y no corre en dev.
    """
    s = RedisSettings.from_dsn(get_settings().redis_url)
    s.conn_timeout = 10
    s.retry_on_timeout = True
    return s


class WorkerSettings:
    functions: ClassVar[list[Any]] = [generar_informe]
    on_startup = startup
    on_shutdown = shutdown
    # Cuatro informes en paralelo. El cuello no es CPU: son las llamadas a
    # modelos, que son I/O.
    max_jobs = 4
    # ⚠️ El límite lo fija el PRIMER informe de un barrio, no el promedio.
    #
    # Con corpus caliente un informe son ~12 s. Con corpus FRÍO hay que extraer
    # features de cada candidato: 22 avisos tardaron 145 s (Etapa 3 §6) y 60
    # avisos —el primer informe de Palermo, el 14/08— se pasaron de los 600 s
    # que había acá y el job murió con TimeoutError.
    #
    # Se sube a 1800 y, sobre todo, se arregló la causa de que doliera: el
    # nodo 4 ahora guarda CADA LOTE apenas termina, así que un timeout deja el
    # trabajo hecho en `listing_features` y el reintento arranca del caché en
    # vez de volver a pagarlo. El timeout es la red, no el plan.
    job_timeout = 1800
    # Los reintentos del grafo los maneja LangGraph con su checkpoint; arq
    # solo reintenta si el proceso murió entero.
    max_tries = 2
    keep_result = 3600

    @staticmethod
    def redis_settings_factory() -> RedisSettings:
        return redis_settings()


# arq lee el atributo, no el método.
WorkerSettings.redis_settings = redis_settings()  # type: ignore[attr-defined]
