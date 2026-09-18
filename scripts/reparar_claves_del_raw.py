"""Claves de `listings.raw` que no están en el contrato — H-25.

`CAMPOS_DEL_RAW` es el contrato con el nodo 2, que lee por nombre exacto
(`de_crudo("parking_spaces")`). Una clave con el nombre viejo es un dato que
está en la base, completo, y que el motor no encuentra: exactamente el bug del
14/08, del que quedaron once filas sin migrar porque el arreglo se dio por
completo sin contar las filas después (R7).

Por defecto NO escribe: informa. `--aplicar` es explícito.

    uv run python scripts/reparar_claves_del_raw.py            # informe
    uv run python scripts/reparar_claves_del_raw.py --aplicar  # renombra

El renombre es idempotente y no pierde datos: si la clave nueva ya existe con
otro valor, la fila se reporta como conflicto y NO se toca.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import sqlalchemy as sa

from tasador.cli import run as run_async
from tasador.db.base import get_session_factory
from tasador.ingest.core import CAMPOS_DEL_RAW

# Lo que `_raw` escribe además de `CAMPOS_DEL_RAW.values()`.
EXTRA_LEGITIMO = {"surface_weighted", "attrs"}

# De la clave vieja a la del contrato. Solo renombres YA decididos: esto no es
# el lugar para inventar mapeos nuevos, que van en `CAMPOS_DEL_RAW`.
RENOMBRES = {atributo: clave for atributo, clave in CAMPOS_DEL_RAW.items() if atributo != clave}


def _declaradas() -> set[str]:
    return set(CAMPOS_DEL_RAW.values()) | EXTRA_LEGITIMO


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true", help="escribe; sin esto solo informa")
    args = ap.parse_args()

    declaradas = _declaradas()

    async with get_session_factory()() as s:
        # 1. Todas las claves que hay en el corpus, con su cuenta.
        filas = (
            await s.execute(
                sa.text(
                    "select k, count(*) n from corpus.listings l, "
                    "jsonb_object_keys(l.raw) k where l.active group by k order by n desc"
                )
            )
        ).all()

        fuera = [(k, n) for k, n in filas if k not in declaradas]
        print(f"claves distintas en `raw`   : {len(filas)}")
        print(f"declaradas en el contrato   : {len(declaradas)}")
        print(f"fuera del contrato          : {len(fuera)}")
        for k, n in fuera:
            destino = RENOMBRES.get(k)
            etiqueta = f"-> {destino}" if destino else "SIN RENOMBRE DECLARADO"
            print(f"    {k:24} {n:6}  {etiqueta}")

        if not fuera:
            print("\nnada que reparar")
            return 0

        # 2. De las que sí tienen renombre: cuántas se pueden migrar sin pisar.
        total_migradas = 0
        for vieja, nueva in RENOMBRES.items():
            if vieja not in {k for k, _ in fuera}:
                continue
            conflictos = (
                await s.execute(
                    sa.text(
                        "select count(*) from corpus.listings where active "
                        "and raw ? :v and raw ? :n and raw->>:v is distinct from raw->>:n"
                    ),
                    {"v": vieja, "n": nueva},
                )
            ).scalar_one()
            migrables = (
                await s.execute(
                    sa.text(
                        "select count(*) from corpus.listings where active and raw ? :v "
                        "and (not raw ? :n or raw->>:v is not distinct from raw->>:n)"
                    ),
                    {"v": vieja, "n": nueva},
                )
            ).scalar_one()
            print(f"\n  {vieja} -> {nueva}")
            print(f"    migrables  {migrables}")
            print(f"    conflictos {conflictos}   (no se tocan)")

            if not args.aplicar or not migrables:
                continue

            # `- vieja` después de `|| jsonb_build_object(...)`: primero copia,
            # después borra. Al revés perdería el valor.
            hechas = (
                await s.execute(
                    sa.text(
                        "update corpus.listings set raw = "
                        "(raw || jsonb_build_object(:n, raw->:v)) - :v "
                        "where active and raw ? :v "
                        "and (not raw ? :n or raw->>:v is not distinct from raw->>:n)"
                    ),
                    {"v": vieja, "n": nueva},
                )
            ).rowcount
            total_migradas += hechas
            print(f"    APLICADO   {hechas} filas")

        if not args.aplicar:
            print("\n(informe: no se escribió nada. `--aplicar` para migrar)")
            return 0

        await s.commit()

        # 3. R7: contar DESPUÉS de actuar, no dar el arreglo por hecho.
        restantes = (
            await s.execute(
                sa.text(
                    "select k, count(*) from corpus.listings l, jsonb_object_keys(l.raw) k "
                    "where l.active and k = any(:vs) group by k"
                ),
                {"vs": list(RENOMBRES)},
            )
        ).all()
        print(f"\nmigradas {total_migradas} filas")
        print(f"quedan con clave vieja: {dict(restantes) or 'ninguna'}")
        return 0


if __name__ == "__main__":
    sys.exit(run_async(main()))
