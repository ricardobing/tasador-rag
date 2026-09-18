"""Nodo 2 y los clusters del corpus: un cluster aporta UN candidato.

El bug que esto impone quedó medido el 14/08 a la tarde: tras aplicar
`dedup_corpus.py --aplicar` sobre la tanda nueva (33% de duplicados en
Palermo), un informe pasó de 22 comparables a 7 — las unidades de una torre
entraban todas juntas por `last_seen` y ocupaban el límite de candidatos, y el
nodo 5 las tiraba DESPUÉS de haber gastado los cupos. El filtro tiene que
estar en el SQL del nodo 2.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.nodes.retrieve import _consulta
from tasador.db.models import Listing, ListingCluster, Neighborhood


async def _barrio(db: AsyncSession) -> Neighborhood:
    n = Neighborhood(name="Palermo", city="CABA", province="CABA")
    db.add(n)
    await db.flush()
    return n


def _aviso(neighborhood_id, i: int) -> Listing:
    return Listing(
        source="PORTAL_A",
        source_id=f"t-{i}",
        operation="SALE",
        currency="USD",
        price=120000 + i,
        address_raw=f"Soler 4200 unidad {i}",
        neighborhood_id=neighborhood_id,
        content_hash=f"{i:064d}"[:64],
        raw={},
    )


async def _traer(db: AsyncSession, barrio_id) -> list[Listing]:
    q = _consulta(
        barrio_ids=[barrio_id],
        sup=None,
        sup_pct=30,
        ambientes=None,
        rooms_delta=1,
        dias=120,
        limite=50,
    )
    return [li for li, _ in (await db.execute(q)).all()]


async def test_de_un_cluster_solo_entra_el_canonico(db: AsyncSession):
    barrio = await _barrio(db)
    avisos = [_aviso(barrio.id, i) for i in range(3)]
    db.add_all(avisos)
    suelto = _aviso(barrio.id, 99)
    db.add(suelto)
    await db.flush()

    cluster = ListingCluster(match_method="EXACT_ADDR", member_count=3, canonical_id=avisos[0].id)
    db.add(cluster)
    await db.flush()
    for a in avisos:
        a.cluster_id = cluster.id
    await db.commit()

    ids = {li.id for li in await _traer(db, barrio.id)}
    assert avisos[0].id in ids, "el canónico entra"
    assert avisos[1].id not in ids and avisos[2].id not in ids, "los duplicados no"
    assert suelto.id in ids, "un aviso sin cluster entra siempre"


async def test_un_cluster_sin_canonico_no_calla_a_sus_miembros(db: AsyncSession):
    """`canonical_id` es SET NULL si el aviso canónico se borra. Ese estado no
    puede hacer desaparecer al cluster entero del mercado."""
    barrio = await _barrio(db)
    avisos = [_aviso(barrio.id, i) for i in range(2)]
    db.add_all(avisos)
    await db.flush()

    cluster = ListingCluster(match_method="FUZZY", member_count=2, canonical_id=None)
    db.add(cluster)
    await db.flush()
    for a in avisos:
        a.cluster_id = cluster.id
    await db.commit()

    ids = {li.id for li in await _traer(db, barrio.id)}
    assert ids == {a.id for a in avisos}


# ── "Solo los enriquecidos": la consulta CORRIENDO, no compilando ────────
#
# ⚠️ Este test existe por un fallo concreto, del 15/08: el filtro se escribió
# con `quality_flags.contains([...])`, mypy pasó, ruff pasó, y CUATRO tests de
# arquitectura sobre la escalera pasaron — porque todos miraban la
# configuración o el fuente, ninguno ejecutaba el SQL.
#
# En producción, los cuatro informes siguientes murieron con
# `NotImplementedError: ARRAY.contains() not implemented for the base ARRAY
# type`. `quality_flags` usa el ARRAY genérico de SQLAlchemy y ahí `contains()`
# no existe; solo el del dialecto de Postgres lo tiene, y eso se ve al
# EJECUTAR.
#
# Un gate que valida la forma del código no reemplaza a uno que corre la
# consulta.


async def _aviso_con_flags(db, *, barrio_id, flags: list[str], sufijo: str):
    from datetime import UTC, datetime
    from decimal import Decimal

    from tasador.db.models import Listing

    li = Listing(
        source="PORTAL_A",
        source_id=f"ficha-{sufijo}",
        url=f"https://x/{sufijo}",
        operation="SALE",
        address_raw=f"Thames {1000 + int(sufijo)}",
        neighborhood_id=barrio_id,
        price=Decimal("240000"),
        currency="USD",
        content_hash=f"h-ficha-{sufijo}",
        surface_weighted=Decimal("62.0"),
        quality_flags=flags,
        active=True,
        last_seen_at=datetime.now(UTC),
        raw={"rooms": "3", "surface_weighted": "62.0"},
    )
    db.add(li)
    await db.flush()
    return li


async def test_el_filtro_de_ficha_completa_corre_contra_postgres(db):
    """El invariante: con la restricción vuelve SOLO el enriquecido."""
    from decimal import Decimal

    from tasador.agents.nodes.retrieve import _consulta
    from tasador.db.models import Neighborhood

    barrio = Neighborhood(name="Palermo", city="CABA", province="CABA")
    db.add(barrio)
    await db.flush()

    con = await _aviso_con_flags(db, barrio_id=barrio.id, flags=["ficha_completa"], sufijo="1")
    sin = await _aviso_con_flags(db, barrio_id=barrio.id, flags=[], sufijo="2")
    otro = await _aviso_con_flags(db, barrio_id=barrio.id, flags=["surface_total_only"], sufijo="3")
    await db.commit()

    def ids(filas):
        return {str(li.id) for li, _ in filas}

    kw = {
        "barrio_ids": [barrio.id],
        "sup": Decimal("62"),
        "sup_pct": 30,
        "ambientes": 3,
        "rooms_delta": 1,
        "dias": 120,
        "limite": 100,
    }

    todos = (await db.execute(_consulta(**kw, solo_ficha_completa=False))).all()
    assert ids(todos) == {str(con.id), str(sin.id), str(otro.id)}

    # Y acá es donde reventaba.
    solo = (await db.execute(_consulta(**kw, solo_ficha_completa=True))).all()
    assert ids(solo) == {str(con.id)}, (
        "el filtro por `ficha_completa` no devolvió exactamente el enriquecido"
    )


async def test_un_aviso_con_otros_flags_no_cuela(db):
    """`quality_flags` es un array con varios valores: el filtro tiene que
    mirar SI CONTIENE `ficha_completa`, no comparar el array entero."""
    from decimal import Decimal

    from tasador.agents.nodes.retrieve import _consulta
    from tasador.db.models import Neighborhood

    barrio = Neighborhood(name="Palermo", city="CABA", province="CABA")
    db.add(barrio)
    await db.flush()

    mixto = await _aviso_con_flags(
        db, barrio_id=barrio.id, flags=["surface_total_only", "ficha_completa"], sufijo="4"
    )
    await db.commit()

    filas = (
        await db.execute(
            _consulta(
                barrio_ids=[barrio.id],
                sup=Decimal("62"),
                sup_pct=30,
                ambientes=3,
                rooms_delta=1,
                dias=120,
                limite=100,
                solo_ficha_completa=True,
            )
        )
    ).all()
    assert [str(li.id) for li, _ in filas] == [str(mixto.id)]
