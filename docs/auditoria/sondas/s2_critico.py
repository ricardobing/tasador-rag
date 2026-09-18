"""¿Cuanto discrimina la fase A del critico sobre un informe REAL?

Reconstruye `datos_del_informe` desde `reports.methodology` de un informe real y
mide que fraccion del espacio de cifras plausibles queda ACEPTADA como trazable.
"""

import os
import random
from decimal import Decimal as D

import sqlalchemy as sa

from tasador.agents.config import load_agents_config
from tasador.agents.nodes.critic import (
    _valores_permitidos,
    cifras_clave_equivocadas,
    cifras_no_trazables,
)

e = sa.create_engine(os.environ["DATABASE_URL"])
random.seed(20260814)

# La tolerancia REAL, leída de la configuración. Estaba hardcodeada en 1% y por
# lo tanto la sonda medía otra cosa que producción en cuanto el YAML cambiara.
TOL = float(load_agents_config().node("critic").param("numeric_tolerance_pct", 1.0))
print(f"tolerancia de config/agents.yaml: {TOL}%")

SQL = """
select r.id, r.methodology, r.narrative_md, r.value_mid, r.comparables_used
from core.reports r
where r.status='SUCCEEDED' and r.narrative_md is not null and r.methodology ? 'detail'
order by r.created_at desc limit 3
"""


def datos_desde_methodology(m):
    """Aproxima `datos_del_informe`: el subconjunto que aporta casi todas las cifras."""
    usados = [d for d in m.get("detail", []) if d.get("included")]
    return {
        "valuacion": {
            "precio_publicacion_sugerido": {
                "minimo": m.get("value_low"),
                "medio": m.get("value_mid"),
                "maximo": m.get("value_high"),
            },
            "rango_de_cierre_esperado": {
                "minimo": m.get("closing_low"),
                "maximo": m.get("closing_high"),
            },
            "usd_por_m2": m.get("price_per_m2"),
            "dispersion": m.get("dispersion"),
        },
        "comparables": {
            "encontrados": m.get("comparables_found"),
            "usados": m.get("comparables_used"),
            "detalle_usados": [
                {
                    "precio": d.get("snapshot_price"),
                    "superficie_m2": d.get("snapshot_surface"),
                    "usd_m2_crudo": d.get("raw_price_per_m2"),
                    "usd_m2_ajustado": d.get("adjusted_price_per_m2"),
                }
                for d in usados
            ],
        },
    }


with e.connect() as c:
    for rid, m, narrativa, mid, usados in c.execute(sa.text(SQL)):
        datos = datos_desde_methodology(m)
        permitidos = _valores_permitidos(datos)
        print(f"\n=== informe {str(rid)[:8]}  ({usados} comparables usados) ===")
        print(f"  cifras distintas aceptadas como 'trazables': {len(permitidos)}")

        # ¿Que fraccion del espacio de precios plausibles pasa la fase A?
        # Se prueba con cifras de 6 digitos en el rango de un depto porteno.
        muestras = [D(random.randint(100_000, 400_000)) for _ in range(20_000)]
        tol = D(str(TOL)) / 100
        pasan = sum(
            1
            for n in muestras
            if any(abs(n - p) <= max(abs(p) * tol, D("0.5")) for p in permitidos)
        )
        print(
            f"  precios inventados entre 100.000 y 400.000 que la fase A DEJA PASAR: "
            f"{pasan}/{len(muestras)} = {pasan / len(muestras):.1%}"
        )

        def rechaza(texto: str) -> bool:
            """Las DOS verificaciones de la fase A, como las corre el nodo."""
            return bool(
                cifras_no_trazables(texto, datos, tolerancia_pct=TOL)
                or cifras_clave_equivocadas(texto, datos, tolerancia_pct=TOL)
            )

        # El caso concreto del docstring: "USD 312.000" cuando el valor es otro.
        for falso in ("312.000", "999.999", "1.234.567"):
            r = rechaza(f"El valor de la propiedad es USD {falso}.")
            print(f"    cifra inventada USD {falso:>10} -> {'RECHAZA' if r else 'PASA'}")

        # Contraste: el mismo texto pero con la cifra real.
        r = rechaza(f"El valor de la propiedad es USD {mid:,.0f}.".replace(",", "."))
        print(
            f"    cifra correcta   USD {mid:>10,.0f} -> {'RECHAZA (falso positivo)' if r else 'pasa'}"
        )
