"""Entorno de Alembic.

Dos criterios explícitos:
  1. La URL viene del entorno, nunca de alembic.ini. Cero credenciales en git.
  2. Alembic solo administra los schemas `core`, `corpus` y `eval`. Las
     extensiones y los schemas los crea ops/sql/00-extensions.sql al
     inicializar el volumen.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tasador.db.base import Base
from tasador.db.models import *  # noqa: F403  registra los modelos en la metadata
from tasador.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# psycopg3 sirve sync y async con el mismo prefijo `+psycopg`. No hay que
# quitarlo: sin el driver explícito, SQLAlchemy busca psycopg2, que no está
# instalado (y no queremos instalarlo).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata

MANAGED_SCHEMAS = {"core", "corpus", "eval"}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """No tocar nada fuera de nuestros schemas."""
    schema = getattr(obj, "schema", None)
    return not (type_ == "table" and schema not in MANAGED_SCHEMAS)


def render_item(type_, obj, autogen_context):  # type: ignore[no-untyped-def]
    """Alembic no conoce los tipos de pgvector: sin esto, la migración
    generada referencia `pgvector.sqlalchemy.VECTOR` sin importarlo y explota
    con NameError al aplicarla."""
    from pgvector.sqlalchemy import Vector

    if type_ == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector(dim={obj.dim})"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            render_item=render_item,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
