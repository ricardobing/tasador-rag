"""Etapa 3 — el grafo de agentes.

  config.py   qué usa cada nodo (YAML, no código)
  state.py    lo que viaja entre nodos
  graph.py    la topología, armada desde la configuración
  runner.py   corre el grafo para un informe y persiste el resultado
  nodes/      la implementación de cada nodo

Regla madre (ADR-002): el LLM extrae, clasifica, juzga y redacta.
**Nunca calcula el precio.**
"""

from __future__ import annotations

from tasador.agents.config import AgentsConfig, NodeConfig, load_agents_config
from tasador.agents.state import Candidate, ReportState, estado_inicial

__all__ = [
    "AgentsConfig",
    "Candidate",
    "NodeConfig",
    "ReportState",
    "estado_inicial",
    "load_agents_config",
]
