"""¿Cuánto aporta el nodo 4 al MdAPE? — el bloqueante #5 de ESTADO §5.1.

Compara el par de datasets `VIGENTES` / `VIGENTES_SIN_FEATURES`, que son los
mismos casos con el motor de ajustes encendido y apagado, sobre las corridas ya
guardadas en `eval.backtest_runs`.

**Contra la amplitud entre semillas, no contra cero** (R6). Este proyecto midió
0,6 pp de varianza entre dos backtests idénticos salvo la semilla: una "mejora"
menor que eso no es una mejora.

    uv run python docs/auditoria/sondas/aporte_nodo4.py
"""

import os
import statistics as st

import sqlalchemy as sa

from tasador.settings import get_settings
from tasador.valuation.adjustments import load_config

e = sa.create_engine(os.environ["DATABASE_URL"])
metodo = f"{get_settings().method_version}+{load_config()['_hash']}"

SQL = """
select dataset, seed, mdape * 100, ppe20 * 100, n_cases
from eval.backtest_runs
where dataset like 'VIGENTES%' and method_version = :m
order by dataset, seed
"""

por: dict[str, list[tuple[int, float, float, int]]] = {}
with e.connect() as c:
    for ds, seed, m, p, n in c.execute(sa.text(SQL), {"m": metodo}):
        por.setdefault(ds, []).append((seed, float(m), float(p), n))

print(f"method_version: {metodo}\n")
print("  dataset                  semillas        MdAPE mediana   rango        PPE20")
for ds, filas in sorted(por.items()):
    ms = [f[1] for f in filas]
    ps = [f[2] for f in filas]
    semillas = [f[0] for f in filas]
    print(
        f"  {ds:<24} {str(semillas):<15} {st.median(ms):>6.1f}%      "
        f"{min(ms):.1f}-{max(ms):.1f}   {st.median(ps):>5.1f}%"
    )

con = [f[1] for f in por.get("VIGENTES", [])]
sin = [f[1] for f in por.get("VIGENTES_SIN_FEATURES", [])]
if len(con) >= 2 and len(sin) >= 2:
    delta = st.median(sin) - st.median(con)
    amplitud = max(max(con) - min(con), max(sin) - min(sin))
    print()
    print(f"  aporte del nodo 4 (mediana sin - mediana con) : {delta:+.2f} pp")
    print(f"  amplitud entre semillas del mismo dataset     :  {amplitud:.2f} pp")
    veredicto = "MEDIBLE" if abs(delta) > amplitud else "NO SE DISTINGUE DEL RUIDO"
    print(f"  -> {veredicto}")
else:
    print("\n  hacen falta al menos 2 semillas de cada dataset")
