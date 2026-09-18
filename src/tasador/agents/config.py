"""Configuración de los agentes — qué usa cada nodo, leído de YAML.

El código pide `node("extract_features")` y recibe un `NodeConfig`. Nunca
nombra un modelo, un proveedor ni una versión de prompt: eso vive en
`config/agents.yaml` (nodo -> tarea) y en `config/litellm.yaml`
(tarea -> proveedor).

La validación es la parte que importa: un YAML mal escrito tiene que explotar
al ARRANCAR, con un mensaje claro, y no seis nodos más adelante en medio de un
informe que el cliente está esperando.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = structlog.get_logger()

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "agents.yaml"
PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

NodeKind = Literal["deterministic", "llm", "hybrid", "crew", "io"]
OnError = Literal["fail", "degrade", "skip"]

# ADR-002, en código y no solo en la doc: el precio no sale de un LLM. Este
# nodo no puede tener modelo, por más que alguien se lo agregue al YAML.
SIN_MODELO = frozenset({"adjust_and_value"})


class NodeConfig(BaseModel):
    """Un nodo del grafo. Inmutable: se lee una vez y no se toca."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    seq: int = Field(ge=1)
    kind: NodeKind
    descripcion: str = ""
    # La TAREA, no el modelo. `None` = este nodo no llama a ningún LLM.
    task: str | None = None
    prompt: str | None = None
    enabled: bool = True
    on_error: OnError = "fail"
    timeout_seconds: int = Field(default=120, ge=1)
    max_attempts: int = Field(default=2, ge=1, le=5)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _coherencia(self) -> NodeConfig:
        if self.id in SIN_MODELO and self.task is not None:
            raise ValueError(
                f"El nodo '{self.id}' NO puede tener un modelo asignado (ADR-002: el "
                f"precio nunca sale de un LLM). Sacale `task: {self.task}` del YAML."
            )
        if self.kind in ("llm", "crew") and not self.task:
            raise ValueError(f"El nodo '{self.id}' es de tipo {self.kind} y no declara `task`.")
        if self.task and not self.prompt:
            raise ValueError(
                f"El nodo '{self.id}' declara `task: {self.task}` pero no `prompt`. "
                f"Un modelo sin prompt versionado no es reproducible (doc 04 §5)."
            )
        return self

    def param(self, clave: str, default: Any = None) -> Any:
        return self.params.get(clave, default)


class AgentsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    nodes: tuple[NodeConfig, ...]
    # Hash del YAML + de los prompts referenciados. Es el
    # `prompt_bundle_version` que se estampa en cada informe.
    bundle_hash: str

    @model_validator(mode="after")
    def _sin_huecos(self) -> AgentsConfig:
        seqs = [n.seq for n in self.nodes]
        if len(set(seqs)) != len(seqs):
            raise ValueError(f"Hay `seq` repetidos en agents.yaml: {sorted(seqs)}")
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Hay `id` repetidos en agents.yaml: {sorted(ids)}")
        return self

    @property
    def enabled(self) -> tuple[NodeConfig, ...]:
        """Los nodos que realmente corren, en orden."""
        return tuple(sorted((n for n in self.nodes if n.enabled), key=lambda n: n.seq))

    def node(self, node_id: str) -> NodeConfig:
        for n in self.nodes:
            if n.id == node_id:
                return n
        conocidos = ", ".join(sorted(n.id for n in self.nodes))
        raise KeyError(f"No existe el nodo '{node_id}' en agents.yaml. Hay: {conocidos}")

    def tasks_in_use(self) -> set[str]:
        """Las tareas que este bundle necesita que LiteLLM tenga declaradas."""
        return {n.task for n in self.enabled if n.task}


def _bundle_hash(raw: bytes, nodes: list[dict[str, Any]], prompts_dir: Path) -> str:
    """Hash del YAML **y del contenido de los prompts que referencia**.

    Hashear solo el YAML sería una trampa: cambiar el texto de
    `prompts/extractor/v1.jinja` sin tocar el nombre dejaría el mismo
    `prompt_bundle_version` y dos corridas incomparables parecerían iguales.
    """
    h = hashlib.sha256(raw)
    for nombre in sorted({n["prompt"] for n in nodes if n.get("prompt")}):
        archivo = prompts_dir / f"{nombre}.jinja"
        # Un prompt que todavía no existe se hashea como ausente, a propósito:
        # cuando se escriba, el bundle cambia y el backtest lo nota.
        h.update(nombre.encode())
        h.update(archivo.read_bytes() if archivo.exists() else b"<ausente>")
    return h.hexdigest()[:12]


def _override_de_entorno(node_id: str) -> str | None:
    """`TASADOR_TASK_<NODO>` pisa la tarea de ese nodo.

    Para iterar: `TASADOR_TASK_CURATE=flash` apunta el nodo 6 al modelo barato
    de desarrollo sin tocar un archivo versionado, y sacando la variable vuelve
    a lo que dice el YAML. Es también el mecanismo para que un tenant que no
    quiera cierto proveedor corra con otro sin un build propio.

    Pasa por la MISMA validación que el YAML: intentar darle un modelo al
    nodo 7 falla igual (ADR-002).
    """
    return os.environ.get(f"TASADOR_TASK_{node_id.upper()}") or None


@lru_cache(maxsize=4)
def load_agents_config(path: str | None = None) -> AgentsConfig:
    p = Path(path) if path else CONFIG_PATH
    raw = p.read_bytes()
    data: dict[str, Any] = yaml.safe_load(raw.decode("utf-8"))

    defaults: dict[str, Any] = data.get("defaults") or {}
    crudos: list[dict[str, Any]] = data["nodes"]

    nodes = []
    for n in crudos:
        # Los defaults se aplican solo a lo que el nodo no declara.
        completo = {**defaults, **n}
        if (override := _override_de_entorno(str(n["id"]))) is not None:
            log.warning(
                "tarea pisada por entorno",
                node=n["id"],
                yaml=completo.get("task"),
                entorno=override,
            )
            completo["task"] = override
        nodes.append(NodeConfig(**completo))

    return AgentsConfig(
        version=data["version"],
        nodes=tuple(nodes),
        bundle_hash=_bundle_hash(raw, crudos, PROMPTS_DIR),
    )


def write_bundle_lock(path: Path | None = None) -> dict[str, Any]:
    """Escribe `prompts/bundle.lock.json`: qué versión de prompt usa cada nodo.

    Es el artefacto que hace auditable un cambio de prompt: entra al diff del
    PR y obliga a correr el backtest antes de mergear (doc 09 §5).
    """
    cfg = load_agents_config()
    lock = {
        "agents_version": cfg.version,
        "bundle_hash": cfg.bundle_hash,
        "nodes": {n.id: {"task": n.task, "prompt": n.prompt} for n in cfg.enabled if n.task},
    }
    destino = path or (PROMPTS_DIR / "bundle.lock.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return lock
