"""Carga de prompts versionados desde `prompts/`.

Los prompts viven en archivos y no en el código (doc 04 §5). No es prolijidad:
es lo que hace que un cambio de prompt entre al diff de un PR, que su hash
entre en `prompt_bundle_version`, y que "el sistema mejoró" sea una medición y
no una opinión.

`StrictUndefined` a propósito: una variable que el template usa y nadie pasó
tiene que explotar al renderizar, no producir un prompt con un agujero que el
modelo va a llenar inventando.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

# `=== nombre ===` en una línea sola abre una sección. Ver `secciones()`.
_SECCION = re.compile(r"^===\s*([\w.\-]+)\s*===\s*$", re.MULTILINE)


@lru_cache(maxsize=1)
def _entorno() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        undefined=StrictUndefined,
        # S701 avisa de XSS por autoescape apagado. Acá no aplica y ponerlo en
        # True sería un bug: esto renderiza PROMPTS, no HTML. Escapar un
        # `&` como `&amp;` en el texto de un aviso le cambiaría el contenido
        # al modelo y ensuciaría la verificación de citas.
        autoescape=False,  # noqa: S701
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render(nombre: str, **contexto: Any) -> str:
    """`render("extractor/v1", avisos=[...])` → el texto del prompt.

    El nombre es el mismo que declara `config/agents.yaml`, sin extensión.
    """
    return _entorno().get_template(f"{nombre}.jinja").render(**contexto)


def secciones(nombre: str, **contexto: Any) -> dict[str, str]:
    """Un prompt con VARIAS piezas, en un solo archivo versionado.

    El nodo 8 necesita cuatro textos por agente (`role`, `goal`, `backstory`,
    `task`) porque CrewAI los pide separados, mientras que el motor secuencial
    necesita uno solo. Partirlos en cuatro archivos rompería el bundle: solo se
    hashea el que `agents.yaml` declara, así que los otros tres podrían cambiar
    sin que el `prompt_bundle_version` se entere.

    Un archivo con secciones `=== nombre ===` mantiene un hash, un texto y dos
    motores que no pueden divergir.

        secciones("market_context/v1", barrio="Belgrano", datos="{}", analisis="")
        -> {"analista.role": "...", "analista.task": "...", ...}
    """
    texto = render(nombre, **contexto)
    partes = _SECCION.split(texto)
    if len(partes) < 3:
        raise ValueError(f"El prompt '{nombre}' no declara ninguna sección `=== nombre ===`.")
    # split() con un grupo devuelve [preámbulo, clave, cuerpo, clave, cuerpo, ...]
    it = iter(partes[1:])
    return {clave: cuerpo.strip() for clave, cuerpo in zip(it, it, strict=True)}


def existe(nombre: str) -> bool:
    return (PROMPTS_DIR / f"{nombre}.jinja").exists()
