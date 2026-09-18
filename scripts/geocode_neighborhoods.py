"""Llena `corpus.neighborhoods.centroid_lat/lng` con Nominatim. Una sola vez.

Sin esto, la escalera de relajación del nodo 2 no tiene de dónde sacar
"barrios cercanos": la base no tiene una sola coordenada ni una tabla de
adyacencia (verificado el 13/08 — 0 de 59 barrios con centroide).

La alternativa era escribir a mano qué barrio limita con cuál. Se descartó:
sería un dato inventado de memoria, imposible de verificar, y encima
"limítrofe" no es lo que queremos — queremos CERCA. Dos barrios pueden
lindar por una punta y tener sus centros a 4 km.

Respeta 1 req/s: 59 barrios tardan ~70 segundos. Es idempotente y cachea en
disco, así que correrlo de nuevo no consulta nada.

    uv run python scripts/geocode_neighborhoods.py
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from tasador.cli import run
from tasador.db.base import get_session_factory
from tasador.db.models import Neighborhood
from tasador.geocoding import Geocoder, dentro_del_amba


async def _geocodificar(solo_faltantes: bool, offline: bool) -> None:
    geo = Geocoder(offline=offline)
    hechos = fallados = salteados = 0

    async with get_session_factory()() as session:
        consulta = select(Neighborhood).order_by(Neighborhood.city, Neighborhood.name)
        barrios = (await session.execute(consulta)).scalars().all()

        for b in barrios:
            if solo_faltantes and b.centroid_lat is not None:
                salteados += 1
                continue

            # El barrio se busca por nombre + ciudad + PROVINCIA, no por una
            # dirección: Nominatim devuelve el centroide del polígono
            # administrativo, y sin la provincia agarra homónimos.
            u = await geo.geocodificar(b.name, ciudad=b.city, provincia=b.province, tipo="lugar")
            if u is None:
                print(f"  ✗ {b.city}/{b.name}: sin resultado")
                fallados += 1
                continue

            if not dentro_del_amba(u.lat, u.lng):
                # NO se guarda. Un centroide en otra provincia haría que la
                # relajación por cercanía del nodo 2 traiga comparables de
                # 900 km, y el informe no tendría cómo notarlo.
                print(
                    f"  ✗ {b.city}/{b.name}: {u.lat:.5f},{u.lng:.5f} cae FUERA del AMBA "
                    f"— homónimo de otra provincia, descartado"
                )
                fallados += 1
                continue

            b.centroid_lat = round(u.lat, 7)  # type: ignore[assignment]
            b.centroid_lng = round(u.lng, 7)  # type: ignore[assignment]
            hechos += 1
            print(f"  ✓ {b.city}/{b.name:<22} {u.lat:>10.5f} {u.lng:>10.5f}")

        await session.commit()

    print(f"\ngeocodificados={hechos}  fallados={fallados}  ya estaban={salteados}")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--todos", action="store_true", help="rehacer también los que ya tienen")
    ap.add_argument("--offline", action="store_true", help="solo desde el caché en disco")
    args = ap.parse_args()
    run(_geocodificar(solo_faltantes=not args.todos, offline=args.offline))


if __name__ == "__main__":
    main()
