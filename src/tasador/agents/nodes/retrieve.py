"""Nodo 2 — `retrieve_candidates`. Determinístico, sin LLM, costo $0.

Filtro duro primero, semántica después. El error clásico de un RAG mal hecho es
buscar por embedding y filtrar al final; acá la mayor parte de la señal es
estructurada: barrio, superficie, ambientes, moneda y frescura.

⚠ **Corrección al SQL de doc 04 §nodo 2.** El diseño hacía
`JOIN corpus.listing_features`. Con eso, hoy, todo informe sobre mercado
vigente devuelve CERO candidatos: los 25 avisos activos tienen 0 features
(medido el 13/08), porque las features las produce el nodo 4 — que corre
DESPUÉS de este. Era un huevo-gallina en el diseño escrito.

Acá el JOIN es LEFT: lo que haya de features enriquece y ordena, lo que falte
no excluye. El filtro duro por atributos vive en el nodo 6 (`curate`), que es
donde doc 04 ya lo tenía puesto y donde las features ya existen.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import Select, and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeError, NodeResult
from tasador.agents.state import Candidate, ReportState
from tasador.corpus.resolve import Barrios
from tasador.db.models import Listing, ListingCluster, ListingFeatures
from tasador.ingest.core import FICHA_COMPLETA
from tasador.settings import get_settings

log = structlog.get_logger()

# Cuánto se ensancha la búsqueda geográfica en cada escalón. `0` = solo el
# barrio del sujeto. Los metros salen de la medición de centroides: barrios
# que se tocan quedan a 1.500-3.100 m, así que 3.500 m ≈ "los pegados" y
# 7.000 m ≈ "la zona".
RADIO_POR_ALCANCE = {"barrio": 0, "barrio_y_limitrofes": 3500, "comuna": 7000}


def _d(v: Any) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


def _rango(centro: Decimal, pct: float) -> tuple[Decimal, Decimal]:
    factor = Decimal(str(pct)) / Decimal("100")
    return centro * (1 - factor), centro * (1 + factor)


def _consulta(
    *,
    barrio_ids: list[uuid.UUID],
    sup: Decimal | None,
    sup_pct: float,
    ambientes: int | None,
    rooms_delta: int,
    dias: int,
    limite: int,
    solo_ficha_completa: bool = False,
) -> Select[Any]:
    """El SQL de filtros duros. Un solo lugar, parametrizado por el escalón."""
    condiciones = [
        Listing.active.is_(True),
        Listing.operation == "SALE",
        Listing.currency == "USD",
        Listing.price_on_request.is_(False),
        Listing.price.is_not(None),
        Listing.neighborhood_id.in_(barrio_ids),
        Listing.last_seen_at > func.now() - func.make_interval(0, 0, 0, dias),
        # Un cluster aporta UN candidato: el canónico. Sin esto, las 20
        # unidades de una torre entran juntas por `last_seen` y ocupan todo el
        # límite de candidatos — medido el 14/08: un informe de Palermo pasó de
        # 22 comparables a 7 porque 8 eran duplicados de cluster y 41 eran
        # unidades del mismo pozo. El nodo 5 sigue existiendo para los pares
        # que el dedup del corpus todavía no vio (avisos recién ingeridos).
        # `canonical_id IS NULL` pasa: un cluster sin canónico no puede callar
        # a todos sus miembros.
        or_(
            Listing.cluster_id.is_(None),
            ListingCluster.canonical_id.is_(None),
            ListingCluster.canonical_id == Listing.id,
        ),
    ]

    if solo_ficha_completa:
        # ⚠️ SOLO los avisos a los que el scraper les abrió la FICHA de detalle.
        #
        # No es una preferencia estética: es la diferencia entre un aviso que
        # declara su estado y uno que no. Medido sobre la tanda del 15/08, con
        # ficha contra sin ficha:
        #
        #     estado 93,8% vs 0%   ·   orientación 60,8% vs 0%
        #     ambientes 95,7% vs 0,5%   ·   baños 94,1% vs 38,9%
        #
        # Y `condition` es el coeficiente más grande del método: de 1,15 a 0,82.
        # Un comparable sin estado entra al promedio con coeficiente 1,00, o sea
        # se lo trata como "muy bueno" sin que nadie lo haya dicho.
        #
        # Va como escalón de la escalera y no como filtro duro a propósito: en
        # un barrio sin avisos enriquecidos el último escalón lo suelta y el
        # informe sale con la confianza más baja, que es lo honesto. Medido en
        # Palermo: 124 a 294 candidatos con ficha en el primer escalón, contra
        # un `max_candidates` de 60. Alcanza de sobra.
        # `= ANY(array)` y no `.contains([...])`: `quality_flags` está declarado
        # con el `ARRAY` genérico de SQLAlchemy, y ahí `contains()` levanta
        # `NotImplementedError` — solo existe en el ARRAY del dialecto de
        # Postgres. Se ve recién al ejecutar, no al compilar.
        condiciones.append(literal(FICHA_COMPLETA) == func.any(Listing.quality_flags))

    if sup is not None and sup > 0:
        lo, hi = _rango(sup, sup_pct)
        # La superficie puede venir de las features (nodo 4) o del crudo del
        # portal. `coalesce` en ese orden: la interpretación pisa al crudo
        # solo cuando existe.
        sup_col = func.coalesce(
            ListingFeatures.surface_covered,
            ListingFeatures.surface_total,
            # La COLUMNA, no `raw->>'surface_weighted'`. Un cast de JSONB
            # adentro de un `coalesce` no es indexable (H-33).
            Listing.surface_weighted,
        )
        # Un aviso SIN superficie conocida NO se descarta acá: puede tenerla en
        # la descripción y el nodo 4 la va a sacar. Se descarta en el nodo 6 si
        # sigue sin aparecer.
        condiciones.append(or_(sup_col.is_(None), and_(sup_col >= lo, sup_col <= hi)))
        # ⚠️ NO hay un pre-filtro sobre `Listing.surface_weighted` a secas, y no
        # es un olvido: se implementó, se midió y se sacó.
        #
        # La idea era acotar por la columna sola —que sí usa índice— con un
        # cerco más ancho que el rango pedido, dejando el `coalesce` como
        # refinamiento. Medido sobre Palermo, escalón 1 (±30%, 120 días):
        #
        #   sin cerco   62,7 ms   36.237 buffers   1.637 candidatos
        #   con cerco   56,7 ms   33.385 buffers   1.632 candidatos
        #
        # 9% de tiempo a cambio de 5 candidatos, 0,3%. Los comparables son el
        # recurso escaso de este sistema: un informe se cae a INSUFFICIENT_DATA
        # por no llegar al mínimo, no por tardar 6 ms más.
        #
        # El cerco perdía cuando el nodo 4 lee una superficie muy distinta de la
        # que declaró el portal, que es justamente el caso donde la
        # interpretación aporta.

    if ambientes is not None:
        amb_col = func.coalesce(
            ListingFeatures.rooms,
            func.nullif(Listing.raw["rooms"].astext, "").cast(ListingFeatures.rooms.type),
        )
        condiciones.append(
            or_(
                amb_col.is_(None),
                and_(amb_col >= ambientes - rooms_delta, amb_col <= ambientes + rooms_delta),
            )
        )

    return (
        select(Listing, ListingFeatures)
        # LEFT y no INNER: ver el aviso de arriba. Con INNER, hoy, esto
        # devuelve cero.
        .outerjoin(ListingFeatures, ListingFeatures.listing_id == Listing.id)
        .outerjoin(ListingCluster, ListingCluster.id == Listing.cluster_id)
        .where(and_(*condiciones))
        .order_by(Listing.last_seen_at.desc())
        .limit(limite)
    )


def _a_candidato(listing: Listing, feats: ListingFeatures | None) -> Candidate:
    """Fila de la base → contrato del grafo.

    Las features pisan al crudo del portal cuando existen: son una
    interpretación mejor, con su versión de modelo. Lo que no hay queda en
    None y lo llena el nodo 4.
    """
    crudo: dict[str, Any] = listing.raw or {}

    def de_crudo(clave: str) -> Any:
        v = crudo.get(clave)
        return v if v not in (None, "") else None

    c: Candidate = {
        "listing_id": str(listing.id),
        "source": listing.source,
        "url": listing.url,
        "address": listing.address_raw,
        "neighborhood_id": str(listing.neighborhood_id) if listing.neighborhood_id else None,
        "price": str(listing.price),
        "currency": listing.currency or "USD",
        "description": listing.description,
        "days_published": None,
        # NO se inventa: ningún aviso del corpus tiene coordenadas (medido:
        # 0 de 85.023). Cuando las tengan, se calcula acá.
        "distance_m": None,
        "similarity_score": None,
        "amenities": [],
        "feature_source": "listing",
        "field_confidence": {},
        "needs_review": False,
        "included": True,
        "exclusion_reason": None,
    }

    if listing.published_at is not None:
        c["days_published"] = (listing.last_seen_at.date() - listing.published_at).days

    campos = (
        "property_type",
        "rooms",
        "surface_total",
        "surface_covered",
        "age_years",
        "floor_number",
        "has_elevator",
        "condition",
        "orientation",
        "parking_spaces",
        "expenses_ars",
    )
    for campo in campos:
        valor = getattr(feats, campo, None) if feats else None
        if valor is None:
            valor = de_crudo(campo)
        c[campo] = str(valor) if isinstance(valor, Decimal) else valor  # type: ignore[literal-required]

    if feats is not None:
        c["feature_source"] = "extracted"
        c["amenities"] = list(feats.amenities or [])
        c["needs_review"] = bool(feats.needs_review)

    # `surface_weighted` del portal cuando no hay cubierta ni total: es lo que
    # calculó el parser al capturar y es mejor que nada.
    if not c.get("surface_total") and not c.get("surface_covered"):
        c["surface_total"] = de_crudo("surface_weighted")

    return c


async def _buscar(
    session: AsyncSession, cfg: NodeConfig, subject: dict[str, Any], barrios: Barrios
) -> tuple[list[Candidate], int, list[dict[str, Any]]]:
    """Corre la escalera de relajación y devuelve en qué escalón se plantó.

    Cada escalón que se usa BAJA la confianza del informe y queda registrado
    (doc 04, nodo 2). No es cosmético: es la diferencia entre "encontré 25
    comparables" y "encontré 25 comparables después de duplicar el radio".
    """
    s = get_settings()
    objetivo = int(cfg.param("target_candidates", 25))
    limite = int(cfg.param("max_candidates", s.max_candidates))
    escalones: list[dict[str, Any]] = list(cfg.param("relaxation") or [])
    if not escalones:
        escalones = [{"surface_pct": 30, "days": 120, "scope": "barrio", "rooms_delta": 1}]

    barrio_id = uuid.UUID(str(subject["neighborhood_id"]))
    from tasador.agents.nodes.valuation import _prop

    sup = _prop(subject).surface_weighted
    ambientes = subject.get("rooms")

    intentos: list[dict[str, Any]] = []
    mejor: list[Candidate] = []
    # En qué escalón apareció el set que finalmente se devuelve. NO es el
    # último escalón que se probó: relajar y no encontrar nada nuevo no es
    # haber relajado. Cobrarle al informe una penalización de confianza por
    # escalones que no aportaron un solo comparable sería castigarlo por un
    # bucle nuestro. Medido en Belgrano: los 5 escalones daban los mismos 22
    # avisos y el informe salía con la penalización máxima.
    paso_del_mejor = 0

    for paso, esc in enumerate(escalones):
        radio = RADIO_POR_ALCANCE.get(str(esc.get("scope", "barrio")), 0)
        ids = [b.id for b in barrios.cercanos(barrio_id, radio_m=radio)] if radio else [barrio_id]

        filas = (
            await session.execute(
                _consulta(
                    barrio_ids=ids,
                    sup=sup,
                    sup_pct=float(esc.get("surface_pct", 30)),
                    ambientes=ambientes,
                    rooms_delta=int(esc.get("rooms_delta", 1)),
                    dias=int(esc.get("days", 120)),
                    limite=limite,
                    # El default es TRUE: solo se sueltan los avisos sin ficha
                    # cuando el escalón lo dice explícitamente. Un escalón nuevo
                    # que se olvide la clave hereda lo estricto, no lo laxo.
                    solo_ficha_completa=bool(esc.get("require_full_detail", True)),
                )
            )
        ).all()

        candidatos = [_a_candidato(li, ft) for li, ft in filas]
        intentos.append(
            {
                "paso": paso,
                "barrios": len(ids),
                "radio_m": radio,
                "superficie_pct": esc.get("surface_pct"),
                "dias": esc.get("days"),
                # Queda en la traza del informe: si un informe salió con avisos
                # sin ficha, se tiene que poder ver cuál escalón lo permitió.
                "solo_ficha_completa": bool(esc.get("require_full_detail", True)),
                "encontrados": len(candidatos),
            }
        )
        if len(candidatos) > len(mejor):
            mejor, paso_del_mejor = candidatos, paso
        if len(candidatos) >= objetivo:
            return candidatos, paso, intentos

    # Se agotó la escalera. Se devuelve lo mejor que hubo: menos de lo buscado
    # no es un error — el nodo 6 y el 7 deciden si alcanza.
    return mejor, paso_del_mejor, intentos


async def retrieve_candidates(state: ReportState, cfg: NodeConfig) -> NodeResult:
    from tasador.db.base import get_session_factory

    subject = state.get("subject") or {}
    if not subject.get("neighborhood_id"):
        raise NodeError("SIN_BARRIO", "El nodo 1 no dejó un barrio resuelto.")

    semantica = cfg.param("semantic") or {}
    async with get_session_factory()() as session:
        barrios = await Barrios.cargar(session)
        if semantica.get("enabled"):
            # El mismo filtro y la misma escalera; cambia el ORDER BY: el pool
            # se puntúa por similitud (doc 18 §3.4). Apagado por default hasta
            # que la medición de doc 18 §4.4 diga lo contrario.
            from tasador.rag.retriever import buscar_semantico

            candidatos, pasos, intentos = await buscar_semantico(
                session, cfg, dict(subject), barrios
            )
        else:
            candidatos, pasos, intentos = await _buscar(session, cfg, dict(subject), barrios)

    barrio = barrios.por_id(uuid.UUID(str(subject["neighborhood_id"])))
    log.info(
        "candidatos recuperados",
        report_id=state["report_id"],
        barrio=barrio.name if barrio else "?",
        candidatos=len(candidatos),
        relajaciones=pasos,
    )

    return NodeResult(
        updates={"candidates": candidatos, "relaxation_steps": pasos},
        detail={
            "candidates": len(candidatos),
            "relaxation_steps": pasos,
            "barrio": barrio.name if barrio else None,
            "escalera": intentos,
            "sin_features": sum(1 for c in candidatos if c.get("feature_source") == "listing"),
            "semantica": bool(semantica.get("enabled")),
        },
    )
