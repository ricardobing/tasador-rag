"""Los cinco departamentos de ejemplo, tal como los describiría un agente.

Sirven para dos cosas: probar la app de punta a punta con casos realistas, y
—sobre todo— dejar a la vista **qué parte de una descripción llega al número y
qué parte no**.

    uv run python scripts/ejemplos_palermo.py            # lista los casos
    uv run python scripts/ejemplos_palermo.py --generar  # los encola (gasta LLM)
    uv run python scripts/ejemplos_palermo.py --ver      # el resultado de cada uno

⚠️ `--generar` cuesta plata: ~USD 0,045 por informe.

Cada caso trae `solo_api`: los campos que **mueven el precio** y que el
formulario web todavía no pide. Están acá para poder medir cuánto cambia el
número por no poder cargarlos — ver el caso 3, que va dos veces.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("TASADOR_URL", "http://127.0.0.1:8000")
ORG = os.environ.get("TASADOR_ORG_SLUG", "inmo-demo")

# El texto libre va en `notes` y **no llega al motor a propósito** (doc 10 §3:
# las notas pueden tener datos del propietario y no tienen por qué viajar a un
# proveedor de LLM). Se guarda con el informe y se ve en la ficha; no ajusta.
CASOS: list[dict] = [
    {
        "ref": "v3-EJ1-soho-refaccionar",
        "titulo": "Palermo Soho · 3 amb · a refaccionar",
        "property": {
            "address_raw": "Gurruchaga 1500, Palermo",
            "property_type": "departamento",
            "rooms": 3,
            "bathrooms": 1,
            "surface_total": 62,
            "surface_covered": 55,
            "floor_number": 2,
            "condition": "a_refaccionar",
            "orientation": "contrafrente",
            "notes": (
                "Unidad con gran potencial sobre la calle Gurruchaga. El living comedor "
                "tiene buena ventilación pero requiere pintura general y cambio de "
                "revestimientos. Balcón corrido con baranda floja y piso desgastado que "
                "necesita reparación. Cocina independiente de época con cañerías a revisar. "
                "Baño funcional pero de estética antigua. Ubicado en una de las zonas "
                "gastronómicas y comerciales con mayor crecimiento y demanda de Palermo "
                "Soho, ideal para inversión o primera vivienda tras reciclado."
            ),
        },
        # "2º piso por escalera": sin ascensor. No penaliza porque el coeficiente
        # `high_no_elevator` arranca en el piso 3 — acá el campo no cambiaría nada,
        # y por eso este caso sirve de control.
        "solo_api": {"has_elevator": False},
    },
    {
        "ref": "v3-EJ2-hollywood-impecable",
        "titulo": "Palermo Hollywood · monoambiente · impecable",
        "property": {
            "address_raw": "Humboldt 1900, Palermo",
            "property_type": "departamento",
            "rooms": 1,
            "bathrooms": 1,
            "surface_total": 42,
            "surface_covered": 38,
            "floor_number": 6,
            "condition": "excelente",
            "orientation": "frente",
            "notes": (
                "Excelente studio súper luminoso sobre Humboldt al 1900. Estado impecable, "
                "pisos de porcelanato sin marcas y pintura reciente en tonos neutros. Balcón "
                "aterrazado con vista abierta en perfectas condiciones. Cocina integrada con "
                "anafe eléctrico y mobiliario completo. El edificio cuenta con terraza, "
                "parrilla y laundry. Zona consolidada rodeada de productoras, bares y "
                "transporte público (cerca de Av. Santa Fe y Subte D)."
            ),
        },
        # "edificio con ascensor y amenities" · terraza, parrilla, laundry -> +5%
        "solo_api": {"has_elevator": True, "amenities": ["terraza", "parrilla", "laundry"]},
    },
    {
        "ref": "v3-EJ3-chico-clasico",
        "titulo": "Palermo Chico · 4 amb · con cochera",
        "property": {
            "address_raw": "República de la India 2800, Palermo",
            "property_type": "departamento",
            "rooms": 4,
            "bathrooms": 2,
            "surface_total": 95,
            "surface_covered": 88,
            "floor_number": 4,
            "condition": "bueno",
            "orientation": "frente",
            "notes": (
                "Semipiso clásico de amplias dimensiones sobre República de la India. "
                "Distribución funcional con pisos de parquet originales que necesitan pulido "
                "y plastificado. Paredes en buen estado estructural aunque con detalles de "
                "humedad en el cielorraso del pasillo por filtración ya reparada del "
                "consorcio. Balcón francés al frente. Servicios individuales pero expensas "
                "elevadas. Entorno exclusivo, residencial y muy silencioso."
            ),
        },
        # Cochera (USD 12.000 absolutos) y expensas altas (-4%). Ninguno de los
        # dos se puede cargar desde el formulario web hoy.
        "solo_api": {"has_elevator": True, "parking_spaces": 1, "expenses_ars": 450000},
    },
    {
        "ref": "v3-EJ3b-chico-SIN-cochera",
        "titulo": "Palermo Chico · el MISMO, como lo carga el formulario hoy",
        # Por sufijo y no por ref completa: renombrar la tanda dejaba esto
        # apuntando a la ANTERIOR, y la comparación mezclaba dos corpus.
        "control_de": "EJ3-chico-clasico",
        "property": {
            "address_raw": "República de la India 2800, Palermo",
            "property_type": "departamento",
            "rooms": 4,
            "bathrooms": 2,
            "surface_total": 95,
            "surface_covered": 88,
            "floor_number": 4,
            "condition": "bueno",
            "orientation": "frente",
            "notes": "Idéntico a EJ3 pero sin cochera, ascensor ni expensas: lo que el web carga.",
        },
        "solo_api": {},
    },
    {
        "ref": "v3-EJ4-nuevo-ph-duplex",
        "titulo": "Palermo Nuevo · PH dúplex · sin expensas",
        "property": {
            "address_raw": "Cerviño 4400, Palermo",
            "property_type": "ph",
            "rooms": 2,
            "bathrooms": 1,
            "surface_total": 50,
            "surface_covered": 40,
            "floor_number": 1,
            "condition": "bueno",
            "orientation": "interno",
            "notes": (
                "Inmueble tipo PH sin expensas en pasaje tranquilo cerca de Av. Cerviño. En "
                "planta baja cuenta con estar-comedor y cocina kitchinette con muebles "
                "desgastados. En planta alta dormitorio con salida a terraza propia pequeña "
                "que tiene la membrana deteriorada y requiere impermeabilización inmediata. "
                "Instalación eléctrica hecha a nuevo hace 3 años. Zona en constante "
                "revalorización, muy cerca de la embajada de EE.UU. y Parques de Palermo."
            ),
        },
        "solo_api": {"has_elevator": False},
    },
    {
        "ref": "v3-EJ5-viejo-deteriorado",
        "titulo": "Palermo Viejo · 3 amb · refacción integral",
        "property": {
            "address_raw": "Thames 1600, Palermo",
            "property_type": "departamento",
            "rooms": 3,
            "bathrooms": 2,
            "surface_total": 78,
            "surface_covered": 72,
            "age_years": 50,
            "floor_number": 3,
            "condition": "a_refaccionar",
            "orientation": "contrafrente",
            "notes": (
                "Departamento de época muy silencioso sobre la calle Thames. Requiere "
                "refacción integral: persiana del living rota que no traba, cerramientos del "
                "balcón corrido picados por oxidación y azulejos de cocina desprendidos. "
                "Falta de mantenimiento general en pintura y carpintería. Los ambientes son "
                "de generosas dimensiones con techos altos. Muy buena iluminación natural "
                "por la mañana. Punto estratégico en plena área turística y de rápida salida "
                "hacia avenidas principales."
            ),
        },
        # Un edificio de 50 años en un 3º: si NO tiene ascensor son -12%. El dato
        # no se puede cargar desde el web y el default es "sin dato" = sin ajuste.
        "solo_api": {"has_elevator": False},
    },
]


def _pedir(ruta: str, cuerpo: dict | None = None, cab: dict | None = None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    # S310: la URL sale de `TASADOR_URL`, que es la API propia. No hay entrada
    # de terceros acá.
    r = urllib.request.Request(  # noqa: S310
        BASE + ruta,
        data=datos,
        headers={"Content-Type": "application/json", "X-Org-Slug": ORG, **(cab or {})},
    )
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def listar() -> None:
    print(f"\n  {len(CASOS)} casos. El texto libre va en `notes` y NO llega al motor.\n")
    for c in CASOS:
        p = c["property"]
        extra = ", ".join(f"{k}={v}" for k, v in c["solo_api"].items()) or "—"
        print(f"  {c['ref']:<26} {c['titulo']}")
        print(
            f"  {'':<26} {p['rooms']} amb · {p['surface_total']} m² · "
            f"{p.get('condition', 'sin dato')} · piso {p.get('floor_number', '?')}"
        )
        print(f"  {'':<26} solo por API: {extra}")
        print()


def generar() -> int:
    for c in CASOS:
        st, r = _pedir(
            "/v1/reports",
            {"external_ref": c["ref"], "property": {**c["property"], **c["solo_api"]}},
            {"Idempotency-Key": c["ref"]},
        )
        estado = "ya existía" if st == 200 else "encolado"
        print(f"  {c['ref']:<26} {estado:<12} {r.get('report_id', r)}")
    print("\n  `--ver` cuando terminen.")
    return 0


def ver() -> int:
    _, lista = _pedir("/v1/reports?limit=50")
    por_ref = {}
    for it in lista.get("items", []):
        _, d = _pedir(f"/v1/reports/{it['report_id']}")
        if d.get("external_ref"):
            por_ref.setdefault(d["external_ref"], d)

    print(f"\n  {'caso':<26}{'estado':<16}{'USD/m²':>9}{'medio':>12}{'conf':>7}{'comp':>6}")
    for c in CASOS:
        d = por_ref.get(c["ref"])
        if d is None:
            print(f"  {c['ref']:<26}{'sin generar':<16}")
            continue
        if d["status"] != "SUCCEEDED":
            print(f"  {c['ref']:<26}{d['status']:<16}{d.get('insufficient_reason', '')}")
            continue
        v, cf = d["valuation"], d["confidence"]
        print(
            f"  {c['ref']:<26}{d['status']:<16}{v['price_per_m2']:>9,.0f}"
            f"{v['suggested_listing_price']['mid']:>12,.0f}{cf['level']:>7}"
            f"{d['comparables']['used']:>6}"
        )

    # El par que muestra lo que cuesta un campo que el formulario no pide.
    for c in CASOS:
        if not c.get("control_de"):
            continue
        # El control es el caso de ESTA misma tanda: se busca por sufijo. Con la
        # ref completa, renombrar la tanda comparaba contra la anterior — dos
        # corpus distintos, y la diferencia dejaba de significar lo que dice.
        hermano = next(
            (x["ref"] for x in CASOS if x["ref"].endswith(c["control_de"])), c["control_de"]
        )
        a, b = por_ref.get(hermano), por_ref.get(c["ref"])
        if not (a and b and a["status"] == b["status"] == "SUCCEEDED"):
            continue
        ma = a["valuation"]["suggested_listing_price"]["mid"]
        mb = b["valuation"]["suggested_listing_price"]["mid"]
        print(f"\n  {hermano} vs {c['ref']}:")
        print(f"    con cochera/ascensor/expensas : USD {ma:,.0f}")
        print(f"    como lo carga el web hoy      : USD {mb:,.0f}")
        print(f"    diferencia                    : USD {ma - mb:,.0f}  ({(ma / mb - 1):+.1%})")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--generar", action="store_true", help="encola los 6 informes (gasta LLM)")
    ap.add_argument("--ver", action="store_true", help="muestra el resultado de cada uno")
    a = ap.parse_args()
    if a.generar:
        sys.exit(generar())
    if a.ver:
        sys.exit(ver())
    listar()
