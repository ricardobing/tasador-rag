"""Mide el nodo 4 contra el golden set. Exactitud POR CAMPO (doc 09 §3.3).

Es la diferencia entre "el extractor anda" y "el extractor acierta el 92% de
los `condition`". Lo primero es una impresión; lo segundo se puede defender.

Distingue tres cosas que suelen mezclarse y que tienen consecuencias distintas:

  ACIERTO      dijo lo que el aviso dice
  ALUCINACIÓN  el aviso NO lo dice y el modelo lo completó  ← el peor error:
               enciende un coeficiente sobre un dato inventado
  OMISIÓN      el aviso lo dice y el modelo lo dejó en null ← cuesta precisión
               pero no miente

    uv run python scripts/eval_extraccion.py
    uv run python scripts/eval_extraccion.py --task extractor_backup   # comparar
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy import select

from tasador.agents.config import load_agents_config
from tasador.agents.nodes.extract import (
    CONF_CITA_FALSA,
    _texto_del_aviso,
    extraer_lotes,
    verificar_citas,
)
from tasador.agents.state import Candidate
from tasador.cli import run
from tasador.db.base import get_session_factory
from tasador.db.models import Listing
from tasador.eval.golden import cargar as cargar_golden
from tasador.eval.golden import resumen as resumen_golden
from tasador.llm import LlmClient


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
            "listing_id": li.source_id,  # se usa el source_id como ref: es estable
            "source": li.source,
            "address": li.address_raw,
            "description": li.description,
            "price": str(li.price),
            "currency": li.currency or "USD",
            "rooms": (li.raw or {}).get("rooms"),
            "surface_total": (li.raw or {}).get("surface_total"),
            "field_confidence": {},
        }  # type: ignore[misc]
        for li in filas
    }


async def _main(args: argparse.Namespace) -> int:
    # TODOS los golden sets, y solo lo revisado. Ver tasador.eval.golden.
    avisos_golden, conteos = cargar_golden()
    print(resumen_golden(conteos))
    ids = [str(a["id"]) for a in avisos_golden]
    cands = await _candidatos(ids)

    faltantes = [i for i in ids if i not in cands]
    if faltantes:
        print(f"⚠️  {len(faltantes)} avisos del golden set no están en la base: {faltantes[:5]}")
    presentes = [a for a in avisos_golden if str(a["id"]) in cands]
    if not presentes:
        raise SystemExit("Ningún aviso del golden set está en la base.")

    cfg = load_agents_config().node("extract_features")
    task = args.task or cfg.task or "extractor"
    tam = int(args.batch or cfg.param("batch_size", 6))
    plantilla = args.prompt or cfg.prompt or "extractor/v1"
    pp = {"reasoning": {"enabled": False}} if args.sin_razonamiento else None
    cliente = LlmClient()

    print(
        f"Golden set: {len(presentes)} avisos · tarea: {task} · "
        f"prompt: {plantilla} · lote: {tam}" + (" · SIN razonamiento" if pp else "")
    )
    print()

    pendientes = [cands[str(a["id"])] for a in presentes]
    if pp:
        cfg = cfg.model_copy(update={"params": {**cfg.params, "provider_params": pp}})
    try:
        # MISMO camino que el nodo, incluido el reintento de omitidos. Un eval
        # que llama a `structured()` pelado mide un fragmento del sistema y no
        # avisa que le falta la mitad.
        resultados, _lotes = await extraer_lotes(
            cliente,
            cfg,
            pendientes,
            task=task,
            plantilla=plantilla,
            tamano=tam,
            use_cache=False,
        )
    finally:
        await cliente.close()

    for salida, usos in resultados:
        print(
            f"  lote -> devolvió {len(salida.avisos) if salida else 0:>2} · "
            f"tokens_out={sum(u.tokens_out for u in usos):>6}"
        )
    extraidos = {f.listing_ref: f for s, _ in resultados if s for f in s.avisos}
    costo = float(sum(u.cost_usd for _, usos in resultados for u in usos))

    # ── Comparación campo por campo ──────────────────────────────────────
    marcador: dict[str, Counter[str]] = {}
    errores: list[str] = []
    citas_falsas = 0

    for a in presentes:
        ref = str(a["id"])
        f = extraidos.get(ref)
        if f is None:
            errores.append(f"{a['ref']}: el modelo NO devolvió este aviso")
            continue

        conf = verificar_citas(f, _texto_del_aviso(cands[ref]))
        citas_falsas += sum(1 for v in conf.values() if v == CONF_CITA_FALSA)
        no_eval = set(a.get("no_evaluar") or [])

        for campo, esperado in (a.get("esperado") or {}).items():
            if campo in no_eval:
                continue
            obtenido = getattr(f, campo, None)
            m = marcador.setdefault(campo, Counter())
            if obtenido == esperado:
                m["acierto"] += 1
            elif esperado is None:
                m["alucinacion"] += 1
                errores.append(f"ALUCINÓ  {a['ref']}  {campo}={obtenido!r} (el aviso no lo dice)")
            elif obtenido is None:
                m["omision"] += 1
                errores.append(f"omitió   {a['ref']}  {campo}: esperaba {esperado!r}")
            else:
                m["error"] += 1
                errores.append(f"ERRÓ    {a['ref']}  {campo}={obtenido!r}, esperaba {esperado!r}")

    # ── Reporte ──────────────────────────────────────────────────────────
    print(f"{'CAMPO':<16} {'N':>3} {'ACIERTO':>8} {'ALUCIN':>7} {'OMITE':>6} {'ERRA':>5}")
    print("-" * 52)
    total = Counter()
    for campo in sorted(marcador, key=lambda c: -sum(marcador[c].values())):
        m = marcador[campo]
        n = sum(m.values())
        total.update(m)
        print(
            f"{campo:<16} {n:>3} {m['acierto'] / n:>7.0%} "
            f"{m['alucinacion']:>7} {m['omision']:>6} {m['error']:>5}"
        )

    n = sum(total.values())
    print("-" * 52)
    print(
        f"{'TOTAL':<16} {n:>3} {total['acierto'] / n:>7.0%} "
        f"{total['alucinacion']:>7} {total['omision']:>6} {total['error']:>5}"
    )
    print(f"\nCitas que NO estaban en el aviso: {citas_falsas}")
    print(f"Costo de esta evaluación: USD {costo:.6f}")

    if errores:
        print(f"\n{'─' * 60}\nDETALLE ({len(errores)})\n")
        for e in errores:
            print(f"  {e}")

    # Objetivo de doc 09 §3.3: > 92% en los campos que mueven el precio.
    exactitud = total["acierto"] / n
    print(
        f"\n{'✅' if exactitud >= 0.92 else '⚠️'}  Exactitud global {exactitud:.1%} "
        f"(objetivo doc 09 §3.3: ≥ 92%)"
    )

    if args.guardar:
        # Sin esto el número se imprime y se pierde, que es lo que pasaba hasta
        # el 14/08: el 68% del nodo 4 vivía en un documento y no en la base, así
        # que "mejoró el prompt" no se podía responder con una consulta.
        from decimal import Decimal

        from tasador.db.base import get_session_factory
        from tasador.eval.componentes import Medicion, comparar, guardar, ultimas

        medicion = Medicion(
            componente="extraccion",
            metrica="exactitud",
            valor=Decimal(str(round(exactitud, 4))),
            n=n,
            prompt=plantilla,
            task=task,
            detalle={c: dict(m) for c, m in marcador.items()},
        )
        async with get_session_factory()() as session:
            # Las previas se leen ANTES de guardar: si no, esta corrida entra
            # en su propia mediana y cualquier caída se diluye sola.
            previas = await ultimas(session, medicion.componente, medicion.metrica)
            fila = await guardar(session, medicion)

        print()
        print("\n".join(comparar(medicion, previas).lineas))
        print(f"\nGuardada como eval.component_runs.id = {fila.id}")

    return 0


def main() -> None:
    _utf8()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", default=None, help="probar otra tarea, ej. extractor_backup")
    ap.add_argument("--batch", type=int, default=None, help="pisar el batch_size del YAML")
    ap.add_argument("--prompt", default=None, help="probar otra versión, ej. extractor/v2")
    ap.add_argument(
        "--guardar",
        action="store_true",
        help="registrar el resultado en eval.component_runs y comparar con el anterior",
    )
    ap.add_argument(
        "--sin-razonamiento",
        action="store_true",
        help="apagar el thinking del modelo (parámetro `reasoning` de OpenRouter)",
    )
    raise SystemExit(run(_main(ap.parse_args())))


if __name__ == "__main__":
    main()
