"""H-33: los tres variantes de la consulta del nodo 2, con los parámetros REALES.

  A · el original      — `(raw->>'surface_weighted')::numeric` adentro del coalesce
  B · columna          — la misma consulta con la columna, sin pre-filtro
  C · columna + cerco  — lo que se implementó

Se mide tiempo y buffers (mediana de 3, R6) y se comparan los CONJUNTOS de ids:
un índice que acelera y cambia el resultado no es una optimización.

Los parámetros salen de `config/agents.yaml`, escalón 1: surface_pct 30,
days 120, rooms_delta 1.
"""

import os
import re
from decimal import Decimal

import sqlalchemy as sa

e = sa.create_engine(os.environ["DATABASE_URL"])

BASE = """
select l.id
from corpus.listings l
left join corpus.listing_features f on f.listing_id = l.id
left join corpus.listing_clusters k on k.id = l.cluster_id
where l.active and l.operation = 'SALE' and l.currency = 'USD'
  and not l.price_on_request and l.price is not null
  and l.neighborhood_id = '{bid}'
  and l.last_seen_at > now() - make_interval(0, 0, 0, 120)
  and (l.cluster_id is null or k.canonical_id is null or k.canonical_id = l.id)
  and ({sup_expr} is null or ({sup_expr} >= {lo} and {sup_expr} <= {hi}))
  and (coalesce(f.rooms, nullif(l.raw->>'rooms','')::smallint) is null
       or (coalesce(f.rooms, nullif(l.raw->>'rooms','')::smallint) between {ra} and {rb}))
  {cerco}
order by l.last_seen_at desc
limit 60
"""

CRUDO = "coalesce(f.surface_covered, f.surface_total, nullif(l.raw->>'surface_weighted','')::numeric)"
COLUMNA = "coalesce(f.surface_covered, f.surface_total, l.surface_weighted)"


def sql(bid, sup, pct, amb, delta, *, expr, cerco=""):
    f = Decimal(str(pct)) / 100
    lo, hi = sup * (1 - f), sup * (1 + f)
    holgura = (hi - lo) * Decimal("0.4")
    c = ""
    if cerco:
        c = (
            f"and (l.surface_weighted is null or "
            f"(l.surface_weighted >= {lo - holgura} and l.surface_weighted <= {hi + holgura}))"
        )
    return BASE.format(
        bid=bid, sup_expr=expr, lo=lo, hi=hi, ra=amb - delta, rb=amb + delta, cerco=c
    )


def medir(c, s):
    t, b = [], []
    for _ in range(3):
        out = "\n".join(r[0] for r in c.execute(sa.text(f"EXPLAIN (ANALYZE, BUFFERS) {s}")).all())
        m = re.search(r"Execution Time: ([\d.]+) ms", out)
        if m:
            t.append(float(m.group(1)))
        b.append(sum(int(x) for x in re.findall(r"shared hit=(\d+)", out)))
    t.sort()
    b.sort()
    ids = {str(r[0]) for r in c.execute(sa.text(s)).all()}
    return t[len(t) // 2], b[len(b) // 2], ids


with e.connect() as c:
    bid = c.execute(
        sa.text("select id from corpus.neighborhoods where name='Palermo' limit 1")
    ).scalar_one()

    print("  Palermo · 80 m² · 3 amb · escalón 1 (surface_pct 30, 120 días)\n")
    print(f"  {'variante':<28}{'ms':>9}{'buffers':>12}{'filas':>8}")
    conj = {}
    for nombre, expr, cerco in (
        ("A · original (raw JSONB)", CRUDO, ""),
        ("B · columna, sin cerco", COLUMNA, ""),
        ("C · columna + cerco", COLUMNA, "si"),
    ):
        ms, buf, ids = medir(c, sql(bid, Decimal("80"), 30, 3, 1, expr=expr, cerco=cerco))
        conj[nombre] = ids
        print(f"  {nombre:<28}{ms:>9.1f}{buf:>12}{len(ids):>8}")

    a = conj["A · original (raw JSONB)"]
    cc = conj["C · columna + cerco"]
    print()
    print(f"  A y C devuelven lo mismo   : {a == cc}")
    if a != cc:
        print(f"    solo en A: {len(a - cc)}   solo en C: {len(cc - a)}")

    # Y el barrido: ¿el cerco pierde algún candidato en ALGÚN caso?
    print()
    print("  barrido: ¿el cerco descarta algo que el criterio real aceptaría?")
    perdidos = c.execute(
        sa.text(
            """
        with r as (select generate_series(30, 250, 10)::numeric sup,
                          unnest(array[30, 40]) pct)
        select count(*) from r, corpus.listings l
        left join corpus.listing_features f on f.listing_id = l.id
        where l.active
          and coalesce(f.surface_covered, f.surface_total, l.surface_weighted)
              between r.sup*(1-r.pct/100) and r.sup*(1+r.pct/100)
          and l.surface_weighted is not null
          and (l.surface_weighted < r.sup*(1-r.pct/100) - (r.sup*r.pct/100*2)*0.4
            or l.surface_weighted > r.sup*(1+r.pct/100) + (r.sup*r.pct/100*2)*0.4)
        """
        )
    ).scalar_one()
    print(f"    candidatos perdidos sobre TODO el corpus, 23 superficies x 2 pct: {perdidos}")
