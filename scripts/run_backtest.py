"""Corre el backtest y muestra el resultado.

    uv run python scripts/run_backtest.py --sample 500

El resultado se publica como salga. Un backtest que solo se muestra cuando da
bien no es un backtest, es marketing (doc 09 §7).
"""

from __future__ import annotations

import argparse
import sys

from tasador.cli import run as run_async
from tasador.db.base import get_session_factory
from tasador.eval.backtest import render, run_backtest


async def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest del motor de valuación")
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dataset", default="BADATA_2020")
    args = ap.parse_args()

    async with get_session_factory()() as session:
        res = await run_backtest(session, dataset=args.dataset, sample=args.sample, seed=args.seed)
        print(render(res))

    return 0 if res.beats_baseline else 2


if __name__ == "__main__":
    sys.exit(run_async(main()))
