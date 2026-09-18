"""Prepara avisos para anotar a mano — doc 09 §3.3.

    uv run python scripts/golden_set.py --preparar --barrio Palermo --n 80
    uv run python scripts/golden_set.py --estado

El golden set es el ÚNICO instrumento que puede decir si el nodo 4 acierta y si
el nodo 6 descarta lo que hay que descartar. Todo lo demás mide otra cosa: que
las citas verifiquen prueba que el modelo **no inventa**, no que **acierte**.

## La garantía estructural: `revisado: false`

Cada candidato se escribe con `revisado: false` y **los evals lo ignoran hasta
que una persona lo pone en true**. Preparar candidatos no puede, por
construcción, agrandar el set medido.

Suena a formalismo y no lo es. El riesgo real de este archivo es que alguien
—yo— lo llene con anotaciones generadas por un modelo que lee el mismo texto
que el modelo evaluado, y entonces el eval mida el acuerdo entre dos modelos y
lo reporte como exactitud. Ya está declarado en la cabecera del golden set
actual que lo anotó Claude; esto impide que vuelva a pasar sin que se note.

Por eso tampoco se pre-rellena `esperado`. Lo que sí se hace es **resaltar con
expresiones regulares** los fragmentos del aviso donde suele estar la respuesta:
es determinístico, se verifica de un vistazo, y no es la opinión de nadie.

## Por qué DOS estratos, y no una muestra al azar

Para medir recall hacen falta DESCARTES, y solo ~20% de los avisos lo son. Una
muestra al azar de 80 avisos da ~16 descartes: sigue sin alcanzar.

  · `azar`      — muestra aleatoria. Es la que mide precisión honestamente y la
                  única que puede encontrar descartes que las palabras clave no
                  ven.
  · `enriquecido` — avisos cuyo texto matchea patrones de descarte. Sube el
                  conteo de positivos rápido.

**La trampa del enriquecido hay que decirla:** solo encuentra los descartes
OBVIOS, los que se anuncian con una palabra. Un recall calculado únicamente
sobre ese estrato sale inflado. Por eso cada entrada lleva su `estrato` y el
eval puede reportar los dos números por separado.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from tasador.cli import run

GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "golden"

# Patrones de DESCARTE, para el estrato enriquecido. Deliberadamente amplios:
# acá un falso positivo solo cuesta que alguien lea un aviso que no servía, y
# un falso negativo cuesta un descarte que nunca se anota.
PATRONES_DESCARTE: dict[str, str] = {
    "en_pozo_o_construccion": (
        r"en\s+pozo|en\s+construcci[oó]n|entrega\s+(?:en\s+)?(?:20\d\d|\w+\s+20\d\d)"
        r"|departamento\s+modelo|pr[oó]xim[ao]\s+lanzamiento|avance\s+de\s+obra"
        r"|cuotas?\s+(?:en\s+)?pesos|fideicomiso|desde\s+el\s+pozo"
    ),
    "permuta_o_financiacion": (
        r"permut|parte\s+de\s+pago|financia|cuotas|acepta(?:mos)?\s+propiedad"
    ),
    "precio_promocional": (
        r"remate|subasta|urge\s+(?:la\s+)?venta|liquidaci[oó]n|oportunidad\s+[uú]nica"
    ),
    "tipologia_distinta": r"\blocal\b|\boficina\b|\bcochera\s+sola\b|\blote\b|\bgalp[oó]n\b",
}

# Fragmentos donde suele estar la respuesta de cada campo del nodo 4. Es una
# AYUDA DE LECTURA, no una anotación: resalta dónde mirar y no dice qué poner.
PATRONES_CAMPO: dict[str, str] = {
    "floor_number": (
        r"\d{1,2}\s*(?:°|º|ro|do|to|mo|vo)?\s*piso|piso\s*\d{1,2}|planta\s+baja|\bPB\b"
    ),
    "orientation": r"contrafrente|al\s+frente|\bfrente\b|lateral|interno|contra\s*frente",
    "has_elevator": r"ascensor(?:es)?|sin\s+ascensor",
    "age_years": (
        r"(?:antig[uü]edad|estrenar|a[nñ]os\s+de\s+construid|construido\s+en)\D{0,20}\d{0,4}"
    ),
    "condition": (
        r"a\s+estrenar|refaccionar|reciclad|excelente\s+estado|muy\s+buen\s+estado|impecable"
    ),
    "parking_spaces": r"cochera|garage|garaje|estacionamiento",
}


def _resaltar(texto: str, patrones: dict[str, str]) -> dict[str, list[str]]:
    """Los fragmentos del aviso que matchean, con un poco de contexto."""
    salida: dict[str, list[str]] = {}
    plano = " ".join(texto.split())
    for campo, patron in patrones.items():
        vistos: list[str] = []
        for m in re.finditer(patron, plano, re.IGNORECASE):
            desde, hasta = max(0, m.start() - 45), min(len(plano), m.end() + 45)
            frag = plano[desde:hasta].strip()
            if frag not in vistos:
                vistos.append(frag)
            if len(vistos) >= 3:
                break
        if vistos:
            salida[campo] = vistos
    return salida


def _motivos_sugeridos(texto: str) -> list[str]:
    plano = " ".join(texto.split())
    return [m for m, p in PATRONES_DESCARTE.items() if re.search(p, plano, re.IGNORECASE)]


async def _preparar(barrio: str, n: int, salida: Path, fuente: str | None) -> int:
    import random

    from sqlalchemy import func, select

    from tasador.db.base import get_session_factory
    from tasador.db.models import Listing, Neighborhood

    async with get_session_factory()() as session:
        q = (
            select(Listing)
            .join(Neighborhood, Neighborhood.id == Listing.neighborhood_id)
            .where(
                Neighborhood.name == barrio,
                Listing.active.is_(True),
                Listing.currency == "USD",
                Listing.price.is_not(None),
                func.length(func.coalesce(Listing.description, "")) > 200,
            )
        )
        if fuente:
            q = q.where(Listing.source == fuente)
        avisos = list((await session.execute(q)).scalars().all())

    if not avisos:
        print(f"No hay avisos de {barrio} con descripción suficiente.")
        return 2

    # Los ya anotados NO se vuelven a proponer: el trabajo hecho no se repite.
    ya = _ids_anotados()
    avisos = [a for a in avisos if a.source_id not in ya]

    def texto_de(a: Any) -> str:
        return f"{a.title or ''}\n{a.description or ''}"

    con_senal = [a for a in avisos if _motivos_sugeridos(texto_de(a))]
    sin_senal = [a for a in avisos if a not in con_senal]

    # 60/40: la mayoría enriquecida para juntar positivos, y un tercio al azar
    # que es lo único que puede encontrar los descartes que ninguna palabra
    # clave anuncia — y lo único que mide precisión sin sesgo.
    rnd = random.Random(42)  # noqa: S311  reproducibilidad, no criptografía
    rnd.shuffle(con_senal)
    rnd.shuffle(sin_senal)
    n_enr = min(len(con_senal), int(n * 0.6))
    elegidos = [("enriquecido", a) for a in con_senal[:n_enr]]
    elegidos += [("azar", a) for a in sin_senal[: n - n_enr]]
    rnd.shuffle(elegidos)

    lineas = _cabecera(barrio, len(elegidos), n_enr)
    for estrato, a in elegidos:
        lineas.extend(_bloque(a, estrato, texto_de(a)))

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print(f"\n{len(elegidos)} avisos escritos en {salida}")
    print(f"  {n_enr} enriquecidos (matchean un patrón de descarte)")
    print(f"  {len(elegidos) - n_enr} al azar")
    print(f"\n  Ya anotados y salteados: {len(ya)}")
    print("\nTodos quedan con `revisado: false`. Los evals los IGNORAN hasta que")
    print("una persona los revise y lo ponga en true.")
    return 0


def _ids_anotados() -> set[str]:
    """Los ids que ya están en algún golden set, revisados o no."""
    import yaml

    ya: set[str] = set()
    for archivo in GOLDEN.glob("*.yaml"):
        try:
            datos = yaml.safe_load(archivo.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for a in datos.get("avisos") or []:
            if isinstance(a, dict) and a.get("id"):
                ya.add(str(a["id"]))
    return ya


def _cabecera(barrio: str, n: int, n_enr: int) -> list[str]:
    return [
        f"# GOLDEN SET — {barrio} · {n} avisos para anotar (doc 09 §3.3)",
        "#",
        "# ⚠️ NADA DE ESTE ARCHIVO SE MIDE hasta que `revisado` sea true.",
        "#",
        "# Cómo se anota, campo por campo:",
        "#",
        "#   esperado.<campo>   el valor que EL AVISO dice. `null` = el aviso NO",
        "#                      lo dice. Que el modelo lo complete es un ERROR,",
        "#                      no una virtud: cada uno mueve un precio real.",
        "#   no_evaluar: [..]   los campos genuinamente ambiguos. Ahí ni el",
        "#                      acierto ni el error cuentan. Declarar la duda es",
        "#                      mejor que forzar una respuesta y medir contra ella.",
        "#   descarte           el motivo del nodo 6, o null si el aviso SIRVE.",
        "#   revisado           ponelo en true cuando lo miraste. Sin esto no",
        "#                      cuenta para nada.",
        "#",
        "# `_pistas` son fragmentos encontrados con expresiones regulares, para",
        "# no tener que leer el aviso entero. NO son una anotación ni la opinión",
        "# de un modelo: son un buscador. Pueden estar incompletas y pueden",
        "# sobrar. Si el campo no está en las pistas, el aviso igual puede",
        "# decirlo de otra forma — ante la duda, leer `_texto`.",
        "#",
        f"# Estratos: {n_enr} `enriquecido` (matchean un patrón de descarte) y",
        f"# {n - n_enr} `azar`. El enriquecido junta positivos rápido pero SOLO",
        "# encuentra los descartes obvios: un recall calculado solo sobre él sale",
        "# inflado. Por eso cada entrada lleva su estrato.",
        "",
        "version: 1",
        "anotado_por: PENDIENTE",
        f"barrio: {barrio}",
        "",
        "avisos:",
        "",
    ]


def _yaml_str(s: str) -> str:
    """Un escalar YAML seguro, sin depender del dumper para el texto largo."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bloque(a: Any, estrato: str, texto: str) -> list[str]:
    pistas = _resaltar(texto, PATRONES_CAMPO)
    motivos = _motivos_sugeridos(texto)
    sup = (a.raw or {}).get("surface_weighted")
    ref = f"{a.address_raw or 's/d'} — USD {int(a.price):,}".replace(",", ".")

    out = [
        f'  - id: "{a.source_id}"',
        f"    ref: {_yaml_str(ref)}",
        f"    estrato: {estrato}",
        f"    fuente: {a.source}",
        f"    # {sup or '?'} m² pond. · {a.url}",
        "    esperado:",
    ]
    for campo in ("condition", "orientation", "floor_number", "has_elevator", "age_years"):
        out.append(f"      {campo}: null")
    out += [
        "    no_evaluar: []",
        f"    descarte: null{'   # candidatos: ' + ', '.join(motivos) if motivos else ''}",
        "    revisado: false",
        "    _pistas:",
    ]
    if not pistas:
        out.append("      # (ninguna: leer el texto)")
    for campo, frags in pistas.items():
        out.append(f"      {campo}:")
        out.extend(f"        - {_yaml_str(f)}" for f in frags)
    out += [
        "    _texto: |",
        *[f"      {linea}" for linea in _envolver(texto, 76)],
        "",
    ]
    return out


def _envolver(texto: str, ancho: int) -> list[str]:
    import textwrap

    plano = " ".join(texto.split())
    return textwrap.wrap(plano, ancho) or [""]


def _estado() -> int:
    import yaml

    print(f"\n{'archivo':<26}{'avisos':>8}{'revisados':>11}{'descartes':>11}")
    print("-" * 56)
    for archivo in sorted(GOLDEN.glob("*.yaml")):
        datos = yaml.safe_load(archivo.read_text(encoding="utf-8")) or {}
        avisos = datos.get("avisos") or []
        # El golden set original NO tiene `revisado`: se anotó entero a mano
        # antes de que este campo existiera, así que ausencia = revisado.
        rev = [a for a in avisos if a.get("revisado", True)]
        desc = [a for a in rev if a.get("descarte")]
        print(f"{archivo.name:<26}{len(avisos):>8}{len(rev):>11}{len(desc):>11}")

    print("\nPara que el número signifique algo (95% de confianza):")
    print("  extracción  ±5 pp  ->   64 avisos revisados")
    print("  curaduría   ±10 pp ->   35 DESCARTES revisados")
    return 0


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preparar", action="store_true")
    ap.add_argument("--estado", action="store_true")
    ap.add_argument("--barrio", default="Palermo")
    ap.add_argument("--fuente", default=None, help="PORTAL_A | PORTAL_B")
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--salida", type=Path, default=None)
    args = ap.parse_args()

    if args.estado:
        return _estado()
    if not args.preparar:
        ap.error("hace falta --preparar o --estado")

    destino = args.salida or GOLDEN / f"{args.barrio.lower()}-para-anotar.yaml"
    if destino.exists():
        print(f"Ya existe {destino}. Movelo o pasá --salida para no pisar trabajo hecho.")
        return 2
    return run(_preparar(args.barrio, args.n, destino, args.fuente))


if __name__ == "__main__":
    raise SystemExit(main())
