"""Mide el nodo 6 contra el golden set (doc 09 §3.3).

La métrica que importa acá NO es la exactitud global: es el **recall** sobre lo
que hay que descartar, y la **precisión** de los descartes. Y las dos fallas
cuestan cosas distintas:

  FALSO NEGATIVO  no descartó un pozo → su precio entra a la mediana y sube
                  la tasación de un usado
  FALSO POSITIVO  descartó un comparable legítimo → menos muestra, y si baja
                  de 5 el informe no se emite

Doc 09 §3.3 pide recall > 0,90 sobre los que deben descartarse.

    uv run python scripts/eval_curaduria.py
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from sqlalchemy import select

from tasador.agents.config import load_agents_config
from tasador.agents.nodes.curate import LoteCurado, _payload, _resumen_sujeto, aplicar_veredicto
from tasador.agents.prompts import render
from tasador.agents.state import Candidate
from tasador.cli import run
from tasador.db.base import get_session_factory
from tasador.db.models import Listing
from tasador.eval.golden import cargar as cargar_golden
from tasador.eval.golden import resumen as resumen_golden
from tasador.llm import AVISO_INJECTION, LlmClient


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")


async def _candidatos(ids: list[str]) -> dict[str, Candidate]:
    async with get_session_factory()() as session:
        filas = (
            (await session.execute(select(Listing).where(Listing.source_id.in_(ids))))
            .scalars()
            .all()
        )
    return {
        li.source_id: {
            "listing_id": li.source_id,
            "source": li.source,
            "address": li.address_raw,
            "description": li.description,
            "price": str(li.price),
            "currency": li.currency or "USD",
            "rooms": (li.raw or {}).get("rooms"),
            "surface_total": (li.raw or {}).get("surface_total"),
            "included": True,
        }  # type: ignore[misc]
        for li in filas
    }


async def _main(args: argparse.Namespace) -> int:
    golden, conteos = cargar_golden()
    print(resumen_golden(conteos))
    cands = await _candidatos([str(a["id"]) for a in golden])
    presentes = [a for a in golden if str(a["id"]) in cands]

    cfg = load_agents_config().node("curate")
    task = args.task or cfg.task or "judge"
    plantilla = args.prompt or cfg.prompt or "curator/v1"
    sujeto = {
        "property_type": "departamento",
        "neighborhood_name": "Belgrano",
        "rooms": 3,
        "surface_total": "95",
    }

    print(f"Golden set: {len(presentes)} avisos · tarea: {task} · prompt: {plantilla}\n")

    cliente = LlmClient()
    tam = int(cfg.param("max_llm_batch", 20))
    veredictos: dict[str, Any] = {}
    costo = 0.0
    try:
        for i in range(0, len(presentes), tam):
            lote = [cands[str(a["id"])] for a in presentes[i : i + tam]]
            prompt = render(
                plantilla,
                aviso_injection=AVISO_INJECTION,
                sujeto=_resumen_sujeto(sujeto),
                avisos=[_payload(c) for c in lote],
            )
            salida, usos = await cliente.structured(
                task,
                [{"role": "user", "content": prompt}],
                LoteCurado,
                temperature=0.0,
                max_tokens=int(cfg.param("max_tokens", 8192)),
                use_cache=False,
            )
            costo += float(sum(u.cost_usd for u in usos))
            print(f"  lote de {len(lote):>2} -> {len(salida.veredictos):>2} veredictos")
            for v in salida.veredictos:
                veredictos[v.listing_ref] = v
    finally:
        await cliente.close()

    # ── Matriz de confusión ──────────────────────────────────────────────
    vp = vn = fp = fn = 0
    sin_cita = 0
    detalle: list[str] = []

    for a in presentes:
        ref = str(a["id"])
        esperado = a.get("descarte")
        v = veredictos.get(ref)
        if v is None:
            detalle.append(f"?  {a['ref']}: el juez no lo devolvió")
            continue

        # Se mide lo que el sistema APLICA, no lo que el modelo dice: un
        # descarte cuya cita no está en el aviso no se aplica.
        aplicado = aplicar_veredicto(cands[ref], v)
        if v.descartar and aplicado is None:
            sin_cita += 1

        if esperado and aplicado == esperado:
            vp += 1
        elif esperado and aplicado:
            vp += 1  # descartó, pero con otro motivo
            detalle.append(f"~  {a['ref']}: descartó por {aplicado}, esperaba {esperado}")
        elif esperado and not aplicado:
            fn += 1
            detalle.append(f"FN {a['ref']}: NO descartó, esperaba {esperado}")
        elif not esperado and aplicado:
            fp += 1
            detalle.append(f"FP {a['ref']}: descartó por {aplicado}, NO correspondía")
        else:
            vn += 1

    a_descartar = vp + fn
    recall = vp / a_descartar if a_descartar else 1.0
    precision = vp / (vp + fp) if (vp + fp) else 1.0

    print(f"\n{'':>18}{'descartó':>10}{'no descartó':>13}")
    print(f"{'debía descartar':<18}{vp:>10}{fn:>13}")
    print(f"{'debía quedarse':<18}{fp:>10}{vn:>13}")
    print(f"\nRecall    {recall:>6.0%}   (objetivo doc 09 §3.3: > 90%)")
    print(f"Precisión {precision:>6.0%}   (un falso positivo borra un comparable legítimo)")
    print(f"Descartes sin cita verificable, NO aplicados: {sin_cita}")
    print(f"Costo: USD {costo:.6f}")

    if detalle:
        print(f"\n{'─' * 60}")
        for d in detalle:
            print(f"  {d}")

    ok = recall >= 0.90 and precision >= 0.90
    print(f"\n{'✅' if ok else '⚠️'}  recall {recall:.0%} · precisión {precision:.0%}")
    return 0


def main() -> None:
    _utf8()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", default=None)
    ap.add_argument("--prompt", default=None)
    raise SystemExit(run(_main(ap.parse_args())))


if __name__ == "__main__":
    main()
