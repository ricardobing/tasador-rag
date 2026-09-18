"""El grafo — LangGraph con checkpointer en Postgres (ADR-001).

La topología **no está escrita acá**: sale de `config/agents.yaml`. Este módulo
lee los nodos habilitados, los ordena por `seq`, los encadena y les pone la
condición de salida temprana. Apagar un nodo, reordenarlos o agregar uno nuevo
es editar el YAML.

Por qué LangGraph y no una crew: esto es una máquina de estados con
reintentos, ciclos y salidas tempranas, y el estado se persiste después de
cada nodo. Si el worker muere en el nodo 6, al reintentar retoma en el 6 y no
vuelve a pagar los nodos 2 a 5, que son los caros.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from tasador.agents.config import AgentsConfig, load_agents_config
from tasador.agents.nodes import implementacion
from tasador.agents.nodes.base import instrument
from tasador.agents.state import ReportState

log = structlog.get_logger()

# Estados que cortan el grafo. `INSUFFICIENT_DATA` corta igual que `FAILED`,
# pero no es un error: es la respuesta honesta cuando no hay comparables. Lo
# importante es que corta ANTES de los nodos 8-11, que son los que gastan.
TERMINALES = frozenset({"FAILED", "INSUFFICIENT_DATA", "CANCELLED"})


def _sigue_o_corta(siguiente: str, reintenta_a: str | None = None) -> Any:
    """Router de un nodo: cortar, volver atrás, o seguir.

    `reintenta_a` es lo que hace posible el ciclo del crítico (doc 04, nodo 10):
    si rechaza el informe, vuelve al redactor con la crítica como feedback. El
    tope de reintentos NO está acá sino en el nodo, que es quien sabe cuántas
    veces rechazó — un router que cuenta vueltas es un bucle esperando a pasar.
    """

    def _router(state: ReportState) -> str:
        if state.get("status") in TERMINALES:
            return END
        if reintenta_a and state.get("rehacer"):
            return reintenta_a
        return siguiente

    return _router


def build_graph(cfg: AgentsConfig | None = None) -> Any:
    """Arma el grafo (sin compilar) desde la configuración."""
    cfg = cfg or load_agents_config()
    nodos = cfg.enabled
    if not nodos:
        raise ValueError("agents.yaml no tiene ningún nodo habilitado.")

    g: Any = StateGraph(ReportState)

    for n in nodos:
        fn, construido = implementacion(n.id)
        g.add_node(n.id, instrument(n, fn))
        if not construido:
            log.debug("nodo sin implementar, corre como stub", node=n.id, seq=n.seq)

    ids = {n.id for n in nodos}
    g.add_edge(START, nodos[0].id)

    for actual, siguiente in pairwise(nodos):
        vuelve = actual.params.get("reintenta_a")
        if vuelve and vuelve not in ids:
            raise ValueError(f"'{actual.id}' reintenta a '{vuelve}', que no está habilitado")
        destinos = {siguiente.id: siguiente.id, END: END}
        if vuelve:
            destinos[vuelve] = vuelve
        # Condicional en TODOS los pasos, no solo después del 7: cualquier
        # nodo con `on_error: fail` puede cortar, y seguir gastando después de
        # una falla es tirar plata.
        g.add_conditional_edges(actual.id, _sigue_o_corta(siguiente.id, vuelve), destinos)

    # El último nodo también puede reintentar (es el caso del crítico cuando
    # `render_pdf` está apagado).
    ultimo = nodos[-1]
    if vuelve := ultimo.params.get("reintenta_a"):
        if vuelve not in ids:
            raise ValueError(f"'{ultimo.id}' reintenta a '{vuelve}', que no está habilitado")
        g.add_conditional_edges(ultimo.id, _sigue_o_corta(END, vuelve), {vuelve: vuelve, END: END})
    else:
        g.add_edge(ultimo.id, END)
    return g


def checkpointer_dsn(database_url: str) -> str:
    """La URL de SQLAlchemy no le sirve al checkpointer.

    SQLAlchemy pide `postgresql+psycopg://` para elegir el driver; psycopg
    directo lo rechaza. Es la misma clase de detalle que ya nos mordió con
    LiteLLM y `DATABASE_URL`.
    """
    return database_url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def compile_graph(checkpointer: Any = None, cfg: AgentsConfig | None = None) -> Any:
    return build_graph(cfg).compile(checkpointer=checkpointer)
