"""Los mismos cinco departamentos, antes y después del corpus enriquecido.

  EJ*    — corpus del 14/08, sin estados declarados, sin restricción de ficha
  v3-EJ* — corpus del 15/08, 924 fichas, y el nodo 2 usando solo enriquecidos
"""

import os

import sqlalchemy as sa

e = sa.create_engine(os.environ["DATABASE_URL"])

Q = """
select sp.external_ref, r.status, r.price_per_m2, r.value_mid, r.confidence,
       r.comparables_used, r.comparables_found, r.insufficient_reason
from core.reports r join core.subject_properties sp on sp.id = r.subject_property_id
where sp.external_ref is not null
order by r.created_at
"""

with e.connect() as c:
    filas = c.execute(sa.text(Q)).all()

por_ref = {f[0]: f for f in filas}
CASOS = [
    ("EJ1-soho-refaccionar", "Soho 3 amb · a refaccionar"),
    ("EJ2-hollywood-impecable", "Hollywood mono · excelente"),
    ("EJ3-chico-clasico", "Chico 4 amb · con cochera"),
    ("EJ4-nuevo-ph-duplex", "PH dúplex 2 amb"),
    ("EJ5-viejo-deteriorado", "Viejo 3 amb · 50 años"),
]


def celda(f):
    if f is None:
        return "—", "—", "—", "—"
    if f[1] != "SUCCEEDED":
        return f[1][:12], "—", "—", "—"
    return f"{f[2]:,.0f}", f"{f[3]:,.0f}", f[4][:5], f"{f[5]}/{f[6]}"


print(f"\n  {'caso':<30}{'ANTES (14/08)':>34}{'DESPUÉS (15/08 enriquecido)':>36}")
print(f"  {'':<30}{'usd/m²':>10}{'medio':>11}{'conf':>7}{'comp':>6}{'usd/m²':>12}{'medio':>11}{'conf':>7}{'comp':>6}")
for ref, nombre in CASOS:
    a = celda(por_ref.get(ref))
    b = celda(por_ref.get("v3-" + ref))
    print(f"  {nombre:<30}{a[0]:>10}{a[1]:>11}{a[2]:>7}{a[3]:>6}{b[0]:>12}{b[1]:>11}{b[2]:>7}{b[3]:>6}")

print("\n  === cuánto se movió el número ===")
for ref, nombre in CASOS:
    a, b = por_ref.get(ref), por_ref.get("v3-" + ref)
    if not (a and b and a[1] == b[1] == "SUCCEEDED"):
        estado = f"{a[1] if a else '—'} -> {b[1] if b else '—'}"
        print(f"  {nombre:<30}{estado}")
        continue
    d = (b[3] / a[3] - 1) * 100
    print(f"  {nombre:<30}USD {a[3]:>9,.0f} -> {b[3]:>9,.0f}   {d:+6.1f}%")

# ¿De qué están hechos los comparables ahora?
with e.connect() as c:
    print("\n  === los comparables que usó cada tanda ===")
    for etiqueta, patron in (("ANTES ", "EJ%"), ("DESPUÉS", "v3-EJ%")):
        r = c.execute(
            sa.text(
                """
            select count(*) usados,
                   count(*) filter (where 'ficha_completa' = any(l.quality_flags)) con_ficha,
                   count(*) filter (where l.raw ? 'condition') con_estado
            from core.report_comparables rc
            join corpus.listings l on l.id = rc.listing_id
            join core.reports r on r.id = rc.report_id
            join core.subject_properties sp on sp.id = r.subject_property_id
            where rc.included and sp.external_ref like :p
              and sp.external_ref not like 'v2-%'
              and (:p <> 'EJ%' or sp.external_ref not like 'v3-%')
            """
            ),
            {"p": patron},
        ).one()
        pct = f"{r[1] / r[0] * 100:.0f}%" if r[0] else "—"
        pe = f"{r[2] / r[0] * 100:.0f}%" if r[0] else "—"
        print(f"    {etiqueta}  {r[0]:>4} usados · {r[1]:>4} con ficha ({pct}) · {r[2]:>4} con estado ({pe})")
