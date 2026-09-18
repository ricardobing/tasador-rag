"""Recuperación híbrida de comparables — doc 18 §3.4 (ADR-012).

El orden no cambia respecto del nodo 2 de siempre: **filtro duro primero**.
La misma consulta SQL, la misma escalera de relajación. Lo que cambia es el
`ORDER BY`: en vez de recencia, un puntaje.

    1. WHERE  barrio · USD · superficie · ambientes · vigencia · canónico
              (`retrieve._consulta`, sin el LIMIT)            → el pool
    2. denso    coseno(consulta, chunk), máximo por aviso      → ranking D
    3. léxico   ts_rank_cd(tsv, consulta) en 'spanish'         → ranking L
    4. fusión   RRF: score = Σ 1/(k + rank_i)                  → top N

**Exacta sobre lo filtrado, no ANN.** Después del filtro quedan cientos de
avisos, a lo sumo ~1.700 (unos miles de chunks): la distancia exacta contra
esos vectores es cuestión de milisegundos, recall 100%, y sin el problema de
ANN + filtro (el índice devuelve los k más cercanos del corpus ENTERO y el
filtro después deja menos de k). El HNSW existe y se mide aparte.

**RRF y no una suma ponderada** porque no pide calibrar escalas entre un
coseno y un `ts_rank`: un hiperparámetro menos contra un set chico, que es
donde este proyecto ya midió 17,6 pp de ruido.

Un aviso que no está indexado (sin chunks para este chunker/modelo) no
desaparece: queda después de los puntuados, en orden de recencia. Un corpus a
medio indexar degrada a lo de siempre en vez de esconder avisos.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.retrieve import RADIO_POR_ALCANCE, _a_candidato, _consulta
from tasador.agents.state import Candidate
from tasador.corpus.resolve import Barrios
from tasador.rag.embedder import Embedder, get_embedder
from tasador.rag.queries import texto_de_consulta

log = structlog.get_logger()

Modo = Literal["denso", "lexico", "hibrido"]

# Tope del pool filtrado. Muy por encima de lo medido (1.637 en Palermo con el
# escalón más laxo) y muy por debajo de "todo el corpus".
POOL_MAX = 5000


@dataclass(slots=True, frozen=True)
class Semantica:
    """Cómo se puntúa el pool. Sale de `agents.yaml` (`params.semantic`)."""

    modo: Modo = "hibrido"
    chunker_version: str = "C-oraciones-v1"
    model: str = "intfloat/multilingual-e5-large"
    rrf_k: int = 60
    usar_notas: bool = True

    @classmethod
    def desde(cls, params: dict[str, Any] | None) -> Semantica:
        p = params or {}
        return cls(
            modo=p.get("modo", "hibrido"),
            chunker_version=p.get("chunker", "C-oraciones-v1"),
            model=p.get("model", "intfloat/multilingual-e5-large"),
            rrf_k=int(p.get("rrf_k", 60)),
            usar_notas=bool(p.get("usar_notas_del_agente", True)),
        )


_SQL_DENSO = text(
    """
    SELECT listing_id, MAX(1 - (embedding <=> CAST(:q AS vector))) AS score
    FROM corpus.listing_chunks
    WHERE listing_id IN :ids AND chunker_version = :cv AND model = :m
    GROUP BY listing_id
    ORDER BY score DESC
    """
).bindparams(bindparam("ids", expanding=True))

_SQL_LEXICO = text(
    """
    SELECT listing_id, MAX(ts_rank_cd(tsv, q, 32)) AS score
    FROM corpus.listing_chunks, to_tsquery('spanish', :consulta) q
    WHERE listing_id IN :ids AND chunker_version = :cv AND model = :m AND tsv @@ q
    GROUP BY listing_id
    ORDER BY score DESC
    """
).bindparams(bindparam("ids", expanding=True))

_PALABRA = re.compile(r"[a-záéíóúñü0-9]{3,}", re.IGNORECASE)


def consulta_lexica(texto: str, *, max_terminos: int = 40) -> str:
    """`to_tsquery` con OR entre términos. Un término con puntuación adentro
    rompe la sintaxis; se filtra por forma, y las stopwords las saca Postgres."""
    vistos: list[str] = []
    for w in _PALABRA.findall(texto.lower()):
        if w not in vistos:
            vistos.append(w)
        if len(vistos) >= max_terminos:
            break
    return " | ".join(vistos)


async def puntuar_denso(
    session: AsyncSession, ids: list[str], vector: list[float], sem: Semantica
) -> dict[str, float]:
    if not ids:
        return {}
    filas = await session.execute(
        _SQL_DENSO,
        {
            "q": str(vector),
            "ids": [uuid.UUID(i) for i in ids],
            "cv": sem.chunker_version,
            "m": sem.model,
        },
    )
    return {str(lid): float(s) for lid, s in filas}


async def puntuar_lexico(
    session: AsyncSession, ids: list[str], texto: str, sem: Semantica
) -> dict[str, float]:
    consulta = consulta_lexica(texto)
    if not ids or not consulta:
        return {}
    try:
        filas = await session.execute(
            _SQL_LEXICO,
            {
                "consulta": consulta,
                "ids": [uuid.UUID(i) for i in ids],
                "cv": sem.chunker_version,
                "m": sem.model,
            },
        )
    except Exception:  # una consulta que Postgres no puede parsear no tira el informe
        log.warning("consulta léxica inválida; se omite la mitad léxica", consulta=consulta[:80])
        await session.rollback()
        return {}
    return {str(lid): float(s) for lid, s in filas}


def fusionar_rrf(rankings: list[list[str]], *, k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion. Un documento suma 1/(k + posición) por cada
    ranking en el que aparece; los que aparecen en varios suben."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for pos, lid in enumerate(ranking):
            score[lid] = score.get(lid, 0.0) + 1.0 / (k + pos + 1)
    return score


def _ranking(puntajes: dict[str, float]) -> list[str]:
    return [lid for lid, _ in sorted(puntajes.items(), key=lambda kv: kv[1], reverse=True)]


async def puntuar(
    session: AsyncSession, ids: list[str], texto: str, sem: Semantica, embedder: Embedder
) -> dict[str, float]:
    """El puntaje final por aviso, según el modo."""
    if sem.modo == "lexico":
        return await puntuar_lexico(session, ids, texto, sem)
    vector = await embedder.consulta(texto)
    denso = await puntuar_denso(session, ids, vector, sem)
    if sem.modo == "denso":
        return denso
    lexico = await puntuar_lexico(session, ids, texto, sem)
    return fusionar_rrf([_ranking(denso), _ranking(lexico)], k=sem.rrf_k)


async def buscar_semantico(
    session: AsyncSession,
    cfg: NodeConfig,
    subject: dict[str, Any],
    barrios: Barrios,
    *,
    sem: Semantica | None = None,
    embedder: Embedder | None = None,
) -> tuple[list[Candidate], int, list[dict[str, Any]]]:
    """La misma escalera del nodo 2, con el pool ordenado por puntaje.

    Devuelve `(candidatos, escalón usado, traza de escalones)`, como `_buscar`.
    """
    from tasador.agents.nodes.valuation import _prop
    from tasador.settings import get_settings

    sem = sem or Semantica.desde(cfg.param("semantic"))
    embedder = embedder or get_embedder()
    s = get_settings()
    objetivo = int(cfg.param("target_candidates", 25))
    limite = int(cfg.param("max_candidates", s.max_candidates))
    escalones: list[dict[str, Any]] = list(cfg.param("relaxation") or [])
    if not escalones:
        escalones = [{"surface_pct": 30, "days": 120, "scope": "barrio", "rooms_delta": 1}]

    barrio_id = uuid.UUID(str(subject["neighborhood_id"]))
    sup = _prop(subject).surface_weighted
    ambientes = subject.get("rooms")
    texto = texto_de_consulta(subject, con_notas=sem.usar_notas)

    intentos: list[dict[str, Any]] = []
    mejor: list[Any] = []
    paso_del_mejor = 0
    for paso, esc in enumerate(escalones):
        radio = RADIO_POR_ALCANCE.get(str(esc.get("scope", "barrio")), 0)
        ids = [b.id for b in barrios.cercanos(barrio_id, radio_m=radio)] if radio else [barrio_id]
        filas = (
            await session.execute(
                _consulta(
                    barrio_ids=ids,
                    sup=sup,
                    sup_pct=float(esc.get("surface_pct", 30)),
                    ambientes=ambientes,
                    rooms_delta=int(esc.get("rooms_delta", 1)),
                    dias=int(esc.get("days", 120)),
                    limite=POOL_MAX,
                    solo_ficha_completa=bool(esc.get("require_full_detail", True)),
                )
            )
        ).all()
        intentos.append(
            {
                "paso": paso,
                "barrios": len(ids),
                "radio_m": radio,
                "superficie_pct": esc.get("surface_pct"),
                "dias": esc.get("days"),
                "solo_ficha_completa": bool(esc.get("require_full_detail", True)),
                "pool": len(filas),
                "modo": sem.modo,
            }
        )
        if len(filas) > len(mejor):
            mejor, paso_del_mejor = list(filas), paso
        if len(filas) >= objetivo:
            break

    if not mejor:
        return [], paso_del_mejor, intentos

    pool_ids = [str(li.id) for li, _ in mejor]
    puntajes = await puntuar(session, pool_ids, texto, sem, embedder)
    # Puntuados primero, por puntaje; los no indexados después, por recencia
    # (el pool ya viene ordenado por `last_seen_at DESC`).
    orden = sorted(
        range(len(mejor)),
        key=lambda i: (-(puntajes.get(pool_ids[i], -1.0)), i),
    )
    candidatos: list[Candidate] = []
    for i in orden[:limite]:
        li, ft = mejor[i]
        c = _a_candidato(li, ft)
        if (sc := puntajes.get(pool_ids[i])) is not None:
            c["similarity_score"] = round(sc, 4)
        candidatos.append(c)
    intentos[-1]["puntuados"] = len(puntajes)
    return candidatos, paso_del_mejor, intentos
