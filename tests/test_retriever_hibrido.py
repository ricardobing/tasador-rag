"""El recuperador híbrido EJECUTA su SQL contra Postgres.

La lección del commit U: cuatro tests de arquitectura pasaban mientras la
consulta reventaba en producción, porque ninguno la ejecutaba. Acá se crean
avisos con chunks de vectores conocidos y se corre el SQL de verdad: el denso
tiene que ordenar por coseno, el léxico por `ts_rank`, y RRF tiene que subir
al que aparece en los dos.

Sin `DATABASE_URL` estos tests se saltean como el resto de los de base.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tasador.db.models import Listing, ListingChunk, Neighborhood
from tasador.rag.retriever import (
    Semantica,
    consulta_lexica,
    fusionar_rrf,
    puntuar_denso,
    puntuar_lexico,
)

DIM = 1024


def _vector(direccion: int, peso: float = 1.0) -> list[float]:
    """Un vector unitario con toda la masa en una coordenada: el coseno con
    otro igual es 1, con uno de otra coordenada es 0."""
    v = [0.0] * DIM
    v[direccion] = peso
    return v


async def _aviso(db, barrio_id, texto: str, vec: list[float]) -> str:  # type: ignore[no-untyped-def]
    li = Listing(
        source="PORTAL_A",
        source_id=uuid.uuid4().hex[:12],
        url="https://portal-a.example/x",
        operation="SALE",
        price=Decimal("200000"),
        currency="USD",
        price_on_request=False,
        address_raw="Thames 1800",
        neighborhood_id=barrio_id,
        description=texto,
        content_hash=uuid.uuid4().hex,
        raw={},
        quality_flags=[],
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        active=True,
    )
    db.add(li)
    await db.flush()
    db.add(
        ListingChunk(
            listing_id=li.id,
            chunk_ix=0,
            chunker_version="test-v1",
            model="test-model",
            text=texto,
            token_count=len(texto.split()),
            content_hash=uuid.uuid4().hex,
            embedding=vec,
        )
    )
    await db.flush()
    return str(li.id)


@pytest.fixture
async def tres_avisos(db):  # type: ignore[no-untyped-def]
    barrio = Neighborhood(
        name=f"Barrio {uuid.uuid4().hex[:6]}", city="CABA", province="CABA", aliases=[]
    )
    db.add(barrio)
    await db.flush()
    a = await _aviso(db, barrio.id, "Departamento a refaccionar, contrafrente, piso 4.", _vector(0))
    b = await _aviso(db, barrio.id, "Unidad impecable a estrenar con cochera fija.", _vector(1))
    c = await _aviso(db, barrio.id, "Monoambiente interno, ideal inversor.", _vector(2))
    return a, b, c


@pytest.mark.asyncio
async def test_el_denso_ordena_por_coseno_contra_la_consulta(db, tres_avisos):  # type: ignore[no-untyped-def]
    a, b, c = tres_avisos
    sem = Semantica(chunker_version="test-v1", model="test-model")
    # Consulta "casi" en la dirección de b, con algo de a.
    q = [0.0] * DIM
    q[1], q[0] = 0.9, 0.3
    puntajes = await puntuar_denso(db, [a, b, c], q, sem)
    assert set(puntajes) == {a, b, c}
    assert puntajes[b] > puntajes[a] > puntajes[c]
    assert puntajes[c] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio
async def test_el_lexico_solo_devuelve_lo_que_matchea_y_pondera_por_rank(db, tres_avisos):  # type: ignore[no-untyped-def]
    a, b, c = tres_avisos
    sem = Semantica(chunker_version="test-v1", model="test-model")
    puntajes = await puntuar_lexico(db, [a, b, c], "cochera a estrenar", sem)
    assert b in puntajes and c not in puntajes
    assert puntajes[b] > puntajes.get(a, 0.0)


@pytest.mark.asyncio
async def test_una_consulta_lexica_rota_no_tira_el_informe(db, tres_avisos):  # type: ignore[no-untyped-def]
    a, b, c = tres_avisos
    sem = Semantica(chunker_version="test-v1", model="test-model")
    assert await puntuar_lexico(db, [a, b, c], "!!! ???", sem) == {}


def test_consulta_lexica_filtra_por_forma_y_deduplica():
    q = consulta_lexica("Cochera fija, cochera FIJA; 3 amb. al frente (piso 4)")
    assert q == "cochera | fija | amb | frente | piso"


def test_rrf_sube_al_que_aparece_en_los_dos_rankings():
    s = fusionar_rrf([["a", "b", "c"], ["c", "a", "x"]], k=60)
    assert s["a"] > s["c"] > s["b"] > s["x"]
    assert fusionar_rrf([], k=60) == {}
