"""El núcleo de la ingesta: normaliza, descarta y persiste.

Un solo camino para todos los orígenes. Cada origen produce una `Card`
(`ingest/cards.py`); acá se descarta lo inservible y se persiste lo demás con
upsert idempotente y snapshot de precio.

**Descarte temprano** — el punto que más importa para el costo. Un aviso
inservible no cuesta la fila que ocupa: cuesta la extracción por LLM que se le
va a correr después y que nunca va a servir. Por eso se filtran **antes** de
tocar la base:

  · sin precio ("Consultar precio")  -> nunca puede ser comparable
  · sin superficie                   -> no se puede calcular USD/m²
  · sin dirección                    -> no se puede ubicar ni deduplicar
  · precio en pesos                  -> el mercado de venta opera en USD
  · USD/m² fuera de [300, 12.000]    -> error de carga

A 40 avisos por captura y ~10% de descarte, son 4 extracciones menos por
corrida. Poco en plata; mucho en ruido que no entra al corpus.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Listing, ListingSnapshot, Neighborhood
from tasador.ingest.badata import _norm_key
from tasador.valuation.adjustments import umbrales_de_plausibilidad

log = structlog.get_logger()

# Los umbrales NO están acá: salen de `config/adjustments.yaml`, que es la
# única fuente para los tres que los preguntan (H-16). Ver
# `umbrales_de_plausibilidad`.


@runtime_checkable
class PortalCard(Protocol):
    """Lo que la ingesta necesita de una tarjeta, venga de donde venga."""

    source_id: str
    url: str
    price: Decimal | None
    currency: str | None
    address: str | None
    neighborhood_label: str | None
    description: str | None

    @property
    def surface_weighted(self) -> Decimal | None: ...
    @property
    def usd_per_m2(self) -> Decimal | None: ...


@dataclass(slots=True)
class CaptureStats:
    paginas: int = 0
    vistos: int = 0
    descartados: int = 0
    nuevos: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    cambios_precio: int = 0
    bloqueados: int = 0
    errores: int = 0
    motivos: Counter[str] = field(default_factory=Counter)

    def resumen(self) -> str:
        return (
            f"{self.paginas} páginas · {self.vistos} vistos · {self.descartados} descartados · "
            f"{self.nuevos} nuevos · {self.actualizados} actualizados"
        )


def motivo_descarte(card: PortalCard) -> str | None:
    """Devuelve el motivo si hay que descartar, o None si el aviso sirve.

    El orden va de lo más barato a lo más caro de evaluar, y de lo más común
    a lo más raro.
    """
    u = umbrales_de_plausibilidad()
    if card.price is None:
        return "sin_precio"
    if card.currency != "USD":
        # El mercado de venta opera en dólares; convertir con un tipo de
        # cambio arbitrario mete más error del que resuelve (doc 05 §3).
        return "precio_no_usd"
    sup = card.surface_weighted
    if sup is None:
        return "sin_superficie"
    if not (u.surface_min <= sup <= u.surface_max):
        return "superficie_implausible"
    if not card.address:
        return "sin_direccion"
    m2 = card.usd_per_m2
    if m2 is None or not (u.usd_m2_min <= m2 <= u.usd_m2_max):
        return "usd_m2_fuera_de_rango"
    return None


# El nombre con el que cada atributo de la tarjeta se guarda en `listings.raw`.
#
# ⚠️ **Las claves de la derecha son el contrato con el nodo 2**, que las lee por
# nombre exacto (`_a_candidato` hace `de_crudo("parking_spaces")`). El 14/08 la
# tarjeta traía `parking` y acá se guardaba como `parking`: el dato estaba en
# la base, completo, y el motor de ajustes no lo encontraba porque buscaba
# `parking_spaces`. Un renombre de una palabra que apagó un coeficiente entero.
#
# ⚠️ Y la resolución es por `getattr(card, attr, None)`, que **no falla** si la
# tarjeta no tiene el atributo: simplemente no se guarda. Por eso hay un test
# —`test_toda_tarjeta_cumple_el_contrato_del_raw`— que cruza este diccionario y
# `CAMPOS_DEL_HASH` contra TODAS las clases de tarjeta. Sin él, `Card`
# estuvo sin `age_years` ni `orientation` mientras las dos estaban en el hash.
CAMPOS_DEL_RAW: dict[str, str] = {
    "surface_total": "surface_total",
    "surface_covered": "surface_covered",
    "surface_semi": "surface_semi",
    "surface_uncovered": "surface_uncovered",
    "rooms": "rooms",
    "bedrooms": "bedrooms",
    "bathrooms": "bathrooms",
    "parking": "parking_spaces",
    "age_years": "age_years",
    # La orientación DECLARADA por el portal, ya normalizada al vocabulario de
    # doc 05 en `csv_scan._orientacion_del_portal`.
    "orientation": "orientation",
    # Y el ESTADO, que sale de la MISMA columna del scraper y hasta el 15/08 se
    # tiraba entero. Es el coeficiente más grande del método (1,15 a 0,82).
    "condition": "condition",
    "expenses_ars": "expenses_ars",
    "raw_features": "raw_features",
    "publisher": "publisher",
}


# Los atributos de la tarjeta que van a una COLUMNA de `listings`, no a `raw`.
#
# ⚠️ Mismo peligro que `CAMPOS_DEL_RAW`, y por el mismo motivo: `_upsert` los
# resuelve con `getattr(card, attr, None)`, que **no falla** si la tarjeta no los
# tiene. El campo simplemente queda en NULL y nadie se entera. `published_at`
# estuvo así: la columna en 0 de 8.497 filas con tres mecanismos apagados detrás.
CAMPOS_DE_COLUMNA = (
    "title",
    "published_at",
    "publisher",
    "only_total_surface",
    "ficha_completa",
)


def campos_del_contrato() -> set[str]:
    """Los atributos que una `PortalCard` tiene que exponer.

    `CAMPOS_DEL_RAW` (lo que se persiste en el crudo), `CAMPOS_DEL_HASH` (lo que
    decide si el aviso cambió) y `CAMPOS_DE_COLUMNA` (lo que va a una columna).
    Está acá y no en el test para que la lista tenga un solo dueño.
    """
    return set(CAMPOS_DEL_RAW) | set(CAMPOS_DEL_HASH) | set(CAMPOS_DE_COLUMNA)


# Lo que decide si el aviso CAMBIÓ. Tiene que cubrir todo lo que se persiste y
# mueve un precio: un campo que se guarda y no está acá queda congelado con su
# primer valor —el portal lo cambia y nosotros no nos enteramos— y, peor,
# reingerir para rellenar un mapeo corregido no escribe nada, porque el upsert
# corta antes de tocar la fila. Las dos cosas pasaron el 14/08.
#
# La descripción NO entra, y es deliberado: un retoque de redacción del
# anunciante dispararía una extracción por LLM que se paga y no cambia ningún
# número.
CAMPOS_DEL_HASH = (
    "price",
    "currency",
    "surface_weighted",
    "rooms",
    "bedrooms",
    "bathrooms",
    "parking",
    "age_years",
    "orientation",
    # Sin esto, reingerir para rellenar el mapeo nuevo de `condition` no
    # escribiría NADA: el upsert corta antes de tocar la fila cuando el hash no
    # cambió. Es la lección que este mismo comentario ya tenía escrita.
    "condition",
    "expenses_ars",
    "address",
    # Mueve tres cosas —el coeficiente de antigüedad del aviso, la frescura de
    # la confianza y la regla `aviso_vencido`— así que un cambio del portal acá
    # es un cambio real y tiene que disparar el upsert.
    "published_at",
)


def _content_hash(card: PortalCard) -> str:
    partes = [card.source_id, *(str(getattr(card, a, None)) for a in CAMPOS_DEL_HASH)]
    return hashlib.sha256("|".join(partes).encode()).hexdigest()


def _raw(card: PortalCard) -> dict[str, Any]:
    """Todo lo que la tarjeta trae, en texto. Reprocesar es gratis; volver a
    bajar cuesta."""
    out: dict[str, Any] = {}
    for attr, clave in CAMPOS_DEL_RAW.items():
        if (v := getattr(card, attr, None)) is not None:
            out[clave] = str(v)
    if sup := card.surface_weighted:
        out["surface_weighted"] = str(sup)
    if extra := getattr(card, "raw_attrs", None):
        out["attrs"] = extra
    return out


FICHA_COMPLETA = "ficha_completa"


def _flags(card: PortalCard) -> list[str]:
    """Lo que hay que saber del aviso sin volver a mirarlo.

    `ficha_completa` marca los avisos a los que el scraper les abrió la ficha
    de detalle. Es la diferencia entre saber el estado de la propiedad y no
    saberlo —93,8% contra 0%— y el estado es el coeficiente más grande del
    método. El nodo 2 los usa para decidir qué entra a un informe.
    """
    flags: list[str] = []
    if getattr(card, "only_total_surface", False):
        flags.append("surface_total_only")
    if getattr(card, "ficha_completa", False):
        flags.append(FICHA_COMPLETA)
    return flags


async def _neighborhood_ids(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(Neighborhood))).scalars().all()
    out: dict[str, Any] = {}
    for n in rows:
        out[_norm_key(n.name)] = n.id
        for alias in n.aliases or []:
            out.setdefault(_norm_key(alias), n.id)
    return out


def _match_neighborhood(label: str | None, barrios: dict[str, Any]) -> Any:
    """Un origen manda "Belgrano, Capital Federal"; otro "Departamento en
    Venta en Belgrano C, Belgrano".

    Se prueba el rótulo entero, después cada parte separada por coma, y por
    último el **final** de cada parte palabra por palabra, del sufijo más largo
    al más corto.

    Ese último paso no es adorno. El rótulo de una tarjeta de un portal es
    "Departamento en Venta en Palermo, Capital Federal": ninguna de sus dos
    partes es un barrio —una es la operación y la otra la ciudad— así que la
    versión anterior devolvía None y el aviso entraba sin `neighborhood_id`. El
    nodo 2 filtra por esa columna: 6 de cada 17 avisos de la captura por HTML
    habrían sido invisibles para todos los informes. No se veía porque el otro
    upsert, el que se borró en H-22, directamente no escribía el campo.

    Del más largo al más corto para que "Palermo Chico" gane sobre "Chico" y
    "Villa Urquiza" sobre "Urquiza".
    """
    if not label:
        return None
    if (nid := barrios.get(_norm_key(label))) is not None:
        return nid
    for parte in reversed([p.strip() for p in label.split(",")]):
        if (nid := barrios.get(_norm_key(parte))) is not None:
            return nid
        palabras = parte.split()
        for corte in range(1, len(palabras)):
            if (nid := barrios.get(_norm_key(" ".join(palabras[corte:])))) is not None:
                return nid
    return None


async def _upsert(
    session: AsyncSession, source: str, card: PortalCard, barrios: dict[str, Any], st: CaptureStats
) -> None:
    existente = (
        await session.execute(
            select(Listing).where(Listing.source == source, Listing.source_id == card.source_id)
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    chash = _content_hash(card)
    datos: dict[str, Any] = {
        "source": source,
        "source_id": card.source_id,
        "url": card.url,
        "operation": "SALE",
        "price": card.price,
        "currency": card.currency,
        "price_on_request": False,
        "address_raw": card.address,
        "neighborhood_label": card.neighborhood_label,
        "neighborhood_id": _match_neighborhood(card.neighborhood_label, barrios),
        "description": card.description,
        # Lo único que `upsert_card` escribía y este no. Ahora que es el único
        # upsert, si no estuviera acá se perdería (H-22).
        "title": getattr(card, "title", None),
        "publisher": getattr(card, "publisher", None),
        # A la COLUMNA, no solo a `raw.attrs`. Ver `Card.published_at`:
        # tres mecanismos dependían de esto y estaban apagados.
        "published_at": getattr(card, "published_at", None),
        # A la COLUMNA además de a `raw`: en `raw` es el crudo del portal y no
        # es indexable adentro del `coalesce` del nodo 2 (H-33).
        "surface_weighted": card.surface_weighted,
        "content_hash": chash,
        "raw": _raw(card),
        "quality_flags": _flags(card),
    }

    if existente is None:
        listing = Listing(**datos, first_seen_at=now, last_seen_at=now)
        session.add(listing)
        await session.flush()
        session.add(
            ListingSnapshot(
                listing_id=listing.id, price=card.price, currency=card.currency, observed_at=now
            )
        )
        st.nuevos += 1
        return

    cambio_precio = existente.price != card.price
    if existente.content_hash == chash:
        existente.last_seen_at = now
        existente.active = True
        st.sin_cambios += 1
        return

    for k, v in datos.items():
        setattr(existente, k, v)
    existente.last_seen_at = now
    existente.active = True
    existente.delisted_at = None
    if cambio_precio:
        session.add(
            ListingSnapshot(
                listing_id=existente.id, price=card.price, currency=card.currency, observed_at=now
            )
        )
        st.cambios_precio += 1
    st.actualizados += 1
