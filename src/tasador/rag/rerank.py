"""Reranking con cross-encoder local — doc 18 §3.5 (ADR-012).

Sobre los finalistas del híbrido (≤ 60 avisos), un cross-encoder lee
consulta y pasaje JUNTOS y devuelve un puntaje de relevancia. Es más preciso
que la similitud de vectores independientes y mucho más caro por par: por eso
va al final, sobre pocos, y nunca sobre el corpus.

Dos modelos, medidos los dos:

    BAAI/bge-reranker-base                    MIT · entrenado en inglés/chino
    jinaai/jina-reranker-v2-base-multilingual CC-BY-NC · castellano incluido

La licencia entra en la decisión: el NC sirve para comparar, no para vender.
Cuál gana en castellano lo dice la tabla de ablación, no el paper.

Mismas dos reglas que el embedder: se carga una vez por proceso y **nunca en
el event loop** (`asyncio.to_thread`). Y un tope de latencia: si el modelo no
está cargado o tarda de más, el orden del híbrido se conserva —degrada, no
falla— y la traza lo dice.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import structlog

from tasador.settings import get_settings

log = structlog.get_logger()

MODELOS = {
    "bge": "BAAI/bge-reranker-base",
    "jina": "jinaai/jina-reranker-v2-base-multilingual",
}


class Reranker:
    def __init__(self, model: str = MODELOS["bge"]) -> None:
        self.model = model
        self._m: Any = None
        self._lock = threading.Lock()

    def _cargar(self) -> Any:
        if self._m is None:
            with self._lock:
                if self._m is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    log.info("cargando reranker", model=self.model)
                    self._m = TextCrossEncoder(
                        self.model, cache_dir=str(get_settings().models_path)
                    )
        return self._m

    async def precargar(self) -> None:
        await asyncio.to_thread(self._cargar)

    # Lotes chicos a propósito. Con el `batch_size=64` por defecto, 60 pares de
    # hasta 512 tokens entran en UN lote y ONNX reserva la memoria de activación
    # de todo el lote de una vez: el proceso del eval llegó a 5,6 GB (18/09) y
    # dejó la máquina sin RAM. Con 8, el pico es un octavo y el tiempo total no
    # cambia en CPU (el cuello es el cómputo, no el paralelismo del lote).
    BATCH = 8

    def _puntuar(self, consulta: str, pasajes: list[str]) -> list[float]:
        m = self._cargar()
        return [float(s) for s in m.rerank(consulta, pasajes, batch_size=self.BATCH)]

    async def puntuar(self, consulta: str, pasajes: list[str]) -> list[float]:
        if not pasajes:
            return []
        return await asyncio.to_thread(self._puntuar, consulta, pasajes)


_INSTANCIAS: dict[str, Reranker] = {}


def get_reranker(model: str = MODELOS["bge"]) -> Reranker:
    if model not in _INSTANCIAS:
        _INSTANCIAS[model] = Reranker(model)
    return _INSTANCIAS[model]


async def reordenar(
    consulta: str,
    ids: list[str],
    textos: dict[str, str],
    *,
    reranker: Reranker,
    top_n: int = 60,
    max_ms: int = 15_000,
) -> tuple[list[str], dict[str, Any]]:
    """Reordena `ids[:top_n]` por el cross-encoder; el resto queda detrás.

    Devuelve el nuevo orden y una traza (ms, pares, si degradó). Un aviso sin
    texto conserva su posición relativa al final del bloque rerankeado.
    """
    cabeza = ids[:top_n]
    cola = ids[top_n:]
    con_texto = [i for i in cabeza if textos.get(i)]
    sin_texto = [i for i in cabeza if not textos.get(i)]
    t0 = time.perf_counter()
    try:
        puntajes = await asyncio.wait_for(
            reranker.puntuar(consulta, [textos[i] for i in con_texto]), timeout=max_ms / 1000
        )
    except (TimeoutError, Exception) as e:
        log.warning("reranker no disponible; se conserva el orden del híbrido", error=str(e)[:120])
        return ids, {"rerank": "degradado", "error": type(e).__name__}
    ms = int((time.perf_counter() - t0) * 1000)
    orden = [i for _, i in sorted(zip(puntajes, con_texto, strict=True), key=lambda p: -p[0])]
    return [*orden, *sin_texto, *cola], {
        "rerank": reranker.model,
        "pares": len(con_texto),
        "ms": ms,
    }
