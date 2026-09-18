"""eval.backtest_runs: la serie histórica de calidad

Doc 09 §5 la pide desde el principio ("todo resultado se guarda en
`eval.backtest_runs` con las tres versiones") y no existía. Los backtests se
imprimían por consola y se perdían: el MdAPE 15,0% de la Etapa 2 vive en un
documento, no en la base, así que no se puede comparar contra el próximo con
una consulta.

El schema `eval` lo crea `ops/sql/00-extensions.sql`, que es un init script del
contenedor de Postgres y por lo tanto **no corre en el CI ni en una base que ya
existía**. La migración lo crea igual, con IF NOT EXISTS: `alembic upgrade head`
sobre una base vacía tiene que bastar, sin depender de que alguien haya montado
el init script primero.

Revision ID: a1c7f2e40d18
Revises: cf54ac6bc9a5
Create Date: 2026-08-14 09:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c7f2e40d18"
down_revision: str | None = "cf54ac6bc9a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS eval")
    op.execute("COMMENT ON SCHEMA eval IS 'Backtest, métricas y golden sets'")

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("sample", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        # Las tres versiones son NOT NULL a propósito: un MdAPE sin saber qué
        # lo produjo no se puede comparar con otro, y una serie de números
        # incomparables es peor que no tener serie.
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("method_version", sa.Text(), nullable=False),
        sa.Column("prompt_bundle_version", sa.Text(), nullable=False),
        sa.Column("n_cases", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_evaluated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # Nullable: una corrida con cobertura 0 es un resultado legítimo. Un 0
        # en MdAPE sería una mentira — "error cero" y "no se midió" no son lo
        # mismo, y es justo la confusión que arruina una serie histórica.
        sa.Column("coverage", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("mdape", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("mape", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("ppe10", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("ppe20", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("hit_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("bias", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("baseline_mdape", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("baseline_ppe20", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column(
            "por_barrio",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "por_confianza",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("git_ref", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_runs")),
        schema="eval",
    )
    op.create_index(
        "idx_backtest_runs_dataset",
        "backtest_runs",
        ["dataset", "created_at"],
        unique=False,
        schema="eval",
    )


def downgrade() -> None:
    op.drop_index("idx_backtest_runs_dataset", table_name="backtest_runs", schema="eval")
    op.drop_table("backtest_runs", schema="eval")
    # El schema NO se borra: puede tener golden sets u otras tablas cargadas a
    # mano, y un downgrade que se lleva puesto trabajo ajeno es peor que uno
    # que deja un schema vacío.
