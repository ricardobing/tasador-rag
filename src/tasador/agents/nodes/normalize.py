"""Nodo 1 — `normalize_subject`. Determinístico, sin LLM, costo $0.

Desarma la dirección, la geocodifica y resuelve el barrio. Es el nodo más
barato del grafo y el que más informes puede salvar de salir mal: **sin barrio
no hay comparables**, y un barrio equivocado produce un informe entero sobre la
zona incorrecta, con números perfectamente calculados y perfectamente inútiles.

Por eso falla explícito en vez de adivinar (doc 04, nodo 1). Fallar acá cuesta
$0 y da un mensaje claro; adivinar cuesta el informe completo y la confianza
del cliente.
"""

from __future__ import annotations

from typing import Any

import structlog

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeError, NodeResult
from tasador.agents.state import ReportState
from tasador.corpus.resolve import Barrios
from tasador.geocoding import Geocoder, normalizar_direccion
from tasador.settings import get_settings

log = structlog.get_logger()


async def normalize_subject(state: ReportState, cfg: NodeConfig) -> NodeResult:
    from tasador.db.base import get_session_factory

    subject: dict[str, Any] = dict(state.get("subject") or {})
    crudo = (subject.get("address_raw") or "").strip()
    if not crudo:
        raise NodeError("SIN_DIRECCION", "La propiedad no tiene dirección.")

    dir_ = normalizar_direccion(crudo)
    subject["street"] = dir_.calle
    subject["street_number"] = dir_.altura
    subject["unit"] = dir_.unidad
    subject["address_normalized"] = dir_.normalizada
    # Una altura "al 2200" ubica la cuadra, no el portal. Se registra porque
    # baja la precisión de la geocodificación y el informe tiene que saberlo.
    subject["street_number_approx"] = dir_.altura_aproximada

    ciudad = subject.get("city") or "CABA"
    detalle: dict[str, Any] = {
        "direccion_normalizada": dir_.normalizada,
        "altura_aproximada": dir_.altura_aproximada,
    }

    async with get_session_factory()() as session:
        barrios = await Barrios.cargar(session)

    # ── Geocodificar ─────────────────────────────────────────────────────
    geo = Geocoder(offline=bool(cfg.param("offline", False)))
    ubic = None
    if subject.get("lat") and subject.get("lng"):
        # Ya venía geocodificada (carga manual o regeneración): no se
        # re-consulta. El rate limit de OSM es un recurso compartido.
        detalle["geocode"] = "ya_tenia_coordenadas"
    else:
        ubic = await geo.geocodificar(
            dir_.para_geocodificar,
            ciudad=ciudad,
            tipo="direccion",
            # Se le pasan calle y altura por separado para que la elección
            # verifique que el resultado ESTÁ en esa dirección, en vez de
            # filtrar por tipo de entidad — ver `_elegir_direccion`.
            calle=dir_.calle,
            altura=dir_.altura,
        )
        if ubic is not None:
            subject["lat"], subject["lng"] = str(ubic.lat), str(ubic.lng)
            subject["geocode_source"] = ubic.fuente
            subject["geocode_confidence"] = round(min(ubic.confianza, 1.0), 2)
            detalle["geocode"] = ubic.display_name[:120]
        else:
            # No es fatal todavía: el barrio puede salir del texto.
            subject["geocode_source"] = "NONE"
            detalle["geocode"] = "sin_resultado"

    # ── Resolver el barrio, de la señal más fuerte a la más débil ────────
    barrio, via = None, None

    if subject.get("neighborhood_id"):
        barrio = barrios.por_id(subject["neighborhood_id"])
        via = "ya_venia"

    if barrio is None and ubic is not None and ubic.barrio_osm:
        barrio, via = barrios.por_nombre(ubic.barrio_osm), "osm_barrio"

    if barrio is None and ubic is not None:
        barrio, via = barrios.en_texto(ubic.display_name), "osm_display_name"

    if barrio is None:
        # El usuario puede haber escrito "Cabildo 2530, Belgrano".
        barrio, via = barrios.en_texto(crudo), "texto_de_la_direccion"

    if barrio is None and subject.get("lat"):
        cercano = barrios.por_coordenadas(float(subject["lat"]), float(subject["lng"]))
        if cercano is not None:
            barrio, via = cercano[0], f"centroide_mas_cercano_{cercano[1]}m"

    if barrio is None:
        # Fallar acá es la decisión correcta y está costeada: $0 gastados.
        raise NodeError(
            "BARRIO_NO_RESUELTO",
            f"No pudimos ubicar '{crudo}' en ningún barrio conocido. "
            f"Probá agregando el barrio a la dirección.",
        )

    subject["neighborhood_id"] = str(barrio.id)
    subject["neighborhood_name"] = barrio.name
    detalle["barrio"] = barrio.name
    detalle["barrio_via"] = via

    # ── Superficie ponderada ─────────────────────────────────────────────
    # No se calcula acá: es una propiedad de `valuation.models.Property` y
    # duplicar la fórmula sería tener dos verdades. Se informa para la traza.
    from tasador.agents.nodes.valuation import _prop

    if (sup := _prop(subject).surface_weighted) is not None:
        detalle["superficie_ponderada"] = str(sup)

    s = get_settings()
    if not subject.get("surface_total") and not subject.get("surface_covered"):
        # Sin superficie el nodo 7 devuelve SIN_SUPERFICIE_SUJETO igual, pero
        # avisar acá ahorra recorrer los nodos 2 a 6 al pedo.
        log.warning("el sujeto no declara superficie", report_id=state["report_id"])
        detalle["aviso"] = "sin_superficie"

    detalle["min_comparables"] = s.min_comparables
    await _persistir_normalizacion(state, subject)
    return NodeResult(updates={"subject": subject}, detail=detalle)


async def _persistir_normalizacion(state: ReportState, subject: dict[str, Any]) -> None:
    """Baja el resultado del nodo 1 a `core.subject_properties`.

    Faltaba. Medido el 14/08: **38 propiedades sujeto, 0 con `neighborhood_id`.**
    El barrio se resolvía, viajaba en el estado del grafo y quedaba en el
    `detail` del evento, pero la fila nunca se actualizaba. La columna y su FK
    estaban desde la migración inicial, esperando.

    Lo que costaba: la columna "Barrio" del listado (doc 07 §3) salía vacía
    siempre, el filtro por barrio de esa misma pantalla no tenía sobre qué
    filtrar, y el error por barrio de `/calidad` (doc 09 §4.2) tampoco.

    Se escribe con `update` y no leyendo el objeto: el nodo no necesita la fila
    entera, y una escritura por columna deja claro qué es del nodo 1 y qué no.
    """
    import uuid as _uuid

    from sqlalchemy import update

    from tasador.db.base import get_session_factory
    from tasador.db.models import SubjectProperty

    campos: dict[str, Any] = {}
    if subject.get("neighborhood_id"):
        campos["neighborhood_id"] = _uuid.UUID(str(subject["neighborhood_id"]))
    for origen, destino in (
        ("lat", "lat"),
        ("lng", "lng"),
        ("street", "street"),
        ("street_number", "street_number"),
        ("geocode_source", "geocode_source"),
        ("geocode_confidence", "geocode_confidence"),
    ):
        if subject.get(origen) is not None:
            campos[destino] = subject[origen]

    if not campos:
        return

    async with get_session_factory()() as session:
        await session.execute(
            update(SubjectProperty)
            .where(SubjectProperty.id == _uuid.UUID(str(state["subject_property_id"])))
            .values(**campos)
        )
        await session.commit()
