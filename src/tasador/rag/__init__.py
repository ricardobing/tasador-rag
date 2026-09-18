"""Recuperación aumentada — doc 18.

    chunking   cómo se parte un aviso (tres estrategias, comparadas)
    embedder   embeddings locales, fuera del event loop
    indexer    corpus.listing_chunks, incremental por hash
    queries    el sujeto → texto de consulta
    retriever  filtro SQL → denso + léxico → fusión
    rerank     cross-encoder sobre los finalistas

Regla que no se negocia: este paquete no importa `tasador.valuation`. Decide
qué avisos se presentan al motor; nunca un número.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def sistemas_para_eval() -> dict[str, Any]:
    """Los sistemas B-H de la tabla de ablación (doc 18 §4.3), para
    `scripts/eval_retrieval.py`."""
    from tasador.agents.config import load_agents_config
    from tasador.corpus.resolve import Barrios
    from tasador.rag.chunking import chunkers
    from tasador.rag.queries import texto_de_consulta
    from tasador.rag.rerank import MODELOS, get_reranker, reordenar
    from tasador.rag.retriever import Semantica, buscar_semantico

    ch = chunkers()

    def _sistema(sem: Semantica, rerank: str | None = None) -> Any:
        async def correr(session: AsyncSession, subject: dict[str, Any]) -> list[str]:
            cfg = load_agents_config().node("retrieve_candidates")
            barrios = await Barrios.cargar(session)
            candidatos, _, _ = await buscar_semantico(session, cfg, subject, barrios, sem=sem)
            ids = [c["listing_id"] for c in candidatos]
            if rerank is None:
                return ids
            textos = {
                c["listing_id"]: " ".join(filter(None, [c.get("address"), c.get("description")]))[
                    :2000
                ]
                for c in candidatos
            }
            orden, _ = await reordenar(
                texto_de_consulta(subject, con_notas=sem.usar_notas),
                ids,
                textos,
                reranker=get_reranker(rerank),
            )
            return orden

        return correr

    hibrido = Semantica(modo="hibrido", chunker_version=ch["C"].version)
    return {
        "B": _sistema(Semantica(modo="denso", chunker_version=ch["A"].version)),
        "C": _sistema(Semantica(modo="denso", chunker_version=ch["C"].version)),
        "D": _sistema(Semantica(modo="lexico", chunker_version=ch["C"].version)),
        "E": _sistema(hibrido),
        "F": _sistema(hibrido, rerank=MODELOS["bge"]),
        "G": _sistema(hibrido, rerank=MODELOS["jina"]),
        # H no estaba en la tabla de doc 18: la agregó la medición. Sobre 112
        # consultas, el denso con truncar (B) le ganó al denso por oraciones (C)
        # en nDCG@25, así que la fusión con B es la pregunta obvia siguiente.
        "H": _sistema(Semantica(modo="hibrido", chunker_version=ch["A"].version)),
    }
