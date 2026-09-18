"""Estado del grafo y contrato entre nodos.

Todo el estado tiene que sobrevivir a una serialización a Postgres: si el
worker muere en el nodo 6, LangGraph lo levanta desde el checkpoint. Por eso
acá no hay objetos ricos — hay `dict` y tipos primitivos. Los `Decimal` van
como string: en JSON, un float de dinero pierde centavos.

**El contrato que más importa es `Candidate`.** Lo produce el nodo 2, lo
enriquece el nodo 4, lo agrupa el 5, lo filtra el 6 y lo consume el 7. Que sea
un dict plano y explícito es lo que permite que el nodo 7 —el determinístico,
el que produce el número— no sepa nada de portales, LLMs ni embeddings.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal, TypedDict


def _ultimo(_viejo: Any, nuevo: Any) -> Any:
    """Reducer: el nodo que escribe pisa. Sin esto LangGraph rechaza que dos
    nodos toquen la misma clave."""
    return nuevo


def _concatenar(viejo: list[Any] | None, nuevo: list[Any] | None) -> list[Any]:
    return [*(viejo or []), *(nuevo or [])]


class Candidate(TypedDict, total=False):
    """Un aviso candidato a comparable, en su viaje por los nodos 2 → 7."""

    # ── nodo 2: recuperación ──
    listing_id: str
    source: str
    url: str | None
    address: str | None
    neighborhood_id: str | None
    price: str  # Decimal serializado
    currency: str
    description: str | None
    days_published: int | None
    distance_m: int | None
    similarity_score: float | None

    # ── atributos: vienen del aviso o los completa el nodo 4 ──
    property_type: str | None
    rooms: int | None
    surface_total: str | None
    surface_covered: str | None
    surface_semi: str | None
    surface_uncovered: str | None
    age_years: int | None
    floor_number: int | None
    has_elevator: bool | None
    condition: str | None
    orientation: str | None
    parking_spaces: int | None
    amenities: list[str]
    expenses_ars: str | None

    # ── procedencia de los atributos ──
    # Qué campos salieron de un LLM y con qué confianza. Un campo por debajo
    # del umbral se guarda pero NO ajusta precio: "sin dato, sin ajuste".
    feature_source: Literal["listing", "extracted", "manual"]
    field_confidence: dict[str, float]
    needs_review: bool

    # ── nodo 5: deduplicación ──
    cluster_id: str | None
    is_canonical: bool

    # ── nodo 6: curaduría ──
    included: bool
    exclusion_reason: str | None


class ReportState(TypedDict, total=False):
    """Lo que viaja por el grafo.

    `Annotated[..., _concatenar]` en `events` y `errors` es lo que permite que
    cada nodo agregue lo suyo sin pisar lo anterior.
    """

    # Identidad. Constante durante toda la corrida.
    report_id: str
    org_id: str
    subject_property_id: str

    # nodo 1
    subject: Annotated[dict[str, Any], _ultimo]

    # nodos 2 y 3
    candidates: Annotated[list[Candidate], _ultimo]
    relaxation_steps: Annotated[int, _ultimo]
    captured: Annotated[int, _ultimo]

    # nodos 4, 5 y 6 reescriben `candidates` en su lugar y anotan acá qué
    # hicieron, para que el informe pueda explicarlo.
    extraction: Annotated[dict[str, Any], _ultimo]
    clusters: Annotated[dict[str, Any], _ultimo]
    curation: Annotated[dict[str, Any], _ultimo]

    # nodo 7 — el número
    valuation: Annotated[dict[str, Any], _ultimo]

    # nodos 8, 9 y 10
    market_context: Annotated[dict[str, Any], _ultimo]
    draft_md: Annotated[str, _ultimo]
    critique: Annotated[dict[str, Any], _ultimo]
    critic_rejections: Annotated[int, _ultimo]
    # Lo prende el nodo 10 para mandar el informe de vuelta al 9.
    rehacer: Annotated[bool, _ultimo]

    # Operación
    status: Annotated[str, _ultimo]
    insufficient_reason: Annotated[str | None, _ultimo]
    error_code: Annotated[str | None, _ultimo]
    error_detail: Annotated[str | None, _ultimo]
    events: Annotated[list[dict[str, Any]], _concatenar]
    errors: Annotated[list[str], _concatenar]
    degraded_nodes: Annotated[list[str], _concatenar]


def estado_inicial(report_id: str, org_id: str, subject_property_id: str) -> ReportState:
    return ReportState(
        report_id=report_id,
        org_id=org_id,
        subject_property_id=subject_property_id,
        subject={},
        candidates=[],
        relaxation_steps=0,
        captured=0,
        extraction={},
        clusters={},
        curation={},
        valuation={},
        market_context={},
        draft_md="",
        critique={},
        critic_rejections=0,
        rehacer=False,
        status="RUNNING",
        insufficient_reason=None,
        error_code=None,
        error_detail=None,
        events=[],
        errors=[],
        degraded_nodes=[],
    )


def dec(v: Any) -> Decimal | None:
    """String/número del estado → Decimal. `None` y `""` son lo mismo: no hay dato."""
    if v is None or v == "":
        return None
    return Decimal(str(v))


def dec_str(v: Decimal | None) -> str | None:
    return None if v is None else str(v)
