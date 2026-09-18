"""Nodos todavía no construidos.

Son pass-through explícitos, no simulaciones. Cada uno deja en
`report_events.detail` la marca `"stub": true` y en qué fase se construye.

El criterio: un nodo que no existe tiene que **verse** que no existe. Un stub
que devuelve datos inventados para que el pipeline "ande" es la forma más
rápida de creer que el sistema funciona cuando no funciona.
"""

from __future__ import annotations

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeFn, NodeResult
from tasador.agents.state import ReportState

# En qué fase del plan se construye cada uno.
FASE = {
    "normalize_subject": "Fase 2",
    "retrieve_candidates": "Fase 2",
    "ondemand_capture": "Fase 5+",
    "extract_features": "Fase 3",
    "dedup_cluster": "Fase 4",
    "curate": "Fase 4",
    "market_context": "Fase 6",
    "write_report": "Fase 6",
    "critic": "Fase 6",
    "render_pdf": "Etapa 4",
}


def stub(node_id: str) -> NodeFn:
    async def _stub(_state: ReportState, cfg: NodeConfig) -> NodeResult:
        return NodeResult(
            updates={},
            detail={
                "stub": True,
                "pendiente": FASE.get(node_id, "sin planificar"),
                "descripcion": cfg.descripcion,
            },
        )

    _stub.__name__ = f"stub_{node_id}"
    return _stub
