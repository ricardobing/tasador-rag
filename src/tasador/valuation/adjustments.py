"""Coeficientes de ajuste — doc 05 §4.

⚠️ Estos números son PRÁCTICA DE MERCADO Y CRITERIO, no una regresión. Está
declarado así acá, en el YAML, en la doc y en el informe que recibe el cliente.
La calibración empírica es la tarea 6.6.

Viven en `config/adjustments.yaml` y no en el código: cambiarlos incrementa
`method_version` y obliga a correr el backtest antes de mergear.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from tasador.valuation.models import Comparable, Property

D = Decimal
CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "adjustments.yaml"


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    data: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8"))
    # El hash del archivo ES la versión del método: dos corridas con el mismo
    # hash son comparables, con hashes distintos no.
    data["_hash"] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    return data


def _d(v: Any) -> Decimal:
    return D(str(v))


@dataclass(frozen=True, slots=True)
class Umbrales:
    """Qué cuenta como un aviso plausible. Ver `plausibility` en el YAML."""

    usd_m2_min: Decimal
    usd_m2_max: Decimal
    surface_min: Decimal
    surface_max: Decimal
    max_days_published: int


def umbrales_de_plausibilidad(path: str | None = None) -> Umbrales:
    """La ÚNICA fuente de los umbrales de descarte (H-16).

    Los leen la ingesta (`ingest.core.motivo_descarte`), el nodo 6
    (`curate.reglas_duras`) y el motor (`valuation.engine`). Hasta el 15/08
    estaban escritos tres veces en el código, con el mismo valor y sin nada que
    los sincronizara: cambiar el YAML movía el motor y dejaba a los otros dos
    descartando con el valor viejo, sin que nada fallara.

    `load_config` está cacheado, así que llamar a esto por aviso no lee el
    disco. El contrapeso es que un cambio del YAML necesita reiniciar el
    proceso, igual que el resto de los coeficientes.
    """
    p = load_config(path)["plausibility"]
    return Umbrales(
        usd_m2_min=_d(p["usd_m2_min"]),
        usd_m2_max=_d(p["usd_m2_max"]),
        surface_min=_d(p["surface_min"]),
        surface_max=_d(p["surface_max"]),
        max_days_published=int(p["max_days_published"]),
    )


def _coef_age(cfg: dict[str, Any], age: int | None) -> Decimal:
    if age is None:
        return D("1.00")
    for band in cfg["age_years"]:
        if age <= band["max"]:
            return _d(band["coef"])
    return D("1.00")


def _coef_floor(cfg: dict[str, Any], p: Property) -> tuple[Decimal, str]:
    rules = cfg["floor"]
    hi = rules["high_no_elevator"]
    if p.floor_number is not None and p.floor_number >= hi["min_floor"] and p.has_elevator is False:
        # El castigo más grande y más real del mercado porteño.
        return _d(hi["coef"]), f"{p.floor_number}_sin_ascensor"
    view = rules["high_with_view"]
    if p.floor_number is not None and p.floor_number >= view["min_floor"]:
        return _d(view["coef"]), f"piso_{p.floor_number}_vista"
    return _d(rules["default"]["coef"]), "normal"


def _coef_amenities(cfg: dict[str, Any], p: Property) -> tuple[Decimal, str]:
    full = {"pileta", "gimnasio", "sum", "seguridad"}
    tiene = {a.lower() for a in p.amenities}
    if len(full & tiene) >= 3:
        return _d(cfg["amenities"]["full"]), "full"
    return _d(cfg["amenities"]["basic"]), "basic"


def compute_adjustments(
    subject: Property,
    comp: Property,
    cfg: dict[str, Any],
    *,
    comparable: Comparable | None = None,
) -> tuple[Decimal, dict[str, object], bool]:
    """Coeficiente total para llevar el comparable a las condiciones del sujeto.

    Devuelve `(total, detalle, capped)`. `capped=True` significa que el
    comparable es demasiado distinto y debe descartarse: ajustar un 40% no es
    ajustar, es inventar (doc 05 §4.2).

    **Regla clave: sin dato, sin ajuste.** Si el comparable no declara estado,
    el coeficiente es 1,00. Nunca se imputa un valor por defecto optimista ni
    pesimista.
    """
    detalle: dict[str, object] = {}
    total = D("1.00")

    def apply(nombre: str, valor: object, coef_comp: Decimal, coef_subj: Decimal) -> None:
        nonlocal total
        # El ajuste es RELATIVO: cuánto vale el comparable respecto del sujeto.
        rel = coef_comp / coef_subj if coef_subj else D("1.00")
        if rel != 1:
            detalle[nombre] = {"valor": str(valor), "coef": str(rel.quantize(D("0.0001")))}
        total *= rel

    cond = cfg["condition"]
    apply(
        "estado",
        comp.condition,
        _d(cond[comp.condition]) if comp.condition else D("1.00"),
        _d(cond[subject.condition]) if subject.condition else D("1.00"),
    )
    apply(
        "antiguedad",
        comp.age_years,
        _coef_age(cfg, comp.age_years),
        _coef_age(cfg, subject.age_years),
    )

    ori = cfg["orientation"]
    apply(
        "orientacion",
        comp.orientation,
        _d(ori[comp.orientation]) if comp.orientation else D("1.00"),
        _d(ori[subject.orientation]) if subject.orientation else D("1.00"),
    )

    fc, fdesc = _coef_floor(cfg, comp)
    fs, _ = _coef_floor(cfg, subject)
    apply("piso", fdesc, fc, fs)

    ac, adesc = _coef_amenities(cfg, comp)
    as_, _ = _coef_amenities(cfg, subject)
    apply("amenities", adesc, ac, as_)

    # La antigüedad del AVISO (no del inmueble). Entra acá y no afuera porque
    # tiene que pasar por el tope y quedar en el detalle como todos los demás:
    # estaba en `engine.value()` después de calcular `capped`, así que el
    # producto real podía caer bajo el piso de 0,75 y el `total` que se
    # persiste no lo incluía. Ver doc 05 §4.2, reglas 1 y 3.
    if comparable is not None:
        apply(
            "antiguedad_del_aviso",
            comparable.days_published,
            listing_age_coef(cfg, comparable),
            D("1.00"),
        )

    lo, hi = _d(cfg["rules"]["min_total_adjustment"]), _d(cfg["rules"]["max_total_adjustment"])
    capped = not (lo <= total <= hi)
    detalle["total"] = str(total.quantize(D("0.0001")))
    detalle["capped"] = capped
    return total, detalle, capped


def parking_value(cfg: dict[str, Any], spaces: int) -> Decimal:
    """Valor absoluto, no proporcional: una cochera no vale más caro por estar
    en un departamento caro (doc 13, Q12)."""
    return _d(cfg["absolute_usd"]["parking_space"]) * spaces


def listing_age_coef(cfg: dict[str, Any], comp: Comparable) -> Decimal:
    """Precio de oferta no convalidado por el mercado."""
    if comp.days_published is None:
        return D("1.00")
    for band in cfg["listing_age"]:
        if comp.days_published <= band["max_days"]:
            return _d(band["coef"])
    return D("1.00")
