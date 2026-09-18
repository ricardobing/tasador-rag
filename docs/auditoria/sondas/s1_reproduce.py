"""Reproduce la matematica del informe REAL desde las filas persistidas.

Toma los comparables incluidos de un informe SUCCEEDED, corre los pasos 4-6 del
motor (mediana, winsorizado, percentiles, rango) y compara con lo guardado en
`core.reports`. Si no coinciden, la implementacion que corrio no es esta.
"""

import os
from decimal import ROUND_HALF_UP, Decimal as D

import sqlalchemy as sa

from tasador.valuation.adjustments import load_config
from tasador.valuation.engine import _median, _pct

cfg = load_config()
st = cfg["statistics"]
e = sa.create_engine(os.environ["DATABASE_URL"])


def q(v, places="1"):
    return v.quantize(D(places), rounding=ROUND_HALF_UP)


SQL_INFORMES = """
select r.id, r.status, r.confidence, r.value_low, r.value_mid, r.value_high,
       r.price_per_m2, r.weighted_surface, r.comparables_used, r.closing_low, r.closing_high
from core.reports r
where r.status='SUCCEEDED' and r.value_mid is not null
order by r.created_at desc limit 5
"""

SQL_COMPS = """
select adjusted_price_per_m2 from core.report_comparables
where report_id = :rid and included and adjusted_price_per_m2 is not null
"""

with e.connect() as c:
    informes = c.execute(sa.text(SQL_INFORMES)).mappings().all()
    for inf in informes:
        vals = [D(str(r[0])) for r in c.execute(sa.text(SQL_COMPS), {"rid": inf["id"]})]
        if len(vals) < 5:
            print(f"{str(inf['id'])[:8]}  <5 comparables persistidos, se omite")
            continue
        wl = _pct(vals, st["winsorize_lower_pct"])
        wu = _pct(vals, st["winsorize_upper_pct"])
        winsor = [min(max(x, wl), wu) for x in vals]
        usd_m2 = _median(winsor)
        p_lo = _pct(winsor, st["range_lower_pct"])
        p_hi = _pct(winsor, st["range_upper_pct"])
        sup = D(str(inf["weighted_surface"]))
        mid, low, high = usd_m2 * sup, p_lo * sup, p_hi * sup
        unc = D(str(st["uncertainty_half_width_pct"])) / 100
        half_min = max(mid * D(str(st["min_range_width_pct"])) / 100 / 2, mid * unc)
        if (high - low) / 2 < half_min:
            low, high = mid - half_min, mid + half_min
        if inf["confidence"] == "BAJA":
            h = (high - low) / 2 * D("1.5")
            low, high = mid - h, mid + h

        def cmp(nombre, calc, guardado):
            g = D(str(guardado))
            ok = abs(q(calc) - g) <= D("1")
            print(
                f"    {nombre:14s} calculado {q(calc):>12}  guardado {g:>12}  {'OK' if ok else '<<< NO COINCIDE'}"
            )

        print(
            f"\ninforme {str(inf['id'])[:8]}  n={len(vals)} (guardado comparables_used={inf['comparables_used']})  conf={inf['confidence']}"
        )
        cmp("price_per_m2", usd_m2, inf["price_per_m2"])
        cmp("value_mid", mid, inf["value_mid"])
        cmp("value_low", low, inf["value_low"])
        cmp("value_high", high, inf["value_high"])
        cmp("closing_low", mid * D(str(cfg["closing_discount"]["low"])), inf["closing_low"])
        cmp("closing_high", mid * D(str(cfg["closing_discount"]["high"])), inf["closing_high"])
