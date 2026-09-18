"""corpus.listing_chunks: fragmentos embebidos, en lugar de un vector por aviso

`listing_embeddings` (un vector por aviso, vacía desde el 13/08) se reemplaza
por `listing_chunks`: varios fragmentos por aviso, cada uno con su texto, su
conteo de tokens, el hash del texto (identidad del trabajo hecho), la versión
del chunker y el modelo en la clave, y una columna `tsv` generada para la
mitad léxica de la búsqueda híbrida (doc 18 §3.3 y §3.4).

Por qué versión y modelo en la clave: un embedding es una interpretación,
igual que `listing_features`. Con la versión en la clave conviven dos modelos
indexados y se comparan sin reindexar, que es justo lo que el eval necesita.

Sin índice vectorial: la columna no tiene dimensión fija (conviven modelos
mientras se comparan) y la búsqueda es exacta sobre el pool filtrado (ADR-012).

Revision ID: 7a1d4e9c2b58
Revises: 5f2a9c31be47
Create Date: 2026-09-18 00:01:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a1d4e9c2b58"
down_revision: str | Sequence[str] | None = "5f2a9c31be47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS corpus.idx_listing_embeddings_hnsw")
    op.drop_table("listing_embeddings", schema="corpus")

    op.create_table(
        "listing_chunks",
        sa.Column("listing_id", sa.UUID(), nullable=False),
        sa.Column("chunk_ix", sa.SmallInteger(), nullable=False),
        sa.Column("chunker_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.SmallInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        # Sin dimensión: conviven modelos de 384 y 1024 d mientras se comparan
        # (ADR-010). Sin HNSW: exige dimensión fija y a esta escala la búsqueda
        # exacta sobre el pool filtrado alcanza (ADR-012).
        sa.Column("embedding", pgvector.sqlalchemy.Vector(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('spanish', text)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["corpus.listings.id"],
            name=op.f("fk_listing_chunks_listing_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "listing_id", "chunk_ix", "chunker_version", "model", name=op.f("pk_listing_chunks")
        ),
        schema="corpus",
    )
    op.create_index("idx_listing_chunks_listing", "listing_chunks", ["listing_id"], schema="corpus")
    op.create_index(
        "idx_listing_chunks_tsv",
        "listing_chunks",
        ["tsv"],
        schema="corpus",
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("idx_listing_chunks_tsv", table_name="listing_chunks", schema="corpus")
    op.drop_index("idx_listing_chunks_listing", table_name="listing_chunks", schema="corpus")
    op.drop_table("listing_chunks", schema="corpus")

    op.create_table(
        "listing_embeddings",
        sa.Column("listing_id", sa.UUID(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1024), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["corpus.listings.id"],
            name=op.f("fk_listing_embeddings_listing_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("listing_id", name=op.f("pk_listing_embeddings")),
        schema="corpus",
    )
    op.execute(
        "CREATE INDEX idx_listing_embeddings_hnsw ON corpus.listing_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )
