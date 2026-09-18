"""Â¿El gate del backtest puede salir VERDE sin haber medido nada?

Ejercita el gate REAL (`tasador.eval.run._correr`) con un resultado de 0 casos.
No toca la base: se reemplazan `run_backtest`, `guardar` y `anterior`.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal as D

from tasador.eval import run as R
from tasador.eval.backtest import BacktestResult


class _FilaFalsa:
    id = "no-se-guardo"


@asynccontextmanager
async def _sesion_falsa():
    yield None


def _instalar(res: BacktestResult, previa):
    R.get_session_factory = lambda: _sesion_falsa  # type: ignore[assignment]
    R.run_backtest = lambda *a, **k: _async(res)  # type: ignore[assignment]
    R.guardar = lambda *a, **k: _async(_FilaFalsa())  # type: ignore[assignment]
    R.anterior = lambda *a, **k: _async(previa)  # type: ignore[assignment]


async def _async(v):
    return v


def _args(**kw):
    d = dict(
        dataset="BADATA_2015_2020",
        sample=300,
        seed=42,
        fail_on_regression=False,
        fail_if_mdape_worse_than=2.0,
        # El default REAL del CLI, no uno inventado acá: si la sonda pusiera
        # otro número mediría un gate que no existe.
        min_casos=R.MINIMO_DE_CASOS,
    )
    d.update(kw)
    return argparse.Namespace(**d)


VACIO = BacktestResult(dataset="BADATA_2015_2020", method_version="", n_cases=0, n_evaluated=0)


class _Previa:
    from datetime import datetime as _dt

    created_at = _dt(2026, 8, 14)
    mdape = D("0.150")
    baseline_mdape = D("0.162")
    ppe20 = D("0.60")
    n_cases = 300
    engine_version = "v"
    prompt_bundle_version = "b"
    method_version = "v"


BUENO = _Previa()
PEOR = BacktestResult(
    dataset="BADATA_2015_2020",
    method_version="v",
    n_cases=300,
    n_evaluated=299,
    mdape=D("0.400"),
    baseline_mdape=D("0.162"),
)

NORMAL = BacktestResult(
    dataset="BADATA_2015_2020",
    method_version="v",
    n_cases=300,
    n_evaluated=299,
    mdape=D("0.150"),
    baseline_mdape=D("0.162"),
)
# Justo por debajo del mínimo: el caso que el gate tampoco distinguía. Un MdAPE
# sobre 30 casos se ve igual de bien que uno sobre 300 y no significa lo mismo.
POCOS = BacktestResult(
    dataset="BADATA_2015_2020",
    method_version="v",
    n_cases=30,
    n_evaluated=30,
    mdape=D("0.150"),
    baseline_mdape=D("0.162"),
)

CASOS = [
    ("CERO CASOS, como en un runner sin corpus  (flags del CI)", VACIO, None, _args()),
    ("CERO CASOS, con una corrida previa buena  (flags del CI)", VACIO, BUENO, _args()),
    ("CERO CASOS, con --fail-on-regression", VACIO, BUENO, _args(fail_on_regression=True)),
    ("30 casos, por debajo del minimo           (flags del CI)", POCOS, BUENO, _args()),
    ("MdAPE 40% (25 pp peor)                    (flags del CI)", PEOR, BUENO, _args()),
    ("MdAPE 15% normal, 299 casos               (flags del CI)", NORMAL, BUENO, _args()),
]

for nombre, res, previa, args in CASOS:
    _instalar(res, previa)
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        codigo = asyncio.run(R._correr(args))
    estado = "VERDE (exit 0)" if codigo == 0 else "ROJO  (exit 1)"
    print(f"  {nombre:<58} -> {estado}")
    if "GATE EN ROJO" in buf.getvalue():
        for linea in buf.getvalue().splitlines():
            if linea.strip().startswith("·"):
                print(f"        {linea.strip()}")
