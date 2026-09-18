"""Carga de los golden sets — doc 09 §3.3.

Un solo cargador para los dos evals. Antes cada script abría
`tests/golden/extraccion.yaml` por su cuenta con un `Path` hardcodeado, así que
un archivo nuevo —el de Palermo, por ejemplo— **no lo veía nadie** hasta tocar
los dos scripts.

## `revisado: false` no cuenta. Nunca.

Es la regla que hace confiable a todo lo demás. `scripts/golden_set.py` prepara
candidatos en lote, y sin este filtro esos candidatos —con `esperado` todo en
`null`— entrarían al eval como si fueran anotación humana. El resultado sería
una exactitud altísima contra un set que dice "el aviso no menciona nada", y el
número subiría justo cuando el trabajo NO se hizo.

El golden set original de Belgrano no tiene el campo, porque se anotó entero a
mano antes de que existiera. Ausencia = revisado, y está declarado acá para que
no se lea como un descuido.

## Los estratos

El de Palermo se preparó en dos estratos (`azar` y `enriquecido`, ver
`scripts/golden_set.py`). El enriquecido junta descartes rápido pero solo
encuentra los OBVIOS, así que un recall calculado sobre él sale inflado. Se
carga el estrato con cada aviso para poder reportar los dos números.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger()

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "tests" / "golden"

# Los archivos que son insumo y no golden set. `*-para-anotar.yaml` se carga
# igual —tiene entradas revisadas apenas alguien empieza— así que no hay nada
# que excluir por nombre hoy. Queda la lista para cuando lo haya.
IGNORAR: tuple[str, ...] = ()


def cargar(
    *, solo_revisados: bool = True, directorio: Path | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Todos los avisos anotados, de todos los archivos.

    Devuelve `(avisos, conteos)`. Cada aviso lleva `_archivo` y `estrato` para
    poder segmentar el reporte.
    """
    raiz = directorio or GOLDEN_DIR
    avisos: list[dict[str, Any]] = []
    conteos = {"archivos": 0, "total": 0, "revisados": 0, "sin_revisar": 0}

    # `privado/` es para golden sets con texto de avisos reales: se cargan igual
    # y no se versionan (`.gitignore`). Lo que se versiona es sintético.
    for archivo in sorted([*raiz.glob("*.yaml"), *raiz.glob("privado/*.yaml")]):
        if archivo.name in IGNORAR:
            continue
        try:
            datos = yaml.safe_load(archivo.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            log.warning("golden set ilegible", archivo=archivo.name, exc_info=True)
            continue

        conteos["archivos"] += 1
        for a in datos.get("avisos") or []:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            conteos["total"] += 1
            # Ausencia = revisado: el set original de Belgrano es anterior a
            # este campo y se anotó entero a mano.
            revisado = bool(a.get("revisado", True))
            conteos["revisados" if revisado else "sin_revisar"] += 1
            if solo_revisados and not revisado:
                continue
            avisos.append({**a, "_archivo": archivo.name, "estrato": a.get("estrato", "azar")})

    return avisos, conteos


def resumen(conteos: dict[str, int]) -> str:
    s = (
        f"{conteos['revisados']} avisos revisados de {conteos['total']} "
        f"en {conteos['archivos']} archivo(s)"
    )
    if conteos["sin_revisar"]:
        s += f"  ·  {conteos['sin_revisar']} SIN revisar, no se miden"
    return s
