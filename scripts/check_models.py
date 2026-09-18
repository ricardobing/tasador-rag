"""Verifica que cada TAREA declarada en config/litellm.yaml responda de verdad.

No infiere: llama. Una llamada mínima por tarea, con el costo real que reporta
el gateway. Hay que correrlo cada vez que se toca config/litellm.yaml, porque
un modelo que dejó de existir en el proveedor no se nota hasta que un informe
falla en producción.

Uso (desde Windows, con el stack de dev levantado):

    uv run python scripts/check_models.py

El puerto 4000 lo publica docker-compose.dev.yml. Desde dentro de la red de
Docker, el host es `litellm`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

# Prompt deliberadamente mínimo: se paga por token y esto se corre seguido.
PING = "Respondé únicamente con la palabra: ok"


def _base_url(cli: str | None) -> str:
    if cli:
        return cli.rstrip("/")
    # Desde el host, `litellm` no resuelve: es un nombre de la red de Docker.
    env = os.environ.get("LITELLM_BASE_URL", "")
    if env and "//litellm" not in env:
        return env.rstrip("/")
    return "http://127.0.0.1:4000"


def _master_key() -> str:
    if key := os.environ.get("LITELLM_MASTER_KEY"):
        return key
    from pathlib import Path

    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("LITELLM_MASTER_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("Falta LITELLM_MASTER_KEY (ni en el entorno ni en .env)")


def tasks(client: httpx.Client) -> list[str]:
    """Las tareas son los `model_name` del YAML. El código nunca ve al proveedor."""
    data = client.get("/v1/models").raise_for_status().json()
    return sorted({m["id"] for m in data["data"]})


def probe(client: httpx.Client, task: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": task,
                "messages": [{"role": "user", "content": PING}],
                "max_tokens": 512,
                "temperature": 0,
                # Sin esto, la segunda corrida devuelve el caché de Redis en
                # 24 ms y no prueba absolutamente nada: un modelo caído
                # seguiría dando "ok". Una verificación cacheada no es una
                # verificación.
                "cache": {"no-cache": True},
            },
            timeout=120,
        )
    except httpx.HTTPError as e:
        return {"task": task, "ok": False, "error": type(e).__name__, "ms": 0}

    ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code != 200:
        detalle = r.text[:160].replace("\n", " ")
        return {"task": task, "ok": False, "error": f"HTTP {r.status_code}: {detalle}", "ms": ms}

    body = r.json()
    usage = body.get("usage") or {}
    texto = (body["choices"][0]["message"].get("content") or "").strip()
    return {
        "task": task,
        "ok": bool(texto),
        # El modelo que atendió: es el ÚNICO lugar del sistema donde aparece un
        # nombre de proveedor, y es informativo.
        "modelo": body.get("model", "?"),
        "respuesta": texto[:40] or "(vacía)",
        "tok_in": usage.get("prompt_tokens", 0),
        "tok_out": usage.get("completion_tokens", 0),
        "costo": float(r.headers.get("x-litellm-response-cost") or 0.0),
        "ms": ms,
    }


class _Sonda(BaseModel):
    """Schema mínimo pero con las tres cosas que rompen a un modelo flojo:
    un enum cerrado, un entero con rango, y un opcional que debe quedar en
    None cuando el dato no está."""

    model_config = {"extra": "forbid"}

    barrio: Literal["belgrano", "palermo", "otro"]
    ambientes: int = Field(ge=1, le=10)
    tiene_cochera: bool
    piso: int | None = None


PROMPT_SONDA = (
    "Extraé los datos de este aviso: 'Depto 3 ambientes en Belgrano, sin cochera. No dice el piso.'"
)


async def probe_structured(task: str) -> dict[str, Any]:
    """Verifica que la tarea sirva para `instructor` — que es de lo que
    dependen los nodos 4, 5, 6, 8 y 10.

    Que un modelo responda texto no significa que sepa devolver un objeto
    validado. Es una capacidad distinta y hay que probarla aparte.
    """
    from tasador.llm import LlmClient, LlmValidationError

    cliente = LlmClient()
    t0 = time.perf_counter()
    try:
        obj, usos = await cliente.structured(
            task,
            [{"role": "user", "content": PROMPT_SONDA}],
            _Sonda,
            use_cache=False,
        )
    except LlmValidationError as e:
        return {
            "task": task,
            "ok": False,
            "error": str(e)[:150],
            "costo": float(sum(u.cost_usd for u in e.usos)),
            "ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:
        return {"task": task, "ok": False, "error": f"{type(e).__name__}: {e}"[:150], "ms": 0}
    finally:
        await cliente.close()

    correcto = obj.barrio == "belgrano" and obj.ambientes == 3 and obj.piso is None
    return {
        "task": task,
        "ok": True,
        "modelo": usos[-1].provider_model,
        "respuesta": f"{obj.barrio}/{obj.ambientes}amb/piso={obj.piso} {'✓' if correcto else '✗'}",
        "tok_in": sum(u.tokens_in for u in usos),
        "tok_out": sum(u.tokens_out for u in usos),
        "costo": float(sum(u.cost_usd for u in usos)),
        "ms": int((time.perf_counter() - t0) * 1000),
        "intentos": len(usos),
    }


def main() -> int:
    # La consola de Windows es cp1252 y revienta con cualquier no-ASCII
    # (medido: UnicodeEncodeError en '✅'). Se fuerza UTF-8 en la salida.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=None, help="default: http://127.0.0.1:4000")
    ap.add_argument("--task", action="append", help="probar solo estas tareas")
    ap.add_argument(
        "--structured",
        action="store_true",
        help="además, verificar salida tipada con instructor (lo que usan los nodos)",
    )
    args = ap.parse_args()

    base = _base_url(args.base_url)
    client = httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {_master_key()}"},
        timeout=30,
    )

    print(f"Gateway: {base}\n")
    objetivo = args.task or tasks(client)

    filas = [probe(client, t) for t in objetivo]

    ancho = max(len(f["task"]) for f in filas)
    print(
        f"{'TAREA':<{ancho}}  {'MODELO QUE ATENDIÓ':<42} {'IN':>5} {'OUT':>5} "
        f"{'USD':>9} {'ms':>6}  RESP"
    )
    print("-" * (ancho + 80))
    total = 0.0
    for f in filas:
        if not f["ok"]:
            print(f"{f['task']:<{ancho}}  ❌ {f.get('error', 'sin respuesta')}")
            continue
        total += f["costo"]
        print(
            f"{f['task']:<{ancho}}  {f['modelo']:<42} {f['tok_in']:>5} {f['tok_out']:>5} "
            f"{f['costo']:>9.6f} {f['ms']:>6}  {f['respuesta']}"
        )

    fallaron = [f["task"] for f in filas if not f["ok"]]

    if args.structured:
        print(f"\n{'─' * 60}\nSALIDA TIPADA (instructor + Pydantic)\n")
        print(
            f"{'TAREA':<{ancho}}  {'MODELO QUE ATENDIÓ':<42} {'IN':>5} {'OUT':>5} "
            f"{'USD':>9} {'ms':>6} {'int':>4}  EXTRAÍDO"
        )
        print("-" * (ancho + 88))
        for t in objetivo:
            f = asyncio.run(probe_structured(t))
            total += f.get("costo", 0.0)
            if not f["ok"]:
                print(f"{f['task']:<{ancho}}  ❌ {f.get('error')}")
                fallaron.append(f"{f['task']} (tipada)")
                continue
            print(
                f"{f['task']:<{ancho}}  {f['modelo']:<42} {f['tok_in']:>5} {f['tok_out']:>5} "
                f"{f['costo']:>9.6f} {f['ms']:>6} {f['intentos']:>4}  {f['respuesta']}"
            )

    print(f"\nCosto total de esta verificación: USD {total:.6f}")
    if fallaron:
        print(f"❌ Tareas que NO responden: {', '.join(fallaron)}")
        return 1
    print(f"✅ Las {len(filas)} tareas responden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
