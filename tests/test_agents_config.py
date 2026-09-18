"""La configuración de agentes es config, y la config también se testea.

El test que más vale de este archivo es
`test_toda_tarea_usada_existe_en_litellm`: un nodo que pide una tarea que el
gateway no declara no falla al arrancar — falla en medio de un informe, a los
60 segundos, después de haber gastado en los nodos anteriores.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tasador.agents.config import (
    CONFIG_PATH,
    NodeConfig,
    load_agents_config,
    write_bundle_lock,
)

LITELLM_PATH = Path(__file__).resolve().parents[1] / "config" / "litellm.yaml"


def test_carga_la_config_real():
    cfg = load_agents_config()
    assert cfg.version
    assert len(cfg.nodes) == 11, "el pipeline de doc 04 tiene 11 nodos"
    assert [n.seq for n in cfg.enabled] == sorted(n.seq for n in cfg.enabled)


def test_el_nodo_del_precio_no_puede_tener_modelo():
    """ADR-002 en código, no solo en la doc.

    Es la restricción que sostiene todo el sistema: si el nodo 7 pudiera pedir
    un modelo, el precio podría salir de un LLM y nada más importaría.
    """
    with pytest.raises(ValidationError, match="ADR-002"):
        NodeConfig(id="adjust_and_value", seq=7, kind="deterministic", task="judge")


def test_el_nodo_7_de_la_config_real_no_tiene_modelo():
    assert load_agents_config().node("adjust_and_value").task is None


def test_un_nodo_llm_sin_tarea_no_valida():
    with pytest.raises(ValidationError, match="no declara `task`"):
        NodeConfig(id="extract_features", seq=4, kind="llm")


def test_una_tarea_sin_prompt_no_valida():
    """Un modelo sin prompt versionado no es reproducible (doc 04 §5)."""
    with pytest.raises(ValidationError, match="no `prompt`"):
        NodeConfig(id="curate", seq=6, kind="hybrid", task="judge")


def test_todo_prompt_declarado_existe_como_archivo():
    """El hueco que dejó el nodo 8 sin que nadie lo notara.

    `agents.yaml` declaraba `prompt: market_context/v1` y el archivo no existía:
    `_bundle_hash` lo hasheaba como `<ausente>` —a propósito, para que el día
    que se escriba el bundle cambie— y mientras tanto el prompt real vivía
    hardcodeado en `market.py`. Resultado: se podía cambiar el texto que ve el
    modelo sin mover el `prompt_bundle_version` estampado en el informe.

    El mecanismo del `<ausente>` está bien; lo que faltaba era que alguien
    avisara que había uno.
    """
    from tasador.agents import prompts

    faltan = [
        f"{n.id} -> prompts/{n.prompt}.jinja"
        for n in load_agents_config().enabled
        if n.prompt and not prompts.existe(n.prompt)
    ]
    assert not faltan, f"nodos que declaran un prompt inexistente: {faltan}"


def test_ningun_nodo_llm_tiene_el_prompt_en_el_codigo():
    """Corolario del anterior, del otro lado: que el archivo exista no alcanza
    si el nodo igual usa una constante suya.

    Se verifica sobre el nodo 8, que es donde pasó: los dos motores tienen que
    salir del MISMO archivo, o la comparación entre ellos mide dos prompts
    distintos y no dos motores.
    """
    import inspect

    from tasador.agents.nodes import market

    fuente = inspect.getsource(market)
    for hardcodeado in ("Analista de mercado inmobiliario", "Escribís para el dueño"):
        assert hardcodeado not in fuente, (
            f"el prompt del nodo 8 volvió al código: {hardcodeado!r}. "
            "Va en prompts/market_context/v1.jinja o el bundle miente."
        )


def test_el_lock_del_repo_esta_al_dia():
    """`prompts/bundle.lock.json` se versiona para que un cambio de prompt entre
    al diff del PR. Si queda viejo, el artefacto que debía hacer auditable el
    cambio es justamente el que lo esconde."""
    import json

    lock_path = Path(__file__).resolve().parents[1] / "prompts" / "bundle.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["bundle_hash"] == load_agents_config().bundle_hash, (
        "bundle.lock.json quedó desactualizado: correr `uv run python scripts/bundle_lock.py`"
    )


def test_toda_tarea_usada_existe_en_litellm():
    """Cruce entre las dos capas de configuración."""
    cfg = load_agents_config()
    declaradas = {
        m["model_name"]
        for m in yaml.safe_load(LITELLM_PATH.read_text(encoding="utf-8"))["model_list"]
    }
    faltantes = cfg.tasks_in_use() - declaradas
    assert not faltantes, (
        f"agents.yaml pide tareas que config/litellm.yaml no declara: {sorted(faltantes)}"
    )


def test_los_fallbacks_de_litellm_apuntan_a_tareas_declaradas():
    """Un fallback a una tarea inexistente es peor que no tener fallback: en vez
    de degradar, revienta justo cuando el primario ya falló."""
    data = yaml.safe_load(LITELLM_PATH.read_text(encoding="utf-8"))
    declaradas = {m["model_name"] for m in data["model_list"]}
    for regla in data["router_settings"].get("fallbacks", []):
        for origen, destinos in regla.items():
            assert origen in declaradas, f"fallback desde una tarea inexistente: {origen}"
            for d in destinos:
                assert d in declaradas, f"fallback de '{origen}' a una tarea inexistente: '{d}'"


def test_una_tarea_es_un_solo_despliegue():
    """Con `simple-shuffle`, dos entradas con el mismo `model_name` se SORTEAN.

    Lo medimos el 13/08: una de cada dos llamadas a `extractor` se iba a otro
    modelo. La degradación se declara en `fallbacks`, no duplicando nombres.
    """
    nombres = [
        m["model_name"]
        for m in yaml.safe_load(LITELLM_PATH.read_text(encoding="utf-8"))["model_list"]
    ]
    repetidos = {n for n in nombres if nombres.count(n) > 1}
    assert not repetidos, f"tareas con más de un despliegue (se sortean): {sorted(repetidos)}"


def test_seq_repetido_no_valida(tmp_path: Path):
    yaml_roto = textwrap.dedent("""
        version: "test"
        nodes:
          - {id: uno, seq: 1, kind: deterministic}
          - {id: dos, seq: 1, kind: deterministic}
    """)
    p = tmp_path / "agents.yaml"
    p.write_text(yaml_roto, encoding="utf-8")
    with pytest.raises(ValidationError, match="seq` repetidos"):
        load_agents_config(str(p))


def test_los_defaults_se_aplican_pero_el_nodo_pisa(tmp_path: Path):
    p = tmp_path / "agents.yaml"
    p.write_text(
        textwrap.dedent("""
            version: "test"
            defaults: {max_attempts: 2, on_error: fail}
            nodes:
              - {id: hereda, seq: 1, kind: deterministic}
              - {id: pisa,   seq: 2, kind: deterministic, on_error: degrade, max_attempts: 4}
        """),
        encoding="utf-8",
    )
    cfg = load_agents_config(str(p))
    assert cfg.node("hereda").on_error == "fail"
    assert cfg.node("hereda").max_attempts == 2
    assert cfg.node("pisa").on_error == "degrade"
    assert cfg.node("pisa").max_attempts == 4


def test_el_bundle_hash_cambia_si_cambia_el_texto_del_prompt(tmp_path: Path):
    """Hashear solo el YAML sería una trampa: cambiar el texto de un prompt sin
    renombrarlo dejaría dos corridas incomparables pareciendo iguales."""
    from tasador.agents import config as mod

    prompts = tmp_path / "prompts"
    (prompts / "extractor").mkdir(parents=True)
    prompt = prompts / "extractor" / "v1.jinja"
    prompt.write_text("versión A", encoding="utf-8")

    agents = tmp_path / "agents.yaml"
    agents.write_text(
        textwrap.dedent("""
            version: "test"
            nodes:
              - {id: extract_features, seq: 4, kind: llm, task: extractor, prompt: extractor/v1}
        """),
        encoding="utf-8",
    )

    original = mod.PROMPTS_DIR
    try:
        mod.PROMPTS_DIR = prompts
        load_agents_config.cache_clear()
        antes = load_agents_config(str(agents)).bundle_hash

        prompt.write_text("versión B — mismo nombre, otro texto", encoding="utf-8")
        load_agents_config.cache_clear()
        despues = load_agents_config(str(agents)).bundle_hash
    finally:
        mod.PROMPTS_DIR = original
        load_agents_config.cache_clear()

    assert antes != despues


def test_write_bundle_lock(tmp_path: Path):
    lock = write_bundle_lock(tmp_path / "bundle.lock.json")
    assert lock["bundle_hash"] == load_agents_config().bundle_hash
    # El nodo 7 no llama a ningún modelo: no puede aparecer en el lock.
    assert "adjust_and_value" not in lock["nodes"]
    assert (tmp_path / "bundle.lock.json").exists()


def test_la_tarea_de_un_nodo_se_puede_pisar_desde_el_entorno(monkeypatch: pytest.MonkeyPatch):
    """`TASADOR_TASK_<NODO>=flash` para iterar barato sin tocar un archivo
    versionado. También es cómo un tenant corre con otro proveedor sin build
    propio."""
    load_agents_config.cache_clear()
    assert load_agents_config().node("curate").task == "judge"

    monkeypatch.setenv("TASADOR_TASK_CURATE", "flash")
    load_agents_config.cache_clear()
    assert load_agents_config().node("curate").task == "flash"

    monkeypatch.delenv("TASADOR_TASK_CURATE")
    load_agents_config.cache_clear()
    assert load_agents_config().node("curate").task == "judge"


def test_el_override_de_entorno_no_puede_saltear_el_adr_002(monkeypatch: pytest.MonkeyPatch):
    """El atajo no puede ser una puerta trasera: pisar la tarea del nodo 7
    desde el entorno tiene que fallar igual que hacerlo en el YAML."""
    monkeypatch.setenv("TASADOR_TASK_ADJUST_AND_VALUE", "flash")
    load_agents_config.cache_clear()
    with pytest.raises(ValidationError, match="ADR-002"):
        load_agents_config()
    monkeypatch.delenv("TASADOR_TASK_ADJUST_AND_VALUE")
    load_agents_config.cache_clear()


def test_el_yaml_del_repo_esta_bien_formado():
    """Si esto falla, el archivo que se va a producción no carga."""
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    ids = [n["id"] for n in data["nodes"]]
    assert "adjust_and_value" in ids
    assert len(set(ids)) == len(ids)
