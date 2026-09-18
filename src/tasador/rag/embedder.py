"""Embeddings locales — doc 18 §3.1 (ADR-010).

`intfloat/multilingual-e5-large` vía `fastembed` (ONNX, CPU): 1024
dimensiones, licencia MIT, sin llamadas de red una vez descargado. El modelo
que el diseño original nombraba (`bge-m3`) no lo sirve la librería instalada;
se verificó antes de escribir esto.

Dos cosas que este módulo hace y que no son opcionales:

1. **Los prefijos de e5.** El modelo se entrenó con `query:` y `passage:`
   delante del texto; sin ellos la similitud consulta↔documento se degrada.
   Están acá adentro para que ningún llamador pueda olvidarlos.

2. **Nada bloquea el event loop.** Embeber es CPU síncrona, exactamente lo que
   dejó al worker "vivo y sordo" cuando WeasyPrint bloqueó el loop y `arq`
   perdió la conexión a Redis (commit V). Todo lo que computa va por
   `asyncio.to_thread`. Hay un test que mide que el loop sigue respondiendo
   mientras se embebe.

El modelo se carga una vez por proceso y se cachea en `models_path`
(`/data/models` en el contenedor, `data/models` local). Son 2,2 GB: no van en
la imagen, van en un volumen.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Iterable, Sequence
from typing import Any

import structlog

from tasador.settings import get_settings

log = structlog.get_logger()

PREFIJO_CONSULTA = "query: "
PREFIJO_PASAJE = "passage: "


def _usa_prefijos(model: str) -> bool:
    """Los e5 se entrenaron con `query:`/`passage:`; MiniLM y mpnet, no. Un
    prefijo en un modelo que no lo espera es ruido en el vector."""
    return "e5" in model.lower()


class Embedder:
    """Un modelo, cargado perezosamente y una sola vez."""

    def __init__(self, model: str | None = None, *, lote: int = 32) -> None:
        s = get_settings()
        self.model = model or s.embedding_model
        self.dim = s.embedding_dim
        self.lote = lote
        self._m: Any = None
        self._tok: Any = None
        self._lock = threading.Lock()

    # ── carga ────────────────────────────────────────────────────────────
    def _cargar(self) -> Any:
        if self._m is None:
            with self._lock:
                if self._m is None:
                    from fastembed import TextEmbedding

                    s = get_settings()
                    log.info("cargando modelo de embeddings", model=self.model)
                    self._m = TextEmbedding(self.model, cache_dir=str(s.models_path))
        return self._m

    async def precargar(self) -> None:
        await asyncio.to_thread(self._cargar)

    # ── tokens ───────────────────────────────────────────────────────────
    def contar_tokens(self, texto: str) -> int:
        """Con el tokenizador del modelo, SIN truncar.

        El tokenizador que usa fastembed para embeber trunca a 512: contar con
        ese devuelve 512 para todo lo que se pasa, y "¿cuántos avisos no
        entran?" se responde "ninguno" por construcción. Medido el 17/09:
        p90 = p99 = máx = 512. Se cuenta con una copia sin truncación.
        """
        m = self._cargar()
        if self._tok is None:
            tok = getattr(getattr(m, "model", None), "tokenizer", None)
            if tok is not None:
                import copy

                self._tok = copy.deepcopy(tok)
                self._tok.no_truncation()
        if self._tok is not None:
            return len(self._tok.encode(texto).ids)
        # Sin tokenizador accesible: ~3,7 caracteres por token en castellano,
        # medido. Se registra para que no pase inadvertido.
        log.warning("sin tokenizador; contando por caracteres", model=self.model)
        return max(1, len(texto) // 4 + 1)

    # ── embeddings ───────────────────────────────────────────────────────
    def _embed(self, textos: Sequence[str]) -> list[list[float]]:
        m = self._cargar()
        return [[float(x) for x in v] for v in m.embed(list(textos), batch_size=self.lote)]

    async def pasajes(self, textos: Iterable[str]) -> list[list[float]]:
        pref = PREFIJO_PASAJE if _usa_prefijos(self.model) else ""
        ts = [pref + t for t in textos]
        if not ts:
            return []
        return await asyncio.to_thread(self._embed, ts)

    async def consulta(self, texto: str) -> list[float]:
        pref = PREFIJO_CONSULTA if _usa_prefijos(self.model) else ""
        return (await asyncio.to_thread(self._embed, [pref + texto]))[0]


_INSTANCIA: Embedder | None = None


def get_embedder() -> Embedder:
    global _INSTANCIA
    if _INSTANCIA is None:
        _INSTANCIA = Embedder()
    return _INSTANCIA


def hash_texto(texto: str) -> str:
    """Identidad del trabajo hecho: si el texto del chunk no cambió, el
    embedding tampoco, y no se recalcula."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()
