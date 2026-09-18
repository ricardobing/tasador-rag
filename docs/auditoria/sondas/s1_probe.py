"""S1 — sondas sobre el motor de valuación. Solo lee, no escribe nada."""

from __future__ import annotations

from decimal import Decimal as D

from tasador.valuation.adjustments import compute_adjustments, listing_age_coef, load_config
from tasador.valuation.engine import value
from tasador.valuation.models import Comparable, Condition, Orientation, Property

cfg = load_config()


def c(ref, price, m2, **kw):
    return Comparable(
        ref=ref,
        price=D(str(price)),
        currency="USD",
        days_published=kw.pop("days", 30),
        prop=Property(surface_covered=D(str(m2)), **kw),
    )


print("=" * 70)
print("A. ¿La cochera del COMPARABLE se descuenta antes de calcular USD/m2?")
print("=" * 70)
# 10 comparables iguales a 2500 USD/m2 (70 m2 = 175.000). A cinco de ellos les
# ponemos cochera: si el motor la descontara, su USD/m2 bajaria y la mediana
# tambien. Si no la descuenta, la mediana no se mueve.
sin_cochera = [c(f"a{i}", 175000, 70) for i in range(10)]
con_cochera = [c(f"b{i}", 175000, 70) for i in range(5)] + [
    Comparable(
        ref=f"p{i}",
        price=D("175000"),
        currency="USD",
        days_published=30,
        prop=Property(surface_covered=D("70"), parking_spaces=1),
    )
    for i in range(5)
]
sujeto = Property(surface_covered=D("70"))
v1 = value(sujeto, sin_cochera)
v2 = value(sujeto, con_cochera)
print(f"  sin cocheras en comparables : USD/m2 {v1.price_per_m2}  mid {v1.value_mid}")
print(f"  5 de 10 CON cochera         : USD/m2 {v2.price_per_m2}  mid {v2.value_mid}")
print(
    f"  -> la cochera del comparable {'SI' if v1.price_per_m2 != v2.price_per_m2 else 'NO'} se descuenta"
)

# Y el doble conteo end-to-end: sujeto con cochera + comparables con cochera.
sujeto_ch = Property(surface_covered=D("70"), parking_spaces=1)
v3 = value(sujeto_ch, con_cochera)
print(f"  sujeto CON cochera vs comparables CON cochera: mid {v3.value_mid}")
print(f"  delta contra el caso sin nada: {v3.value_mid - v1.value_mid}")

print()
print("=" * 70)
print("B. ¿El tope de +-25% se respeta DESPUES de listing_age_coef?")
print("=" * 70)
peor = Property(
    surface_covered=D("70"), condition=Condition.A_REFACCIONAR, orientation=Orientation.INTERNO
)
total, det, capped = compute_adjustments(Property(surface_covered=D("70")), peor, cfg)
la = listing_age_coef(cfg, c("x", 175000, 70, days=400))
print(f"  compute_adjustments -> total {total}  capped={capped}")
print(f"  listing_age_coef (400 dias) -> {la}")
print(f"  producto REAL aplicado al precio -> {total * la}")
print(f"  piso declarado en adjustments.yaml -> {cfg['rules']['min_total_adjustment']}")
print(
    f"  -> {'VIOLA' if total * la < D(str(cfg['rules']['min_total_adjustment'])) else 'respeta'} el piso"
)

print()
print("=" * 70)
print("C. ¿Que coeficientes del YAML nunca se aplican?")
print("=" * 70)
import inspect

from tasador.valuation import adjustments as adj_mod

src = inspect.getsource(adj_mod) + inspect.getsource(
    __import__("tasador.valuation.engine", fromlist=["x"])
)
for clave in ["expenses", "ground_with_patio", "amenities", "listing_age", "absolute_usd"]:
    usado = clave in src
    print(f"  cfg['{clave}'] referenciado en el codigo: {usado}")
print(f"  cfg['expenses'] = {cfg['expenses']}")
print(f"  cfg['floor']['ground_with_patio'] = {cfg['floor']['ground_with_patio']}")
print(f"  cfg['amenities']['none'] = {cfg['amenities']['none']}")

print()
print("=" * 70)
print("D. El paso p5-p95: ¿cuantos comparables saca, y esta en doc 05?")
print("=" * 70)
for n in (7, 8, 10, 21, 41, 60):
    comps = [c(f"c{i}", 175000 + i * 2000, 70) for i in range(n)]
    v = value(sujeto, comps)
    fuera = [a for a in v.detail if a.exclusion_reason == "outlier_estadistico"]
    print(f"  n={n:3d} -> usados {v.comparables_used:3d}  descartados por p5-p95: {len(fuera)}")

print()
print("=" * 70)
print("E. El 'ancho minimo 8%' de doc 05 §6, ¿opera alguna vez?")
print("=" * 70)
v = value(sujeto, [c(f"c{i}", 175000, 70) for i in range(12)])
ancho = (v.value_high - v.value_low) / v.value_mid
print(f"  set perfectamente homogeneo -> ancho {ancho:.1%}  (doc 05 dice +-4% = 8%)")
print(
    f"  min_range_width_pct={cfg['statistics']['min_range_width_pct']}  "
    f"uncertainty_half_width_pct={cfg['statistics']['uncertainty_half_width_pct']}"
)
print(f"  -> el piso efectivo es max(4%, 20%) = 20%: min_range_width_pct nunca gana")

print()
print("=" * 70)
print("F. Completitud de la confianza: ¿mira al sujeto o a los comparables?")
print("=" * 70)
pelado = Property(surface_covered=D("70"))
completo = Property(
    surface_covered=D("70"),
    condition=Condition.MUY_BUENO,
    orientation=Orientation.LATERAL,
    age_years=10,
    floor_number=3,
)
comps = [c(f"c{i}", 175000, 70) for i in range(15)]
a = value(pelado, comps)
b = value(completo, comps)
print(f"  sujeto sin atributos -> score {a.confidence_score}  {a.confidence}")
print(f"  sujeto con 4 atributos -> score {b.confidence_score}  {b.confidence}")
print(
    f"  delta {b.confidence_score - a.confidence_score} = peso completeness ({cfg['confidence']['weights']['completeness']})"
)
