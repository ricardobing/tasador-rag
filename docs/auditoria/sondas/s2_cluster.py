"""¿El cluster de 198 avisos es un cluster, o una cadena?"""

import os
from itertools import combinations

import sqlalchemy as sa

from tasador.agents.config import load_agents_config
from tasador.agents.nodes.dedup import _capa_1, _capa_2

cfg = load_agents_config().node("dedup_cluster")
e = sa.create_engine(os.environ["DATABASE_URL"])

SQL_TOP = """
select cluster_id, count(*) n from corpus.listings
where cluster_id is not null and active group by 1 order by 2 desc limit 4
"""
SQL_MIEMBROS = """
select id::text, address_raw, price::text, currency,
       raw->>'rooms', raw->>'surface_covered', raw->>'surface_total'
from corpus.listings where cluster_id = :cid and active
"""

with e.connect() as c:
    print(f"{'n':>5}  {'pares posibles':>15}  {'pares que MATCHEAN':>19}  {'%':>7}  direccion")
    for cid, n in c.execute(sa.text(SQL_TOP)):
        filas = c.execute(sa.text(SQL_MIEMBROS), {"cid": cid}).fetchall()
        cands = [
            {
                "listing_id": f[0],
                "address": f[1],
                "price": f[2],
                "currency": f[3],
                "rooms": f[4],
                "surface_covered": f[5],
                "surface_total": f[6],
                "included": True,
            }
            for f in filas
        ]
        posibles = len(cands) * (len(cands) - 1) // 2
        if posibles > 60_000:
            print(f"{n:>5}  (demasiados pares, se omite)")
            continue
        matchean = sum(
            1 for a, b in combinations(cands, 2) if _capa_1(a, b, cfg) or _capa_2(a, b, cfg)
        )
        pct = matchean / posibles * 100 if posibles else 0
        print(f"{n:>5}  {posibles:>15,}  {matchean:>19,}  {pct:>6.2f}%  {str(filas[0][1])[:40]}")

    print()
    print("--- clusters: metodo de match declarado y huerfanos")
    for r in c.execute(
        sa.text("select match_method, count(*) from corpus.listing_clusters group by 1")
    ):
        print("   ", r[0], r[1])
    print(
        "    filas en listing_clusters:",
        c.execute(sa.text("select count(*) from corpus.listing_clusters")).scalar(),
    )
    print(
        "    cluster_id distintos en listings:",
        c.execute(
            sa.text(
                "select count(distinct cluster_id) from corpus.listings where cluster_id is not null"
            )
        ).scalar(),
    )
    print(
        "    clusters SIN ningun miembro (huerfanos):",
        c.execute(
            sa.text(
                "select count(*) from corpus.listing_clusters cl "
                "where not exists (select 1 from corpus.listings l where l.cluster_id = cl.id)"
            )
        ).scalar(),
    )
