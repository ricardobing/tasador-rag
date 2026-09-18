"""corpus.listings.surface_weighted: la superficie, como columna

El nodo 2 filtra por superficie con

    coalesce(f.surface_covered, f.surface_total,
             (listings.raw->>'surface_weighted')::numeric)

y un cast de JSONB adentro de un `coalesce` **no es indexable**. Postgres traía
los 8.383 avisos vigentes del barrio, hacía un nested loop contra
`listing_features` fila por fila, y recién después filtraba por superficie:
34.144 buffers para devolver 48 filas.

No es un problema de 117 ms hoy: es la forma. El costo crece lineal con el
stock del barrio, el corpus está diseñado para crecer, y el nodo 2 corre esta
consulta hasta CINCO veces por informe (la escalera de relajación).

La superficie ponderada es un dato del aviso, no una interpretación. Va a una
columna tipada, con un índice parcial sobre los vigentes. En `raw` se sigue
guardando: ahí es el crudo del portal.

El backfill sale del propio `raw`, así que no inventa nada ni depende de una
recaptura. Los avisos sin el dato quedan en NULL, que es lo correcto: el nodo 2
los deja pasar a propósito porque el nodo 4 todavía puede sacar la superficie de
la descripción.

Revision ID: 5f2a9c31be47
Revises: c93e1b7d4a52
Create Date: 2026-08-15 03:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5f2a9c31be47"
down_revision: str | Sequence[str] | None = "c93e1b7d4a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("surface_weighted", sa.Numeric(8, 1), nullable=True),
        schema="corpus",
    )

    # El backfill. `nullif` por los `""` y el regex porque `raw` es texto libre
    # del portal: un `'62,5'` o un `'consultar'` reventarían el cast y se
    # llevarían la migración entera puesta.
    op.execute(
        """
        update corpus.listings
        set surface_weighted = (raw->>'surface_weighted')::numeric
        where nullif(raw->>'surface_weighted', '') ~ '^[0-9]+(\\.[0-9]+)?$'
          and (raw->>'surface_weighted')::numeric between 0 and 999999
        """
    )

    # Sin índice sobre la columna, a propósito. Se creó uno por
    # (neighborhood_id, surface_weighted) y se midió: los buffers de la consulta
    # del nodo 2 quedaron IDÉNTICOS con y sin él. Ver el comentario en
    # `models.Listing.__table_args__`.


def downgrade() -> None:
    op.drop_column("listings", "surface_weighted", schema="corpus")
