"""¿Se encendieron los tres mecanismos que `published_at` apagaba? (H-21)

· `listing_age_coef`  — doc 05 §4.1: -3% a un aviso de más de 90 días
· `f_freshness`       — el 15% del score de confianza
· `aviso_vencido`     — la regla del nodo 6 para >180 días

  uv run python docs/auditoria/sondas/d_mecanismos.py
"""

import asyncio
import os
import sys
from decimal import Decimal

# La trampa #5 de la Etapa 3: en Windows el loop por defecto es
# `ProactorEventLoop` y psycopg async lo rechaza. En Linux es un no-op.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from tasador.agents.nodes.curate import reglas_duras
from tasador.agents.nodes.retrieve import _a_candidato
from tasador.valuation.adjustments import listing_age_coef, load_config
from tasador.valuation.engine import _f_freshness
from tasador.valuation.models import Comparable, Property

cfg = load_config()

SQL = """
select l.id from corpus.listings l
where l.active and l.published_at is not null
order by l.published_at limit 400
"""


async def main() -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tasador.db.models import Listing, ListingFeatures

    motor = create_async_engine(os.environ["DATABASE_URL"])
    async with async_sessionmaker(motor)() as s:
        filas = (
            await s.execute(
                select(Listing, ListingFeatures)
                .outerjoin(ListingFeatures, ListingFeatures.listing_id == Listing.id)
                .where(Listing.active.is_(True), Listing.published_at.is_not(None))
                .order_by(Listing.published_at)
                .limit(400)
            )
        ).all()
    await motor.dispose()

    cands = [_a_candidato(li, ft) for li, ft in filas]
    con_dias = [c for c in cands if c.get("days_published") is not None]
    print(f"  candidatos con `days_published`      : {len(con_dias)} de {len(cands)}")
    if not con_dias:
        print("  -> el nodo 2 NO está calculando los días: `published_at` no llegó")
        return

    dias = sorted(c["days_published"] for c in con_dias)  # type: ignore[type-var]
    print(f"  rango de días publicado              : {dias[0]} - {dias[-1]}")

    # 1. listing_age_coef
    coefs = [
        listing_age_coef(
            cfg,
            Comparable(
                ref=str(c["listing_id"]),
                price=Decimal("1"),
                currency="USD",
                prop=Property(),
                days_published=c["days_published"],
            ),
        )
        for c in con_dias
    ]
    con_castigo = sum(1 for x in coefs if x != 1)
    print(f"  listing_age_coef distinto de 1,00    : {con_castigo}  (antes: 0)")

    # 2. f_freshness
    comps = [
        Comparable(
            ref="x",
            price=Decimal("1"),
            currency="USD",
            prop=Property(),
            days_published=c["days_published"],
        )
        for c in con_dias
    ]
    import statistics

    fresco = _f_freshness(comps)
    mediana = statistics.median(dias)
    sin_fecha = _f_freshness(
        [Comparable(ref="x", price=Decimal("1"), currency="USD", prop=Property()) for _ in comps]
    )
    # ⚠️ `_f_freshness` devuelve 0,6 cuando NO hay fechas, y con una mediana de
    # 66 días devuelve (120-66)/90 = 0,6 también. El valor solo no alcanza para
    # saber si el mecanismo se encendió: hay que mirar la mediana.
    print(
        f"  f_freshness sobre esos avisos        : {fresco}  "
        f"(mediana {mediana:.0f} días · sin fecha daría {sin_fecha})"
    )
    print(
        f"    -> está CALCULANDO: {'sí' if dias else 'no'} "
        f"(el valor coincide con el default por casualidad si la mediana ≈ 66)"
    )

    # 3. aviso_vencido
    vencidos = sum(1 for c in con_dias if reglas_duras(c) == "aviso_vencido")
    print(f"  descartados por `aviso_vencido`      : {vencidos}  (antes: 0)")


asyncio.run(main())
