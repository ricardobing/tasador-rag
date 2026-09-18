"""Indexado incremental de `corpus.listing_chunks` — doc 18 §3.3 y §3.6.

Idempotente por hash del texto del chunk: correrlo dos veces sin cambios
escribe cero filas. Un aviso cuya descripción cambió tiene chunks nuevos con
hash nuevo; los viejos se borran. Y se guarda **lote a lote**: un timeout a la
mitad deja el trabajo hecho hasta ahí, y el reintento arranca de donde quedó
(la lección del nodo 4, 14/08).

`listings.content_hash` NO cubre la descripción (medido: `CAMPOS_DEL_HASH`),
así que no sirve de disparador. La identidad es el sha256 del texto embebido,
encabezado incluido.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Listing, ListingChunk, ListingFeatures, Neighborhood
from tasador.rag.chunking import Chunker, encabezado
from tasador.rag.embedder import Embedder, hash_texto

log = structlog.get_logger()


@dataclass(slots=True)
class IndexStats:
    avisos_vistos: int = 0
    avisos_sin_cambios: int = 0
    avisos_indexados: int = 0
    chunks_escritos: int = 0
    chunks_borrados: int = 0
    tokens: list[int] = field(default_factory=list)

    def resumen(self) -> str:
        return (
            f"{self.avisos_vistos} vistos · {self.avisos_sin_cambios} sin cambios · "
            f"{self.avisos_indexados} indexados · {self.chunks_escritos} chunks escritos · "
            f"{self.chunks_borrados} borrados"
        )


def _texto_del_aviso(li: Listing) -> str:
    partes = [li.title or "", li.description or ""]
    return "\n\n".join(p for p in partes if p and p.strip())


async def _avisos(
    session: AsyncSession, *, barrio: str | None, limite: int | None, solo_activos: bool
) -> list[tuple[Listing, ListingFeatures | None, str | None]]:
    q = (
        select(Listing, ListingFeatures, Neighborhood.name)
        .outerjoin(ListingFeatures, ListingFeatures.listing_id == Listing.id)
        .outerjoin(Neighborhood, Neighborhood.id == Listing.neighborhood_id)
        .where(func.coalesce(func.length(Listing.description), 0) > 0)
        .order_by(Listing.last_seen_at.desc())
    )
    if solo_activos:
        q = q.where(Listing.active.is_(True))
    if barrio:
        q = q.where(Neighborhood.name == barrio)
    if limite:
        q = q.limit(limite)
    return [(li, ft, nombre) for li, ft, nombre in (await session.execute(q)).all()]


async def indexar(
    session: AsyncSession,
    *,
    chunker: Chunker,
    embedder: Embedder,
    barrio: str | None = None,
    limite: int | None = None,
    lote_avisos: int = 50,
    solo_activos: bool = True,
    dry_run: bool = False,
) -> IndexStats:
    st = IndexStats()
    avisos = await _avisos(session, barrio=barrio, limite=limite, solo_activos=solo_activos)
    st.avisos_vistos = len(avisos)
    contar = embedder.contar_tokens

    for inicio in range(0, len(avisos), lote_avisos):
        tanda = avisos[inicio : inicio + lote_avisos]
        ids = [li.id for li, _, _ in tanda]
        existentes = (
            await session.execute(
                select(ListingChunk.listing_id, ListingChunk.content_hash).where(
                    ListingChunk.listing_id.in_(ids),
                    ListingChunk.chunker_version == chunker.version,
                    ListingChunk.model == embedder.model,
                )
            )
        ).all()
        hashes_previos: dict[uuid.UUID, set[str]] = {}
        for lid, h in existentes:
            hashes_previos.setdefault(lid, set()).add(h)

        pendientes: list[tuple[Listing, list[tuple[int, str, int, str]]]] = []
        for li, ft, nombre in tanda:
            enc = encabezado(
                property_type=(ft.property_type if ft else None)
                or (li.raw or {}).get("property_type"),
                rooms=(ft.rooms if ft else None) or _int((li.raw or {}).get("rooms")),
                surface_m2=li.surface_weighted,
                barrio=nombre,
                floor_number=ft.floor_number if ft else None,
                price=li.price if li.currency == "USD" else None,
                condition=(ft.condition if ft else None) or (li.raw or {}).get("condition"),
            )
            chunks = chunker.chunk(_texto_del_aviso(li), enc, contar)
            filas = [(c.ix, c.text, c.token_count, hash_texto(c.text)) for c in chunks]
            st.tokens.extend(c.token_count for c in chunks)
            if {h for _, _, _, h in filas} == hashes_previos.get(li.id, set()):
                st.avisos_sin_cambios += 1
                continue
            pendientes.append((li, filas))

        if not pendientes or dry_run:
            st.avisos_indexados += len(pendientes)
            st.chunks_escritos += sum(len(f) for _, f in pendientes)
            continue

        textos = [t for _, filas in pendientes for _, t, _, _ in filas]
        vectores = await embedder.pasajes(textos)
        k = 0
        ahora = datetime.now(UTC)
        for li, filas in pendientes:
            borrados = await session.execute(
                delete(ListingChunk).where(
                    ListingChunk.listing_id == li.id,
                    ListingChunk.chunker_version == chunker.version,
                    ListingChunk.model == embedder.model,
                )
            )
            st.chunks_borrados += int(getattr(borrados, "rowcount", 0) or 0)
            for ix, texto, n_tok, h in filas:
                session.add(
                    ListingChunk(
                        listing_id=li.id,
                        chunk_ix=ix,
                        chunker_version=chunker.version,
                        model=embedder.model,
                        text=texto,
                        token_count=n_tok,
                        content_hash=h,
                        embedding=vectores[k],
                        created_at=ahora,
                    )
                )
                k += 1
                st.chunks_escritos += 1
            st.avisos_indexados += 1
        # Lote a lote: lo embebido queda escrito aunque el siguiente lote muera.
        await session.commit()
        log.info(
            "lote indexado", hasta=inicio + len(tanda), de=len(avisos), chunks=st.chunks_escritos
        )

    return st


def _int(v: object) -> int | None:
    try:
        return int(str(v)) if v not in (None, "") else None
    except ValueError:
        return None
