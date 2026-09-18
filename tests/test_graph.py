"""El grafo: topología, salida temprana y política de errores.

Sin base de datos y sin red: la escritura de `report_events` se intercepta.
Lo que se prueba acá es el CONTROL DE FLUJO, que es lo que decide si un
informe gasta o no gasta.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from tasador.agents.config import NodeConfig, load_agents_config
from tasador.agents.graph import build_graph, checkpointer_dsn
from tasador.agents.nodes import implementacion
from tasador.agents.nodes.base import NodeResult, instrument
from tasador.agents.nodes.valuation import adjust_and_value
from tasador.agents.state import estado_inicial


@pytest.fixture(autouse=True)
def sin_base(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Captura los report_events en memoria en vez de escribirlos."""
    capturados: list[dict[str, Any]] = []

    async def _fake(report_id: str, **kw: Any) -> None:
        capturados.append({"report_id": report_id, **kw})

    monkeypatch.setattr("tasador.agents.nodes.base._persist_event", _fake)
    return capturados


def _cfg(node_id: str = "prueba", **kw: Any) -> NodeConfig:
    base = {"id": node_id, "seq": 1, "kind": "deterministic"}
    return NodeConfig(**{**base, **kw})


# ── Topología ────────────────────────────────────────────────────────────
def test_el_grafo_se_arma_desde_el_yaml():
    cfg = load_agents_config()
    grafo = build_graph(cfg).compile()
    dibujo = grafo.get_graph()
    nombres = {n for n in dibujo.nodes if not n.startswith("__")}
    assert nombres == {n.id for n in cfg.enabled}


def test_los_nodos_apagados_no_estan_en_el_grafo():
    cfg = load_agents_config()
    apagados = {n.id for n in cfg.nodes if not n.enabled}
    assert apagados, "el YAML del repo tiene nodos apagados a propósito"
    nombres = set(build_graph(cfg).compile().get_graph().nodes)
    assert not (apagados & nombres)


def test_reordenar_el_yaml_reordena_el_grafo(tmp_path: Path):
    """La topología no está escrita en el código."""
    p = tmp_path / "agents.yaml"
    p.write_text(
        textwrap.dedent("""
            version: "test"
            nodes:
              - {id: tercero, seq: 30, kind: deterministic}
              - {id: primero, seq: 10, kind: deterministic}
              - {id: segundo, seq: 20, kind: deterministic}
        """),
        encoding="utf-8",
    )
    load_agents_config.cache_clear()
    cfg = load_agents_config(str(p))
    assert [n.id for n in cfg.enabled] == ["primero", "segundo", "tercero"]
    load_agents_config.cache_clear()


def test_dsn_del_checkpointer():
    """psycopg directo rechaza el prefijo que SQLAlchemy necesita."""
    assert (
        checkpointer_dsn("postgresql+psycopg://u:p@host:5432/db") == "postgresql://u:p@host:5432/db"
    )


# ── Política de errores ──────────────────────────────────────────────────
async def test_on_error_fail_marca_el_informe_como_fallado():
    async def revienta(*_: Any) -> NodeResult:
        raise RuntimeError("se cayó")

    envuelto = instrument(_cfg(on_error="fail", max_attempts=1), revienta)
    salida = await envuelto(estado_inicial("r1", "o1", "s1"))
    assert salida["status"] == "FAILED"
    assert "se cayó" in salida["error_detail"]


async def test_on_error_degrade_no_tumba_el_informe():
    async def revienta(*_: Any) -> NodeResult:
        raise RuntimeError("se cayó pero no importa")

    envuelto = instrument(_cfg(on_error="degrade", max_attempts=1), revienta)
    salida = await envuelto(estado_inicial("r1", "o1", "s1"))
    assert "status" not in salida
    assert salida["degraded_nodes"] == [_cfg().id]


async def test_reintenta_y_lo_marca_como_retried(sin_base: list[dict[str, Any]]):
    intentos = {"n": 0}

    async def falla_una_vez(*_: Any) -> NodeResult:
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise RuntimeError("primera vez falla")
        return NodeResult(updates={"captured": 7})

    envuelto = instrument(_cfg(max_attempts=2), falla_una_vez)
    salida = await envuelto(estado_inicial("r1", "o1", "s1"))
    assert salida == {"captured": 7}
    assert sin_base[-1]["status"] == "RETRIED"


async def test_todo_nodo_deja_traza_aunque_falle(sin_base: list[dict[str, Any]]):
    async def revienta(*_: Any) -> NodeResult:
        raise RuntimeError("boom")

    await instrument(_cfg(on_error="degrade", max_attempts=1), revienta)(
        estado_inicial("r1", "o1", "s1")
    )
    assert len(sin_base) == 1
    assert sin_base[0]["status"] == "FAILED"
    assert "boom" in sin_base[0]["detail"]["error"]


# ── Stubs ────────────────────────────────────────────────────────────────
async def test_un_nodo_sin_implementar_se_marca_como_stub(sin_base: list[dict[str, Any]]):
    """Un stub que devuelve datos inventados es la forma más rápida de creer
    que el sistema funciona cuando no funciona."""
    # Ya no queda ningún nodo del pipeline sin implementar, así que el
    # mecanismo se prueba con uno inventado. El test sigue valiendo: protege
    # el día que se agregue un nodo nuevo al YAML antes que su código.
    fn, construido = implementacion("nodo_que_no_existe")
    assert not construido
    salida = await instrument(_cfg("nodo_que_no_existe"), fn)(estado_inicial("r1", "o1", "s1"))
    assert salida == {}
    assert sin_base[0]["detail"]["stub"] is True


@pytest.mark.parametrize(
    "node_id",
    [
        "normalize_subject",
        "retrieve_candidates",
        "extract_features",
        "curate",
        "adjust_and_value",
        "market_context",
        "write_report",
        "critic",
        "render_pdf",
        "dedup_cluster",
    ],
)
def test_los_nodos_construidos_no_son_stubs(node_id: str):
    _, construido = implementacion(node_id)
    assert construido, f"{node_id} debería estar implementado"


# ── Nodo 7 dentro del grafo ──────────────────────────────────────────────
def _candidato(i: int, precio: str, m2: str) -> dict[str, Any]:
    return {
        "listing_id": f"00000000-0000-0000-0000-0000000000{i:02d}",
        "source": "PORTAL_B",
        "price": precio,
        "currency": "USD",
        "surface_covered": m2,
        "rooms": 3,
    }


async def test_menos_de_5_comparables_da_insufficient_data():
    estado = estado_inicial("r1", "o1", "s1")
    estado["subject"] = {"surface_covered": "75"}
    estado["candidates"] = [_candidato(i, "180000", "75") for i in range(3)]

    r = await adjust_and_value(estado, load_agents_config().node("adjust_and_value"))
    assert r.updates["status"] == "INSUFFICIENT_DATA"
    assert r.updates["insufficient_reason"] == "pocos_comparables"


async def test_con_comparables_suficientes_produce_un_valor():
    estado = estado_inicial("r1", "o1", "s1")
    estado["subject"] = {"surface_covered": "75"}
    estado["candidates"] = [_candidato(i, str(175000 + i * 3000), "75") for i in range(6)]

    r = await adjust_and_value(estado, load_agents_config().node("adjust_and_value"))
    v = r.updates["valuation"]
    assert "status" not in r.updates
    assert v["value_mid"] is not None
    assert float(v["value_low"]) <= float(v["value_mid"]) <= float(v["value_high"])
    assert v["comparables_used"] == 6


async def test_el_nodo_6_manda_sobre_que_entra():
    """`included=False` lo pone la curaduría; el nodo 7 lo respeta sin discutir.

    Pero los descartados NO desaparecen del informe: `found` los cuenta a todos
    y `used` solo a los que entraron al cálculo. Es lo que permite responder
    "¿por qué no usaste el de Cabildo 2500?" (doc 03 §3.6).
    """
    estado = estado_inicial("r1", "o1", "s1")
    estado["subject"] = {"surface_covered": "75"}
    estado["candidates"] = [_candidato(i, "180000", "75") for i in range(8)]
    for c in estado["candidates"][:4]:
        c["included"] = False
        c["exclusion_reason"] = "en_pozo_o_construccion"

    r = await adjust_and_value(estado, load_agents_config().node("adjust_and_value"))
    v = r.updates["valuation"]
    assert v["comparables_found"] == 8, "los descartados también se informan"
    assert v["comparables_used"] == 4, "pero solo los 4 vivos entran al cálculo"

    excluidos = [d for d in v["detail"] if not d["included"]]
    assert len(excluidos) == 4
    assert all(d["exclusion_reason"] == "en_pozo_o_construccion" for d in excluidos)
    # No pasaron por el motor: no tienen ajuste, pero sí precio y motivo.
    assert all(d["adjusted_price_per_m2"] is None for d in excluidos)
    assert all(d["snapshot_price"] == "180000" for d in excluidos)


async def test_un_campo_inferido_con_baja_confianza_no_mueve_el_precio():
    """ "Sin dato, sin ajuste" (doc 05 §4.2) también vale para lo que un LLM
    infirió flojo: se guarda, pero no ajusta."""
    cfg = load_agents_config().node("adjust_and_value")
    base = estado_inicial("r1", "o1", "s1")
    base["subject"] = {"surface_covered": "75"}

    def con_estado(confianza: float) -> list[dict[str, Any]]:
        cands = [_candidato(i, "180000", "75") for i in range(6)]
        for c in cands:
            c["condition"] = "a_refaccionar"
            c["field_condence"] = None
            c["field_confidence"] = {"condition": confianza}
        return cands

    alta = {**base, "candidates": con_estado(0.95)}
    baja = {**base, "candidates": con_estado(0.10)}

    r_alta = await adjust_and_value(alta, cfg)  # type: ignore[arg-type]
    r_baja = await adjust_and_value(baja, cfg)  # type: ignore[arg-type]

    # Con confianza alta el ajuste se aplica; con baja, el coeficiente es 1,00.
    assert (
        r_alta.updates["valuation"]["price_per_m2"] != (r_baja.updates["valuation"]["price_per_m2"])
    )


# ── La conexión a Redis del worker (15/08) ───────────────────────────────
def test_el_worker_no_se_queda_sin_cola_por_un_timeout_de_un_segundo():
    """Los defaults de arq dejan el worker vivo y sordo.

    `conn_timeout=1` y `retry_on_timeout=False`: con `max_jobs=4` y el nodo 11
    renderizando PDF con WeasyPrint —CPU, bloquea el event loop— el poll no
    llega a conectar y no reintenta.

    Medido: dos informes 95 minutos en QUEUED con el contenedor "Up" y ocioso.
    Al reiniciar el worker los tomó al instante, `delayed=5737.98s`.
    """
    from tasador.worker import WorkerSettings, redis_settings

    s = redis_settings()
    assert s.conn_timeout >= 5, (
        f"conn_timeout={s.conn_timeout}s: con {WorkerSettings.max_jobs} informes en "
        f"paralelo y WeasyPrint bloqueando el loop, un timeout corto deja al worker "
        f"sin poder consumir la cola"
    )
    assert s.retry_on_timeout is True, (
        "sin reintento, un solo timeout deja el worker vivo y sordo hasta que "
        "alguien lo reinicie a mano"
    )


def test_el_default_de_arq_es_el_que_mordio():
    """El autotest: si arq cambiara sus defaults, el test de arriba pasaría sin
    estar protegiendo nada — y este diría por qué."""
    from arq.connections import RedisSettings

    crudo = RedisSettings.from_dsn("redis://x:6379")
    assert crudo.conn_timeout == 1, (
        "arq cambió su default de conn_timeout: revisar si el override sigue haciendo falta"
    )
    assert crudo.retry_on_timeout is False
