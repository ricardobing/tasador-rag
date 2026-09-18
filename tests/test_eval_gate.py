"""El gate del backtest tiene que fallar cuando NO PUEDE medir — H-44.

Las tres condiciones de falla de `eval/run.py` exigían `res.mdape is not None`.
Con cero casos evaluados `mdape` es `None`, ninguna se evaluaba, y el proceso
devolvía 0. La única que atrapaba el caso vacío —`not res.beats_baseline`— solo
corre con `--fail-on-regression`, y el CI usa `--fail-if-mdape-worse-than`.

O sea: **con los flags que el CI usa de verdad, el gate salía verde sin haber
medido nada.** El propio `ci.yml` dice querer evitar exactamente eso:

    «correrlo contra una base vacía daría "0 casos evaluados" y un gate verde,
     que es peor que no tener gate»

El diagnóstico estaba bien y la mitigación elegida fue desactivar el paso. La
causa quedó sin arreglar: el día que alguien pusiera `EVAL_CORPUS_LISTO=true`
con un corpus incompleto, el gate iba a dar verde sin medir y esta vez con la
casilla tildada.

Esto vive en el suite y no solo en la sonda `s8_gate.py` porque un gate sobre
el gate tiene que correr en cada commit, no cuando alguien se acuerda.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tasador.eval import run as eval_run
from tasador.eval.backtest import BacktestResult


class _FilaFalsa:
    id = 1


class _Previa:
    """Una corrida anterior buena: el escenario donde el gate más fácil se
    duerme, porque hay contra qué comparar y el resultado nuevo no dice nada."""

    mdape = Decimal("0.150")
    baseline_mdape = Decimal("0.162")
    ppe20 = Decimal("0.60")
    n_cases = 300
    engine_version = "v"
    prompt_bundle_version = "b"
    method_version = "v"
    created_at = datetime(2026, 8, 14, tzinfo=UTC)


class _SesionFalsa:
    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *a: object) -> None:
        return None


async def _async(v):  # type: ignore[no-untyped-def]
    return v


def _args(**kw: object) -> argparse.Namespace:
    """Los flags del CI, tal cual están en ci.yml."""
    d: dict[str, object] = {
        "dataset": "BADATA_2015_2020",
        "sample": 300,
        "seed": 42,
        "fail_on_regression": False,
        "fail_if_mdape_worse_than": 2.0,
        # El default REAL del CLI. Si el test pusiera otro número mediría un
        # gate que no existe.
        "min_casos": eval_run.MINIMO_DE_CASOS,
    }
    d.update(kw)
    return argparse.Namespace(**d)


def _resultado(**kw: object) -> BacktestResult:
    base: dict[str, object] = {
        "dataset": "BADATA_2015_2020",
        "method_version": "v",
        "n_cases": 300,
        "n_evaluated": 299,
        "mdape": Decimal("0.150"),
        "baseline_mdape": Decimal("0.162"),
    }
    base.update(kw)
    return BacktestResult(**base)  # type: ignore[arg-type]


def _correr(monkeypatch: pytest.MonkeyPatch, res: BacktestResult, previa: object, args) -> tuple:  # type: ignore[no-untyped-def]
    """Corre el gate REAL con la base reemplazada. No toca Postgres ni gasta."""
    monkeypatch.setattr(eval_run, "get_session_factory", lambda: _SesionFalsa)
    monkeypatch.setattr(eval_run, "run_backtest", lambda *a, **k: _async(res))
    monkeypatch.setattr(eval_run, "guardar", lambda *a, **k: _async(_FilaFalsa()))
    monkeypatch.setattr(eval_run, "anterior", lambda *a, **k: _async(previa))

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        codigo = asyncio.run(eval_run._correr(args))
    return codigo, buf.getvalue()


# ── El caso que el gate dejaba pasar ─────────────────────────────────────
@pytest.mark.parametrize("previa", [None, _Previa()], ids=["sin corrida previa", "con previa"])
def test_cero_casos_con_los_flags_del_ci_va_en_rojo(monkeypatch, previa) -> None:
    vacio = _resultado(n_cases=0, n_evaluated=0, mdape=None, baseline_mdape=None)
    codigo, salida = _correr(monkeypatch, vacio, previa, _args())

    assert codigo == 1, "el gate salió verde sin haber evaluado un solo caso"
    assert "0 casos" in salida
    assert "no puede medir" in salida


def test_pocos_casos_tambien(monkeypatch) -> None:
    """La otra dirección de la misma falla: el gate no distinguía 300 casos de
    3. Un MdAPE del 15% sobre 30 casos se ve igual de bien y no significa lo
    mismo — la amplitud medida entre semillas sobre 300 casos ya es de 1,83 pp.
    """
    pocos = _resultado(n_cases=30, n_evaluated=30)
    codigo, salida = _correr(monkeypatch, pocos, _Previa(), _args())

    assert codigo == 1
    assert "30 casos" in salida


def test_el_minimo_es_configurable_y_se_respeta(monkeypatch) -> None:
    """Bajar el mínimo es una decisión explícita de quien corre el gate, no
    algo que pase solo."""
    pocos = _resultado(n_cases=30, n_evaluated=30)
    codigo, _ = _correr(monkeypatch, pocos, _Previa(), _args(min_casos=10))
    assert codigo == 0


# ── Y que siga midiendo lo que ya medía ──────────────────────────────────
def test_una_corrida_normal_sigue_en_verde(monkeypatch) -> None:
    """El autotest del gate: sin esto, un gate que devolviera 1 SIEMPRE pasaría
    los tests de arriba y nadie podría mergear nada."""
    codigo, salida = _correr(monkeypatch, _resultado(), _Previa(), _args())
    assert codigo == 0, salida


def test_una_regresion_de_mdape_sigue_en_rojo(monkeypatch) -> None:
    peor = _resultado(mdape=Decimal("0.400"))
    codigo, salida = _correr(monkeypatch, peor, _Previa(), _args())
    assert codigo == 1
    assert "empeoró más de 2.0 puntos" in salida


def test_los_flags_del_ci_son_los_que_el_test_ejercita() -> None:
    """R2: verificar que lo que se prueba es lo que corre.

    Este test compara los flags de `_args()` con el comando real del workflow.
    Si mañana el CI cambia a otros flags, los tests de arriba seguirían pasando
    sobre una configuración que ya nadie usa.
    """
    from pathlib import Path

    ci = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    invocaciones = [ln for ln in ci.splitlines() if "tasador.eval.run" in ln]
    assert invocaciones, "el CI ya no invoca el backtest: este test dejó de medir algo real"

    contexto = ci[ci.index(invocaciones[0]) : ci.index(invocaciones[0]) + 400]
    assert "--fail-if-mdape-worse-than" in contexto, (
        "el CI cambió de flags; `_args()` está ejercitando otra configuración"
    )
