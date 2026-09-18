"""Reescribe `prompts/bundle.lock.json`.

Se corre cuando cambia `config/agents.yaml` o el texto de cualquier prompt. El
lock se versiona a propósito: es lo que hace que un cambio de prompt entre al
diff del PR y obligue a correr el backtest antes de mergear (doc 09 §5).

Hay un test que falla si el lock del repo quedó viejo — porque un artefacto de
auditoría desactualizado es peor que no tenerlo: esconde exactamente el cambio
que existía para mostrar.
"""

from __future__ import annotations

import json

from tasador.agents.config import load_agents_config, write_bundle_lock


def main() -> int:
    antes = json.loads(
        (__import__("pathlib").Path("prompts/bundle.lock.json")).read_text(encoding="utf-8")
    ).get("bundle_hash")
    lock = write_bundle_lock()
    ahora = lock["bundle_hash"]

    if antes == ahora:
        print(f"bundle.lock.json ya estaba al día  ({ahora})")
        return 0

    print(f"bundle_hash: {antes} -> {ahora}")
    for nodo, d in sorted(lock["nodes"].items()):
        print(f"  {nodo:<20} {d['task']:<10} {d['prompt']}")
    print(f"\nversión de agents.yaml: {load_agents_config().version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
