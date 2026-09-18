"""eval.component_runs: la serie de los evals de componente

Doc 09 §3.3 mide extracción, dedup y curaduría contra anotación humana, y hasta
hoy esos números vivían en la consola: el 68% del nodo 4 está en un documento y
no en la base, así que "mejoró el prompt" no se puede responder con una
consulta.

Tabla aparte de `backtest_runs` a propósito. Ver el docstring del modelo: un
MdAPE y una exactitud son dos cosas distintas, y compartir columna porque las
dos son porcentajes produce una serie donde dos filas contiguas no significan
lo mismo.

Revision ID: c93e1b7d4a52
Revises: a1c7f2e40d18
Create Date: 2026-08-14 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c93e1b7d4a52"
down_revision: str | None = "a1c7f2e40d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS eval")
    op.create_table(
        "component_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("componente", sa.Text(), nullable=False),
        sa.Column("metrica", sa.Text(), nullable=False),
        sa.Column("valor", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("n", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("objetivo", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("prompt_bundle_version", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column(
            "detalle",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("git_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "componente in ('extraccion', 'dedup', 'curaduria')",
            name=op.f("ck_component_runs_componente"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_component_runs")),
        schema="eval",
    )
    op.create_index(
        "idx_component_runs_busqueda",
        "component_runs",
        ["componente", "metrica", "created_at"],
        unique=False,
        schema="eval",
    )


def downgrade() -> None:
    op.drop_index("idx_component_runs_busqueda", table_name="component_runs", schema="eval")
    op.drop_table("component_runs", schema="eval")
