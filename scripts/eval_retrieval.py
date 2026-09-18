"""Mide la recuperación de comparables contra juicios de relevancia.

    uv run python scripts/eval_retrieval.py                       # sistema actual (A)
    uv run python scripts/eval_retrieval.py --sistemas A,E        # varios, con Δ contra A
    uv run python scripts/eval_retrieval.py --juzgar --avisos 40  # juzgar el pool y medir
    uv run python scripts/eval_retrieval.py --guardar             # a eval.component_runs

Doc 18 §4. Las consultas son los informes pasados más, con `--avisos N`, N
avisos del corpus leídos como sujetos (leave-one-out). Con `--juzgar`, el pool
—la unión del top-30 de cada sistema— se juzga con el propio pipeline (nodos
4 a 7) y los juicios quedan en `data/eval/retrieval/`; sin `--juzgar` se usan
los juicios guardados, o los del informe original si no hay.

Cada métrica sale con su intervalo por bootstrap; la diferencia contra el
primer sistema es apareada. Sin intervalo, un número de recuperación es una
impresión.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tasador.cli import run
from tasador.eval import juicios, retrieval
from tasador.eval.memoria import esperar_memoria

SISTEMAS: dict[str, retrieval.Sistema] = {}


def _registrar_sistemas_rag() -> None:
    """Los sistemas B-G existen solo si el paquete `rag` está; A no depende de él."""
    SISTEMAS.update(retrieval.sistemas_disponibles())


async def _main(args: argparse.Namespace) -> int:
    from tasador.db.base import get_session_factory
    from tasador.eval.componentes import Medicion, guardar

    _registrar_sistemas_rag()
    nombres = [s.strip() for s in args.sistemas.split(",") if s.strip()]
    desconocidos = [n for n in nombres if n not in SISTEMAS]
    if desconocidos:
        print(f"✗ sistemas desconocidos: {desconocidos}. Hay: {sorted(SISTEMAS)}")
        return 2
    sistemas = {n: SISTEMAS[n] for n in nombres}

    async with get_session_factory()() as session:
        consultas = await retrieval.cargar_consultas(session, min_juicios=args.min_juicios)
        if args.avisos:
            consultas += await juicios.consultas_de_avisos_fijas(
                session, n=args.avisos, semilla=args.semilla
            )
        if args.desde:
            consultas = consultas[args.desde :]
        if args.limite:
            consultas = consultas[: args.limite]
        if not consultas:
            print("✗ no hay consultas: ni informes con comparables juzgados ni avisos.")
            return 1

        if args.juzgar:
            print(f"juzgando el pool de {len(consultas)} consultas con {nombres}…")
            for i, c in enumerate(consultas, 1):
                # Un proceso que corre horas cede el paso si la máquina se queda
                # sin memoria (18/09: seis de estos dejaron 0,1 GB libres de 32).
                esperar_memoria(args.memoria_libre)
                guardado = None if args.rejuzgar else juicios.cargar_juicios(c.report_id)
                rankings = await retrieval.rankings_de(session, sistemas, c)
                nuevos = {lid for r in rankings.values() for lid in r[:30]} - c.excluir
                if guardado and nuevos <= set(guardado["pool"]):
                    # Ya está juzgado y ningún sistema trajo un documento nuevo:
                    # el pool es el mismo, el juicio también. Reanudable.
                    c.juicios = {k: int(v) for k, v in guardado["juicios"].items()}
                    continue
                c.juicios = await juicios.juzgar_pool(session, c, rankings, guardado=guardado)
                rel = sum(1 for g in c.juicios.values() if g == retrieval.COMPARABLE)
                previos = len(guardado["juicios"]) if guardado else 0
                print(
                    f"  {i:>3}/{len(consultas)} {c.report_id[:14]:<14} "
                    f"pool {len(c.juicios):>3} · comparables {rel:>3}"
                    + (f" · nuevos {len(c.juicios) - previos}" if previos else "")
                )
        else:
            n = juicios.aplicar_juicios_guardados(consultas)
            print(f"{n} consultas con juicios guardados; el resto usa los del informe original")

        consultas = [c for c in consultas if c.juicios]
        print(f"\n{len(consultas)} consultas · k={args.k} · sistemas {nombres}\n")

        resultados = await retrieval.correr(session, sistemas, consultas, k=args.k)
        print(retrieval.tabla(resultados, contra=nombres[0] if len(nombres) > 1 else None))

        if args.json:
            salida = {
                n: {
                    "resumen": {m: asdict(iv) for m, iv in r.resumen().items()},
                    "por_consulta": r.por_consulta,
                }
                for n, r in resultados.items()
            }
            Path(args.json).write_text(json.dumps(salida, indent=2), encoding="utf-8")
            print(f"\n→ {args.json}")

        if args.guardar:
            for r in resultados.values():
                for metrica, valor, detalle in retrieval.resumen_para_guardar(r):
                    await guardar(
                        session,
                        Medicion(
                            componente="retrieval",
                            metrica=metrica,
                            valor=Decimal(str(round(valor, 4))),
                            n=len(consultas),
                            detalle=detalle,
                        ),
                    )
            print(f"\n→ guardado en eval.component_runs ({len(resultados)} sistemas)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sistemas", default="A")
    ap.add_argument("--k", type=int, default=25)
    ap.add_argument("--min-juicios", type=int, default=5)
    ap.add_argument("--avisos", type=int, default=0, help="N avisos del corpus como consultas")
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--desde", type=int, default=0, help="saltear las primeras N consultas")
    ap.add_argument("--limite", type=int, default=0, help="solo las primeras N consultas")
    ap.add_argument("--juzgar", action="store_true", help="juzgar el pool con el pipeline")
    ap.add_argument("--rejuzgar", action="store_true", help="aunque el pool no haya cambiado")
    ap.add_argument("--json", default="", help="guardar el detalle por consulta")
    ap.add_argument("--guardar", action="store_true")
    ap.add_argument(
        "--memoria-libre",
        type=float,
        default=0.20,
        help="fracción de RAM que tiene que quedar libre antes de juzgar cada consulta",
    )
    return run(_main(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
