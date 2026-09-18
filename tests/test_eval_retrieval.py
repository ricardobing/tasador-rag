"""Las métricas de recuperación, contra casos calculados a mano.

Un harness de evaluación con un bug mide otra cosa y no avisa (Etapa 3 §10.4).
Cada métrica tiene un caso chico cuyo valor se puede verificar con lápiz.
"""

from __future__ import annotations

import math

import pytest

from tasador.eval.retrieval import (
    Resultado,
    bootstrap,
    bpref,
    descartados_at,
    diferencia,
    evaluar,
    grado_de,
    juzgados_at,
    mrr,
    ndcg_at,
    recall_at,
)

# a, b comparables · c marginal · d, e no comparables · x, y sin juicio
JUICIOS = {"a": 2, "b": 2, "c": 1, "d": 0, "e": 0}


def test_los_grados_salen_del_veredicto_y_del_motivo():
    assert grado_de(True, None) == 2
    assert grado_de(False, "recorte_p5_p95") == 1
    assert grado_de(False, "outlier_estadistico") == 1
    assert grado_de(False, "en_pozo_o_construccion") == 0
    assert grado_de(False, "ajuste_excede_el_tope") == 0
    assert grado_de(False, None) == 0


def test_ndcg_perfecto_es_uno_y_el_orden_inverso_es_menor():
    assert ndcg_at(["a", "b", "c", "d", "e"], JUICIOS, 5) == pytest.approx(1.0)
    peor = ndcg_at(["e", "d", "c", "b", "a"], JUICIOS, 5)
    assert 0 < peor < 1


def test_ndcg_a_mano():
    # ranking [d, a]: ganancias 0, 3 → DCG = 3/log2(3). Ideal [a, b] (k=2): 3 + 3/log2(3).
    obtenido = ndcg_at(["d", "a"], JUICIOS, 2)
    esperado = (3 / math.log2(3)) / (3 + 3 / math.log2(3))
    assert obtenido == pytest.approx(esperado)


def test_los_no_juzgados_no_cuentan_ni_a_favor_ni_en_contra():
    """La lista condensada: `x` e `y` desaparecen antes de medir."""
    con = ndcg_at(["x", "a", "y", "b"], JUICIOS, 2)
    sin = ndcg_at(["a", "b"], JUICIOS, 2)
    assert con == pytest.approx(sin) == pytest.approx(1.0)
    assert mrr(["x", "y", "a"], JUICIOS) == 1.0


def test_recall_cuenta_solo_los_comparables_de_grado_dos():
    assert recall_at(["a", "c", "d"], JUICIOS, 60) == 0.5
    assert recall_at(["a", "b"], JUICIOS, 60) == 1.0
    assert recall_at([], JUICIOS, 60) == 0.0


def test_mrr_es_la_posicion_del_primer_comparable():
    assert mrr(["d", "e", "b"], JUICIOS) == pytest.approx(1 / 3)
    assert mrr(["d", "e"], JUICIOS) == 0.0


def test_bpref_a_mano():
    """R=2 relevantes. [a, d, b]: a sin no-relevantes antes → 1; b con uno antes
    → 1 - 1/2. bpref = (1 + 0.5)/2 = 0.75. El marginal `c` no cuenta."""
    assert bpref(["a", "d", "b"], JUICIOS) == pytest.approx(0.75)
    assert bpref(["a", "c", "b"], JUICIOS) == pytest.approx(1.0)
    assert bpref(["d", "e", "a", "b"], JUICIOS) == pytest.approx(0.0)


def test_descartados_cuenta_lo_que_la_curaduria_no_usa_entre_los_juzgados():
    # top-4: a (2), c (1), d (0), x (sin juicio): de 3 juzgados, 2 no se usan.
    assert descartados_at(["a", "c", "d", "x"], JUICIOS, 4) == pytest.approx(2 / 3)
    assert descartados_at(["a", "b"], JUICIOS, 2) == 0.0
    assert descartados_at(["x", "y"], JUICIOS, 2) == 0.0


def test_juzgados_at_k_mide_cuanto_del_top_se_esta_evaluando():
    assert juzgados_at(["a", "x", "b", "y"], JUICIOS, 4) == 0.5
    assert juzgados_at([], JUICIOS, 4) == 0.0


def test_evaluar_devuelve_todas_las_metricas_con_el_k_en_el_nombre():
    m = evaluar(["a", "b"], JUICIOS, 25)
    assert set(m) == {
        "ndcg@25",
        "recall@30",
        "mrr",
        "bpref",
        "descartados@30",
        "juzgados@25",
        "devueltos",
    }
    assert m["devueltos"] == 2


def test_el_bootstrap_contiene_la_media_y_es_reproducible():
    xs = [0.2, 0.4, 0.6, 0.8, 1.0]
    iv = bootstrap(xs, n=500)
    assert iv.lo <= iv.media <= iv.hi
    assert iv.media == pytest.approx(0.6)
    assert str(bootstrap(xs, n=500)) == str(iv)
    assert bootstrap([]).media == 0.0


def test_la_diferencia_apareada_de_un_sistema_consigo_mismo_es_cero():
    xs = [0.3, 0.5, 0.9]
    d = diferencia(xs, xs)
    assert d.media == 0.0 and d.lo == 0.0 and d.hi == 0.0
    with pytest.raises(ValueError):
        diferencia([0.1], [0.1, 0.2])


def test_la_diferencia_detecta_una_mejora_consistente():
    a = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    b = [x + 0.1 for x in a]
    d = diferencia(a, b)
    assert d.lo > 0, "una mejora en TODAS las consultas no puede tener un intervalo que cruce cero"


def test_resultado_resume_por_metrica():
    r = Resultado("A", 25)
    r.por_consulta["q1"] = evaluar(["a", "b"], JUICIOS, 25)
    r.por_consulta["q2"] = evaluar(["d", "a"], JUICIOS, 25)
    res = r.resumen()
    assert set(res) == set(r.por_consulta["q1"])
    assert res["ndcg@25"].media < 1.0
