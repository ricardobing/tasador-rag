"""`python -m tasador.eval.run` — el gate de calidad de doc 09 §5.

Lo invocan el CI (job `eval-gate`) y `make eval`. Hasta el 14/08 el módulo no
existía: **todo PR que tocara `prompts/`, `config/adjustments.yaml` o
`src/tasador/valuation/` disparaba el gate y el gate reventaba con
`ModuleNotFoundError`**. Es el peor modo de falla para un control de calidad —
falla igual si el cambio era bueno que si era malo, así que no informa nada, y
lo primero que se aprende es a ignorarlo.

Dos cosas que hace y que la consola sola no hacía:

**Guarda la corrida en `eval.backtest_runs`.** Un backtest que se imprime y se
pierde no construye una serie. La comparación "¿mejoró o empeoró?" necesita la
corrida anterior EN LA BASE, no en un documento.

**Compara contra la última corrida del mismo dataset**, no contra un número
escrito a mano en un YAML. Un umbral hardcodeado envejece: se actualiza cuando
alguien se acuerda, y mientras tanto el gate pasa por inercia.

Modos:

    python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300
    python -m tasador.eval.run --dataset GOLDEN_SET --fail-on-regression
    python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300 \\
                               --fail-if-mdape-worse-than 2.0
    python -m tasador.eval.run --history --dataset BADATA_2015_2020
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import load_agents_config
from tasador.cli import run as run_async
from tasador.db.base import get_session_factory
from tasador.db.models import BacktestRun
from tasador.eval.backtest import BacktestResult, render, run_backtest
from tasador.settings import get_settings

log = structlog.get_logger()

# Los nombres de doc 09 §3. `GOLDEN_SET` mide componentes (extracción,
# curaduría) y no el end-to-end, así que tiene su propio camino.
#
# El par VIGENTES / VIGENTES_SIN_FEATURES (14/08) es el experimento del motor
# de ajustes: mismos casos, misma semilla, lo único que cambia es si los
# comparables llevan las features del nodo 4. La resta de los dos MdAPE es el
# aporte del motor — la medición que ESTADO §5.1 #5 pedía.
DATASETS = (
    "BADATA_2015_2020",
    "CRM_INVENTORY",
    "GOLDEN_SET",
    "VIGENTES",
    "VIGENTES_SIN_FEATURES",
)

# Por debajo de esto el MdAPE es ruido con forma de número, y el gate no puede
# distinguir "mejoró" de "tocaron otros casos". Con la variabilidad medida
# entre semillas (1,83 pp de amplitud sobre 300 casos), 50 es el piso donde una
# diferencia de 2 pp empieza a significar algo. Ver H-44.
MINIMO_DE_CASOS = 50


def _git_ref() -> str | None:
    """El commit, para poder atar una fila de métricas a un diff.

    Si no hay git —el contenedor no lo trae— devuelve None y no pasa nada: la
    fila vale igual. Lo que no puede es romper el backtest.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


async def guardar(
    session: AsyncSession, res: BacktestResult, *, sample: int, seed: int, duration_ms: int
) -> BacktestRun:
    """Persiste la corrida con las tres versiones (doc 09 §5)."""
    s = get_settings()
    fila = BacktestRun(
        dataset=res.dataset,
        sample=sample,
        seed=seed,
        engine_version=s.engine_version,
        # El método lo declara el motor de valuación en la propia corrida; si
        # no evaluó ni un caso, no hay método que declarar y se registra como
        # tal en vez de inventarle uno.
        method_version=res.method_version or "sin-casos",
        prompt_bundle_version=load_agents_config().bundle_hash,
        n_cases=res.n_cases,
        n_evaluated=res.n_evaluated,
        coverage=res.coverage,
        mdape=res.mdape,
        mape=res.mape,
        ppe10=res.ppe10,
        ppe20=res.ppe20,
        hit_rate=res.hit_rate,
        bias=res.bias,
        baseline_mdape=res.baseline_mdape,
        baseline_ppe20=res.baseline_ppe20,
        por_barrio={k: float(v) for k, v in res.por_barrio.items()},
        por_confianza={k: float(v) for k, v in res.por_confianza.items()},
        git_ref=_git_ref(),
        duration_ms=duration_ms,
    )
    session.add(fila)
    await session.commit()
    return fila


async def anterior(session: AsyncSession, dataset: str, excluir: Any = None) -> BacktestRun | None:
    """La última corrida del MISMO dataset, que es la única comparable.

    Comparar el MdAPE de 300 casos de BA Data contra el de 24 avisos de
    Belgrano no dice nada sobre si el sistema mejoró.
    """
    q = (
        select(BacktestRun)
        .where(BacktestRun.dataset == dataset, BacktestRun.mdape.is_not(None))
        .order_by(BacktestRun.created_at.desc())
        .limit(1)
    )
    if excluir is not None:
        q = q.where(BacktestRun.id != excluir)
    return (await session.execute(q)).scalar_one_or_none()


def _pct(v: Decimal | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


def comparar(res: BacktestResult, previa: BacktestRun | None, *, tolerancia_pp: float) -> list[str]:
    """El veredicto del gate, en texto y con el número a la vista.

    `tolerancia_pp` está en PUNTOS PORCENTUALES, no en porcentaje relativo:
    "empeoró 2 puntos" es 15,0% -> 17,0%, que es lo que dice doc 09 §5.
    """
    if previa is None:
        return [
            "",
            "Sin corrida previa de este dataset en eval.backtest_runs:",
            "esta queda como la línea de base. No hay nada que comparar todavía.",
        ]
    if res.mdape is None or previa.mdape is None:
        return ["", "Alguna de las dos corridas no produjo MdAPE: no se compara."]

    delta_pp = (res.mdape - previa.mdape) * 100
    fecha = previa.created_at.date().isoformat()
    lineas = [
        "",
        "CONTRA LA CORRIDA ANTERIOR",
        f"  {fecha}  ·  motor {previa.engine_version}  ·  bundle {previa.prompt_bundle_version}",
        f"  MdAPE {_pct(previa.mdape)}  ->  {_pct(res.mdape)}   ({delta_pp:+.1f} pp)",
    ]
    if delta_pp > tolerancia_pp:
        lineas.append(f"  🔴 EMPEORÓ más de {tolerancia_pp:.1f} pp — el gate corta acá.")
    elif delta_pp > 0:
        lineas.append(f"  ⚠️  Empeoró {delta_pp:.1f} pp, dentro de la tolerancia.")
    else:
        lineas.append(f"  ✅ Mejoró {abs(delta_pp):.1f} pp.")
    return lineas


async def _historia(dataset: str) -> int:
    async with get_session_factory()() as session:
        filas = (
            (
                await session.execute(
                    select(BacktestRun)
                    .where(BacktestRun.dataset == dataset)
                    .order_by(BacktestRun.created_at.desc())
                    .limit(25)
                )
            )
            .scalars()
            .all()
        )
    if not filas:
        print(f"No hay corridas registradas para {dataset}.")
        return 0
    print(f"\n{dataset} — últimas {len(filas)} corridas\n")
    print(f"{'fecha':<12}{'casos':>7}{'MdAPE':>9}{'base':>9}{'PPE20':>9}  motor / bundle")
    print("─" * 78)
    for f in filas:
        print(
            f"{f.created_at.date().isoformat():<12}{f.n_evaluated:>7}"
            f"{_pct(f.mdape):>9}{_pct(f.baseline_mdape):>9}{_pct(f.ppe20):>9}"
            f"  {f.engine_version} / {f.prompt_bundle_version}"
        )
    return 0


async def _correr(args: argparse.Namespace) -> int:
    if args.dataset == "GOLDEN_SET":
        # El golden set mide COMPONENTES (extracción, dedup, curaduría), no el
        # end-to-end (doc 09 §3.3). Meterlo por el mismo camino que el backtest
        # daría un MdAPE sobre 24 avisos de un solo barrio: un número que se ve
        # bien y no significa nada.
        return await _golden_set(args)

    t0 = datetime.now(UTC)
    async with get_session_factory()() as session:
        res = await run_backtest(session, dataset=args.dataset, sample=args.sample, seed=args.seed)
        ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        print(render(res))

        previa = await anterior(session, args.dataset)
        fila = await guardar(session, res, sample=args.sample, seed=args.seed, duration_ms=ms)

    lineas = comparar(res, previa, tolerancia_pp=args.fail_if_mdape_worse_than)
    print("\n".join(lineas))
    print(f"\nGuardada como eval.backtest_runs.id = {fila.id}")

    # ── El gate ──────────────────────────────────────────────────────────
    fallas: list[str] = []

    # ⚠️ ESTO VA PRIMERO Y SIN CONDICIÓN, y es el arreglo de H-44.
    #
    # Las tres condiciones de abajo exigen `res.mdape is not None`. Con cero
    # casos evaluados, `mdape` es None y NINGUNA se evalúa: `fallas` queda
    # vacío y el proceso devuelve 0. La única que atrapaba el caso vacío
    # —`not res.beats_baseline`— solo corre con `--fail-on-regression`, y el CI
    # usa `--fail-if-mdape-worse-than`. O sea: el gate salía verde sin haber
    # medido nada, con los flags que el CI usa de verdad.
    #
    # El propio ci.yml dice querer evitar exactamente eso ("correrlo contra una
    # base vacía daría 0 casos evaluados y un gate verde, que es peor que no
    # tener gate"). El diagnóstico era correcto; la causa había quedado sin
    # arreglar, y la mitigación era desactivar el paso.
    #
    # Y falla en la otra dirección también: 3 casos no es lo mismo que 300. Por
    # debajo de `--min-casos` el MdAPE es ruido con forma de número.
    if res.n_evaluated < args.min_casos:
        fallas.append(
            f"solo se evaluaron {res.n_evaluated} casos de {args.sample} pedidos "
            f"(mínimo {args.min_casos}): el gate no puede medir"
        )

    empeoro = (
        args.fail_on_regression
        and previa is not None
        and res.mdape is not None
        and previa.mdape is not None
        and res.mdape > previa.mdape
    )
    if empeoro and previa is not None:
        fallas.append(f"el MdAPE empeoró ({_pct(previa.mdape)} -> {_pct(res.mdape)})")
    if (
        args.fail_if_mdape_worse_than is not None
        and previa is not None
        and res.mdape is not None
        and previa.mdape is not None
        and (res.mdape - previa.mdape) * 100 > Decimal(str(args.fail_if_mdape_worse_than))
    ):
        fallas.append(f"el MdAPE empeoró más de {args.fail_if_mdape_worse_than} puntos")
    if args.fail_on_regression and not res.beats_baseline:
        # Doc 09 §7: no le gana al baseline es un hallazgo válido, pero no puede
        # mergear en silencio.
        fallas.append("no le gana al baseline (doc 09 §7)")

    if fallas:
        print("\n🔴 GATE EN ROJO:")
        for f in fallas:
            print(f"   · {f}")
        return 1
    return 0


async def _golden_set(args: argparse.Namespace) -> int:
    """El golden set mide COMPONENTES (doc 09 §3.3), no el end-to-end.

    La medición no se reimplementa acá: los scripts `eval_extraccion.py` y
    `eval_curaduria.py` comparten la MISMA implementación que los nodos —esa
    fue una de las correcciones de la Etapa 3, porque el eval anterior llamaba
    a `structured()` pelado y medía un fragmento del sistema creyendo que medía
    el sistema—. Lo que hace este modo es leer la última corrida de cada
    componente y compararla con la anterior.

    Por eso el orden importa y se dice: primero se corren los scripts, después
    el gate. Si el gate midiera por su cuenta, habría dos implementaciones.
    """
    from tasador.eval.componentes import OBJETIVOS, VENTANA, Medicion, comparar, ultimas

    async with get_session_factory()() as session:
        pendientes: list[str] = []
        fallas: list[str] = []

        for componente, metrica in sorted(OBJETIVOS):
            # Una más que la ventana: la primera es la corrida que se evalúa y
            # el resto son las previas. Meterla en su propia mediana diluiría
            # cualquier caída.
            filas = await ultimas(session, componente, metrica, k=VENTANA + 1)
            if not filas:
                pendientes.append(f"{componente}/{metrica}")
                continue

            ultima = filas[0]
            m = Medicion(
                componente=componente,
                metrica=metrica,
                valor=ultima.valor,
                n=ultima.n,
                prompt=ultima.prompt,
            )
            v = comparar(m, filas[1:])
            print("\n".join(v.lineas))
            if args.fail_on_regression and v.regresion:
                fallas.append(f"{componente}/{metrica} empeoró")
            if args.fail_on_regression and v.bajo_objetivo:
                fallas.append(f"{componente}/{metrica} está por debajo del objetivo")

    if pendientes:
        print(
            "\nSin mediciones registradas para: " + ", ".join(pendientes) + "\n"
            "Correr los evals antes del gate:\n"
            "  uv run python scripts/eval_extraccion.py --guardar\n"
            "  uv run python scripts/eval_curaduria.py --guardar\n"
        )
        # En rojo, y por el motivo correcto: no es que empeoró, es que no se
        # midió. Un gate que pasa sin medir es peor que uno que no existe.
        #
        # Antes esto dependía de `--fail-on-regression`, que es el mismo hueco
        # que tenía el backtest (H-44): sin ese flag, el gate del golden set
        # salía verde sobre CERO componentes medidos. "No hay regresión" no es
        # una conclusión válida cuando no hay mediciones que comparar.
        print("🔴 el gate no puede pasar sin mediciones.")
        return 1

    if fallas:
        print("\n🔴 GATE EN ROJO:")
        for f in fallas:
            print(f"   · {f}")
        return 1
    return 0


def main() -> int:
    # La consola de Windows es cp1252 y este reporte usa ═, · y emojis. Sin
    # esto el gate hace TODO el trabajo y revienta al imprimir el veredicto:
    # se ve un traceback de encoding donde tendría que verse si pasó o no.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(prog="tasador.eval.run", description=__doc__)
    p.add_argument("--dataset", choices=DATASETS, default="BADATA_2015_2020")
    p.add_argument("--sample", type=int, default=500)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="corta si el MdAPE empeoró contra la corrida anterior, o si no le gana al baseline",
    )
    p.add_argument(
        "--fail-if-mdape-worse-than",
        type=float,
        default=None,
        metavar="PP",
        help="corta si el MdAPE empeoró más de PP PUNTOS PORCENTUALES (doc 09 §5: 2.0)",
    )
    p.add_argument(
        "--min-casos",
        type=int,
        default=MINIMO_DE_CASOS,
        metavar="N",
        help=(
            "corta si se evaluaron menos de N casos. Un MdAPE sobre pocos casos "
            "no es una medición mala: no es una medición"
        ),
    )
    p.add_argument("--history", action="store_true", help="lista las últimas corridas y sale")
    args = p.parse_args()

    if args.history:
        return run_async(_historia(args.dataset))

    if not os.environ.get("DATABASE_URL") and not get_settings().database_url:
        print("Falta DATABASE_URL. El backtest lee el corpus de la base.")
        return 2

    # El default de la comparación es la tolerancia de doc 09 §5.
    if args.fail_if_mdape_worse_than is None:
        args.fail_if_mdape_worse_than = 2.0
    return run_async(_correr(args))


if __name__ == "__main__":
    raise SystemExit(main())
