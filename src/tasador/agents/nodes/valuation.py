"""Nodo 7 — `adjust_and_value`. DETERMINÍSTICO, sin LLM (ADR-002).

Este nodo no calcula nada por su cuenta: traduce el estado del grafo a los
tipos del motor (`tasador.valuation`), lo llama, y traduce la respuesta de
vuelta. El motor ya existe, tiene 24 tests y está medido contra 1.500 casos.

Que el adaptador sea tan flaco es el punto. El camino entre "un LLM leyó un
aviso" y "este es el precio" pasa obligatoriamente por una mediana de un
conjunto, y no hay otro.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeResult
from tasador.agents.state import Candidate, ReportState, dec
from tasador.valuation.engine import value
from tasador.valuation.models import (
    Comparable,
    Condition,
    Orientation,
    Property,
    Valuation,
)


def _enum(clase: Any, crudo: Any) -> Any:
    """Texto → enum, o None. Un valor fuera del vocabulario es *sin dato*, no
    un error: viene de una extracción por LLM y la regla es "sin dato, sin
    ajuste" (doc 05 §4.2)."""
    if not crudo:
        return None
    try:
        return clase(str(crudo))
    except ValueError:
        return None


def _ent(crudo: Any) -> int | None:
    """Texto → int, o None. El gemelo de `dec()` para los campos enteros.

    **No era una precaución teórica.** `corpus.listings.raw` guarda TODO como
    texto a propósito (`_raw()` en ingest/core.py hace `str(v)`), y el nodo 2
    hidrata los candidatos desde ahí. Los decimales pasaban por `dec()` y los
    enteros no pasaban por nada: llegaban como `"17"` a `Property.age_years`.

    Mientras el corpus fueron 24 avisos de Portal B nadie lo notó, porque esas
    tarjetas no traían antigüedad y el campo quedaba en None. El 14/08 entraron
    288 avisos de Portal A con `age` cargado en 231, y el nodo 7 —el que
    calcula EL PRECIO— murió con:

        TypeError: '<=' not supported between instances of 'str' and 'int'

    Es el modo de falla más caro posible: el informe se cae en el único nodo
    que no puede degradar, después de haber pagado la extracción de 60 avisos.
    Y solo aparece cuando cambian los datos de entrada, no el código.

    Un valor que no es entero es *sin dato*, no un error: mismo criterio que
    `_enum` y que "sin dato, sin ajuste" (doc 05 §4.2).
    """
    if crudo is None or isinstance(crudo, bool):
        return None
    try:
        return int(str(crudo).strip())
    except (TypeError, ValueError):
        return None


def _bool(crudo: Any) -> bool | None:
    """Idem para los booleanos: en `raw` viajan como `"True"`/`"False"`."""
    if crudo is None:
        return None
    if isinstance(crudo, bool):
        return crudo
    texto = str(crudo).strip().lower()
    if texto in ("true", "1", "si", "sí"):
        return True
    if texto in ("false", "0", "no"):
        return False
    return None


def _prop(d: dict[str, Any], *, min_conf: float = 0.0) -> Property:
    """Construye una `Property` desde un dict del estado.

    `min_conf` descarta los campos que un LLM infirió con poca confianza: se
    guardaron en el candidato, pero no se usan para mover el precio.
    """
    conf: dict[str, float] = d.get("field_confidence") or {}

    def campo(nombre: str) -> Any:
        v = d.get(nombre)
        if v is None:
            return None
        if min_conf and nombre in conf and conf[nombre] < min_conf:
            return None
        return v

    return Property(
        surface_covered=dec(d.get("surface_covered")),
        surface_semi=dec(d.get("surface_semi")),
        surface_uncovered=dec(d.get("surface_uncovered")),
        surface_total=dec(d.get("surface_total")),
        rooms=_ent(d.get("rooms")),
        age_years=_ent(campo("age_years")),
        floor_number=_ent(campo("floor_number")),
        has_elevator=_bool(campo("has_elevator")),
        condition=_enum(Condition, campo("condition")),
        orientation=_enum(Orientation, campo("orientation")),
        parking_spaces=_ent(d.get("parking_spaces")) or 0,
        amenities=list(d.get("amenities") or []),
        expenses_ars=dec(d.get("expenses_ars")),
    )


def _comparable(c: Candidate, min_conf: float) -> Comparable:
    return Comparable(
        ref=c["listing_id"],
        price=dec(c.get("price")) or Decimal("0"),
        currency=c.get("currency") or "USD",
        prop=_prop(dict(c), min_conf=min_conf),
        source=c.get("source") or "UNKNOWN",
        days_published=c.get("days_published"),
        distance_m=c.get("distance_m"),
        is_manual=(c.get("source") in ("MANUAL", "PASTED")),
    )


def _serializar(v: Valuation) -> dict[str, Any]:
    """`Valuation` → JSON para el estado y para `reports.methodology`.

    Guarda TODOS los comparables, incluidos los excluidos con su motivo: es lo
    que permite responder "¿por qué no usaste el de Cabildo 2500?" seis meses
    después (doc 03 §3.6).
    """
    return {
        "currency": v.currency,
        "value_low": str(v.value_low) if v.value_low is not None else None,
        "value_mid": str(v.value_mid) if v.value_mid is not None else None,
        "value_high": str(v.value_high) if v.value_high is not None else None,
        "closing_low": str(v.closing_low) if v.closing_low is not None else None,
        "closing_high": str(v.closing_high) if v.closing_high is not None else None,
        "price_per_m2": str(v.price_per_m2) if v.price_per_m2 is not None else None,
        "weighted_surface": str(v.weighted_surface) if v.weighted_surface is not None else None,
        "comparables_found": v.comparables_found,
        "comparables_used": v.comparables_used,
        "dispersion": str(v.dispersion) if v.dispersion is not None else None,
        "confidence": v.confidence.value if v.confidence else None,
        "confidence_score": str(v.confidence_score) if v.confidence_score is not None else None,
        "insufficient_reason": v.insufficient_reason.value if v.insufficient_reason else None,
        "method_version": v.method_version,
        "notes": list(v.notes),
        "detail": [
            {
                "listing_id": ac.comparable.ref,
                "source": ac.comparable.source,
                "included": ac.included,
                "exclusion_reason": ac.exclusion_reason,
                "snapshot_price": str(ac.comparable.price),
                "snapshot_currency": ac.comparable.currency,
                "snapshot_surface": (
                    str(ac.comparable.prop.surface_weighted)
                    if ac.comparable.prop.surface_weighted is not None
                    else None
                ),
                "raw_price_per_m2": (
                    str(ac.raw_price_per_m2) if ac.raw_price_per_m2 is not None else None
                ),
                "adjusted_price_per_m2": (
                    str(ac.adjusted_price_per_m2) if ac.adjusted_price_per_m2 is not None else None
                ),
                "adjustments": ac.adjustments,
                "distance_m": ac.comparable.distance_m,
            }
            for ac in v.detail
        ],
    }


def _excluido(c: Candidate) -> dict[str, Any]:
    """Un candidato que un nodo anterior descartó, en el formato del detalle.

    No pasó por el motor —no tiene USD/m² ajustado ni coeficientes— pero tiene
    precio, superficie y motivo, que es lo que el informe necesita mostrar.
    """
    sup = dec(c.get("surface_covered")) or dec(c.get("surface_total"))
    precio = dec(c.get("price"))
    crudo = precio / sup if precio and sup and sup > 0 else None
    return {
        "listing_id": c["listing_id"],
        "source": c.get("source") or "UNKNOWN",
        "included": False,
        "exclusion_reason": c.get("exclusion_reason") or "descartado_sin_motivo",
        "snapshot_price": str(precio) if precio is not None else "0",
        "snapshot_currency": c.get("currency") or "USD",
        "snapshot_surface": str(sup) if sup is not None else None,
        "raw_price_per_m2": str(round(crudo)) if crudo is not None else None,
        "adjusted_price_per_m2": None,
        "adjustments": {},
        "distance_m": c.get("distance_m"),
    }


async def adjust_and_value(state: ReportState, cfg: NodeConfig) -> NodeResult:
    from tasador.settings import get_settings

    s = get_settings()
    # El umbral de confianza lo fija el nodo 4 en agents.yaml; acá se lee para
    # que un campo inferido flojo no termine moviendo el precio.
    min_conf = float(_extractor_min_conf())

    todos = list(state.get("candidates", []))
    # Solo entran los que el nodo 6 dejó pasar. Sin nodo 6, entran todos:
    # `included` ausente se interpreta como True.
    vivos = [c for c in todos if c.get("included", True)]
    ya_excluidos = [c for c in todos if not c.get("included", True)]

    subject = _prop(state.get("subject", {}) or {})
    resultado = value(
        subject,
        [_comparable(c, min_conf) for c in vivos],
        config_path=cfg.param("config_path") or None,
        min_comparables=s.min_comparables,
        relaxation_steps=state.get("relaxation_steps", 0),
    )

    datos = _serializar(resultado)
    # Los que descartaron los nodos 5 y 6 NO llegan al motor, pero SÍ tienen que
    # quedar en el informe con su motivo. Es lo que permite responder "¿por qué
    # no usaste el de Cabildo 2500?" seis meses después (doc 03 §3.6). Sin esto
    # desaparecían del informe y el descarte se volvía invisible.
    datos["detail"] = [*datos["detail"], *(_excluido(c) for c in ya_excluidos)]
    datos["comparables_found"] = len(todos)
    updates: dict[str, Any] = {"valuation": datos}

    if not resultado.ok:
        # No es un error: es la respuesta honesta cuando no hay con qué.
        # El grafo corta acá y no gasta un centavo en redactar la nada.
        updates["status"] = "INSUFFICIENT_DATA"
        updates["insufficient_reason"] = datos["insufficient_reason"]

    return NodeResult(
        updates=updates,
        detail={
            "comparables_found": resultado.comparables_found,
            "comparables_used": resultado.comparables_used,
            "confidence": datos["confidence"],
            "insufficient_reason": datos["insufficient_reason"],
            "method_version": resultado.method_version,
        },
    )


def _extractor_min_conf() -> float:
    """El umbral vive en el nodo 4, que es quien produce esos campos."""
    from tasador.agents.config import load_agents_config

    try:
        return float(load_agents_config().node("extract_features").param("min_field_confidence", 0))
    except KeyError:
        return 0.0
