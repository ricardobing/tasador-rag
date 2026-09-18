"""Corpus de demostración: avisos SINTÉTICOS con la verdad conocida por construcción.

    uv run python scripts/generar_corpus_demo.py --n 600
    uv run python scripts/generar_corpus_demo.py --n 40 --sin-llm   # solo estructura

Produce dos cosas:

  data/demo/avisos_demo.csv         en el esquema que lee `scripts/ingest_csv.py`
  tests/golden/demo-extraccion.yaml golden set de extracción y curaduría

## Por qué existe

El corpus real son avisos de portales: contenido de terceros que no se
redistribuye (doc 10 §2). Sin un corpus versionado el repo no se puede correr
ni evaluar desde cero. Este genera uno con las **distribuciones del corpus
real** (ambientes, superficie por ambientes, USD/m² por barrio, estado,
orientación, antigüedad) y descripciones redactadas por un modelo de lenguaje
a partir de los hechos sorteados.

## La verdad por construcción

Para cada aviso se sortean los hechos ANTES de redactar, y se le dice al
modelo cuáles mencionar y cuáles callar. Después se verifica, sin LLM, que las
frases obligatorias estén en el texto; un aviso cuya descripción no dice lo
que tenía que decir no entra al golden set con ese campo. Así el `esperado`
del golden no lo anotó nadie leyendo el texto: es el dato del que el texto
salió. Es un ground truth independiente de verdad, que es justo lo que el
golden set real —anotado por quien lee el mismo texto que el modelo— no era.

Incluye a propósito los casos difíciles del dominio: pozos, permutas, precios
promocionales, y el mismo inmueble publicado en los dos portales con la altura
aproximada y la superficie medida distinto (doc 04, nodo 5).

Los USD/m² siguen las distribuciones reales, así que un informe sobre el
corpus demo da un número plausible — pero es un número sobre datos inventados
y ninguna cifra de los informes de este repo sale de acá.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tasador.cli import run

RAIZ = Path(__file__).resolve().parents[1]
SALIDA_CSV = RAIZ / "data" / "demo" / "avisos_demo.csv"
SALIDA_GOLDEN = RAIZ / "tests" / "golden" / "demo-extraccion.yaml"

# ── Distribuciones, medidas sobre el corpus real el 17/09/2026 ───────────
BARRIOS = {"Palermo": 0.6, "Belgrano": 0.4}
USD_M2_MEDIANO = {"Palermo": 3800, "Belgrano": 2800}
CALLES = {
    "Palermo": [
        "Thames",
        "Gorriti",
        "Honduras",
        "Nicaragua",
        "Costa Rica",
        "Guatemala",
        "Gurruchaga",
        "Malabia",
        "Armenia",
        "Scalabrini Ortiz",
        "Cabello",
        "Cerviño",
        "Bulnes",
        "Charcas",
        "Paraguay",
        "Soler",
        "Fitz Roy",
        "Ravignani",
        "Uriarte",
        "Humboldt",
        "Darregueyra",
        "Salguero",
        "Cabrera",
        "Godoy Cruz",
        "Juncal",
        "Arenales",
        "Bonpland",
        "Serrano",
    ],
    "Belgrano": [
        "Cabildo",
        "Zabala",
        "Sucre",
        "Echeverría",
        "Juramento",
        "Mendoza",
        "Olazábal",
        "Vuelta de Obligado",
        "Ciudad de la Paz",
        "Moldes",
        "3 de Febrero",
        "Arcos",
        "O'Higgins",
        "Cuba",
        "Migueletes",
        "Blanco Encalada",
        "Virrey del Pino",
        "Conde",
        "Freire",
        "Amenábar",
        "Montañeses",
        "Zapiola",
    ],
}
AMBIENTES = {1: 0.15, 2: 0.28, 3: 0.30, 4: 0.20, 5: 0.07}
SUPERFICIE = {
    1: (29, 34, 39),
    2: (37, 44, 52),
    3: (61, 74, 90),
    4: (90, 112, 147),
    5: (125, 169, 220),
}
ESTADO = {
    "a_estrenar": 0.08,
    "excelente": 0.35,
    "muy_bueno": 0.30,
    "bueno": 0.20,
    "a_refaccionar": 0.07,
}
COEF_ESTADO = {
    "a_estrenar": 1.15,
    "excelente": 1.08,
    "muy_bueno": 1.00,
    "bueno": 0.94,
    "a_refaccionar": 0.82,
}
ORIENTACION = {"frente": 0.45, "contrafrente": 0.30, "lateral": 0.12, "interno": 0.13}
COEF_ORIENTACION = {"frente": 1.03, "lateral": 1.00, "contrafrente": 0.98, "interno": 0.93}
PUBLICADORES = [
    "Norte Propiedades",
    "Estudio Lacroze",
    "Sur & Asociados",
    "Inmobiliaria del Parque",
    "Casa Nueva Negocios Inmobiliarios",
    "Grupo Arcos",
    "Mirador Propiedades",
    "Belgrano Brokers",
]
# Cómo se dice cada hecho en un aviso: la frase EXACTA que el modelo tiene que
# incluir, y que después se verifica. Es lo que hace al golden verificable.
FRASE_ESTADO = {
    "a_estrenar": "a estrenar",
    "excelente": "excelente estado",
    "muy_bueno": "muy buen estado",
    "bueno": "buen estado general",
    "a_refaccionar": "a refaccionar",
}
ESTADO_PORTAL = {
    "a_estrenar": "A Estrenar",
    "excelente": "Excelente",
    "muy_bueno": "Muy Bueno",
    "bueno": "Bueno",
    "a_refaccionar": "A Refaccionar",
}
ESTADO_PORTAL = {
    "a_estrenar": "A Estrenar",
    "excelente": "Excelente",
    "muy_bueno": "Muy Bueno",
    "bueno": "Bueno",
    "a_refaccionar": "A Refaccionar",
}
FRASE_ORIENTACION = {
    "frente": "al frente",
    "contrafrente": "al contrafrente",
    "lateral": "orientación lateral",
    "interno": "unidad interna",
}


def _elegir(pesos: dict[Any, float], rng: random.Random) -> Any:
    return rng.choices(list(pesos), weights=list(pesos.values()), k=1)[0]


@dataclass(slots=True)
class Hechos:
    """Lo que ES el aviso. La descripción se redacta a partir de esto."""

    id: str
    portal: str
    barrio: str
    calle: str
    altura: int
    piso: int | None
    depto: str | None
    ambientes: int
    dormitorios: int
    banios: int
    sup_cubierta: float
    sup_semi: float
    estado: str
    orientacion: str
    antiguedad: int
    ascensor: bool
    cocheras: int
    expensas: int | None
    precio: int
    publicado: date
    ficha_completa: bool
    publicador: str
    # Qué se menciona en el texto (y por lo tanto qué puede pedirse al extractor)
    dice_estado: bool
    dice_orientacion: bool
    dice_piso: bool
    dice_ascensor: bool
    dice_antiguedad: bool
    # Casos difíciles
    caso: str | None = None  # pozo | permuta | promocional
    duplicado_de: str | None = None
    amenities: list[str] = field(default_factory=list)
    descripcion: str = ""
    frases_verificadas: dict[str, bool] = field(default_factory=dict)

    @property
    def sup_ponderada(self) -> float:
        return round(self.sup_cubierta + self.sup_semi / 2, 1)

    @property
    def direccion(self) -> str:
        return f"{self.calle} {self.altura}"


def sortear(i: int, rng: random.Random, portal: str) -> Hechos:
    barrio = _elegir(BARRIOS, rng)
    amb = _elegir(AMBIENTES, rng)
    p25, p50, p75 = SUPERFICIE[amb]
    sup = max(18.0, rng.triangular(p25 * 0.85, p75 * 1.15, p50))
    semi = round(rng.choice([0, 0, 0, 3, 5, 8, 12]) * (1 if amb > 1 else 0.5), 1)
    estado = _elegir(ESTADO, rng)
    orientacion = _elegir(ORIENTACION, rng)
    antiguedad = int(rng.choice([0, 0, 2, 5, 8, 12, 15, 20, 25, 30, 40, 45, 50, 60, 70]))
    if estado == "a_estrenar":
        antiguedad = 0
    piso = rng.choice([None, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15])
    ascensor = piso is None or piso >= 3 or rng.random() < 0.6
    if piso is not None and piso >= 4:
        ascensor = rng.random() < 0.92
    cocheras = 1 if rng.random() < 0.12 else 0
    usd_m2 = (
        USD_M2_MEDIANO[barrio]
        * COEF_ESTADO[estado]
        * COEF_ORIENTACION[orientacion]
        * (
            1.06
            if antiguedad <= 5
            else 1.0
            if antiguedad <= 15
            else 0.96
            if antiguedad <= 30
            else 0.92
        )
        * rng.lognormvariate(0, 0.18)
    )
    precio = int(round((usd_m2 * (sup + semi / 2) + 12000 * cocheras) / 1000) * 1000)
    ficha = rng.random() < 0.7
    publicado = date.today() - timedelta(days=int(rng.triangular(1, 170, 40)))
    caso = None
    r = rng.random()
    if r < 0.08:
        caso = "pozo"
        precio = int(round(precio * rng.uniform(1.10, 1.35) / 1000) * 1000)
        estado, antiguedad = "a_estrenar", 0
    elif r < 0.10:
        caso = "permuta"
    elif r < 0.11:
        caso = "promocional"
        precio = int(round(precio * 0.72 / 1000) * 1000)
    return Hechos(
        id=f"demo-{i:05d}",
        portal=portal,
        barrio=barrio,
        calle=rng.choice(CALLES[barrio]),
        altura=rng.randrange(300, 5900),
        piso=piso,
        depto=rng.choice(["A", "B", "C", "D", None]) if piso else None,
        ambientes=amb,
        dormitorios=max(0, amb - 1),
        banios=1 if amb <= 3 else rng.choice([1, 2, 2, 3]),
        sup_cubierta=round(sup, 1),
        sup_semi=semi,
        estado=estado,
        orientacion=orientacion,
        antiguedad=antiguedad,
        ascensor=ascensor,
        cocheras=cocheras,
        expensas=int(rng.triangular(80_000, 600_000, 230_000) // 1000 * 1000)
        if rng.random() < 0.7
        else None,
        precio=precio,
        publicado=publicado,
        ficha_completa=ficha,
        publicador=rng.choice(PUBLICADORES),
        dice_estado=rng.random() < 0.75,
        dice_orientacion=rng.random() < 0.55,
        dice_piso=piso is not None and rng.random() < 0.7,
        dice_ascensor=rng.random() < 0.45,
        dice_antiguedad=rng.random() < 0.5,
        caso=caso,
        amenities=rng.sample(
            ["pileta", "gimnasio", "sum", "parrilla", "laundry", "seguridad 24 h", "solarium"],
            k=rng.choice([0, 0, 1, 2, 3, 5]),
        ),
    )


def duplicar(h: Hechos, i: int, rng: random.Random) -> Hechos:
    """El mismo inmueble en el otro portal: altura aproximada a la cuadra, la
    superficie medida distinto (uno cuenta el balcón, el otro no), el precio
    retocado, y otra redacción. Es el caso que el nodo 5 tiene que agrupar."""
    d = Hechos(**asdict(h))
    d.id = f"demo-{i:05d}"
    d.portal = "demo-b" if h.portal == "demo-a" else "demo-a"
    d.duplicado_de = h.id
    d.altura = h.altura // 100 * 100
    d.sup_cubierta = round(h.sup_cubierta * rng.uniform(0.96, 1.06), 1)
    d.precio = int(round(h.precio * rng.uniform(0.97, 1.03) / 1000) * 1000)
    d.publicador = rng.choice(PUBLICADORES)
    d.publicado = h.publicado + timedelta(days=rng.randint(-10, 10))
    d.dice_estado, d.dice_orientacion = rng.random() < 0.7, rng.random() < 0.5
    d.dice_piso, d.dice_ascensor, d.dice_antiguedad = (
        rng.random() < 0.6,
        rng.random() < 0.4,
        rng.random() < 0.4,
    )
    d.descripcion = ""
    d.frases_verificadas = {}
    return d


def frases_obligatorias(h: Hechos) -> dict[str, str]:
    """Campo → frase exacta que el texto tiene que contener."""
    out: dict[str, str] = {}
    if h.dice_estado:
        out["condition"] = FRASE_ESTADO[h.estado]
    if h.dice_orientacion:
        out["orientation"] = FRASE_ORIENTACION[h.orientacion]
    if h.dice_piso and h.piso is not None:
        out["floor_number"] = "planta baja" if h.piso == 0 else f"piso {h.piso}"
    if h.dice_ascensor:
        out["has_elevator"] = "con ascensor" if h.ascensor else "sin ascensor"
    if h.dice_antiguedad:
        out["age_years"] = (
            "a estrenar" if h.antiguedad == 0 else f"{h.antiguedad} años de antigüedad"
        )
    if h.caso == "pozo":
        out["_pozo"] = "entrega estimada"
    if h.caso == "permuta":
        out["_permuta"] = "se acepta permuta"
    if h.caso == "promocional":
        out["_promocional"] = "precio promocional"
    return out


def brief(h: Hechos) -> str:
    fr = frases_obligatorias(h)
    callar = [
        c
        for c, d in (
            ("estado", h.dice_estado),
            ("orientación", h.dice_orientacion),
            ("piso", h.dice_piso),
            ("ascensor", h.dice_ascensor),
            ("antigüedad", h.dice_antiguedad),
        )
        if not d
    ]
    lineas = [
        f"Tipo: departamento de {h.ambientes} ambientes, {h.dormitorios} dormitorios, "
        f"{h.banios} baño(s).",
        f"Ubicación: {h.calle} al {h.altura // 100 * 100}, {h.barrio}, CABA.",
        f"Superficie cubierta {h.sup_cubierta:.0f} m²"
        + (f" más {h.sup_semi:.0f} m² de balcón/terraza." if h.sup_semi else "."),
        f"Cocheras: {h.cocheras}." if h.cocheras else "Sin cochera.",
        f"Amenities: {', '.join(h.amenities)}." if h.amenities else "Sin amenities.",
        "FRASES QUE TENÉS QUE INCLUIR TEXTUALMENTE: " + " · ".join(f'"{v}"' for v in fr.values()),
        "NO MENCIONES, ni de forma indirecta: " + ", ".join(callar) + "." if callar else "",
        "NO escribas el precio ni ninguna cifra en dólares.",
    ]
    if h.caso == "pozo":
        lineas.append(
            "Es un emprendimiento EN CONSTRUCCIÓN: mencioná que las fotos son del modelo "
            "y una fecha de entrega."
        )
    if h.caso == "permuta":
        lineas.append("El vendedor acepta permuta por otra unidad o financiación en cuotas.")
    if h.caso == "promocional":
        lineas.append("Es una oportunidad con precio rebajado por tiempo limitado.")
    return "\n".join(x for x in lineas if x)


PROMPT = """Escribí la descripción de un aviso inmobiliario de venta, en castellano
rioplatense, como lo escribiría una inmobiliaria de Buenos Aires: entre 80 y 220
palabras, tono comercial pero no exagerado, sin emojis, sin listas, en un solo
bloque de prosa o dos párrafos. No inventes datos que no estén abajo. Respondé
SOLO con el texto del aviso.

DATOS DEL INMUEBLE:
{brief}
"""


def _norm(s: str) -> str:
    from tasador.geocoding import sin_tildes

    return " ".join(sin_tildes(s).lower().split())


def verificar(h: Hechos) -> None:
    texto = _norm(h.descripcion)
    h.frases_verificadas = {
        campo: _norm(frase) in texto for campo, frase in frases_obligatorias(h).items()
    }


async def redactar_todo(hechos: list[Hechos], task: str, paralelo: int) -> None:
    from tasador.llm import LlmClient, LlmError

    cliente = LlmClient()
    sem = asyncio.Semaphore(paralelo)
    hechos_por_id = {h.id: h for h in hechos}

    async def uno(h: Hechos) -> None:
        async with sem:
            for intento in range(2):
                try:
                    texto, _ = await cliente.complete(
                        task,
                        [{"role": "user", "content": PROMPT.format(brief=brief(h))}],
                        temperature=0.8 if intento == 0 else 0.4,
                        max_tokens=600,
                        use_cache=False,
                    )
                except LlmError as e:
                    print(f"  ✗ {h.id}: {str(e)[:120]}")
                    return
                h.descripcion = texto.strip()
                verificar(h)
                if all(h.frases_verificadas.values()):
                    return
        faltan = [c for c, ok in h.frases_verificadas.items() if not ok]
        print(
            f"  ~ {h.id}: el texto no dice {faltan} tras 2 intentos; "
            "esos campos no entran al golden"
        )

    try:
        await asyncio.gather(*(uno(h) for h in hechos))
    finally:
        await cliente.close()
    print(
        f"  redactados {sum(1 for h in hechos_por_id.values() if h.descripcion)} de {len(hechos)}"
    )


def texto_sin_llm(h: Hechos) -> str:
    """Para `--sin-llm`: una plantilla que contiene las frases obligatorias.
    Sirve para que el pipeline corra en CI sin gastar; no para evaluar prosa."""
    fr = frases_obligatorias(h)
    partes = [
        f"Departamento de {h.ambientes} ambientes en {h.calle} al {h.altura // 100 * 100}, "
        f"{h.barrio}."
    ]
    partes += [f"{v[0].upper()}{v[1:]}." for v in fr.values()]
    partes.append(
        f"{h.sup_cubierta:.0f} m² cubiertos."
        + (" Amenities: " + ", ".join(h.amenities) + "." if h.amenities else "")
    )
    return " ".join(partes)


def fila_csv(h: Hechos) -> dict[str, Any]:
    return {
        "portal": h.portal,
        "portal_property_id": h.id,
        "url": f"https://{h.portal}.example/aviso/{h.id}",
        "canonical_url": f"https://{h.portal}.example/aviso/{h.id}",
        "title": f"Departamento {h.ambientes} ambientes en {h.barrio}",
        "price": h.precio,
        "currency": "USD",
        "expenses": h.expensas or "",
        "expenses_currency": "ARS" if h.expensas else "",
        "address": h.direccion
        + (f", Piso {h.piso}" if h.piso and h.depto and h.ficha_completa else ""),
        "neighborhood": f"{h.barrio}, Capital Federal",
        "publisher_name": h.publicador,
        "covered_area": h.sup_cubierta,
        "semi_covered_area": h.sup_semi or "",
        "total_area": round(h.sup_cubierta + h.sup_semi, 1),
        "rooms": h.ambientes if h.ficha_completa else "",
        "bedrooms": h.dormitorios if h.ficha_completa else "",
        "bathrooms": h.banios if h.ficha_completa else "",
        "age": h.antiguedad if h.ficha_completa and h.dice_antiguedad else "",
        "parking_count": h.cocheras if h.ficha_completa else "",
        # Como un recolector real: el estado en `condition` y, a veces, la
        # orientación en `orientation`. Solo con ficha de detalle.
        "condition": ESTADO_PORTAL[h.estado] if h.ficha_completa else "",
        "orientation": h.orientacion.title() if h.ficha_completa and h.dice_orientacion else "",
        "floor": h.piso if h.ficha_completa and h.piso is not None else "",
        "amenities": ", ".join(h.amenities),
        "publication_date": h.publicado.isoformat(),
        "description": h.descripcion,
        "detail_fetched": "true" if h.ficha_completa else "false",
        "raw_data": json.dumps({"sintetico": True, "caso": h.caso, "duplicado_de": h.duplicado_de}),
    }


def entrada_golden(h: Hechos) -> dict[str, Any]:
    fr = frases_obligatorias(h)
    esperado: dict[str, Any] = {
        "condition": h.estado
        if fr.get("condition") and h.frases_verificadas.get("condition")
        else None,
        "orientation": h.orientacion
        if fr.get("orientation") and h.frases_verificadas.get("orientation")
        else None,
        "floor_number": h.piso
        if fr.get("floor_number") and h.frases_verificadas.get("floor_number")
        else None,
        "has_elevator": h.ascensor
        if fr.get("has_elevator") and h.frases_verificadas.get("has_elevator")
        else None,
        "age_years": h.antiguedad
        if fr.get("age_years") and h.frases_verificadas.get("age_years")
        else None,
    }
    # Un campo que el texto tenía que decir y no dijo es ambiguo: ni acierto ni error.
    no_evaluar = [c for c, ok in h.frases_verificadas.items() if not ok and not c.startswith("_")]
    descarte = {
        "pozo": "en_pozo_o_construccion",
        "permuta": "permuta_o_financiacion",
        "promocional": "precio_promocional",
    }.get(h.caso or "")
    if descarte and not h.frases_verificadas.get(f"_{h.caso}", False):
        descarte = None
    return {
        "id": h.id,
        "ref": f"{h.direccion} — USD {h.precio:,}".replace(",", "."),
        "estrato": "sintetico",
        "fuente": "PORTAL_A" if h.portal == "demo-a" else "PORTAL_B",
        "esperado": esperado,
        "no_evaluar": no_evaluar,
        "descarte": descarte,
        "revisado": True,
        "_texto": h.descripcion,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--semilla", type=int, default=2026)
    ap.add_argument("--task", default="flash", help="tarea del gateway para redactar")
    ap.add_argument("--paralelo", type=int, default=8)
    ap.add_argument("--sin-llm", action="store_true", help="descripciones de plantilla, sin gastar")
    ap.add_argument(
        "--reusar-csv",
        action="store_true",
        help="rehacer el golden con las descripciones ya generadas",
    )
    ap.add_argument("--golden-n", type=int, default=60, help="cuántos avisos entran al golden")
    args = ap.parse_args()

    rng = random.Random(args.semilla)  # noqa: S311 — datos sintéticos, no criptografía
    hechos: list[Hechos] = []
    i = 0
    while len(hechos) < args.n:
        h = sortear(i, rng, rng.choice(["demo-a", "demo-a", "demo-b"]))
        hechos.append(h)
        i += 1
        if rng.random() < 0.10 and len(hechos) < args.n:
            hechos.append(duplicar(h, i, rng))
            i += 1

    if args.sin_llm:
        for h in hechos:
            h.descripcion = texto_sin_llm(h)
            verificar(h)
    elif args.reusar_csv:
        # Misma semilla → mismos hechos; las descripciones ya redactadas se
        # leen del CSV anterior. Sirve para rehacer el golden sin volver a pagar.
        with SALIDA_CSV.open(encoding="utf-8", newline="") as f:
            previas = {
                fila["portal_property_id"]: fila["description"] for fila in csv.DictReader(f)
            }
        for h in hechos:
            h.descripcion = previas.get(h.id, "")
            verificar(h)
    else:
        run(redactar_todo(hechos, args.task, args.paralelo))

    SALIDA_CSV.parent.mkdir(parents=True, exist_ok=True)
    filas = [fila_csv(h) for h in hechos if h.descripcion]
    with SALIDA_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    # Golden: un tercio de casos difíciles (miden el recall de la curaduría) y
    # dos tercios de avisos normales (miden la extracción y la PRECISIÓN de la
    # curaduría: un juez que descarta de más solo se ve con avisos que sirven).
    con_texto = [h for h in hechos if h.descripcion and h.duplicado_de is None]
    dificiles = [h for h in con_texto if h.caso]
    resto = [h for h in con_texto if not h.caso]
    rng.shuffle(dificiles)
    rng.shuffle(resto)
    cupo_dificiles = args.golden_n // 3
    golden = dificiles[:cupo_dificiles] + resto[: args.golden_n - cupo_dificiles]
    doc = {
        "version": 1,
        "anotado_por": "construccion",
        "fecha": date.today().isoformat(),
        "fuente": "SINTETICO",
        "nota": (
            "Avisos sintéticos generados por scripts/generar_corpus_demo.py. El `esperado` "
            "es el dato del que se redactó el texto, verificado por frase exacta: ground "
            "truth por construcción, no anotación."
        ),
        "avisos": [entrada_golden(h) for h in golden],
    }
    SALIDA_GOLDEN.write_text(
        "# GOLDEN SET SINTÉTICO — generado, no anotado. Ver scripts/generar_corpus_demo.py\n"
        + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    verificados = sum(all(h.frases_verificadas.values()) for h in hechos if h.descripcion)
    print(
        f"✓ {len(filas)} avisos → {SALIDA_CSV.relative_to(RAIZ)} · "
        f"{sum(1 for h in hechos if h.duplicado_de)} duplicados · "
        f"{sum(1 for h in hechos if h.caso)} casos difíciles · "
        f"{verificados} con todas las frases verificadas\n"
        f"✓ {len(golden)} al golden → {SALIDA_GOLDEN.relative_to(RAIZ)}\n"
        f"  Ingerir con: PORTALES=demo-a=PORTAL_A,demo-b=PORTAL_B "
        f"uv run python scripts/ingest_csv.py --carpeta data/demo"
    )
    print(f"  generado {datetime.now(UTC).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
