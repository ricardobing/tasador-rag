"""Modelos del sistema: el corpus de avisos (Etapa 1) y el informe (Etapa 3).

Criterio: los CHECK constraints hacen imposible el estado incoherente en la
base, no solo en la aplicación.

Orden del archivo:
  1. Vocabularios cerrados
  2. `core`   — tenant, usuarios, claves de API
  3. `corpus` — avisos, features, embeddings, índice de mercado, ingesta
  4. `core`   — la propiedad a tasar, el informe y su traza (doc 03 §3.4-3.8)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tasador.db.base import Base

# ── Vocabularios cerrados ────────────────────────────────────────────────
# Casing uniforme desde el día 1 (doc 03 §1, corrección 1).
SOURCES = (
    "PORTAL_A",
    "PORTAL_B",
    "MELI",
    "CRM",
    "PANEL",
    "BADATA",
    "PROPERATI",
    "MANUAL",
    "PASTED",
)
PROPERTY_TYPES = (
    "departamento",
    "casa",
    "ph",
    "local",
    "oficina",
    "cochera",
    "terreno",
    "galpon",
    "otro",
)
CONDITIONS = ("a_estrenar", "excelente", "muy_bueno", "bueno", "a_refaccionar")
ORIENTATIONS = ("frente", "contrafrente", "lateral", "interno")
CURRENCIES = ("USD", "ARS")
OPERATIONS = ("SALE", "RENT")

# ── Etapa 3 ──────────────────────────────────────────────────────────────
# `INSUFFICIENT_DATA` es un estado de ÉXITO operativo, separado de `FAILED` a
# propósito: uno es "el sistema funcionó y la respuesta honesta es que no hay
# datos", el otro es "el sistema se rompió". Confundirlos arruina las
# métricas (doc 03 §3.5).
REPORT_STATUSES = (
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "INSUFFICIENT_DATA",
    "FAILED",
    "CANCELLED",
)
NODE_STATUSES = ("STARTED", "OK", "RETRIED", "FAILED", "SKIPPED")
GEOCODE_SOURCES = ("NOMINATIM", "GOOGLE", "MANUAL", "NONE")
REQUESTED_VIA = ("UI", "API")
CONFIDENCE_LEVELS = ("ALTA", "MEDIA", "BAJA")
SUBJECT_PROPERTY_TYPES = ("departamento", "casa", "ph")
USER_ROLES = ("owner", "admin", "agent", "viewer")


def _enum_check(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} in ({joined})"


# Los defaults de Python no llegan a la base: una columna NOT NULL con
# `default=` a nivel ORM revienta ante cualquier escritura que no pase por
# SQLAlchemy (psql, un script de migración, otra herramienta). Todo NOT NULL
# con default lleva también `server_default`, para que el esquema se sostenga
# solo. Verificado a los golpes el 13/08.
EMPTY_JSONB = text("'{}'::jsonb")
EMPTY_ARRAY = text("'{}'::text[]")
FALSE = text("false")
TRUE = text("true")
ZERO = text("0")
GEN_UUID = text("gen_random_uuid()")  # pgcrypto, ya instalada por ops/sql/00-extensions.sql


class Organization(Base):
    """Tenant. la inmobiliaria es el #1; el sistema no le pertenece."""

    __tablename__ = "organizations"
    __table_args__ = ({"schema": "core"},)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'America/Argentina/Buenos_Aires'")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=TRUE)
    monthly_report_quota: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("200")
    )
    fetch_budget_monthly: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2000")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """Usuario de un tenant.

    Existe ahora porque es el destino de FKs que hasta hoy eran UUID sueltos:
    `listings.created_by`, `listings.verified_by`, `subject_properties.created_by`
    y `reports.requested_by`. Un UUID sin FK que apunta a la nada es un dato
    que miente en silencio.

    La autenticación (login, sesiones) es Etapa 4. Acá está solo la identidad,
    que es lo que las FKs necesitan. Guardar el hash desde ya evita una
    migración de datos después: doc 10 §3 fija argon2id y "email + hash, nada
    más" — ni teléfono, ni nombre completo, ni dirección.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("org_id", "email", name="user_email_unique"),
        CheckConstraint(_enum_check("role", USER_ROLES), name="role"),
        Index("idx_users_org", "org_id", "active"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    # argon2id. NULL = usuario creado por integración, sin login propio.
    password_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'agent'"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=TRUE)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    """Clave de API por tenant (doc 06 §1).

    **Solo el hash.** La clave en claro se muestra una vez, al crearla, y no se
    guarda nunca. `prefix` son los primeros caracteres visibles (`tsk_live_a3f1`)
    y sirven para que un humano identifique cuál revocar sin poder reconstruirla.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="api_key_hash_unique"),
        Index("idx_api_keys_org", "org_id", "revoked_at"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.users.id", ondelete="SET NULL")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Neighborhood(Base):
    """Barrio. `aliases` resuelve el problema real: los portales inventan
    sub-barrios ("Palermo Hollywood", "Las Cañitas") que no existen en la
    nomenclatura oficial del GCBA. `parent_id` los cuelga del barrio real
    para poder cruzar con la serie de BA Data."""

    __tablename__ = "neighborhoods"
    __table_args__ = (
        UniqueConstraint("city", "name", name="neighborhood_unique"),
        Index("idx_neighborhoods_aliases", "aliases", postgresql_using="gin"),
        {"schema": "corpus"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    province: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    badata_key: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.neighborhoods.id")
    )
    centroid_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    centroid_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=TRUE)


class ListingCluster(Base):
    """El mismo inmueble publicado en varios portales.

    Sin esto, una propiedad en Portal A + Portal B + el CRM pesa TRIPLE en la
    mediana y sesga el precio. Es el bug silencioso más peligroso del sistema
    (doc 13, R4). Un cluster aporta un solo comparable: el canónico.
    """

    __tablename__ = "listing_clusters"
    __table_args__ = (
        CheckConstraint(
            _enum_check("match_method", ("EXACT_ADDR", "FUZZY", "EMBEDDING", "LLM", "MANUAL")),
            name="match_method",
        ),
        {"schema": "corpus"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    # FK circular a propósito: `listings.cluster_id` apunta acá y `canonical_id`
    # apunta de vuelta al aviso elegido como representante. `use_alter` hace que
    # Alembic la cree DESPUÉS de las dos tablas, que es la única forma de que el
    # DDL no se trabe. Sin la FK, un canónico podía quedar apuntando a un aviso
    # borrado y el cluster entero se volvía irrecuperable en silencio.
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "corpus.listings.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_listing_clusters_canonical_id",
        ),
    )
    member_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    match_method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Listing(Base):
    """Un aviso del mercado.

    `org_id` NULLABLE es la decisión de fondo (doc 14 §6.1):
      NULL          -> aviso público (Portal A, BA Data). Compartido entre
                       tenants: por eso el costo marginal de un cliente nuevo
                       es casi cero.
      <uuid tenant> -> cargado a mano por ese tenant. SOLO ese tenant lo ve.
                       Lo que un agente sabe de una venta cerrada es
                       información comercial suya.
    """

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="listings_source_unique"),
        CheckConstraint(_enum_check("source", SOURCES), name="source"),
        CheckConstraint(_enum_check("operation", OPERATIONS), name="operation"),
        CheckConstraint(
            "currency is null or " + _enum_check("currency", CURRENCIES), name="currency"
        ),
        CheckConstraint("price is null or price > 0", name="price_positivo"),
        # Un aviso manual sin dueño no tiene sentido y rompería el aislamiento.
        CheckConstraint(
            "source not in ('MANUAL','PASTED') or org_id is not null", name="manual_tiene_org"
        ),
        Index("idx_listings_neigh", "neighborhood_id", "active", "published_at"),
        # ⚠️ NO hay índice por (neighborhood_id, surface_weighted). Se creó, se
        # midió y se tiró: los buffers de la consulta del nodo 2 son IDÉNTICOS
        # con y sin él —37.821 y 36.237, al buffer— porque sin un predicado
        # sobre la columna sola el planner no lo puede usar, y el predicado que
        # lo habilitaba perdía candidatos (ver `retrieve._consulta`). Un índice
        # que no se usa solo cuesta escrituras. H-33.
        Index("idx_listings_cluster", "cluster_id", postgresql_where="cluster_id is not null"),
        Index("idx_listings_org", "org_id", postgresql_where="org_id is not null"),
        Index("idx_listings_stale", "last_seen_at", postgresql_where="active"),
        Index(
            "idx_listings_addr_trgm",
            "address_raw",
            postgresql_using="gin",
            postgresql_ops={"address_raw": "gin_trgm_ops"},
        ),
        {"schema": "corpus"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SALE'"))

    # Corpus público vs. privado del tenant
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.organizations.id", ondelete="CASCADE")
    )

    # Crudo: SIEMPRE se guarda, aunque la extracción falle. Reprocesar es
    # gratis; volver a bajar cuesta plata y riesgo de bloqueo.
    raw_html_path: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSONB)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Normalizado en la ingesta, sin LLM
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(Text)
    price_on_request: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=FALSE)
    address_raw: Mapped[str | None] = mapped_column(Text)
    neighborhood_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.neighborhoods.id")
    )
    neighborhood_label: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    publisher: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[date | None] = mapped_column(Date)
    # ⚠️ COLUMNA y no `raw->>'surface_weighted'`, que es donde vivía.
    #
    # El nodo 2 filtra por superficie, y un cast de JSONB adentro de un
    # `coalesce` no es indexable: Postgres traía los 8.383 avisos del barrio y
    # recién después filtraba. 34.144 buffers para devolver 48 filas, con el
    # costo creciendo lineal con el stock del barrio — y el nodo 2 corre esta
    # consulta hasta cinco veces por informe (H-33).
    #
    # La superficie ponderada es un dato del aviso, no una interpretación. Se
    # sigue guardando también en `raw` porque ahí es el crudo del portal; esta
    # es la misma cifra, tipada.
    surface_weighted: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))

    # Ciclo de vida. `delisted_at` es oro escondido: la diferencia contra
    # `published_at` es el mejor proxy de "tiempo hasta la venta" disponible.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=TRUE)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    delisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.listing_clusters.id", ondelete="SET NULL")
    )
    quality_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )

    # Quién lo cargó a mano y quién lo verificó. SET NULL, no CASCADE: si se
    # borra el usuario, el aviso sobrevive — el dato del mercado no le pertenece.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.users.id", ondelete="SET NULL")
    )
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    features: Mapped[ListingFeatures | None] = relationship(back_populates="listing", uselist=False)


class ListingFeatures(Base):
    """Extracción estructurada. Tabla SEPARADA de `listings` a propósito:

    `listings` es el HECHO (lo que publicaron). `listing_features` es una
    INTERPRETACIÓN, con una versión de modelo y prompt. Cuando mejore el
    extractor se reprocesa esto sin tocar el crudo, y se pueden comparar
    versiones. `extractor_version` dice qué hay que reprocesar.
    """

    __tablename__ = "listing_features"
    __table_args__ = (
        CheckConstraint(
            "property_type is null or " + _enum_check("property_type", PROPERTY_TYPES),
            name="property_type",
        ),
        CheckConstraint(
            "condition is null or " + _enum_check("condition", CONDITIONS), name="condition"
        ),
        CheckConstraint(
            "orientation is null or " + _enum_check("orientation", ORIENTATIONS), name="orientation"
        ),
        CheckConstraint("rooms is null or rooms between 1 and 20", name="rooms_rango"),
        CheckConstraint("surface_total is null or surface_total > 0", name="surface_positiva"),
        Index("idx_listing_features_match", "property_type", "rooms", "surface_covered"),
        {"schema": "corpus"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.listings.id", ondelete="CASCADE"), primary_key=True
    )
    property_type: Mapped[str | None] = mapped_column(Text)
    rooms: Mapped[int | None] = mapped_column(SmallInteger)
    bedrooms: Mapped[int | None] = mapped_column(SmallInteger)
    bathrooms: Mapped[int | None] = mapped_column(SmallInteger)
    surface_total: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    surface_covered: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    floor_number: Mapped[int | None] = mapped_column(SmallInteger)
    age_years: Mapped[int | None] = mapped_column(SmallInteger)
    condition: Mapped[str | None] = mapped_column(Text)
    orientation: Mapped[str | None] = mapped_column(Text)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    parking_spaces: Mapped[int | None] = mapped_column(SmallInteger)
    balcony: Mapped[bool | None] = mapped_column(Boolean)
    amenities: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    expenses_ars: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    credit_eligible: Mapped[bool | None] = mapped_column(Boolean)

    extractor_model: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_version: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=FALSE)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    listing: Mapped[Listing] = relationship(back_populates="features")


class ListingSnapshot(Base):
    """Historial de precio. Solo se inserta cuando el precio CAMBIÓ.

    Habilita un dato que vale por sí solo en el informe: "el 34% de los avisos
    comparables bajó de precio en los últimos 90 días, en promedio un 7%".
    """

    __tablename__ = "listing_snapshots"
    __table_args__ = (
        Index("idx_listing_snapshots_time", "listing_id", "observed_at"),
        {"schema": "corpus"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.listings.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ListingChunk(Base):
    """Un fragmento de un aviso, embebido — doc 18 §3.3 (ADR-011).

    `chunker_version` y `model` están en la clave: un embedding es una
    INTERPRETACIÓN versionada, igual que `listing_features` lo es del texto.
    Dos modelos pueden convivir indexados y compararse sin reindexar.

    `tsv` es una columna generada por Postgres (`to_tsvector('spanish', text)`)
    para la mitad léxica de la búsqueda híbrida (doc 18 §3.4).
    """

    __tablename__ = "listing_chunks"
    __table_args__ = (
        # HNSW sobre IVFFlat por recall/latencia a esta escala (ADR-004), con
        # los defaults de pgvector. A esta escala la búsqueda exacta sobre lo
        # filtrado alcanza; el índice se mide aparte (doc 18 §3.4).
        Index(
            "idx_listing_chunks_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_listing_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("idx_listing_chunks_listing", "listing_id"),
        {"schema": "corpus"},
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.listings.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_ix: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    chunker_version: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('spanish', text)", persisted=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketIndex(Base):
    """Serie de USD/m² por barrio.

    `source='BADATA'`   -> la serie oficial del GCBA (el ancla).
    `source='INTERNAL'` -> la misma serie calculada sobre NUESTRO corpus.

    Comparar las dos es el chequeo de sesgo: si divergen más del umbral, o el
    corpus está sesgado o hay un bug. Y como la serie oficial se construye
    sobre la base de Portal A, un desvío grande señala un problema nuestro.
    """

    __tablename__ = "market_index"
    __table_args__ = (
        UniqueConstraint(
            "neighborhood_id",
            "period",
            "property_type",
            "rooms",
            "condition_group",
            "source",
            name="market_index_unique",
        ),
        CheckConstraint(_enum_check("source", ("BADATA", "PROPERATI", "INTERNAL")), name="source"),
        CheckConstraint(
            "condition_group is null or "
            + _enum_check("condition_group", ("usado", "a_estrenar", "todos")),
            name="condition_group",
        ),
        {"schema": "corpus"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    neighborhood_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.neighborhoods.id")
    )
    period: Mapped[date] = mapped_column(Date, nullable=False)
    property_type: Mapped[str] = mapped_column(Text, nullable=False)
    rooms: Mapped[int | None] = mapped_column(SmallInteger)
    condition_group: Mapped[str | None] = mapped_column(Text)
    usd_per_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class IngestRun(Base):
    """Una corrida de ingesta.

    `blocked` es la métrica a vigilar: si empieza a subir, el portal cambió su
    defensa y hay que actuar ANTES de que el corpus se pudra y los informes
    empiecen a salir mal en silencio.
    """

    __tablename__ = "ingest_runs"
    __table_args__ = (
        CheckConstraint(
            _enum_check("mode", ("DISCOVER", "FETCH", "BACKFILL", "ONDEMAND")), name="mode"
        ),
        CheckConstraint(
            _enum_check("status", ("RUNNING", "OK", "PARTIAL", "FAILED")), name="status"
        ),
        {"schema": "corpus"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'RUNNING'"))
    discovered: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    created: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    blocked: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, server_default=ZERO)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestItem(Base):
    __tablename__ = "ingest_items"
    __table_args__ = (
        CheckConstraint(
            _enum_check(
                "status",
                ("OK", "BLOCKED", "PARSE_ERROR", "SKIPPED_CACHE", "HTTP_ERROR", "SKIPPED_ROBOTS"),
            ),
            name="status",
        ),
        Index("idx_ingest_items_run", "run_id", "status"),
        {"schema": "corpus"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.ingest_runs.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.listings.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryProperty(Base):
    """Inventario propio del tenant (las 285 de la inmobiliaria).

    Fuente: la Supabase de la inmobiliaria, en modo lectura (doc 01 §2.2). Sirve como
    ground truth del backtest y para poder decir en el informe "de estos
    comparables, N son de tu propia cartera" — dato que ningún competidor
    puede dar.
    """

    __tablename__ = "inventory_properties"
    __table_args__ = (
        UniqueConstraint("org_id", "source", "source_id", name="inventory_source_unique"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PANEL'"))
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    address_raw: Mapped[str | None] = mapped_column(Text)
    neighborhood_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.neighborhoods.id")
    )
    neighborhood_label: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    operation: Mapped[str | None] = mapped_column(Text)
    rooms: Mapped[int | None] = mapped_column(SmallInteger)
    surface_total: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    surface_covered: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    published: Mapped[bool | None] = mapped_column(Boolean)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=EMPTY_JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ══════════════════════════════════════════════════════════════════════════
#  core — la propiedad a tasar, el informe y su traza (doc 03 §3.4-3.8)
# ══════════════════════════════════════════════════════════════════════════


class SubjectProperty(Base):
    """La propiedad a tasar.

    **Casi todo es nullable a propósito.** El agente genera el informe *antes*
    de la visita con lo poco que sabe (dirección, tipo, ambientes) y lo
    regenera después con el estado y la orientación reales. Forzar NOT NULL
    modelaría un flujo que no existe. Lo obligatorio es `address_raw` y
    `property_type`: sin eso no hay tasación.

    `external_ref` es OPACO: es un string. El Tasador no sabe ni le importa que
    sea el UUID de una fila en otro sistema (regla de aislamiento #4).
    """

    __tablename__ = "subject_properties"
    __table_args__ = (
        CheckConstraint(_enum_check("property_type", SUBJECT_PROPERTY_TYPES), name="property_type"),
        CheckConstraint(
            "geocode_source is null or " + _enum_check("geocode_source", GEOCODE_SOURCES),
            name="geocode_source",
        ),
        CheckConstraint(
            "condition is null or " + _enum_check("condition", CONDITIONS), name="condition"
        ),
        CheckConstraint(
            "orientation is null or " + _enum_check("orientation", ORIENTATIONS), name="orientation"
        ),
        CheckConstraint("rooms is null or rooms between 1 and 15", name="rooms_rango"),
        CheckConstraint("bedrooms is null or bedrooms between 0 and 12", name="bedrooms_rango"),
        CheckConstraint("bathrooms is null or bathrooms between 0 and 10", name="bathrooms_rango"),
        CheckConstraint("age_years is null or age_years between 0 and 200", name="age_rango"),
        CheckConstraint("surface_total is null or surface_total > 0", name="surface_total_pos"),
        CheckConstraint(
            "surface_covered is null or surface_covered > 0", name="surface_covered_pos"
        ),
        # Cubierta mayor que total es un error de carga, no un caso raro.
        CheckConstraint(
            "surface_covered is null or surface_total is null or surface_covered <= surface_total",
            name="surface_coherente",
        ),
        Index("idx_subject_properties_org", "org_id", "created_at"),
        Index(
            "idx_subject_properties_external",
            "org_id",
            "external_ref",
            postgresql_where="external_ref is not null",
        ),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    external_ref: Mapped[str | None] = mapped_column(Text)

    # Dirección
    address_raw: Mapped[str] = mapped_column(Text, nullable=False)
    street: Mapped[str | None] = mapped_column(Text)
    street_number: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    neighborhood_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.neighborhoods.id")
    )
    city: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CABA'"))
    province: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'CABA'"))
    lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    geocode_source: Mapped[str | None] = mapped_column(Text)
    # Si la geocodificación es mala, el barrio puede estar mal — y el barrio
    # determina los comparables. Se guarda para poder degradar el informe.
    geocode_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))

    # Inmueble
    property_type: Mapped[str] = mapped_column(Text, nullable=False)
    rooms: Mapped[int | None] = mapped_column(SmallInteger)
    bedrooms: Mapped[int | None] = mapped_column(SmallInteger)
    bathrooms: Mapped[int | None] = mapped_column(SmallInteger)
    surface_total: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    surface_covered: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    age_years: Mapped[int | None] = mapped_column(SmallInteger)
    floor_number: Mapped[int | None] = mapped_column(SmallInteger)
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    condition: Mapped[str | None] = mapped_column(Text)
    orientation: Mapped[str | None] = mapped_column(Text)
    amenities: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY
    )
    parking_spaces: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=ZERO)
    expenses_ars: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    notes: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Report(Base):
    """El job y su resultado.

    Las **tres columnas de versión** son el corazón del backtest: sin saber con
    qué versión del motor, de los prompts y del método se generó cada informe,
    comparar corridas no significa nada.

    Los CHECK hacen imposible el estado incoherente: un informe exitoso sin
    valores, o con `low > high`, o un `INSUFFICIENT_DATA` sin explicar por qué,
    NO PUEDEN EXISTIR en la base.
    """

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(_enum_check("status", REPORT_STATUSES), name="status"),
        CheckConstraint(_enum_check("requested_via", REQUESTED_VIA), name="requested_via"),
        CheckConstraint(
            "currency is null or " + _enum_check("currency", CURRENCIES), name="currency"
        ),
        CheckConstraint(
            "confidence is null or " + _enum_check("confidence", CONFIDENCE_LEVELS),
            name="confidence",
        ),
        CheckConstraint(
            "status <> 'SUCCEEDED' or ("
            "value_low is not null and value_mid is not null and value_high is not null "
            "and value_low <= value_mid and value_mid <= value_high)",
            name="valores_coherentes",
        ),
        CheckConstraint(
            "status <> 'INSUFFICIENT_DATA' or insufficient_reason is not null",
            name="insufficient_tiene_razon",
        ),
        # Dos clicks del botón no cuestan el doble (doc 06 §2).
        Index(
            "idx_reports_idempotency",
            "org_id",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key is not null",
        ),
        Index("idx_reports_org_lista", "org_id", "created_at"),
        Index("idx_reports_status", "status", postgresql_where="status in ('QUEUED','RUNNING')"),
        Index("idx_reports_subject", "subject_property_id", "created_at"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    subject_property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.subject_properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'QUEUED'"))
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.users.id", ondelete="SET NULL")
    )
    requested_via: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'UI'"))

    # Versionado. Sin esto el backtest no significa nada (doc 03 §3.5).
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_bundle_version: Mapped[str] = mapped_column(Text, nullable=False)
    method_version: Mapped[str] = mapped_column(Text, nullable=False)

    # Resultado
    currency: Mapped[str | None] = mapped_column(Text)
    value_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    value_mid: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    value_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_per_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    weighted_surface: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    comparables_used: Mapped[int | None] = mapped_column(SmallInteger)
    comparables_found: Mapped[int | None] = mapped_column(SmallInteger)
    dispersion: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    confidence: Mapped[str | None] = mapped_column(Text)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    narrative_md: Mapped[str | None] = mapped_column(Text)
    # La traza completa del cálculo: cada comparable, su USD/m² crudo, cada
    # coeficiente y el resultado. Es lo que le permite al crítico verificar y a
    # un humano auditar seis meses después.
    methodology: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )

    # Operación
    insufficient_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    # Métrica de calidad del prompt de redacción: si sube, algo se degradó.
    critic_rejections: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=ZERO
    )
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, server_default=ZERO)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportComparable(Base):
    """Qué se usó y qué se descartó.

    **La decisión más importante de esta tabla:** se guarda un SNAPSHOT del
    precio y la superficie, no solo la FK al aviso. Un informe entregado en
    septiembre tiene que seguir mostrando en diciembre exactamente los números
    que mostró. Si el aviso bajó de precio o se dio de baja, el informe no puede
    mutar retroactivamente. **Un informe es un documento, no una vista.**

    Y se guardan también los EXCLUIDOS con su motivo, que es lo que permite
    responder "¿por qué no usaste el de Cabildo 2500?".
    """

    __tablename__ = "report_comparables"
    __table_args__ = (
        UniqueConstraint("report_id", "listing_id", name="report_comp_unico"),
        CheckConstraint("included or exclusion_reason is not null", name="excluido_tiene_razon"),
        CheckConstraint(_enum_check("snapshot_currency", CURRENCIES), name="snapshot_currency"),
        Index("idx_report_comparables_report", "report_id", "included"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=GEN_UUID
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.reports.id", ondelete="CASCADE"), nullable=False
    )
    # RESTRICT, no CASCADE: borrar un aviso no puede mutilar un informe ya
    # entregado. Si hace falta borrarlo, primero se decide qué pasa con los
    # informes que lo citan.
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus.listings.id", ondelete="RESTRICT"), nullable=False
    )
    included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    similarity_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    distance_m: Mapped[int | None] = mapped_column(Integer)

    snapshot_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    snapshot_currency: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_surface: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    raw_price_per_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    adjustments: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )
    adjusted_price_per_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportEvent(Base):
    """Traza por nodo del grafo.

    Alimenta el stepper en vivo de la UI y el desglose de costo por informe.
    Es también la traza AUDITABLE del sistema: mientras Langfuse esté apagado,
    esta tabla es la única fuente de verdad sobre qué modelo corrió, cuánto
    gastó y cuánto tardó cada nodo.
    """

    __tablename__ = "report_events"
    __table_args__ = (
        CheckConstraint(_enum_check("status", NODE_STATUSES), name="status"),
        Index("idx_report_events_report", "report_id", "seq"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.reports.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    node: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # La TAREA que se pidió ("extractor"), no el proveedor. El proveedor va en
    # `detail.provider_model`, que es informativo y puede cambiar sin avisar.
    model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )
    trace_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReportArtifact(Base):
    """El PDF generado. `sha256` permite probar que el archivo entregado al
    cliente es exactamente el que se generó."""

    __tablename__ = "report_artifacts"
    __table_args__ = ({"schema": "core"},)

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core.reports.id", ondelete="CASCADE"), primary_key=True
    )
    pdf_path: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    template_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── `eval` — la serie histórica de calidad (doc 09 §5) ───────────────────
class BacktestRun(Base):
    """Una corrida de backtest, con las tres versiones que la explican.

    Doc 09 §5 lo pide desde el principio y hasta el 14/08 no existía: los
    backtests se imprimían en la consola y se perdían. El costo de eso es
    concreto — la pantalla `/calidad` muestra "evolución del MdAPE por versión
    de motor", y sin una fila por corrida esa serie no se puede construir. El
    número del informe de la Etapa 2 ("MdAPE 15,0%") vive hoy en un documento y
    no en la base, así que no se puede comparar contra el próximo por consulta.

    **Las tres versiones no son metadatos decorativos.** Un MdAPE sin saber qué
    prompts, qué coeficientes y qué motor lo produjeron no se puede comparar
    con otro: la serie de `/calidad` sería una línea de números incomparables.
    Por eso las tres son NOT NULL.

    `sample`, `seed` y `dataset` completan la reproducibilidad: con esas tres y
    las versiones, la corrida se repite.
    """

    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("idx_backtest_runs_dataset", "dataset", "created_at"),
        {"schema": "eval"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID
    )
    dataset: Mapped[str] = mapped_column(Text, nullable=False)
    sample: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)

    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    method_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_bundle_version: Mapped[str] = mapped_column(Text, nullable=False)

    n_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    n_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)

    # Todas nullable: una corrida que no evaluó ni un caso es un resultado
    # legítimo (cobertura 0) y tiene que poder guardarse. Un 0 en MdAPE sería
    # una mentira — "error cero" y "no se midió" no son lo mismo.
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    mdape: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    mape: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    ppe10: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    ppe20: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    hit_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    bias: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    baseline_mdape: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    baseline_ppe20: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    # Segmentación de doc 09 §4.2: el número global miente. `por_confianza` es
    # además el chequeo de calibración, que es el que decide si el nivel de
    # confianza que ve el usuario significa algo.
    por_barrio: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )
    por_confianza: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )

    # Contra qué corrida se comparó y qué se decidió. `git_ref` permite atarlo
    # al commit sin depender de que alguien lo anote.
    git_ref: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ComponentRun(Base):
    """Una corrida de eval de COMPONENTE (doc 09 §3.3): extracción, dedup,
    curaduría.

    Tabla aparte de `backtest_runs` y no un `dataset` más adentro de aquella,
    aunque hubiera sido más corto. El backtest mide el error de un PRECIO
    (MdAPE, PPE20, hit rate); un eval de componente mide exactitud o recall
    sobre anotación humana. Meter un 68% de exactitud en la columna `mdape`
    porque "las dos son porcentajes" produce una serie histórica en la que dos
    filas contiguas no significan lo mismo — que es exactamente la forma en que
    un tablero de calidad empieza a mentir.

    `metrica` nombra qué se midió (`exactitud`, `recall`, `precision`) y `valor`
    es el número. Un componente puede reportar varias filas por corrida.
    """

    __tablename__ = "component_runs"
    __table_args__ = (
        CheckConstraint(
            _enum_check("componente", ("extraccion", "dedup", "curaduria")), name="componente"
        ),
        Index("idx_component_runs_busqueda", "componente", "metrica", "created_at"),
        {"schema": "eval"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID
    )
    componente: Mapped[str] = mapped_column(Text, nullable=False)
    metrica: Mapped[str] = mapped_column(Text, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    # Sobre cuántos casos. Una exactitud del 100% sobre 3 casos y otra del 95%
    # sobre 300 no son comparables, y sin esto la serie las pone una al lado de
    # la otra como si lo fueran.
    n: Mapped[int] = mapped_column(Integer, nullable=False, server_default=ZERO)
    objetivo: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_bundle_version: Mapped[str] = mapped_column(Text, nullable=False)
    # Qué prompt EXACTO corrió. El bundle cubre a todos los nodos juntos; esto
    # dice cuál de ellos se estaba midiendo, que es lo que se itera.
    prompt: Mapped[str | None] = mapped_column(Text)
    task: Mapped[str | None] = mapped_column(Text)

    detalle: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )
    git_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
