"""Impacto medido de NO normalizar la cochera del comparable, sobre un informe real."""

import os
from decimal import ROUND_HALF_UP, Decimal as D

import sqlalchemy as sa

from tasador.valuation.adjustments import load_config
from tasador.valuation.engine import _median, _pct

cfg = load_config()
st = cfg["statistics"]
VAL_COCHERA = D(str(cfg["absolute_usd"]["parking_space"]))
e = sa.create_engine(os.environ["DATABASE_URL"])

SQL = """
select rc.snapshot_price, rc.snapshot_surface, rc.adjusted_price_per_m2,
       coalesce(nullif(l.raw->>'parking_spaces','')::int, 0) cocheras
from core.report_comparables rc
join corpus.listings l on l.id = rc.listing_id
where rc.report_id = :rid and rc.included and rc.adjusted_price_per_m2 is not null
"""


def resumen(vals):
    wl, wu = _pct(vals, st["winsorize_lower_pct"]), _pct(vals, st["winsorize_upper_pct"])
    w = [min(max(x, wl), wu) for x in vals]
    return _median(w).quantize(D("1"), rounding=ROUND_HALF_UP)


with e.connect() as c:
    for rid, sup in c.execute(
        sa.text(
            "select id, weighted_surface from core.reports "
            "where status='SUCCEEDED' and value_mid is not null order by created_at desc limit 6"
        )
    ):
        filas = c.execute(sa.text(SQL), {"rid": rid}).fetchall()
        if len(filas) < 5:
            continue
        con_coch = sum(1 for f in filas if f[3] > 0)
        tal_cual = [D(str(f[2])) for f in filas]
        # Contrafactual: al comparable con cochera se le descuenta el valor de la
        # cochera del precio ANTES de dividir por la superficie, con el mismo
        # coeficiente de ajuste que ya tenia aplicado.
        normalizado = []
        for precio, superficie, ajustado, coch in filas:
            if coch and superficie:
                factor = D(str(ajustado)) / (D(str(precio)) / D(str(superficie)))
                nuevo_crudo = (D(str(precio)) - VAL_COCHERA * coch) / D(str(superficie))
                normalizado.append(nuevo_crudo * factor)
            else:
                normalizado.append(D(str(ajustado)))
        a, b = resumen(tal_cual), resumen(normalizado)
        sup_d = D(str(sup))
        print(
            f"{str(rid)[:8]}  n={len(filas):3d}  con cochera={con_coch:2d}  "
            f"USD/m2 hoy {a:>6}  normalizado {b:>6}  "
            f"valor medio {a * sup_d:>12,.0f} -> {b * sup_d:>12,.0f}  "
            f"({(b - a) / a * 100:+.2f}%)"
        )
