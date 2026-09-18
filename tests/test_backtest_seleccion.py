"""La selección de comparables por recuperador, acotada al pool del backtest."""

from __future__ import annotations

from decimal import Decimal

from tasador.eval.backtest import Case, comparables_por_ranking
from tasador.valuation.models import Comparable, Property


def _comp(ref: str) -> Comparable:
    return Comparable(ref=ref, price=Decimal(100000), currency="USD", prop=Property())


def test_respeta_el_orden_del_recuperador_y_excluye_el_caso_y_lo_que_no_esta_en_el_pool():
    pool = [_comp("a"), _comp("b"), _comp("c"), _comp("yo")]
    caso = Case(
        ref="yo",
        neighborhood_id=1,
        neighborhood="Palermo",
        actual_price=Decimal(1),
        surface=Decimal(50),
        rooms=2,
    )
    # "z" no está en el pool (no es canónico de su cluster); "yo" es el propio caso.
    orden = comparables_por_ranking(pool, caso, ["c", "z", "yo", "a", "b"], k=2)
    assert [c.ref for c in orden] == ["c", "a"]


def test_sin_ranking_no_hay_comparables():
    caso = Case(
        ref="yo",
        neighborhood_id=1,
        neighborhood="Palermo",
        actual_price=Decimal(1),
        surface=Decimal(50),
        rooms=2,
    )
    assert comparables_por_ranking([_comp("a")], caso, []) == []
