"""Datos iniciales. Idempotente: se puede correr las veces que haga falta.

`make seed` apuntaba a `python -m tasador.scripts.seed`, un módulo que nunca
existió. El que lo corría veía un `ModuleNotFoundError` y tenía que ir a buscar
a mano cuáles eran los scripts de verdad.

Hace las dos cosas que una base recién creada necesita antes de poder producir
un informe:

  1. **el tenant** — sin una organización activa, `POST /v1/reports` devuelve
     401 al no poder resolver el `org_id`;
  2. **los centroides de los 59 barrios** — sin ellos la escalera de relajación
     del nodo 2 no tiene con qué medir "cerca" y todo informe cae al primer
     escalón (informe Etapa 3 §5.2).

Lo que NO hace es cargar BA Data: son ~85.000 filas desde un CSV que primero
hay que bajar, y meter eso en un `seed` que alguien corre sin pensar es una
sorpresa de varios minutos. Se dice al final cómo se hace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tasador.cli import run


def _agregar_scripts_al_path() -> None:
    """Para que funcione tanto `python scripts/seed.py` como `-m scripts.seed`."""
    aqui = str(Path(__file__).resolve().parent)
    if aqui not in sys.path:
        sys.path.insert(0, aqui)


async def _seed(slug: str, name: str, *, offline: bool) -> int:
    # `python scripts/seed.py` pone `scripts/` en sys.path[0], así que los
    # hermanos se importan por nombre. Se reusan las funciones y no se
    # duplica la lógica: si mañana `seed_org` cambia, esto cambia con él.
    _agregar_scripts_al_path()
    from geocode_neighborhoods import _geocodificar  # type: ignore[import-not-found]
    from seed_org import _seed as _seed_org  # type: ignore[import-not-found]

    from tasador.corpus.neighborhoods import seed_neighborhoods
    from tasador.db.base import get_session_factory

    print("── 1/3 · organización ───────────────────────────────────────────")
    await _seed_org(slug, name)

    # Los barrios los sembraba solo `load_badata.py`. Con el corpus demo del
    # README (sin BA Data) la tabla quedaba vacía, el paso de centroides
    # decía "geocodificados=0" con cara de éxito, y el primer informe moría
    # con BARRIO_NO_RESUELTO (18/09, demo desde una base vacía).
    print("\n── 2/3 · barrios ───────────────────────────────────────────────")
    async with get_session_factory()() as session:
        creados, actualizados = await seed_neighborhoods(session)
        print(f"   {creados} nuevos, {actualizados} actualizados")

    print("\n── 3/3 · centroides de los barrios ──────────────────────────────")
    # `solo_faltantes=True`: los positivos de geocodificación no caducan nunca
    # —una coordenada no se mueve— así que reconsultar Nominatim por barrios
    # que ya están es gastarle requests a un servicio gratuito sin motivo.
    await _geocodificar(solo_faltantes=True, offline=offline)

    print("\n── listo ────────────────────────────────────────────────────────")
    print("BA Data (el corpus histórico del backtest) NO entra acá porque son")
    print("~85.000 filas y hay que bajar el CSV primero:")
    print("  uv run python scripts/fetch_badata.py")
    print("  uv run python scripts/load_badata.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="inmo-demo")
    ap.add_argument("--name", default="la inmobiliaria")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="geocodificar solo desde el caché en disco, sin tocar Nominatim",
    )
    args = ap.parse_args()
    return run(_seed(args.slug, args.name, offline=args.offline))


if __name__ == "__main__":
    raise SystemExit(main())
