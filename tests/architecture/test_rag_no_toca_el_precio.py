"""El paquete `rag` decide qué avisos se presentan al motor; nunca un número.

ADR-002 dice que el precio no sale de un LLM. Doc 18 §8 agrega el riesgo
nuevo: que la recuperación semántica termine, por comodidad, importando el
motor o alimentándolo con algo que no sea una lista de candidatos. Este gate
lee los imports de `src/tasador/rag/` y falla si alguno toca `valuation`.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAG = Path(__file__).resolve().parents[2] / "src" / "tasador" / "rag"


def _imports(ruta: Path) -> set[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    out: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            out.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            out.add(nodo.module)
    return out


def test_rag_no_importa_el_motor_de_valuacion():
    archivos = sorted(RAG.glob("*.py"))
    assert archivos, "el paquete rag no existe o está vacío: este gate no mide nada"
    culpables = {
        a.name: sorted(m for m in _imports(a) if m.startswith("tasador.valuation"))
        for a in archivos
    }
    culpables = {k: v for k, v in culpables.items() if v}
    assert not culpables, f"rag/ importa el motor de valuación: {culpables}"


def test_el_nodo_de_valuacion_sigue_sin_conocer_al_rag():
    """La dirección contraria también: el motor no sabe que existe `rag`."""
    valuation = Path(__file__).resolve().parents[2] / "src" / "tasador" / "valuation"
    for a in valuation.glob("*.py"):
        assert not any(m.startswith("tasador.rag") for m in _imports(a)), a.name
