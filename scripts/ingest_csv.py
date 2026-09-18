"""Incorpora al corpus los CSV que deja el scraper externo.

    uv run python scripts/ingest_csv.py --carpeta C:\\datos\avisos\\output
    uv run python scripts/ingest_csv.py --carpeta ... --dry-run
    uv run python scripts/ingest_csv.py --carpeta ... --forzar

Es idempotente por diseño: se puede dejar corriendo periódicamente y solo entra
lo que todavía no está. Ver `tasador.ingest.csv_scan` para el porqué de cada
decisión.

⚠️ Desde Windows hay que apuntar `DATABASE_URL` al HOST y no a la red de Docker:

    $env:DATABASE_URL="postgresql+psycopg://tasador:<PASS>@127.0.0.1:5433/tasador"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tasador.cli import run


async def _correr(carpeta: Path, *, forzar: bool, dry_run: bool, patron: str) -> int:
    from tasador.db.base import get_session_factory
    from tasador.ingest.csv_scan import (
        _archivos_a_ingerir,
        escanear,
        fila_a_card,
        leer_filas,
        motivo_descarte,
        parece_de_avisos,
        sha256_de,
    )

    if not carpeta.is_dir():
        print(f"No existe la carpeta: {carpeta}")
        return 2

    archivos = (
        sorted(p for p in carpeta.rglob(patron) if p.is_file())
        if patron
        else _archivos_a_ingerir(carpeta)
    )
    if not archivos:
        print(f"No hay archivos con avisos bajo {carpeta}")
        return 0

    if dry_run:
        # Sin tocar la base: sirve para ver qué se va a descartar ANTES de
        # meter 300 filas y descubrir después que la mitad no servía.
        print(f"DRY RUN — {len(archivos)} archivos, no se escribe nada\n")
        for archivo in archivos:
            filas = leer_filas(archivo)
            if not filas:
                # NO es lo mismo que "ajeno". Un archivo NUESTRO con 0 filas es
                # una corrida del scraper que volvió vacía —el 14/08, por
                # CAPTCHA de Portal B— y ese es exactamente el síntoma que
                # `/admin/fuentes` existe para mostrar. Llamarlo "ignorado" lo
                # esconde detrás de la misma etiqueta que un metadata.json de
                # Chrome, que no tiene nada que ver.
                print(f"{archivo.relative_to(carpeta)}")
                print("  ⚠️  VACÍO: el archivo existe y no trae ni una fila\n")
                continue
            if not parece_de_avisos(filas):
                # No es nuestro: un metadata.json del perfil de Chrome del
                # scraper, por ejemplo. Se dice y se sigue.
                print(f"{archivo.relative_to(carpeta)}")
                print("  (ignorado: no tiene esquema de avisos)\n")
                continue
            motivos: dict[str, int] = {}
            usables = 0
            for fila in filas:
                card = fila_a_card(fila)
                if card is None:
                    motivos["fila_invalida"] = motivos.get("fila_invalida", 0) + 1
                    continue
                m = motivo_descarte(card)
                if m is None:
                    usables += 1
                else:
                    motivos[m] = motivos.get(m, 0) + 1
            print(f"{archivo.relative_to(carpeta)}")
            print(f"  sha256   {sha256_de(archivo)[:16]}…")
            print(f"  filas    {len(filas)}   usables {usables}")
            for k, v in sorted(motivos.items(), key=lambda kv: -kv[1]):
                print(f"    descartadas por {k:<26}{v:>5}")
            print()
        return 0

    async with get_session_factory()() as session:
        total = await escanear(session, carpeta, forzar=forzar)

    print()
    print("═" * 66)
    print(f"INGESTA DE CSV — {carpeta}")
    print("═" * 66)
    print(f"  archivos vistos     {total.archivos_vistos}")
    print(f"  archivos nuevos     {total.archivos_nuevos}")
    print(f"  ya estaban          {total.archivos_salteados}")
    if total.archivos_ajenos:
        print(f"  ignorados (ajenos)  {total.archivos_ajenos}")
    if total.archivos_con_error:
        print(f"  con error           {total.archivos_con_error}")
    print()
    s = total.stats
    print(f"  filas leídas        {s.vistos}")
    print(f"  avisos NUEVOS       {s.nuevos}")
    print(f"  actualizados        {s.actualizados}")
    print(f"  sin cambios         {s.sin_cambios}")
    print(f"  cambios de precio   {s.cambios_precio}")
    print(f"  descartados         {s.descartados}")
    for k, v in s.motivos.most_common():
        print(f"      {k:<28}{v:>5}")
    if s.errores:
        print(f"  errores             {s.errores}")
    return 0


def main() -> int:
    # La consola de Windows es cp1252 y este resumen usa ═ y ·. Sin esto el
    # script hace TODO el trabajo, commitea, y revienta al imprimir el
    # resultado — el usuario ve un traceback y cree que no se cargó nada.
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--carpeta", required=True, type=Path)
    ap.add_argument(
        "--patron",
        default=None,
        help="glob explícito; por defecto detecta csv/jsonl/json recursivamente",
    )
    ap.add_argument(
        "--forzar", action="store_true", help="reprocesar archivos ya vistos (mismo sha256)"
    )
    ap.add_argument("--dry-run", action="store_true", help="qué entraría, sin escribir nada")
    args = ap.parse_args()
    return run(_correr(args.carpeta, forzar=args.forzar, dry_run=args.dry_run, patron=args.patron))


if __name__ == "__main__":
    raise SystemExit(main())
