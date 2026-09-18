"""Los umbrales de descarte tienen UNA fuente — H-16.

El rango plausible de USD/m² estaba escrito tres veces:

    src/tasador/ingest/core.py:40    MIN_USD_M2 = Decimal("300")
    src/tasador/agents/nodes/curate.py:57  MIN_USD_M2 = Decimal("300")
    config/adjustments.yaml:95          outlier_usd_m2_min: 300

Mismo valor, nada que los sincronizara. Subir el máximo de 12.000 a 15.000 en
el YAML habría movido el nodo 7 dejando a la ingesta y al nodo 6 descartando con
el valor viejo, en silencio: ninguna de las dos cosas falla, y el corpus queda
con un criterio de entrada distinto al del cálculo.

Es el patrón que más caro salió en este proyecto (R2): dos copias, se arregla la
que no corre, y nada cambia.

Dos gates, y el que importa es el primero:

  1. **De comportamiento** — se mueve el umbral en una config de mentira y se
     verifica que los TRES se muevan. Mide el invariante, no la forma del
     código.
  2. **Estático** — que no vuelvan a aparecer literales. Un renombre lo pasa,
     por eso no alcanza solo.
"""

from __future__ import annotations

import ast
import contextlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parents[2]

# Los tres que preguntan "¿este aviso es plausible?", cada uno en su etapa:
# al entrar al corpus, antes de gastar en el juez, y al calcular.
CONSUMIDORES = (
    "src/tasador/ingest/core.py",
    "src/tasador/agents/nodes/curate.py",
    "src/tasador/valuation/engine.py",
)


@pytest.fixture
def con_umbrales(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Devuelve una función que reescribe el bloque `plausibility` en memoria.

    Parchea `load_config` dentro de `adjustments`, que es donde
    `umbrales_de_plausibilidad` lo resuelve: así llega a los tres consumidores
    sin importar cómo hayan importado la función.
    """
    from tasador.valuation import adjustments

    def aplicar(**cambios: Any) -> None:
        base = adjustments.load_config()
        nuevo = {**base, "plausibility": {**base["plausibility"], **cambios}}
        monkeypatch.setattr(adjustments, "load_config", lambda path=None: nuevo)

    return aplicar


# ── 1. El gate de comportamiento ─────────────────────────────────────────


def test_bajar_el_maximo_mueve_a_los_tres(con_umbrales) -> None:  # type: ignore[no-untyped-def]
    """Un aviso a 6.000 USD/m² es plausible con el máximo real (12.000) y deja
    de serlo con un máximo de 5.000. Los tres tienen que cambiar de opinión."""
    from tasador.agents.nodes.curate import reglas_duras
    from tasador.ingest.core import motivo_descarte
    from tasador.valuation.engine import value
    from tasador.valuation.models import Comparable, Property

    class _Tarjeta:
        source_id, url = "x", "https://x/1"
        price, currency = Decimal("420000"), "USD"
        address, neighborhood_label, description = "Falsa 123", "Palermo", None
        surface_weighted = Decimal("70")
        usd_per_m2 = Decimal("6000")

    candidato = {
        "listing_id": "x",
        "price": "420000",
        "currency": "USD",
        "surface_covered": "70",
        "days_published": 10,
    }

    def excluido_por_el_motor() -> bool:
        comps = [
            Comparable(
                ref=f"c{i}",
                price=Decimal("420000"),
                currency="USD",
                days_published=30,
                prop=Property(surface_covered=Decimal("70")),
            )
            for i in range(8)
        ]
        v = value(Property(surface_covered=Decimal("70")), comps)
        return all(d.exclusion_reason == "usd_m2_fuera_de_rango" for d in v.detail)

    # Con el umbral real: los tres lo aceptan.
    assert motivo_descarte(_Tarjeta()) is None  # type: ignore[arg-type]
    assert reglas_duras(candidato) is None  # type: ignore[arg-type]
    assert not excluido_por_el_motor()

    # Se baja el máximo a 5.000 en UN solo lugar.
    con_umbrales(usd_m2_max=5000)

    assert motivo_descarte(_Tarjeta()) == "usd_m2_fuera_de_rango"  # type: ignore[arg-type]
    assert reglas_duras(candidato) == "usd_m2_fuera_de_rango"  # type: ignore[arg-type]
    assert excluido_por_el_motor(), "el motor siguió con el umbral viejo"


def test_la_superficie_minima_tambien_sale_del_yaml(con_umbrales) -> None:  # type: ignore[no-untyped-def]
    """`curate.MIN_SUPERFICIE = 15` y el `superficie_implausible` de la ingesta
    eran el mismo criterio escrito dos veces."""
    from tasador.agents.nodes.curate import reglas_duras
    from tasador.ingest.core import motivo_descarte

    class _Tarjeta:
        source_id, url = "x", "https://x/1"
        price, currency = Decimal("120000"), "USD"
        address, neighborhood_label, description = "Falsa 123", "Palermo", None
        surface_weighted = Decimal("40")
        usd_per_m2 = Decimal("3000")

    candidato = {
        "listing_id": "x",
        "price": "120000",
        "currency": "USD",
        "surface_covered": "40",
        "days_published": 10,
    }

    assert motivo_descarte(_Tarjeta()) is None  # type: ignore[arg-type]
    assert reglas_duras(candidato) is None  # type: ignore[arg-type]

    con_umbrales(surface_min=50)

    assert motivo_descarte(_Tarjeta()) == "superficie_implausible"  # type: ignore[arg-type]
    assert reglas_duras(candidato) == "sin_superficie"


def test_los_dias_de_vencimiento_salen_del_yaml(con_umbrales) -> None:  # type: ignore[no-untyped-def]
    """`MAX_DIAS_PUBLICADO = 180` era el cuarto literal suelto."""
    from tasador.agents.nodes.curate import reglas_duras

    candidato = {
        "listing_id": "x",
        "price": "210000",
        "currency": "USD",
        "surface_covered": "70",
        "days_published": 100,
    }
    assert reglas_duras(candidato) is None  # type: ignore[arg-type]

    con_umbrales(max_days_published=90)
    assert reglas_duras(candidato) == "aviso_vencido"  # type: ignore[arg-type]


# ── 2. El gate estático, y su autotest ───────────────────────────────────


def _numeros_en(nodo: ast.AST) -> set[Decimal]:
    """Los números escritos dentro de un subárbol, vengan como `300`,
    `Decimal("300")` o `D("12000")`."""
    out: set[Decimal] = set()
    for n in ast.walk(nodo):
        # `bool` es subclase de `int` y `str(True)` no es un Decimal.
        if not isinstance(n, ast.Constant) or isinstance(n.value, bool):
            continue
        if isinstance(n.value, int | float | str):
            # La mayoría de los strings de un fuente no son números.
            with contextlib.suppress(Exception):
                out.add(Decimal(str(n.value)))
    return out


def _literales_de_umbral(codigo: str) -> set[Decimal]:
    """Los números escritos en el fuente **en un lugar donde un umbral vive**:
    como operando de una comparación, o asignados a una constante en MAYÚSCULAS.

    No es "todo número del archivo": eso marcaba `str(e)[:300]` —un recorte de
    string— como si fuera el mínimo de USD/m². Un gate que marca lo que no es
    se termina desactivando, y entonces no queda gate.
    """
    out: set[Decimal] = set()
    for n in ast.walk(ast.parse(codigo)):
        if isinstance(n, ast.Compare):
            for parte in (n.left, *n.comparators):
                out |= _numeros_en(parte)
        elif isinstance(n, ast.Assign):
            nombres = [t.id for t in n.targets if isinstance(t, ast.Name)]
            if any(x.isupper() for x in nombres):
                out |= _numeros_en(n.value)
    return out


def _valores_del_yaml() -> set[Decimal]:
    from tasador.valuation.adjustments import umbrales_de_plausibilidad

    u = umbrales_de_plausibilidad()
    return {
        u.usd_m2_min,
        u.usd_m2_max,
        u.surface_min,
        u.surface_max,
        Decimal(u.max_days_published),
    }


def test_ningun_consumidor_repite_un_umbral_como_literal() -> None:
    valores = _valores_del_yaml()
    reincidentes: dict[str, list[str]] = {}
    for rel in CONSUMIDORES:
        encontrados = _literales_de_umbral((RAIZ / rel).read_text(encoding="utf-8")) & valores
        if encontrados:
            reincidentes[rel] = sorted(str(v) for v in encontrados)
    assert not reincidentes, (
        f"volvieron los literales de umbral: {reincidentes}. "
        f"Salen de `plausibility` en config/adjustments.yaml, una sola vez."
    )


def test_el_gate_estatico_encuentra_lo_que_busca() -> None:
    """El autotest: sin esto, el test de arriba pasaría en verde aunque
    `_literales_de_umbral` devolviera siempre el conjunto vacío."""
    valores = _valores_del_yaml()
    assert valores, "el YAML no trajo ningún umbral: el gate no está midiendo nada"

    # Las dos formas en las que la recaída se escribiría de verdad.
    constante = 'from decimal import Decimal\nMAX_USD_M2 = Decimal("12000")\nDIAS = 180\n'
    hallados = _literales_de_umbral(constante) & valores
    assert Decimal("12000") in hallados
    assert Decimal("180") in hallados

    comparacion = "def f(m2):\n    return 300 <= m2 <= 12000\n"
    assert {Decimal("300"), Decimal("12000")} <= _literales_de_umbral(comparacion)

    assert not (_literales_de_umbral("X = 7\nY = 'hola'\n") & valores)


def test_el_gate_estatico_no_marca_lo_que_no_es() -> None:
    """El otro lado del autotest, y no es hipotético: la primera versión miraba
    TODOS los números del archivo y marcaba `str(e)[:300]` —un recorte de
    string— como si fuera el mínimo de USD/m². Un gate con falsos positivos se
    termina desactivando."""
    valores = _valores_del_yaml()
    inocentes = (
        "msg = str(e)[:300]",
        "espera = 15  # segundos",  # una local en minúscula no es un umbral
        "http.get(url, timeout=180)",
    )
    for fuente in inocentes:
        assert not (_literales_de_umbral(fuente) & valores), f"falso positivo en: {fuente}"


def test_badata_conserva_su_propio_rango() -> None:
    """El de `ingest/badata.py` es distinto A PROPÓSITO: series históricas del
    GCBA, [200, 20.000]. Unificarlo sería el bug opuesto — este test existe
    para que nadie lo "arregle" leyendo el hallazgo por arriba."""
    from tasador.ingest.badata import MAX_USD_M2, MIN_USD_M2
    from tasador.valuation.adjustments import umbrales_de_plausibilidad

    u = umbrales_de_plausibilidad()
    assert u.usd_m2_min > MIN_USD_M2
    assert u.usd_m2_max < MAX_USD_M2


# ── De qué avisos se puede tasar (15/08) ─────────────────────────────────
#
# Los cuatro primeros escalones de la escalera usan SOLO avisos con ficha de
# detalle. El último la suelta, y esa es la parte que hay que proteger: sin ese
# escape, un barrio que todavía no se terminó de scrapear no se puede tasar y
# el informe diría "no hay datos" cuando lo que falta es una corrida del
# scraper.


def _escalera() -> list[dict]:
    from tasador.agents.config import load_agents_config

    return list(load_agents_config().node("retrieve_candidates").param("relaxation", []))


def test_los_primeros_escalones_exigen_ficha_de_detalle() -> None:
    esc = _escalera()
    assert esc, "sin escalera el nodo 2 no relaja nada"
    assert all(p.get("require_full_detail") for p in esc[:-1]), (
        "algún escalón intermedio dejó de exigir ficha: un comparable sin estado "
        "entra al cálculo con coeficiente 1,00, o sea tratado como 'muy bueno'"
    )


def test_el_ultimo_escalon_la_suelta() -> None:
    """El escape. Hoy solo Palermo tiene avisos enriquecidos: sin esto,
    Belgrano —107 avisos, ninguno con ficha— no se puede tasar."""
    assert _escalera()[-1].get("require_full_detail") is False


def test_el_default_del_codigo_es_el_estricto() -> None:
    """Un escalón nuevo que se olvide la clave tiene que heredar lo ESTRICTO.

    Al revés —default laxo— agregar un escalón sin pensarlo abriría el corpus
    entero sin que nada lo diga, que es exactamente cómo un filtro se apaga
    solo.
    """
    import inspect

    from tasador.agents.nodes import retrieve

    firma = inspect.signature(retrieve._consulta)
    assert firma.parameters["solo_ficha_completa"].default is False, (
        "el default de la FUNCIÓN es laxo a propósito: la que decide es la "
        "escalera. Lo que tiene que ser estricto es el default de la CLAVE."
    )
    fuente = inspect.getsource(retrieve)
    assert 'esc.get("require_full_detail", True)' in fuente, (
        "el default de la clave dejó de ser True: un escalón sin la clave "
        "abriría el corpus entero en silencio"
    )


def test_la_traza_dice_si_el_informe_uso_avisos_sin_ficha() -> None:
    """Si un informe salió con avisos sin ficha, se tiene que poder ver cuál
    escalón lo permitió — sin eso, dos informes con la misma pinta no son
    comparables y nadie puede saberlo."""
    import inspect

    from tasador.agents.nodes import retrieve

    assert '"solo_ficha_completa"' in inspect.getsource(retrieve)
