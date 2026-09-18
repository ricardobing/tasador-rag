"""Corre el backtest y muestra el resultado.

    uv run python scripts/run_backtest.py --sample 500

El resultado se publica como salga. Un backtest que solo se muestra cuando da
bien no es un backtest, es marketing (doc 09 §7).
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from tasador.cli import run as run_async
from tasador.db.base import get_session_factory
from tasador.eval.backtest import render, run_backtest


async def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest del motor de valuación")
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dataset", default="BADATA_2020")
    ap.add_argument(
        "--seleccion",
        default="superficie",
        help="superficie (default) o un sistema del eval de recuperación: A, E, F… (solo VIGENTES)",
    )
    ap.add_argument(
        "--seeds",
        default="",
        help="varias semillas separadas por coma: imprime el MdAPE de cada una y la amplitud",
    )
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()] or [args.seed]
    mdapes: list[Decimal] = []

    async with get_session_factory()() as session:
        for seed in seeds:
            res = await run_backtest(
                session,
                dataset=args.dataset,
                sample=args.sample,
                seed=seed,
                seleccion=args.seleccion,
            )
            print(render(res))
            if res.mdape is not None:
                mdapes.append(res.mdape)
    if len(seeds) > 1 and mdapes:
        print(
            f"MdAPE por semilla {seeds}: "
            + " · ".join(f"{m * 100:.1f}%" for m in mdapes)
            + f"   media {sum(mdapes) / len(mdapes) * 100:.1f}%"
            + f"   amplitud {(max(mdapes) - min(mdapes)) * 100:.1f} pp"
        )

    return 0 if res.beats_baseline else 2


if __name__ == "__main__":
    sys.exit(run_async(main()))
