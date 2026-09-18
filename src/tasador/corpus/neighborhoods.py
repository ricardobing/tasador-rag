"""Barrios de CABA y GBA Norte, con sus alias.

Los alias resuelven un problema real y bastante molesto: los portales inventan
sub-barrios que no existen en la nomenclatura oficial del GCBA ("Palermo Soho",
"Belgrano C", "Las Cañitas"). BA Data usa los 48 barrios oficiales. Sin una
tabla de equivalencias, el corpus y el ancla oficial no se pueden cruzar y el
chequeo de sesgo es imposible.

`badata_key` es el nombre EXACTO como aparece en los CSV del GCBA.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Neighborhood

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class Barrio:
    name: str
    city: str = "CABA"
    province: str = "CABA"
    badata_key: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Los 48 barrios oficiales de CABA. `badata_key` en MAYÚSCULA porque así vienen
# en `departamentos-en-venta-<año>.csv` (verificado: "ALMAGRO").
CABA: list[Barrio] = [
    Barrio("Agronomía", badata_key="AGRONOMIA"),
    Barrio("Almagro", badata_key="ALMAGRO"),
    Barrio("Balvanera", badata_key="BALVANERA", aliases=("Once", "Abasto", "Congreso")),
    Barrio("Barracas", badata_key="BARRACAS", aliases=("Barracas Este", "Barracas Oeste")),
    Barrio(
        "Belgrano",
        badata_key="BELGRANO",
        aliases=(
            "Belgrano C",
            "Belgrano R",
            "Belgrano Chico",
            "Bajo Belgrano",
            "Barrio River",
            "Belgrano Barrancas",
            # OSM devuelve este nombre para la zona de Arribeños y Juramento.
            # Verificado el 13/08: geocodificar "Juramento 2100" da
            # `neighbourhood: "Barrio Chino"`, que sin este alias no resolvía.
            "Barrio Chino",
        ),
    ),
    Barrio("Boedo", badata_key="BOEDO"),
    Barrio("Caballito", badata_key="CABALLITO", aliases=("Primera Junta", "Parque Rivadavia")),
    Barrio("Chacarita", badata_key="CHACARITA"),
    Barrio("Coghlan", badata_key="COGHLAN"),
    Barrio("Colegiales", badata_key="COLEGIALES"),
    Barrio("Constitución", badata_key="CONSTITUCION"),
    Barrio(
        "Flores", badata_key="FLORES", aliases=("Parque Chacabuco", "Flores Norte", "Flores Sur")
    ),
    Barrio("Floresta", badata_key="FLORESTA"),
    Barrio("La Boca", badata_key="BOCA", aliases=("Boca",)),
    Barrio("La Paternal", badata_key="PATERNAL", aliases=("Paternal",)),
    Barrio("Liniers", badata_key="LINIERS"),
    Barrio("Mataderos", badata_key="MATADEROS"),
    Barrio("Monte Castro", badata_key="MONTE CASTRO"),
    Barrio("Monserrat", badata_key="MONSERRAT", aliases=("Montserrat", "Monserrat")),
    Barrio("Nueva Pompeya", badata_key="NUEVA POMPEYA", aliases=("Pompeya",)),
    Barrio("Núñez", badata_key="NUÑEZ", aliases=("Nunez", "Bajo Núñez")),
    Barrio(
        "Palermo",
        badata_key="PALERMO",
        aliases=(
            "Palermo Soho",
            "Palermo Hollywood",
            "Palermo Chico",
            "Palermo Nuevo",
            "Palermo Viejo",
            "Palermo Botánico",
            "Las Cañitas",
            "Barrio Parque",
            "Alto Palermo",
        ),
    ),
    Barrio("Parque Avellaneda", badata_key="PARQUE AVELLANEDA"),
    Barrio("Parque Chacabuco", badata_key="PARQUE CHACABUCO"),
    Barrio("Parque Chas", badata_key="PARQUE CHAS"),
    Barrio("Parque Patricios", badata_key="PARQUE PATRICIOS"),
    Barrio("Puerto Madero", badata_key="PUERTO MADERO", aliases=("Madero", "Dique 1", "Dique 4")),
    Barrio("Recoleta", badata_key="RECOLETA", aliases=("Barrio Norte",)),
    Barrio("Retiro", badata_key="RETIRO"),
    Barrio("Saavedra", badata_key="SAAVEDRA"),
    Barrio("San Cristóbal", badata_key="SAN CRISTOBAL"),
    Barrio(
        "San Nicolás", badata_key="SAN NICOLAS", aliases=("Centro", "Microcentro", "Tribunales")
    ),
    Barrio("San Telmo", badata_key="SAN TELMO"),
    Barrio("Vélez Sársfield", badata_key="VELEZ SARSFIELD"),
    Barrio("Versalles", badata_key="VERSALLES"),
    Barrio("Villa Crespo", badata_key="VILLA CRESPO"),
    Barrio("Villa del Parque", badata_key="VILLA DEL PARQUE"),
    Barrio(
        "Villa Devoto",
        badata_key="VILLA DEVOTO",
        aliases=("Devoto", "Villa Devoto Norte", "Villa Devoto Sur"),
    ),
    Barrio("Villa General Mitre", badata_key="VILLA GRAL. MITRE"),
    Barrio("Villa Lugano", badata_key="VILLA LUGANO"),
    Barrio("Villa Luro", badata_key="VILLA LURO"),
    Barrio("Villa Ortúzar", badata_key="VILLA ORTUZAR"),
    Barrio("Villa Pueyrredón", badata_key="VILLA PUEYRREDON"),
    Barrio("Villa Real", badata_key="VILLA REAL"),
    Barrio("Villa Riachuelo", badata_key="VILLA RIACHUELO"),
    Barrio("Villa Santa Rita", badata_key="VILLA SANTA RITA"),
    Barrio("Villa Soldati", badata_key="VILLA SOLDATI"),
    Barrio("Villa Urquiza", badata_key="VILLA URQUIZA"),
]

# GBA Norte — sin `badata_key`: BA Data solo cubre CABA. Para estos barrios no
# hay ancla oficial y el chequeo de sesgo no aplica. Está dicho a propósito.
GBA_NORTE: list[Barrio] = [
    Barrio("Vicente López", city="Vicente López", province="Buenos Aires"),
    Barrio("Olivos", city="Vicente López", province="Buenos Aires"),
    Barrio("Florida", city="Vicente López", province="Buenos Aires"),
    Barrio("Martínez", city="San Isidro", province="Buenos Aires"),
    Barrio("San Isidro", city="San Isidro", province="Buenos Aires"),
    Barrio("Acassuso", city="San Isidro", province="Buenos Aires"),
    Barrio("Beccar", city="San Isidro", province="Buenos Aires"),
    Barrio("Boulogne", city="San Isidro", province="Buenos Aires"),
    Barrio("San Fernando", city="San Fernando", province="Buenos Aires"),
    Barrio("Tigre", city="Tigre", province="Buenos Aires"),
    Barrio("Nordelta", city="Tigre", province="Buenos Aires"),
]

ALL_BARRIOS = CABA + GBA_NORTE

# Barrios donde opera la inmobiliaria. Es el alcance real de la ingesta: no se
# crawlea el sitio entero, solo donde hace falta.
PANEL_SCOPE = [
    "Palermo",
    "Belgrano",
    "Núñez",
    "Colegiales",
    "Villa Urquiza",
    "Coghlan",
    "Saavedra",
    "Villa Ortúzar",
    "Chacarita",
]


async def seed_neighborhoods(session: AsyncSession) -> tuple[int, int]:
    """Idempotente: correrlo dos veces no duplica ni pisa ediciones manuales
    de los alias."""
    created = updated = 0
    for b in ALL_BARRIOS:
        existing = (
            await session.execute(
                select(Neighborhood).where(Neighborhood.city == b.city, Neighborhood.name == b.name)
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                Neighborhood(
                    name=b.name,
                    city=b.city,
                    province=b.province,
                    badata_key=b.badata_key,
                    aliases=list(b.aliases),
                )
            )
            created += 1
        else:
            # Los alias se SUMAN, no se pisan: si alguien agregó uno a mano
            # desde el panel, no se pierde al re-sembrar.
            faltantes = [a for a in b.aliases if a not in (existing.aliases or [])]
            if faltantes or existing.badata_key != b.badata_key:
                existing.badata_key = b.badata_key
                existing.aliases = [*(existing.aliases or []), *faltantes]
                updated += 1

    await session.commit()
    log.info("barrios sembrados", created=created, updated=updated, total=len(ALL_BARRIOS))
    return created, updated
