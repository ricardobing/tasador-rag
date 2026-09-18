"""Marca como FAILED los informes que quedaron colgados.

    uv run python scripts/cosechar_colgados.py            # solo informa
    uv run python scripts/cosechar_colgados.py --aplicar

## Por qué hace falta

`worker._marcar_fallado` cubre el caso "el job levantó una excepción". No cubre
los dos que de verdad pasan:

  · el PROCESO murió (el contenedor se reinició a mitad de un informe), y
  · el job nunca se tomó de la cola.

En los dos, el informe queda en `RUNNING` o `QUEUED` para siempre. Medido el
15/08: cuatro informes llevaban entre 8 y 13 horas así, uno de ellos con 22
eventos y USD 0,109 ya gastados.

Desde el front, "encolado" y "roto" se ven igual: el usuario mira un stepper que
no termina nunca. Es textualmente lo que dice el docstring de `_marcar_fallado`,
y el caso que cubre no es el que estaba pasando.

## Los umbrales

`job_timeout` de arq es 1.800 s, y el primer informe de un barrio nuevo tarda
~5 minutos. El umbral de RUNNING tiene que ser **mayor** que el timeout, no
menor, o el cosechador mata informes vivos.

Pensado para `ops/crontab`, cada 15 minutos.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

from tasador.cli import run

# 2x el `job_timeout` de arq (1.800 s). Por debajo de esto un informe puede
# estar corriendo de verdad.
HORAS_RUNNING = 1.0
# Un job que la cola no tomó en una hora no lo va a tomar.
HORAS_QUEUED = 1.0


async def _cosechar(*, aplicar: bool) -> int:
    from sqlalchemy import or_, select

    from tasador.db.base import get_session_factory
    from tasador.db.models import Report

    ahora = datetime.now(UTC)
    limite_running = ahora - timedelta(hours=HORAS_RUNNING)
    limite_queued = ahora - timedelta(hours=HORAS_QUEUED)

    async with get_session_factory()() as session:
        colgados = (
            (
                await session.execute(
                    select(Report).where(
                        or_(
                            (Report.status == "RUNNING") & (Report.started_at < limite_running),
                            (Report.status == "QUEUED") & (Report.created_at < limite_queued),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

        if not colgados:
            print("No hay informes colgados.")
            return 0

        print(f"{len(colgados)} informe(s) colgado(s):\n")
        for r in colgados:
            desde = r.started_at or r.created_at
            horas = (ahora - desde).total_seconds() / 3600
            print(
                f"  {str(r.id)[:8]}  {r.status:<8} hace {horas:>5.1f} h  "
                f"gastado USD {float(r.cost_usd or 0):.6f}"
            )
            if aplicar:
                r.status = "FAILED"
                r.error_code = "ABANDONADO"
                r.error_detail = (
                    f"El informe quedó en {r.status} durante {horas:.1f} h sin terminar. "
                    "El proceso murió o el job nunca se tomó de la cola."
                )
                r.finished_at = ahora

        if aplicar:
            await session.commit()
            print(f"\n{len(colgados)} marcados como FAILED (error_code=ABANDONADO).")
        else:
            print("\n(solo informa; con --aplicar se marcan como FAILED)")
    return 0


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true", help="marcarlos como FAILED")
    return run(_cosechar(aplicar=ap.parse_args().aplicar))


if __name__ == "__main__":
    raise SystemExit(main())
