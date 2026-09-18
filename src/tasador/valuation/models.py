"""Tipos del motor de valuación.

Deliberadamente desacoplados del ORM: el motor recibe estructuras planas y
devuelve estructuras planas. Eso lo hace testeable como función pura y
permite alimentarlo con comparables de cualquier origen —portal, carga
manual, texto pegado, BA Data— sin que se entere de dónde vinieron
(doc 14 §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

D = Decimal


class Condition(StrEnum):
    A_ESTRENAR = "a_estrenar"
    EXCELENTE = "excelente"
    MUY_BUENO = "muy_bueno"
    BUENO = "bueno"
    A_REFACCIONAR = "a_refaccionar"


class Orientation(StrEnum):
    FRENTE = "frente"
    CONTRAFRENTE = "contrafrente"
    LATERAL = "lateral"
    INTERNO = "interno"


class Confidence(StrEnum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAJA = "BAJA"


class InsufficientReason(StrEnum):
    POCOS_COMPARABLES = "pocos_comparables"
    SIN_SUPERFICIE_SUJETO = "sin_superficie_sujeto"
    SIN_BARRIO = "sin_barrio"


@dataclass(slots=True)
class Property:
    """Propiedad, sujeto o comparable. Casi todo opcional a propósito: el dato
    llega tarde por diseño del negocio (doc 03 §3.4)."""

    surface_covered: Decimal | None = None
    surface_semi: Decimal | None = None
    surface_uncovered: Decimal | None = None
    surface_total: Decimal | None = None
    rooms: int | None = None
    age_years: int | None = None
    floor_number: int | None = None
    has_elevator: bool | None = None
    condition: Condition | None = None
    orientation: Orientation | None = None
    parking_spaces: int = 0
    amenities: list[str] = field(default_factory=list)
    expenses_ars: Decimal | None = None

    @property
    def surface_weighted(self) -> Decimal | None:
        """Cubierta + 50% de lo no cubierto (doc 05 §2).

        Comparar por superficie total castiga al departamento sin balcón y
        premia al que tiene patio enorme.
        """
        if self.surface_covered is not None:
            extra = (self.surface_semi or D(0)) + (self.surface_uncovered or D(0))
            if extra == 0 and self.surface_total and self.surface_total > self.surface_covered:
                extra = self.surface_total - self.surface_covered
            return self.surface_covered + extra / 2
        return self.surface_total

    @property
    def only_total_surface(self) -> bool:
        return self.surface_covered is None and self.surface_total is not None


@dataclass(slots=True)
class Comparable:
    """Un comparable con su precio. `ref` es opaco para el motor: puede ser un
    listing_id, una URL o un identificador de carga manual."""

    ref: str
    price: Decimal
    currency: str
    prop: Property
    source: str = "UNKNOWN"
    days_published: int | None = None
    distance_m: int | None = None
    is_manual: bool = False

    @property
    def raw_price_per_m2(self) -> Decimal | None:
        sup = self.prop.surface_weighted
        if sup and sup > 0 and self.currency == "USD":
            return self.price / sup
        return None


@dataclass(slots=True)
class AdjustedComparable:
    comparable: Comparable
    included: bool
    exclusion_reason: str | None = None
    raw_price_per_m2: Decimal | None = None
    adjusted_price_per_m2: Decimal | None = None
    adjustments: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Valuation:
    """Resultado. `insufficient_reason` distinto de None significa que el
    sistema funcionó y la respuesta honesta es que no hay datos — no es un
    error (doc 03 §3.5)."""

    currency: str = "USD"
    value_low: Decimal | None = None
    value_mid: Decimal | None = None
    value_high: Decimal | None = None
    closing_low: Decimal | None = None
    closing_high: Decimal | None = None
    price_per_m2: Decimal | None = None
    weighted_surface: Decimal | None = None
    comparables_found: int = 0
    comparables_used: int = 0
    dispersion: Decimal | None = None
    confidence: Confidence | None = None
    confidence_score: Decimal | None = None
    insufficient_reason: InsufficientReason | None = None
    detail: list[AdjustedComparable] = field(default_factory=list)
    method_version: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.insufficient_reason is None and self.value_mid is not None
