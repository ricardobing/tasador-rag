"""La tarjeta: un aviso tal como llega de afuera, crudo y sin interpretar.

Es el contrato de entrada de la ingesta. Cualquier origen —un CSV de un
recolector externo, un JSON exportado de un panel, una carga manual— se traduce
a esta clase y de ahí en adelante todo es un solo camino: descarte temprano,
superficie ponderada, hash de contenido, upsert idempotente, snapshot de
precio. Tener una clase por origen con la misma fórmula copiada es exactamente
cómo dos verdades se separan.

Lo único que la tarjeta CALCULA es lo que pertenece al método y no al origen:
la superficie ponderada de doc 05 §2 y el USD/m² que sale de ella.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from tasador.db.models import Listing


@dataclass(slots=True)
class Card:
    """Un aviso tal como sale del origen. Campos crudos, sin interpretar."""

    source_id: str
    url: str
    price: Decimal | None = None
    currency: str | None = None
    expenses_ars: Decimal | None = None
    address: str | None = None
    neighborhood_label: str | None = None
    publisher: str | None = None
    # Un aviso puede declarar VARIAS superficies (cubierta + semicubierta +
    # descubierta). Guardarlas por separado no es prolijidad: la superficie
    # ponderada de doc 05 §2 las pesa distinto, y colapsarlas en un campo hacía
    # que la última pisara a la primera —bug real detectado por los tests el
    # 13/08: un 4 ambientes de 91 m² quedaba en 10 m², dando USD/m² de 25.900.
    surface_covered: Decimal | None = None
    surface_semi: Decimal | None = None
    surface_uncovered: Decimal | None = None
    surface_total: Decimal | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    age_years: int | None = None
    parking: int | None = None
    # Orientación DECLARADA por el origen, ya normalizada al vocabulario de
    # doc 05 (`frente` / `contrafrente` / `lateral` / `interno`).
    #
    # Precedencia, que es la misma que para todo lo demás (nodo 2,
    # `_a_candidato`): si el nodo 4 extrajo el campo, gana la extracción; esto
    # LLENA EL HUECO cuando la descripción no lo dice o el aviso todavía no se
    # extrajo. No se invirtió la precedencia porque sería cambiar de qué
    # depende un precio sin haberlo medido.
    orientation: str | None = None
    # El ESTADO declarado por el origen, ya normalizado al vocabulario de doc 05
    # §4.1 en `csv_scan._condicion_del_portal`. Es el coeficiente MÁS GRANDE del
    # método —de 1,15 a 0,82— y hasta el 15/08 no llegaba.
    condition: str | None = None
    # ¿El recolector abrió la FICHA de detalle, o solo vio la tarjeta del
    # listado? No es metadato de proceso: decide qué sabe el aviso. Medido con
    # ficha contra sin ficha:
    #
    #     estado        93,8%  vs   0%     orientación   60,8%  vs   0%
    #     ambientes     95,7%  vs 0,5%     baños         94,1%  vs 38,9%
    #
    # Un aviso sin ficha no puede aportar el coeficiente más grande del método.
    # `_upsert` lo persiste como el flag `ficha_completa` en `quality_flags`.
    ficha_completa: bool = False
    # Fecha de publicación DECLARADA por el origen. Sin esto se apagan TRES
    # mecanismos a la vez: `listing_age_coef`, `f_freshness` (15% del score de
    # confianza) y la regla `aviso_vencido` del nodo 6.
    published_at: date | None = None
    title: str | None = None
    description: str | None = None
    # La tira de features cruda, si el origen la trae. El contrato de
    # `CAMPOS_DEL_RAW` tiene que ser TOTAL: es lo que hace seguro el
    # `getattr(card, attr, None)` de `_raw`.
    raw_features: str | None = None
    raw_attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def surface_weighted(self) -> Decimal | None:
        """Superficie ponderada = cubierta + 50% de lo no cubierto (doc 05 §2).

        Comparar por superficie total castiga al departamento sin balcón y
        premia al que tiene patio enorme. El 50% es el criterio de mercado.
        """
        if self.surface_covered is not None:
            extra = (self.surface_semi or Decimal(0)) + (self.surface_uncovered or Decimal(0))
            if extra == 0 and self.surface_total and self.surface_total > self.surface_covered:
                extra = self.surface_total - self.surface_covered
            return self.surface_covered + extra / 2
        if self.surface_total is not None:
            # Solo hay total: se usa y se marca. Resta confianza (doc 05 §2).
            return self.surface_total
        return None

    @property
    def only_total_surface(self) -> bool:
        return self.surface_covered is None and self.surface_total is not None

    @property
    def usd_per_m2(self) -> Decimal | None:
        sup = self.surface_weighted
        if self.price and sup and self.currency == "USD" and sup > 0:
            return (self.price / sup).quantize(Decimal("1"))
        return None

    @property
    def is_usable(self) -> bool:
        """Mínimo para ser candidato a comparable. La curaduría fina es del
        nodo 6; acá solo se descarta lo que no tiene sentido guardar."""
        return bool(self.price and self.currency and self.address)


def usd_per_m2(listing: Listing) -> Decimal | None:
    """USD/m² desde el crudo guardado. Se recalcula en vez de persistirlo:
    si mañana cambia el criterio de superficie ponderada, no hay que migrar
    una columna derivada."""
    sup = listing.raw.get("surface_weighted") if listing.raw else None
    if listing.price and sup and listing.currency == "USD":
        s = Decimal(str(sup))
        if s > 0:
            return (listing.price / s).quantize(Decimal("1"))
    return None
