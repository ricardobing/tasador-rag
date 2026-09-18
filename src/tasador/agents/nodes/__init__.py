"""Registro de implementaciones de nodos.

`config/agents.yaml` dice QUÉ nodos hay y en qué orden; este registro dice
CUÁL es el código de cada uno. Un nodo declarado en el YAML sin entrada acá
corre como stub y queda marcado como tal en la traza — nunca falla en
silencio ni se saltea sin dejar rastro.
"""

from __future__ import annotations

from tasador.agents.nodes.base import NodeFn, NodeResult
from tasador.agents.nodes.critic import critic
from tasador.agents.nodes.curate import curate
from tasador.agents.nodes.dedup import dedup_cluster
from tasador.agents.nodes.extract import extract_features
from tasador.agents.nodes.market import market_context
from tasador.agents.nodes.normalize import normalize_subject
from tasador.agents.nodes.pending import stub
from tasador.agents.nodes.render import render_pdf
from tasador.agents.nodes.retrieve import retrieve_candidates
from tasador.agents.nodes.valuation import adjust_and_value
from tasador.agents.nodes.write import write_report

# Solo lo que está construido de verdad.
IMPLEMENTADOS: dict[str, NodeFn] = {
    "normalize_subject": normalize_subject,
    "retrieve_candidates": retrieve_candidates,
    "extract_features": extract_features,
    "dedup_cluster": dedup_cluster,
    "curate": curate,
    "adjust_and_value": adjust_and_value,
    "market_context": market_context,
    "write_report": write_report,
    "critic": critic,
    "render_pdf": render_pdf,
}


def implementacion(node_id: str) -> tuple[NodeFn, bool]:
    """Devuelve (función, está_implementado)."""
    if fn := IMPLEMENTADOS.get(node_id):
        return fn, True
    return stub(node_id), False


__all__ = ["IMPLEMENTADOS", "NodeFn", "NodeResult", "implementacion"]
