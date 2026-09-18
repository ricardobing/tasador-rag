"""Juicios de relevancia para la recuperación: el pipeline como anotador.

Doc 18 §4.1 preveía usar los comparables de informes pasados como juicios, y
la primera medición mostró el problema: **los juicios son relativos al pool de
candidatos de aquel momento**. La escalera de recuperación cambió el 15/08
(`require_full_detail`), el corpus creció, y el recuperador de hoy devuelve
otros avisos: `juzgados@25` dio 0,27 y en 17 de 23 consultas fue cero. Medir
un sistema nuevo contra esos juicios sería medirlo contra un pool que ya no
existe.

La salida es la clásica de TREC: **pooling**. Para cada consulta se junta el
top-60 de todos los sistemas que se comparan y se juzga la unión, así ningún
sistema tiene documentos sin juicio en lo que devuelve. El anotador es el
propio pipeline —nodos 4, 5, 6 y 7 corridos sobre el pool— porque la pregunta
que importa es exactamente la que esos nodos responden: *¿este aviso entra a
la valuación como comparable válido?*

    2  entró a la valuación (o quedó en la cola estadística: era comparable)
    0  lo descartó una regla, el juez, el dedup, o pedía un ajuste mayor al tope

El grado 1 (marginal) de `retrieval.py` se conserva para los descartes
estadísticos, con una salvedad que hay que saber: el recorte por percentil
depende del CONJUNTO, no del aviso. Por eso el pool se juzga entero y de una
vez, y cuando un sistema nuevo agrega documentos, se vuelve a juzgar el pool
completo de esa consulta.

Los juicios se guardan en `data/eval/retrieval/<consulta>.json` (no se
versionan: dependen del corpus local). Reproducirlos es correr
`scripts/eval_retrieval.py --juzgar`.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import AgentsConfig, load_agents_config
from tasador.agents.nodes.retrieve import _a_candidato
from tasador.agents.state import Candidate, estado_inicial
from tasador.db.models import Listing, ListingFeatures, Neighborhood
from tasador.eval.retrieval import Consulta, grado_de

log = structlog.get_logger()

CARPETA = Path(__file__).resolve().parents[3] / "data" / "eval" / "retrieval"


async def candidatos_por_id(session: AsyncSession, ids: list[str]) -> list[Candidate]:
    """Los avisos → el contrato del grafo, por el mismo camino que el nodo 2."""
    if not ids:
        return []
    filas = (
        await session.execute(
            select(Listing, ListingFeatures)
            .outerjoin(ListingFeatures, ListingFeatures.listing_id == Listing.id)
            .where(Listing.id.in_([uuid.UUID(i) for i in ids]))
        )
    ).all()
    por_id = {str(li.id): _a_candidato(li, ft) for li, ft in filas}
    # En el orden pedido: el orden del pool no importa para el juicio, pero sí
    # para que el resultado sea reproducible.
    return [por_id[i] for i in ids if i in por_id]


async def juzgar(
    subject: dict[str, Any], candidatos: list[Candidate], *, cfg: AgentsConfig | None = None
) -> dict[str, int]:
    """Corre los nodos 4 → 5 → 6 → 7 sobre el pool y traduce a grados.

    Sin grafo, sin checkpoint, sin persistir el informe: solo lo que los
    nodos escriben por su cuenta (el nodo 4 guarda las features extraídas,
    que es caché y sirve para todos).
    """
    from tasador.agents.nodes.curate import curate
    from tasador.agents.nodes.dedup import dedup_cluster
    from tasador.agents.nodes.extract import extract_features
    from tasador.agents.nodes.valuation import adjust_and_value

    cfg = cfg or load_agents_config()
    estado = estado_inicial(f"eval-{uuid.uuid4()}", "eval", "eval")
    estado["subject"] = dict(subject)
    estado["candidates"] = [dict(c) for c in candidatos]  # type: ignore[misc]

    for nombre, nodo in (
        ("extract_features", extract_features),
        ("dedup_cluster", dedup_cluster),
        ("curate", curate),
        ("adjust_and_value", adjust_and_value),
    ):
        resultado = await nodo(estado, cfg.node(nombre))
        estado.update(resultado.updates)  # type: ignore[typeddict-item]

    # El nodo 6 degrada sin juez (sobreviven todos) y eso está bien para un
    # informe; para un juicio de relevancia es veneno: todo queda en grado 2.
    # 18/09: el gateway perdió el puerto en el host y 41 consultas se guardaron
    # "juzgadas" así. Acá se corta y no se guarda nada.
    sin_juez = int((estado.get("curation") or {}).get("lotes_sin_juez", 0))
    if sin_juez:
        raise RuntimeError(f"el juez no respondió en {sin_juez} lote(s): no se guarda el juicio")

    detalle = (estado.get("valuation") or {}).get("detail", [])
    return {
        str(d["listing_id"]): grado_de(bool(d.get("included")), d.get("exclusion_reason"))
        for d in detalle
    }


# ── Consultas a partir de avisos: el corpus como banco de preguntas ──────


async def consultas_de_avisos(
    session: AsyncSession,
    *,
    n: int,
    semilla: int = 7,
    barrios: tuple[str, ...] = ("Palermo", "Belgrano"),
) -> list[Consulta]:
    """Un aviso con ficha completa y features extraídas, leído como sujeto.

    Es *leave-one-out* sobre el corpus: el propio aviso se excluye del ranking.
    Multiplica las consultas sin depender de que alguien haya pedido un
    informe, y las consultas salen de la misma distribución que los
    comparables — que es la del uso real.
    """
    filas = (
        await session.execute(
            select(Listing, ListingFeatures, Neighborhood.name)
            .join(ListingFeatures, ListingFeatures.listing_id == Listing.id)
            .join(Neighborhood, Neighborhood.id == Listing.neighborhood_id)
            .where(
                Listing.active.is_(True),
                Listing.currency == "USD",
                Listing.price.is_not(None),
                Listing.surface_weighted.is_not(None),
                Neighborhood.name.in_(barrios),
                ListingFeatures.rooms.is_not(None),
            )
        )
    ).all()
    candidatas = list(filas)
    rng = random.Random(semilla)  # noqa: S311 — muestreo, no criptografía
    rng.shuffle(candidatas)
    return [
        Consulta(f"aviso:{li.id}", sujeto_de_aviso(li, ft, barrio), {}, excluir={str(li.id)})
        for li, ft, barrio in candidatas[:n]
    ]


def sujeto_de_aviso(li: Listing, ft: ListingFeatures, barrio: str) -> dict[str, Any]:
    """Un aviso con features, en el contrato del sujeto que recibe el nodo 2.

    Lo usan el eval de recuperación (consultas *leave-one-out*) y el backtest
    con selección por recuperador: el mismo aviso tiene que ser el mismo
    sujeto en los dos.
    """
    return {
        "address_raw": li.address_raw or "",
        "city": "CABA",
        "province": "CABA",
        "neighborhood_id": str(li.neighborhood_id),
        "neighborhood_name": barrio,
        "property_type": ft.property_type or "departamento",
        "rooms": ft.rooms,
        "bedrooms": ft.bedrooms,
        "bathrooms": ft.bathrooms,
        "surface_total": str(ft.surface_total) if ft.surface_total is not None else None,
        "surface_covered": str(ft.surface_covered)
        if ft.surface_covered is not None
        else str(li.surface_weighted),
        "age_years": ft.age_years,
        "floor_number": ft.floor_number,
        "has_elevator": ft.has_elevator,
        "condition": ft.condition,
        "orientation": ft.orientation,
        "amenities": list(ft.amenities or []),
        "parking_spaces": ft.parking_spaces or 0,
        "expenses_ars": str(ft.expenses_ars) if ft.expenses_ars is not None else None,
        # Lo único que un formulario no tiene y un aviso sí: su texto. Es
        # la entrada de la consulta semántica (doc 18 §3.4).
        "notes": li.description or "",
    }


async def consultas_de_avisos_fijas(
    session: AsyncSession, *, n: int, semilla: int = 7
) -> list[Consulta]:
    """Las mismas N consultas de avisos en todas las corridas.

    `consultas_de_avisos` muestrea entre los avisos CON features, y el propio
    eval crea features (el nodo 4 extrae lo que el pool trae): entre una corrida
    y la siguiente cambiaba el universo, cambiaba el sorteo, y aparecían
    consultas nuevas mientras las viejas quedaban huérfanas (18/09: 102 archivos
    de juicios para 60 consultas). El set se fija en disco la primera vez.
    """
    ruta = CARPETA / "_consultas_avisos.json"
    if ruta.exists():
        ids = set(json.loads(ruta.read_text(encoding="utf-8"))["listing_ids"])
        todas = await consultas_de_avisos(session, n=100_000, semilla=semilla)
        fijas = [c for c in todas if c.report_id.removeprefix("aviso:") in ids]
        if len(fijas) < len(ids):
            log.warning("consultas fijas que ya no existen", faltan=len(ids) - len(fijas))
        return fijas
    consultas = await consultas_de_avisos(session, n=n, semilla=semilla)
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(
            {
                "listing_ids": [c.report_id.removeprefix("aviso:") for c in consultas],
                "semilla": semilla,
                "fijado_en": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return consultas


# ── Persistencia de los juicios ──────────────────────────────────────────


def _ruta(consulta_id: str) -> Path:
    return CARPETA / (consulta_id.replace(":", "_") + ".json")


def cargar_juicios(consulta_id: str) -> dict[str, Any] | None:
    ruta = _ruta(consulta_id)
    if not ruta.exists():
        return None
    datos: dict[str, Any] = json.loads(ruta.read_text(encoding="utf-8"))
    return datos


def guardar_juicios(consulta: Consulta, pool: list[str], juicios: dict[str, int]) -> None:
    CARPETA.mkdir(parents=True, exist_ok=True)
    _ruta(consulta.report_id).write_text(
        json.dumps(
            {
                "consulta": consulta.report_id,
                "subject": consulta.subject,
                "pool": pool,
                "juicios": juicios,
                "juzgado_en": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def aplicar_juicios_guardados(consultas: list[Consulta]) -> int:
    """Reemplaza los juicios de cada consulta por los del disco, si hay."""
    n = 0
    for c in consultas:
        guardado = cargar_juicios(c.report_id)
        if guardado:
            c.juicios = {k: int(v) for k, v in guardado["juicios"].items()}
            n += 1
    return n


async def juzgar_pool(
    session: AsyncSession,
    consulta: Consulta,
    rankings: dict[str, list[str]],
    *,
    top: int = 30,
    cfg: AgentsConfig | None = None,
    guardado: dict[str, Any] | None = None,
) -> dict[str, int]:
    """La unión de los top-N de todos los sistemas, juzgada de una vez.

    N = 30 y no 60: con cinco sistemas, la unión de los top-60 daba pools de
    150-170 avisos y ~6 minutos de juez por consulta (18/09). Con 30, el pool
    cubre lo que las métricas miran (nDCG@25, recall@30) a la mitad del costo.

    Con `guardado` (los juicios que ya están en disco para esta consulta) se
    juzga SOLO lo que ningún sistema había traído antes. Agregar un sistema a
    la comparación —el reranker, un modelo nuevo— cuesta entonces sus
    candidatos nuevos y no el pool entero, y los juicios de los sistemas que
    ya estaban no cambian de una corrida a la otra (el juez no es
    determinístico).
    """
    pool: list[str] = []
    vistos: set[str] = set()
    for ranking in rankings.values():
        for lid in ranking[:top]:
            if lid not in vistos and lid not in consulta.excluir:
                vistos.add(lid)
                pool.append(lid)
    if not pool:
        return {}
    previos: dict[str, int] = (
        {k: int(v) for k, v in guardado["juicios"].items()} if guardado else {}
    )
    nuevos = [lid for lid in pool if lid not in previos]
    juicios = dict(previos)
    if nuevos:
        candidatos = await candidatos_por_id(session, nuevos)
        juicios.update(await juzgar(consulta.subject, candidatos, cfg=cfg))
    # El pool guardado es la unión: lo de antes más lo que trajo el sistema nuevo.
    pool_total = list(dict.fromkeys([*(guardado["pool"] if guardado else []), *pool]))
    guardar_juicios(consulta, pool_total, juicios)
    return juicios
