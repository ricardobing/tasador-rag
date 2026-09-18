"""El pooling juzga solo lo nuevo cuando ya hay juicios guardados.

Agregar un sistema a la comparación (el reranker, un modelo nuevo) tiene que
costar SUS candidatos y no el pool entero: el juez no es determinístico, y
volver a juzgar lo ya juzgado cambiaría los juicios de los sistemas que ya
estaban de una corrida a la otra.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tasador.eval import juicios
from tasador.eval.retrieval import Consulta


@pytest.fixture
def carpeta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(juicios, "CARPETA", tmp_path)
    return tmp_path


@pytest.fixture
def juez_falso(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Reemplaza la base y el juez: registra QUÉ ids se juzgaron en cada llamada."""
    llamadas: list[list[str]] = []

    async def candidatos_por_id(session: object, ids: list[str]) -> list[str]:
        return list(ids)

    async def juzgar(
        subject: object, candidatos: list[str], *, cfg: object = None
    ) -> dict[str, int]:
        llamadas.append(list(candidatos))
        return dict.fromkeys(candidatos, 2)

    monkeypatch.setattr(juicios, "candidatos_por_id", candidatos_por_id)
    monkeypatch.setattr(juicios, "juzgar", juzgar)
    return llamadas


async def test_sin_juicios_guardados_se_juzga_el_pool_entero(
    carpeta: Path, juez_falso: list[list[str]]
):
    c = Consulta(report_id="aviso:x", subject={}, juicios={}, excluir={"x"})
    rankings = {"A": ["a", "b", "x"], "B": ["b", "c"]}

    res = await juicios.juzgar_pool(None, c, rankings)  # type: ignore[arg-type]

    assert juez_falso == [["a", "b", "c"]]  # unión en orden, sin el excluido
    assert res == {"a": 2, "b": 2, "c": 2}
    guardado = json.loads((carpeta / "aviso_x.json").read_text(encoding="utf-8"))
    assert guardado["pool"] == ["a", "b", "c"]


async def test_con_juicios_guardados_solo_se_juzga_lo_nuevo(
    carpeta: Path, juez_falso: list[list[str]]
):
    c = Consulta(report_id="aviso:x", subject={}, juicios={})
    guardado = {"pool": ["a", "b"], "juicios": {"a": 2, "b": 0}}
    rankings = {"A": ["a", "b"], "F": ["c", "a"]}  # F trae uno nuevo

    res = await juicios.juzgar_pool(None, c, rankings, guardado=guardado)  # type: ignore[arg-type]

    assert juez_falso == [["c"]]
    # Los juicios previos se conservan tal cual (b sigue en 0, no se rejuzga).
    assert res == {"a": 2, "b": 0, "c": 2}
    en_disco = json.loads((carpeta / "aviso_x.json").read_text(encoding="utf-8"))
    assert en_disco["pool"] == ["a", "b", "c"]
    assert en_disco["juicios"] == {"a": 2, "b": 0, "c": 2}


async def test_sin_nada_nuevo_no_se_llama_al_juez(carpeta: Path, juez_falso: list[list[str]]):
    c = Consulta(report_id="aviso:x", subject={}, juicios={})
    guardado = {"pool": ["a", "b"], "juicios": {"a": 2, "b": 1}}

    res = await juicios.juzgar_pool(None, c, {"A": ["b", "a"]}, guardado=guardado)  # type: ignore[arg-type]

    assert juez_falso == []
    assert res == {"a": 2, "b": 1}
