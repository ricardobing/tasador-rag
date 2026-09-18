"""Evaluación de la recuperación de comparables — doc 18 §4.

La vara se construye ANTES que lo que se va a medir con ella. Este módulo
existe para poder decir, con intervalo, si un recuperador nuevo trae mejores
comparables que el de hoy — y para que "mejor" no sea una impresión.

## Los juicios de relevancia

`core.report_comparables` es un set de relevancia que nadie diseñó como tal:
cada informe es una consulta (un sujeto), y cada comparable que se le presentó
tiene un veredicto con motivo. Se traduce a grados:

    2  comparable        `included = true`
    1  marginal          descartado por estadística (recorte por percentil, outlier)
    0  no comparable     descartado por curaduría, vencido, duplicado, o porque
                         llevarlo al sujeto pedía un ajuste mayor al tope

## El sesgo, dicho antes de que lo diga otro

Solo están juzgados los avisos que el recuperador ACTUAL trajo. Un sistema
nuevo va a traer avisos sin juicio, y "sin juzgar" no es "irrelevante". Por
eso todas las métricas se calculan sobre la **lista condensada** —el ranking
sin los documentos no juzgados— y se reporta además `juzgados@k`, la fracción
del top-k que sí tenía juicio. Un sistema con `juzgados@k` bajo está siendo
medido sobre poco, y la tabla lo tiene que decir. `bpref` está por lo mismo:
es la métrica diseñada para juicios incompletos.

## Intervalos

Bootstrap sobre las consultas: se remuestrean las consultas con reposición y
se toma el intervalo del 95% de la media. Para comparar dos sistemas, la
diferencia es **apareada** (misma consulta, dos sistemas) y el intervalo es el
de la diferencia. Una diferencia cuyo intervalo cruza el cero no es una mejora
y no se reporta como tal (doc 18 §4.3).
"""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Listing, Report, ReportComparable, SubjectProperty

log = structlog.get_logger()

COMPARABLE = 2
MARGINAL = 1
NO_COMPARABLE = 0

# Descartes que dicen "casi": el aviso era comparable pero cayó en la cola de
# la distribución. No es lo mismo que un pozo.
MOTIVOS_MARGINALES = ("recorte_p", "outlier_estadistico")


def grado_de(included: bool, motivo: str | None) -> int:
    if included:
        return COMPARABLE
    if motivo and motivo.startswith(MOTIVOS_MARGINALES):
        return MARGINAL
    return NO_COMPARABLE


@dataclass(slots=True)
class Consulta:
    """Un informe pasado, leído como consulta con juicios."""

    report_id: str
    subject: dict[str, Any]
    juicios: dict[str, int]  # listing_id -> grado
    # Lo que ningún sistema puede devolver para esta consulta: el propio aviso,
    # cuando la consulta ES un aviso del corpus (leave-one-out).
    excluir: set[str] = field(default_factory=set)

    @property
    def relevantes(self) -> set[str]:
        return {lid for lid, g in self.juicios.items() if g == COMPARABLE}


async def cargar_consultas(session: AsyncSession, *, min_juicios: int = 5) -> list[Consulta]:
    """Los informes pasados con juicios suficientes, leídos como consultas.

    Los juicios que traen son los de AQUEL momento (ver `juicios.py`): sirven
    para arrancar y para el sistema actual; para comparar sistemas se
    reemplazan por los del pool juzgado.
    """
    from collections import Counter

    from tasador.agents.runner import _subject_dict

    filas = (
        await session.execute(
            select(Report, SubjectProperty)
            .join(SubjectProperty, SubjectProperty.id == Report.subject_property_id)
            .where(Report.status.in_(("SUCCEEDED", "INSUFFICIENT_DATA")))
            .order_by(Report.created_at)
        )
    ).all()

    consultas: list[Consulta] = []
    for informe, sujeto in filas:
        comps = (
            await session.execute(
                select(ReportComparable, Listing.neighborhood_id)
                .join(Listing, Listing.id == ReportComparable.listing_id)
                .where(ReportComparable.report_id == informe.id)
            )
        ).all()
        juicios = {str(c.listing_id): grado_de(c.included, c.exclusion_reason) for c, _ in comps}
        if len(juicios) < min_juicios:
            continue
        subject = _subject_dict(sujeto)
        if not subject.get("neighborhood_id"):
            # El nodo 1 resuelve el barrio en el estado del grafo y no siempre
            # lo persiste en el sujeto. El barrio de los comparables que se le
            # presentaron es el que el informe usó: se toma el mayoritario.
            barrios = Counter(str(b) for _, b in comps if b is not None)
            if not barrios:
                continue
            subject["neighborhood_id"] = barrios.most_common(1)[0][0]
        consultas.append(Consulta(str(informe.id), subject, juicios))
    return consultas


# ── Métricas sobre una consulta ──────────────────────────────────────────


def _condensar(ranking: list[str], juicios: dict[str, int]) -> list[str]:
    return [lid for lid in ranking if lid in juicios]


def dcg(grados: list[int]) -> float:
    return float(sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grados)))


def ndcg_at(ranking: list[str], juicios: dict[str, int], k: int) -> float:
    """nDCG@k con ganancia 2^g - 1 sobre la lista condensada."""
    cond = _condensar(ranking, juicios)[:k]
    ideal = sorted(juicios.values(), reverse=True)[:k]
    idcg = dcg(ideal)
    return dcg([juicios[lid] for lid in cond]) / idcg if idcg > 0 else 0.0


def recall_at(ranking: list[str], juicios: dict[str, int], k: int) -> float:
    """Qué fracción de los comparables (grado 2) aparece en el top-k condensado."""
    rel = {lid for lid, g in juicios.items() if g == COMPARABLE}
    if not rel:
        return 0.0
    cond = _condensar(ranking, juicios)[:k]
    return len(rel & set(cond)) / len(rel)


def mrr(ranking: list[str], juicios: dict[str, int]) -> float:
    for i, lid in enumerate(_condensar(ranking, juicios)):
        if juicios[lid] == COMPARABLE:
            return 1.0 / (i + 1)
    return 0.0


def bpref(ranking: list[str], juicios: dict[str, int]) -> float:
    """bpref (Buckley & Voorhees 2004): penaliza a cada relevante por los NO
    relevantes juzgados que aparecen antes. Los no juzgados no cuentan, que es
    exactamente lo que hace falta con juicios incompletos."""
    rel = {lid for lid, g in juicios.items() if g == COMPARABLE}
    norel = {lid for lid, g in juicios.items() if g == NO_COMPARABLE}
    if not rel:
        return 0.0
    r = len(rel)
    total = 0.0
    no_rel_vistos = 0
    for lid in _condensar(ranking, juicios):
        if lid in rel:
            total += 1.0 - min(no_rel_vistos, r) / r
        elif lid in norel:
            no_rel_vistos += 1
    return total / r


def juzgados_at(ranking: list[str], juicios: dict[str, int], k: int) -> float:
    top = ranking[:k]
    return sum(1 for lid in top if lid in juicios) / len(top) if top else 0.0


def evaluar(ranking: list[str], juicios: dict[str, int], k: int) -> dict[str, float]:
    return {
        f"ndcg@{k}": ndcg_at(ranking, juicios, k),
        "recall@60": recall_at(ranking, juicios, 60),
        "mrr": mrr(ranking, juicios),
        "bpref": bpref(ranking, juicios),
        f"juzgados@{k}": juzgados_at(ranking, juicios, k),
        "devueltos": float(len(ranking)),
    }


# ── Intervalos ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class Intervalo:
    media: float
    lo: float
    hi: float

    def __str__(self) -> str:
        return f"{self.media:.3f} [{self.lo:.3f}, {self.hi:.3f}]"


def _media(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def bootstrap(valores: list[float], *, n: int = 1000, semilla: int = 42) -> Intervalo:
    """Intervalo del 95% de la media, por remuestreo de las consultas."""
    if not valores:
        return Intervalo(0.0, 0.0, 0.0)
    rng = random.Random(semilla)  # noqa: S311 — remuestreo, no criptografía
    medias = sorted(_media(rng.choices(valores, k=len(valores))) for _ in range(n))
    return Intervalo(_media(valores), medias[int(0.025 * n)], medias[int(0.975 * n) - 1])


def diferencia(a: list[float], b: list[float], *, n: int = 1000, semilla: int = 42) -> Intervalo:
    """Intervalo de la diferencia APAREADA (b - a), consulta a consulta."""
    if len(a) != len(b):
        raise ValueError("las listas tienen que estar apareadas por consulta")
    return bootstrap([y - x for x, y in zip(a, b, strict=True)], n=n, semilla=semilla)


# ── Sistemas ─────────────────────────────────────────────────────────────

Sistema = Callable[[AsyncSession, dict[str, Any]], Awaitable[list[str]]]


async def sistema_actual(session: AsyncSession, subject: dict[str, Any]) -> list[str]:
    """A · lo que corre hoy: filtro SQL + escalera + recencia (nodo 2)."""
    from tasador.agents.config import load_agents_config
    from tasador.agents.nodes.retrieve import _buscar
    from tasador.corpus.resolve import Barrios

    cfg = load_agents_config().node("retrieve_candidates")
    barrios = await Barrios.cargar(session)
    candidatos, _, _ = await _buscar(session, cfg, dict(subject), barrios)
    return [c["listing_id"] for c in candidatos]


@dataclass(slots=True)
class Resultado:
    sistema: str
    k: int
    por_consulta: dict[str, dict[str, float]] = field(default_factory=dict)

    def serie(self, metrica: str) -> list[float]:
        return [m[metrica] for m in self.por_consulta.values()]

    def resumen(self) -> dict[str, Intervalo]:
        if not self.por_consulta:
            return {}
        metricas = next(iter(self.por_consulta.values())).keys()
        return {m: bootstrap(self.serie(m)) for m in metricas}


async def correr(
    session: AsyncSession,
    sistemas: dict[str, Sistema],
    consultas: list[Consulta],
    *,
    k: int = 25,
) -> dict[str, Resultado]:
    """Cada sistema sobre cada consulta. El orden de las consultas es el mismo
    para todos, así que las series quedan apareadas."""
    out = {nombre: Resultado(nombre, k) for nombre in sistemas}
    for c in consultas:
        for nombre, sistema in sistemas.items():
            ranking = [lid for lid in await sistema(session, c.subject) if lid not in c.excluir]
            out[nombre].por_consulta[c.report_id] = evaluar(ranking, c.juicios, k)
    return out


async def rankings_de(
    session: AsyncSession, sistemas: dict[str, Sistema], c: Consulta
) -> dict[str, list[str]]:
    """El ranking de cada sistema para una consulta, ya sin lo excluido."""
    return {
        nombre: [lid for lid in await sistema(session, c.subject) if lid not in c.excluir]
        for nombre, sistema in sistemas.items()
    }


def tabla(resultados: dict[str, Resultado], *, contra: str | None = None) -> str:
    """Una fila por sistema; si `contra` se da, la diferencia apareada contra él."""
    if not resultados:
        return "(sin resultados)"
    metricas = list(next(iter(resultados.values())).resumen().keys())
    ancho = max(len(n) for n in resultados) + 2
    lineas = [" " * ancho + "".join(f"{m:>28}" for m in metricas)]
    for nombre, r in resultados.items():
        celdas = [str(v) for v in r.resumen().values()]
        lineas.append(f"{nombre:<{ancho}}" + "".join(f"{c:>28}" for c in celdas))
        if contra and nombre != contra and contra in resultados:
            base = resultados[contra]
            difs = [str(diferencia(base.serie(m), r.serie(m))) for m in metricas]
            lineas.append(f"{'  Δ vs ' + contra:<{ancho}}" + "".join(f"{d:>28}" for d in difs))
    return "\n".join(lineas)


def resumen_para_guardar(r: Resultado) -> list[tuple[str, float, dict[str, Any]]]:
    """(métrica, valor, detalle) por métrica, listo para `componentes.guardar`."""
    out: list[tuple[str, float, dict[str, Any]]] = []
    for m, iv in r.resumen().items():
        if m == "devueltos":
            continue
        out.append((f"{r.sistema}.{m}", iv.media, {"lo": iv.lo, "hi": iv.hi, "k": r.k}))
    return out


def _uuid(s: str) -> uuid.UUID:
    return uuid.UUID(str(s))
