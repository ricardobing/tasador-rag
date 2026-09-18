"""Siembra barrios y carga los CSV de BA Data al corpus.

    uv run python scripts/fetch_badata.py --years 2020     # bajar primero
    uv run python scripts/load_badata.py                   # cargar

Idempotente: correrlo dos veces no duplica.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tasador.cli import run as run_async
from tasador.corpus.coverage import coverage_report, render
from tasador.corpus.neighborhoods import seed_neighborhoods
from tasador.db.base import get_session_factory
from tasador.ingest.badata import load_listings_csv, load_market_index


async def main() -> int:
    ap = argparse.ArgumentParser(description="Carga BA Data al corpus")
    ap.add_argument("--dir", default="data/raw/badata")
    ap.add_argument("--skip-listings", action="store_true", help="solo la serie oficial")
    args = ap.parse_args()

    src = Path(args.dir)
    if not src.exists():
        print(f"No existe {src}. Corré primero: uv run python scripts/fetch_badata.py")
        return 1

    async with get_session_factory()() as session:
        print("→ sembrando barrios")
        created, updated = await seed_neighborhoods(session)
        print(f"   {created} nuevos, {updated} actualizados")

        serie = src / "precio-venta-deptos.csv"
        if serie.exists():
            print("\n→ serie oficial de USD/m² (el ancla del chequeo de sesgo)")
            n = await load_market_index(session, serie)
            print(f"   {n} puntos cargados")
        else:
            print(f"\n⚠️  falta {serie.name}")

        if not args.skip_listings:
            for csv_path in sorted(src.glob("departamentos-en-venta-*.csv")):
                year = int(csv_path.stem.rsplit("-", 1)[-1])
                print(f"\n→ avisos históricos {year} — dataset de backtest")
                stats = await load_listings_csv(session, csv_path, year)
                print(f"   filas leídas      {stats.rows:>8,}")
                print(f"   cargadas          {stats.loaded:>8,}")
                print(f"   inválidas         {stats.skipped_invalid:>8,}")
                print(f"   barrio sin mapear {stats.skipped_no_neighborhood:>8,}")
                print(f"   duplicadas        {stats.duplicates:>8,}")

        print("\n" + "=" * 78)
        print("COBERTURA DEL CORPUS")
        print(render(await coverage_report(session)))

    return 0


if __name__ == "__main__":
    sys.exit(run_async(main()))
