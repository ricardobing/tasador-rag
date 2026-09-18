"""Mide «Preguntale al informe» — doc 18 §5.4.

    uv run python scripts/eval_qa.py                    # el último informe SUCCEEDED
    uv run python scripts/eval_qa.py --informe <uuid> --corridas 3 --guardar

Las preguntas se generan por PLANTILLA a partir del informe elegido, así el
eval corre sobre cualquier base —la real o la demo— sin un archivo de
preguntas atado a ids concretos. Tres grupos:

  · con respuesta y chequeo determinístico: la pregunta apunta a un hecho
    (cuántos comparables se usaron, por qué se descartó tal dirección, cuál
    es el precio sugerido) y se verifica que la respuesta cite el fragmento
    correcto y contenga la cifra correcta;
  · con respuesta sobre la metodología (por qué la mediana, qué es la
    superficie ponderada): se verifica que cite `Met §…`;
  · SIN respuesta (valor futuro, dueño del comparable, opinión): se verifica
    que RECHACE.

Métricas: precisión de citas (toda cita apunta a un fragmento que existe y
es pertinente), exactitud de rechazo, tasa de cifras trazables, y la
calibración del umbral: el coseno de las preguntas sin respuesta contra el
de las que sí la tienen. Se corre N veces y se reporta la mediana, como todo
eval de componente de este proyecto (17,6 pp de ruido en el de extracción).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tasador.cli import run


@dataclass(slots=True)
class Caso:
    pregunta: str
    grupo: str  # hecho | metodologia | sin_respuesta
    cita_esperada: str | None = None
    cifra_esperada: str | None = None


def _plata(v: Any) -> str:
    return f"{float(v):,.0f}".replace(",", ".")


def casos_de(informe: dict[str, Any]) -> list[Caso]:
    v = informe["valuation"]
    comps = informe["comparables"]
    items = comps["items"]
    usados = [(i + 1, it) for i, it in enumerate(items) if it["included"]]
    descartados = [(i + 1, it) for i, it in enumerate(items) if not it["included"]]
    casos = [
        Caso(
            "¿Cuál es el precio de publicación sugerido?",
            "hecho",
            "V",
            _plata(v["suggested_listing_price"]["mid"]),
        ),
        Caso(
            "¿Entre qué valores se espera cerrar la venta?",
            "hecho",
            "V",
            _plata(v["expected_closing_range"]["low"]),
        ),
        Caso("¿Cuántos comparables se usaron para la valuación?", "hecho", "N", str(comps["used"])),
        Caso("¿Cuántos avisos se descartaron?", "hecho", "N", str(comps["excluded"])),
        Caso("¿Qué nivel de confianza tiene el informe?", "hecho", "CONF", None),
        Caso(
            "¿Cuál es el valor por metro cuadrado que se usó?",
            "hecho",
            "V",
            _plata(v["price_per_m2"]),
        ),
    ]
    for n, it in descartados[:4]:
        casos.append(
            Caso(f"¿Por qué no se usó el aviso de {it['address']}?", "hecho", f"C-{n:02d}", None)
        )
    for n, it in usados[:3]:
        casos.append(
            Caso(
                f"¿A qué precio está publicado el comparable de {it['address']}?",
                "hecho",
                f"C-{n:02d}",
                _plata(it["price"]),
            )
        )
    casos += [
        Caso("¿Por qué se usa la mediana y no el promedio?", "metodologia", "Met §5", None),
        Caso("¿Qué es la superficie ponderada?", "metodologia", "Met §2", None),
        Caso(
            "¿Cómo se ajusta un comparable por estado de conservación?",
            "metodologia",
            "Met §4",
            None,
        ),
        Caso("¿Qué significa que la confianza sea baja?", "metodologia", "Met §7", None),
        Caso(
            "¿Por qué el rango de cierre es menor que el precio sugerido?",
            "metodologia",
            "Met §6",
            None,
        ),
    ]
    casos += [
        Caso("¿Cuánto va a valer la propiedad dentro de dos años?", "sin_respuesta"),
        Caso("¿Quién es el dueño del comparable más caro?", "sin_respuesta"),
        Caso(
            "¿Cuál es el teléfono de la inmobiliaria que publicó el primer comparable?",
            "sin_respuesta",
        ),
        Caso("¿Conviene vender ahora o esperar?", "sin_respuesta"),
        Caso("¿Cuánto cuesta el alquiler de una propiedad así?", "sin_respuesta"),
        Caso("¿Qué opinás de la política económica actual?", "sin_respuesta"),
        Caso("¿Cuál es la tasa de interés de los créditos hipotecarios hoy?", "sin_respuesta"),
        Caso("¿En cuánto tiempo se va a vender?", "sin_respuesta"),
    ]
    return casos


def _cita_correcta(citas: list[str], esperada: str) -> bool:
    if esperada.startswith("Met"):
        return any(c.startswith(esperada) or c.startswith("Met") for c in citas)
    return esperada in citas


async def _corrida(casos: list[Caso], indice: Any, umbral: float) -> dict[str, Any]:
    from tasador.llm import LlmClient
    from tasador.rag import qa
    from tasador.rag.embedder import get_embedder
    from tasador.settings import get_settings

    s = get_settings()
    cliente = LlmClient()
    filas: list[dict[str, Any]] = []
    try:
        for c in casos:
            r = await qa.responder(
                c.pregunta,
                indice,
                embedder=get_embedder(),
                cliente=cliente,
                task=s.qa_task,
                umbral=umbral,
                umbral_lexico=s.qa_umbral_lexico,
                k=s.qa_k,
            )
            citas = [f.id for f in r.citas]
            ok_cita = c.cita_esperada is None or (
                not r.rechazada and _cita_correcta(citas, c.cita_esperada)
            )
            ok_cifra = c.cifra_esperada is None or (
                not r.rechazada and c.cifra_esperada in r.respuesta
            )
            filas.append(
                {
                    "grupo": c.grupo,
                    "pregunta": c.pregunta,
                    "rechazada": r.rechazada,
                    "coseno": r.mejor_coseno,
                    "ok_rechazo": r.rechazada == (c.grupo == "sin_respuesta"),
                    "ok_cita": ok_cita,
                    "ok_cifra": ok_cifra,
                    "citas": citas,
                    "respuesta": r.respuesta[:160],
                    "motivo": r.motivo,
                    "cost": r.cost_usd,
                }
            )
    finally:
        await cliente.close()

    con = [f for f in filas if f["grupo"] != "sin_respuesta"]
    sin = [f for f in filas if f["grupo"] == "sin_respuesta"]
    return {
        "exactitud_rechazo": statistics.fmean(f["ok_rechazo"] for f in filas),
        "precision_citas": statistics.fmean(f["ok_cita"] for f in con) if con else 0.0,
        "cifras_correctas": statistics.fmean(
            f["ok_cifra"] for f in con if f["ok_cifra"] is not None
        ),
        "coseno_con_respuesta_p10": sorted(f["coseno"] for f in con)[max(0, len(con) // 10)]
        if con
        else 0,
        "coseno_sin_respuesta_p90": sorted(f["coseno"] for f in sin)[
            min(len(sin) - 1, int(0.9 * len(sin)))
        ]
        if sin
        else 0,
        "costo_usd": sum(f["cost"] for f in filas),
        "filas": filas,
    }


async def _main(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from tasador.db.base import get_session_factory
    from tasador.db.models import Organization, Report
    from tasador.eval.componentes import Medicion, guardar
    from tasador.rag import qa
    from tasador.rag.embedder import get_embedder
    from tasador.settings import get_settings
    from tasador.v1.reports import obtener_informe

    s = get_settings()
    async with get_session_factory()() as session:
        if args.informe:
            informe_db = (
                await session.execute(select(Report).where(Report.id == uuid.UUID(args.informe)))
            ).scalar_one()
        else:
            informe_db = (
                await session.execute(
                    select(Report)
                    .where(Report.status == "SUCCEEDED")
                    .order_by(Report.created_at.desc())
                    .limit(1)
                )
            ).scalar_one()
        org = (
            await session.execute(select(Organization).where(Organization.id == informe_db.org_id))
        ).scalar_one()
        informe = await obtener_informe(informe_db.id, session, org, incluir="descartados")

        embedder = get_embedder()
        await embedder.precargar()
        hechos = qa.fragmentos_del_informe(informe)
        met = qa.fragmentos_de_metodologia()
        indice = await qa.Indice.construir([*hechos, *met], embedder)
        casos = casos_de(informe)
        print(
            f"informe {informe_db.id} · {len(hechos)} hechos + {len(met)} de metodología · "
            f"{len(casos)} preguntas · umbral {args.umbral}\n"
        )

        corridas = []
        for i in range(args.corridas):
            r = await _corrida(casos, indice, args.umbral)
            corridas.append(r)
            print(
                f"corrida {i + 1}: rechazo {r['exactitud_rechazo']:.2f} · "
                f"citas {r['precision_citas']:.2f} · cifras {r['cifras_correctas']:.2f} · "
                f"coseno con-resp p10 {r['coseno_con_respuesta_p10']:.3f} · "
                f"sin-resp p90 {r['coseno_sin_respuesta_p90']:.3f} · USD {r['costo_usd']:.4f}"
            )
            if args.detalle:
                for f in r["filas"]:
                    marca = "✓" if (f["ok_rechazo"] and f["ok_cita"] and f["ok_cifra"]) else "✗"
                    que = "RECHAZO" if f["rechazada"] else f["citas"]
                    print(
                        f"  {marca} [{f['grupo'][:4]}] {f['pregunta'][:60]:<60} "
                        f"coseno {f['coseno']:.3f} {que} {f['motivo'] or ''}"
                    )

        med = {
            k: statistics.median(c[k] for c in corridas)
            for k in ("exactitud_rechazo", "precision_citas", "cifras_correctas")
        }
        print(f"\nmediana de {args.corridas}: {med}")
        if args.guardar:
            for k, v in med.items():
                await guardar(
                    session,
                    Medicion(
                        componente="qa",
                        metrica=k,
                        valor=Decimal(str(round(v, 4))),
                        n=len(casos),
                        task=s.qa_task,
                        prompt="qa/v1",
                    ),
                )
            print("→ guardado en eval.component_runs")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--informe", default=None)
    ap.add_argument("--corridas", type=int, default=1)
    ap.add_argument("--umbral", type=float, default=None)
    ap.add_argument("--detalle", action="store_true")
    ap.add_argument("--guardar", action="store_true")
    args = ap.parse_args()
    if args.umbral is None:
        from tasador.settings import get_settings

        args.umbral = get_settings().qa_umbral
    return run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
