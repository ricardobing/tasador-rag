"""¿Que regla de `_valores_permitidos` abre el agujero?"""

import os
import random
from decimal import Decimal as D

import sqlalchemy as sa

from tasador.agents.nodes.critic import _RE_NUMERO, _a_decimal

e = sa.create_engine(os.environ["DATABASE_URL"])
random.seed(20260814)


def base(datos):
    """`_valores_permitidos` SIN los derivados (sin *100 y sin /1000)."""
    permitidos = set()

    def rec(x):
        if isinstance(x, dict):
            [rec(v) for v in x.values()]
        elif isinstance(x, list):
            [rec(v) for v in x]
        elif isinstance(x, bool) or x is None:
            return
        elif isinstance(x, int | float | D):
            permitidos.add(D(str(x)))
        elif isinstance(x, str):
            if (d := _a_decimal(x)) is not None:
                permitidos.add(d)
            else:
                for t in _RE_NUMERO.findall(x):
                    if (d2 := _a_decimal(t)) is not None:
                        permitidos.add(d2)

    rec(datos)
    return permitidos


def cobertura(permitidos, tol=D("0.01"), n=20000):
    m = [D(random.randint(100_000, 400_000)) for _ in range(n)]
    return (
        sum(1 for x in m if any(abs(x - p) <= max(abs(p) * tol, D("0.5")) for p in permitidos)) / n
    )


SQL = """
select r.id, r.methodology from core.reports r
where r.status='SUCCEEDED' and r.methodology ? 'detail' order by r.created_at desc limit 1
"""
with e.connect() as c:
    rid, m = c.execute(sa.text(SQL)).fetchone()

usados = [d for d in m.get("detail", []) if d.get("included")]
datos = {
    "valuacion": {"medio": m.get("value_mid"), "usd_por_m2": m.get("price_per_m2")},
    "comparables": {
        "detalle_usados": [
            {
                "precio": d.get("snapshot_price"),
                "superficie_m2": d.get("snapshot_surface"),
                "usd_m2_crudo": d.get("raw_price_per_m2"),
                "usd_m2_ajustado": d.get("adjusted_price_per_m2"),
            }
            for d in usados
        ]
    },
}

b = base(datos)
con_x100 = b | {v * 100 for v in b}
con_div = b | {(v / 1000).quantize(D("0.1")) for v in b if v > 1000}
todo = con_x100 | {(v / 1000).quantize(D("0.1")) for v in b if v > 1000}

print(f"informe {str(rid)[:8]}  ({len(usados)} comparables)\n")
print(f"  {'solo las cifras de los datos':<42} {len(b):>4} valores  cobertura {cobertura(b):>6.1%}")
print(
    f"  {'+ la regla v/1000':<42} {len(con_div):>4} valores  cobertura {cobertura(con_div):>6.1%}"
)
print(
    f"  {'+ la regla v*100':<42} {len(con_x100):>4} valores  cobertura {cobertura(con_x100):>6.1%}"
)
print(
    f"  {'las dos (lo que corre hoy)':<42} {len(todo):>4} valores  cobertura {cobertura(todo):>6.1%}"
)
print()
print("  y con tolerancias distintas, sobre lo que corre hoy:")
for t in ("0.001", "0.005", "0.01", "0.02"):
    print(f"    tolerancia {float(t):.1%}  -> cobertura {cobertura(todo, D(t)):>6.1%}")
