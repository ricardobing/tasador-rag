"""Embeber no puede bloquear el event loop.

Es la lección del commit V, medida: WeasyPrint bloqueó el loop unos segundos,
`arq` no llegó a reconectar a Redis en su `conn_timeout` de 1 s, y el worker
quedó vivo sin consumir la cola durante 95 minutos. Un modelo de embeddings
en CPU es exactamente el mismo tipo de trabajo. Este test corre un embedding
real y mide, en paralelo, cuánto se retrasa un `sleep(0.05)`: si el loop está
bloqueado, el sleep tarda lo que tarda el modelo.

Necesita el modelo descargado en `models_path`; si no está, se saltea (no
descarga 2 GB en un test).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tasador.rag.embedder import Embedder
from tasador.settings import get_settings

pytestmark = pytest.mark.asyncio


def _modelo_descargado() -> bool:
    ruta = get_settings().models_path
    return ruta.exists() and any(ruta.rglob("*.onnx"))


@pytest.mark.skipif(not _modelo_descargado(), reason="modelo de embeddings no descargado")
async def test_el_loop_sigue_respondiendo_mientras_se_embebe():
    e = Embedder()
    await e.precargar()
    textos = [
        f"Departamento de {i} ambientes, luminoso, a refaccionar, piso {i}." for i in range(40)
    ]

    retrasos: list[float] = []

    async def latido() -> None:
        for _ in range(20):
            t0 = time.perf_counter()
            await asyncio.sleep(0.05)
            retrasos.append(time.perf_counter() - t0 - 0.05)

    t0 = time.perf_counter()
    vectores, _ = await asyncio.gather(e.pasajes(textos), latido())
    duracion = time.perf_counter() - t0

    assert len(vectores) == 40 and len(vectores[0]) == e.dim
    # Si el loop se bloqueara, un latido esperaría todo lo que tarda el modelo.
    assert max(retrasos) < duracion / 2, (
        f"el loop se bloqueó {max(retrasos):.2f}s de {duracion:.2f}s"
    )


@pytest.mark.skipif(not _modelo_descargado(), reason="modelo de embeddings no descargado")
async def test_consulta_y_pasaje_son_del_mismo_espacio():
    from tasador.rag.embedder import _usa_prefijos

    e = Embedder()
    await e.precargar()
    q = await e.consulta("3 ambientes a refaccionar")
    p = (await e.pasajes(["3 ambientes a refaccionar"]))[0]
    assert len(q) == len(p) == e.dim
    num = sum(x * y for x, y in zip(q, p, strict=True))
    den = sum(x * x for x in q) ** 0.5 * sum(y * y for y in p) ** 0.5
    coseno = num / den
    if _usa_prefijos(e.model):
        # Mismo texto con distinto prefijo: parecidos, pero no idénticos.
        assert 0.8 < coseno < 0.999
    else:
        # Sin prefijos, consulta y pasaje del mismo texto son el mismo vector.
        assert coseno == pytest.approx(1.0, abs=1e-4)
