"""Extrae features de TODO el corpus vigente, no solo de los candidatos de un
informe.

    uv run python scripts/extraer_corpus.py --barrio Palermo --dry-run
    uv run python scripts/extraer_corpus.py --barrio Palermo
    uv run python scripts/extraer_corpus.py            # todos los barrios

## Para qué existe

El nodo 4 extrae bajo demanda: los avisos que ningún informe tocó no tienen
features. Eso alcanza para producir informes, pero deja dos cosas cojas:

  1. **El backtest con el motor de ajustes activo** (el pendiente de ESTADO
     §5.1 #5): sin features en los comparables, `value()` no ajusta nada y el
     backtest mide el motor apagado.
  2. El nodo 2 ordena y filtra mejor con features que con el crudo del portal.

## Qué reusa, y por qué no reimplementa nada

`extraer_lotes` + `verificar_citas` + `_persistir` son LOS MISMOS que usa el
nodo 4 en producción (la lección del eval que medía un fragmento del sistema:
informe Etapa 3 §5.4). Solo se extrae el CANÓNICO de cada cluster: extraer las
20 copias de una torre pagaría 20 veces la misma descripción.

Costo medido: ~60 avisos ≈ 90 s y centavos con el extractor configurado
(deepseek-v4-flash). El corpus entero de Palermo (~2.600 sin features) es del
orden de una hora y menos de un dólar. Es reanudable: los ya extraídos no se
vuelven a procesar (se filtran por la existencia de la fila en
`listing_features`).
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from tasador.agents.config import load_agents_config
from tasador.agents.nodes.extract import (
    CONF_CITA_FALSA,
    LoteExtraido,
    _persistir,
    _texto_del_aviso,
    extraer_lotes,
    verificar_citas,
)
from tasador.agents.nodes.retrieve import _a_candidato
from tasador.agents.state import Candidate
from tasador.cli import run as run_async
from tasador.db.base import get_session_factory
from tasador.db.models import Listing, ListingCluster, ListingFeatures, Neighborhood
from tasador.llm import LlmClient, LlmUsage, UsageLedger

log = structlog.get_logger()


async def _pendientes(
    barrio: str | None, limite: int | None, reextraer: str | None = None
) -> list[Candidate]:
    """Avisos vigentes SIN features, solo canónicos de cluster.

    Con `reextraer='extractor/v2'` devuelve los que YA tienen features pero de
    esa versión de prompt. Es lo que hace posible corregir una decisión de
    prompt sin borrar la tabla: `listing_features` es una INTERPRETACIÓN con su
    `extractor_version`, y reprocesar es exactamente para lo que se guardó esa
    columna (doc 03 §3.2).
    """
    from sqlalchemy import or_

    async with get_session_factory()() as session:
        q = (
            select(Listing)
            .outerjoin(ListingFeatures, ListingFeatures.listing_id == Listing.id)
            .outerjoin(ListingCluster, ListingCluster.id == Listing.cluster_id)
            .outerjoin(Neighborhood, Neighborhood.id == Listing.neighborhood_id)
            .where(
                Listing.active.is_(True),
                Listing.operation == "SALE",
                Listing.description.is_not(None),
                ListingFeatures.extractor_version == reextraer
                if reextraer
                else ListingFeatures.listing_id.is_(None),
                or_(
                    Listing.cluster_id.is_(None),
                    ListingCluster.canonical_id.is_(None),
                    ListingCluster.canonical_id == Listing.id,
                ),
            )
            .order_by(Listing.first_seen_at.desc())
        )
        if barrio:
            q = q.where(Neighborhood.name == barrio)
        if limite:
            q = q.limit(limite)
        filas = (await session.execute(q)).scalars().all()
    return [_a_candidato(li, None) for li in filas]


async def _extraer(candidatos: list[Candidate], paralelo: int | None, lote: int | None) -> None:
    cfg = load_agents_config().node("extract_features")
    # Para una corrida masiva el paralelismo de un informe (4 lotes) es poco:
    # acá no compite con nada. `model_copy` porque NodeConfig es frozen.
    if paralelo or lote:
        params = dict(cfg.params)
        if paralelo:
            params["max_concurrent_batches"] = paralelo
        if lote:
            params["batch_size"] = lote
        cfg = cfg.model_copy(update={"params": params})
    umbral = float(cfg.param("min_field_confidence", 0.7))
    por_ref = {c["listing_id"]: c for c in candidatos}
    ledger = UsageLedger()
    hechos = fallados = citas_falsas = 0
    t0 = datetime.now(UTC)
    session_factory = get_session_factory()

    async def _guardar_lote(
        lote: list[Candidate], salida: LoteExtraido | None, usos: list[LlmUsage]
    ) -> None:
        """Igual que el nodo: guardar y commitear POR LOTE. El trabajo que solo
        vive en memoria no existe (auditoría 14/08 §4)."""
        nonlocal hechos, fallados, citas_falsas
        ledger.extend(usos)
        if salida is None:
            fallados += len(lote)
            return
        modelo = usos[-1].provider_model if usos else "?"
        async with session_factory() as session:
            for f in salida.avisos:
                c = por_ref.get(f.listing_ref)
                if c is None:
                    continue
                confianzas = verificar_citas(f, _texto_del_aviso(c))
                citas_falsas += sum(1 for v in confianzas.values() if v == CONF_CITA_FALSA)
                await _persistir(
                    session,
                    c["listing_id"],
                    f,
                    confianzas,
                    modelo,
                    cfg.prompt or "extractor/v1",
                    umbral,
                )
                hechos += 1
            await session.commit()
        transcurrido = (datetime.now(UTC) - t0).total_seconds()
        print(
            f"  {hechos:>5} extraídos · {fallados} fallados · "
            f"USD {ledger.cost_usd:.4f} · {transcurrido:.0f} s",
            flush=True,
        )

    cliente = LlmClient()
    try:
        await extraer_lotes(cliente, cfg, candidatos, al_completar=_guardar_lote)
    finally:
        await cliente.close()

    print(
        f"\nLISTO: {hechos} extraídos, {fallados} fallados, "
        f"{citas_falsas} citas falsas (marcadas needs_review), "
        f"USD {ledger.cost_usd:.4f}, "
        f"{(datetime.now(UTC) - t0).total_seconds():.0f} s"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--barrio", default=None)
    ap.add_argument("--limite", type=int, default=None, help="tope de avisos (para probar)")
    ap.add_argument(
        "--paralelo", type=int, default=None, help="lotes en paralelo (default: agents.yaml)"
    )
    ap.add_argument("--lote", type=int, default=None, help="avisos por lote (default: agents.yaml)")
    ap.add_argument(
        "--reextraer",
        default=None,
        metavar="VERSION",
        help="reprocesar los que ya tienen features de esa versión (ej: extractor/v2)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    candidatos = run_async(_pendientes(args.barrio, args.limite, args.reextraer))
    print(
        f"{len(candidatos)} avisos vigentes "
        + (f"con features de {args.reextraer}" if args.reextraer else "sin features")
        + (f" en {args.barrio}" if args.barrio else "")
    )
    if args.dry_run or not candidatos:
        return 0
    run_async(_extraer(candidatos, args.paralelo, args.lote))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
