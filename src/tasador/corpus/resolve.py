"""Resolver barrios: por nombre, por coordenadas y por cercanía.

Lo usan el nodo 1 (¿en qué barrio está la propiedad?) y el nodo 2 (¿dónde más
busco si acá no hay suficientes?).

**La cercanía sale de los centroides**, no de una tabla de adyacencia escrita a
mano. Fundamento en `scripts/geocode_neighborhoods.py`: la adyacencia sería un
dato inventado e imposible de verificar, y además no es lo que queremos —
queremos CERCA. Dos barrios pueden lindar por una punta y tener sus centros a
4 km, y un comparable de esa punta no sirve más que uno del barrio de al lado.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Neighborhood
from tasador.geocoding import distancia_m, sin_tildes

log = structlog.get_logger()

# Más lejos que esto, decir "está en este barrio" es adivinar. CABA mide ~19 km
# de punta a punta y el barrio más lejos del centro está a 9,7 km (medido), así
# que 4 km es holgado para una asignación por cercanía y ajustado como para no
# mandar una propiedad de Núñez a Villa Lugano.
MAX_M_ASIGNACION = 4000


def clave(s: str) -> str:
    """Normaliza para comparar nombres de barrio.

    Es el mismo criterio que `ingest/badata._norm_key`, replicado acá para que
    este módulo no dependa del cargador de CSVs del GCBA.
    """
    limpio = "".join(c for c in sin_tildes(s or "") if c.isalnum() or c.isspace())
    return " ".join(limpio.upper().split())


@dataclass(frozen=True, slots=True)
class BarrioRef:
    id: uuid.UUID
    name: str
    city: str
    lat: float | None = None
    lng: float | None = None

    @property
    def tiene_centroide(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass(slots=True)
class Barrios:
    """Índice en memoria de los 59 barrios. Se carga una vez por informe."""

    todos: list[BarrioRef]
    _por_clave: dict[str, BarrioRef]

    @classmethod
    async def cargar(cls, session: AsyncSession) -> Barrios:
        filas = (
            (await session.execute(select(Neighborhood).where(Neighborhood.active.is_(True))))
            .scalars()
            .all()
        )
        todos, indice = [], {}
        for n in filas:
            ref = BarrioRef(
                id=n.id,
                name=n.name,
                city=n.city,
                lat=float(n.centroid_lat) if n.centroid_lat is not None else None,
                lng=float(n.centroid_lng) if n.centroid_lng is not None else None,
            )
            todos.append(ref)
            # El nombre oficial gana; los alias solo llenan lo que falta. Si un
            # alias de un barrio coincide con el nombre de otro, manda el nombre.
            indice[clave(n.name)] = ref
            if n.badata_key:
                indice.setdefault(clave(n.badata_key), ref)
            for a in n.aliases or []:
                indice.setdefault(clave(a), ref)
        return cls(todos=todos, _por_clave=indice)

    def por_nombre(self, etiqueta: str | None) -> BarrioRef | None:
        """Resuelve "Belgrano, Capital Federal" o "Palermo Hollywood".

        Prueba la etiqueta entera y después cada parte separada por coma, de la
        más específica a la más general — que es como la escriben los portales.
        """
        if not etiqueta:
            return None
        if (b := self._por_clave.get(clave(etiqueta))) is not None:
            return b
        for parte in reversed([p.strip() for p in etiqueta.split(",")]):
            if (b := self._por_clave.get(clave(parte))) is not None:
                return b
        return None

    def en_texto(self, texto: str | None) -> BarrioRef | None:
        """Busca un nombre de barrio DENTRO de un texto libre.

        Sirve para "Av. Cabildo 2530, Belgrano" y para el `display_name` que
        devuelve Nominatim. Gana el nombre más largo que aparezca: sin eso,
        "Palermo" le ganaría a "Palermo Hollywood" y perderíamos precisión.
        """
        if not texto:
            return None
        objetivo = clave(texto)
        mejor: BarrioRef | None = None
        largo = 0
        for k, ref in self._por_clave.items():
            if len(k) > largo and f" {k} " in f" {objetivo} ":
                mejor, largo = ref, len(k)
        return mejor

    def por_coordenadas(self, lat: float, lng: float) -> tuple[BarrioRef, int] | None:
        """El barrio con el centroide más cercano, si está lo bastante cerca."""
        con_centro = [b for b in self.todos if b.tiene_centroide]
        if not con_centro:
            return None
        mejor = min(con_centro, key=lambda b: distancia_m(lat, lng, b.lat, b.lng))  # type: ignore[arg-type]
        d = distancia_m(lat, lng, mejor.lat, mejor.lng)  # type: ignore[arg-type]
        return (mejor, d) if d <= MAX_M_ASIGNACION else None

    def cercanos(self, barrio_id: uuid.UUID, *, radio_m: int) -> list[BarrioRef]:
        """Los barrios a menos de `radio_m` del centroide del dado, él incluido.

        Ordenados por distancia: si después hay que recortar, se recorta por lo
        más lejano, que es lo que menos aporta.
        """
        origen = next((b for b in self.todos if b.id == barrio_id), None)
        if origen is None or not origen.tiene_centroide:
            return [origen] if origen else []

        con_dist = [
            (distancia_m(origen.lat, origen.lng, b.lat, b.lng), b)  # type: ignore[arg-type]
            for b in self.todos
            if b.tiene_centroide
        ]
        return [b for d, b in sorted(con_dist, key=lambda x: x[0]) if d <= radio_m]

    def por_id(self, barrio_id: uuid.UUID) -> BarrioRef | None:
        return next((b for b in self.todos if b.id == barrio_id), None)
