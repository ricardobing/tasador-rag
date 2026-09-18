"""Nodo 9 — `write_report`. El LLM redacta; los números ya vinieron hechos.

La única tarea del modelo acá es explicar. Recibe la valuación completa, la
tabla de comparables con sus motivos de exclusión, y escribe markdown. No
calcula: si calculara, el precio dependería de un modelo de lenguaje y todo el
ADR-002 se caería por el último eslabón.

Lo que lo hace sostenible no es el prompt —un prompt es una intención— sino el
nodo 10, que extrae cada cifra del texto y la busca en los datos. El prompt
dice qué se espera; el crítico lo hace cumplir.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeError, NodeResult
from tasador.agents.prompts import render
from tasador.agents.state import ReportState
from tasador.llm import LlmClient, LlmError, UsageLedger

log = structlog.get_logger()


def datos_del_informe(state: ReportState) -> dict[str, Any]:
    """Todo lo que el redactor puede citar, y nada más.

    Es también el conjunto contra el que el nodo 10 verifica: lo que no está
    acá no puede aparecer en el informe.
    """
    v = state.get("valuation") or {}
    subject = state.get("subject") or {}
    curacion = state.get("curation") or {}

    usados = [d for d in v.get("detail", []) if d.get("included")]
    excluidos = [d for d in v.get("detail", []) if not d.get("included")]

    motivos: dict[str, int] = {}
    for d in excluidos:
        motivo = d.get("exclusion_reason") or "sin_motivo"
        motivos[motivo] = motivos.get(motivo, 0) + 1

    return {
        "propiedad": {
            "direccion": subject.get("address_raw"),
            "barrio": subject.get("neighborhood_name"),
            "tipo": subject.get("property_type"),
            "ambientes": subject.get("rooms"),
            "superficie_total_m2": subject.get("surface_total"),
            "superficie_cubierta_m2": subject.get("surface_covered"),
            "superficie_ponderada_m2": v.get("weighted_surface"),
            "estado": subject.get("condition"),
            "orientacion": subject.get("orientation"),
            "piso": subject.get("floor_number"),
        },
        "valuacion": {
            "moneda": v.get("currency"),
            "precio_publicacion_sugerido": {
                "minimo": v.get("value_low"),
                "medio": v.get("value_mid"),
                "maximo": v.get("value_high"),
            },
            "rango_de_cierre_esperado": {
                "minimo": v.get("closing_low"),
                "maximo": v.get("closing_high"),
            },
            "usd_por_m2": v.get("price_per_m2"),
            "dispersion": v.get("dispersion"),
        },
        "confianza": {
            "nivel": v.get("confidence"),
            "score": v.get("confidence_score"),
            "notas": v.get("notes") or [],
        },
        "comparables": {
            "encontrados": v.get("comparables_found"),
            "usados": v.get("comparables_used"),
            # ⚠️ UN SOLO conteo de descartes, desglosado por etapa y con la
            # suma explícita. Antes se exponían dos —el total y el de la
            # curaduría— sin decir cómo se relacionaban, y el redactor escribió
            # un párrafo tratando de reconciliarlos que terminó contradiciendo
            # al informe. Si los datos son ambiguos, la prosa sale ambigua:
            # el problema no era del modelo.
            "descartados_total": len(excluidos),
            "descartados_por_curaduria": curacion.get("por_reglas", 0)
            + curacion.get("por_juez", 0),
            "descartados_por_estadistica": len(excluidos)
            - (curacion.get("por_reglas", 0) + curacion.get("por_juez", 0)),
            "motivos_de_descarte": motivos,
            "detalle_usados": [
                {
                    "direccion_o_id": d.get("listing_id"),
                    "precio": d.get("snapshot_price"),
                    "superficie_m2": d.get("snapshot_surface"),
                    "usd_m2_crudo": d.get("raw_price_per_m2"),
                    "usd_m2_ajustado": d.get("adjusted_price_per_m2"),
                }
                for d in usados
            ],
        },
        "curaduria": {
            "por_reglas_automaticas": curacion.get("por_reglas"),
            "por_criterio_del_tasador": curacion.get("por_juez"),
        },
        "contexto_de_mercado": state.get("market_context") or {},
    }


async def write_report(state: ReportState, cfg: NodeConfig) -> NodeResult:
    v = state.get("valuation") or {}
    if not v.get("value_mid"):
        raise NodeError("SIN_VALUACION", "No hay un valor que redactar.")

    datos = datos_del_informe(state)
    critica = (state.get("critique") or {}).get("feedback") if state.get("rehacer") else None

    prompt = render(
        cfg.prompt or "writer/v1",
        datos=json.dumps(datos, ensure_ascii=False, indent=2),
        critica=critica,
    )

    ledger = UsageLedger()
    cliente = LlmClient()
    try:
        texto, uso = await cliente.complete(
            cfg.task or "writer",
            [{"role": "user", "content": prompt}],
            temperature=float(cfg.param("temperature", 0.3)),
            max_tokens=int(cfg.param("max_tokens", 4096)),
            # Un reintento tras un rechazo NO puede volver del caché: sería la
            # misma redacción que ya fue rechazada.
            use_cache=not state.get("rehacer", False),
        )
    except LlmError as e:
        raise NodeError("REDACCION_FALLIDA", str(e)[:300]) from e
    finally:
        await cliente.close()

    ledger.add(uso)
    if not texto.strip():
        raise NodeError("REDACCION_VACIA", "El redactor no devolvió texto.")

    return NodeResult(
        updates={"draft_md": texto, "rehacer": False},
        detail={
            "caracteres": len(texto),
            "secciones": texto.count("\n## "),
            "es_reintento": bool(critica),
        },
        usage=ledger,
    )
