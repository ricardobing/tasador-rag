"""Fixtures compartidas.

La regla de ADR-009 no cambia: **los tests no tocan internet y no gastan**. Lo
que sí tocan, cuando está disponible, es una base de datos local — que no es
internet y no cuesta.

## La decisión que importa: cuándo se saltea

La fixture `db` **salta solo si `DATABASE_URL` no está definida**. Si está y la
base no responde, el test FALLA.

Es a propósito. Un `pytest.skip` ante cualquier error convierte el gate en
decorativo: el CI define `DATABASE_URL`, así que si un día Postgres no levanta,
el job sale verde con 30 tests salteados y nadie lo mira. Ya pasó en este
proyecto de otra forma — `pytest tests/architecture` sobre un directorio vacío
salía en rojo diciendo "no tests ran", que se leía como un problema de
configuración y no como que el gate no existía.

El contrato queda así:

    sin DATABASE_URL   -> se saltea, y se dice por qué (una laptop sin Docker)
    con DATABASE_URL   -> corre de verdad, o falla ruidosamente
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# La trampa #5 de la Etapa 3, otra vez y desde otro lado. En Windows el loop
# por defecto es `ProactorEventLoop` y psycopg en modo async lo rechaza:
#
#   InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode
#
# En el worker se resolvió fijando la política al IMPORTAR `worker.py`; acá hay
# que hacerlo antes de que pytest-asyncio cree su loop, o sea al importar el
# conftest. En Linux —el CI y los contenedores— es un no-op.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Lo mismo que `ops/sql/00-extensions.sql`, que es un init script del contenedor
# de Postgres y por lo tanto NO corre en el CI ni en una base que ya existía.
EXTENSIONES = ("vector", "pg_trgm", "unaccent", "pgcrypto", "btree_gin")
SCHEMAS = ("core", "corpus", "eval")


def _url() -> str | None:
    return os.environ.get("DATABASE_URL") or None


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _url()
    if not url:
        pytest.skip(
            "sin DATABASE_URL: los tests que tocan la base se saltean. "
            "Para correrlos: DATABASE_URL=postgresql+psycopg://... uv run pytest"
        )
    return url


@pytest_asyncio.fixture
async def db(database_url: str) -> AsyncIterator[AsyncSession]:
    """Un esquema limpio por test, en su propia base descartable.

    Una base por test y no un `TRUNCATE` entre tests: los CHECK y las FKs de
    este esquema son parte de lo que se está probando (doc 03), y un truncate en
    cascada sobre 18 tablas con 28 FKs es más frágil que crear y tirar.
    """
    from sqlalchemy import text

    # `models` se importa por el efecto de registrar las tablas en
    # `Base.metadata`. Sin esto `create_all` crea CERO tablas y en silencio: el
    # primer INSERT falla con "relation core.organizations does not exist", que
    # se lee como un problema de schemas y no de imports.
    from tasador.db import models  # noqa: F401
    from tasador.db.base import Base

    # `tasador_t_<hex>`: el nombre entra en el límite de 63 caracteres de
    # Postgres aunque el nombre de la base original sea largo.
    nombre = f"tasador_t_{uuid.uuid4().hex[:16]}"
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        await c.execute(text(f'CREATE DATABASE "{nombre}"'))
    await admin.dispose()

    url_test = database_url.rsplit("/", 1)[0] + f"/{nombre}"
    engine = create_async_engine(url_test)
    try:
        async with engine.begin() as c:
            for ext in EXTENSIONES:
                # `IF NOT EXISTS` y no un try: una extensión que falta es un
                # problema de la imagen de Postgres y tiene que verse.
                await c.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
            for s in SCHEMAS:
                await c.execute(text(f"CREATE SCHEMA IF NOT EXISTS {s}"))
            await c.execute(
                text(
                    "CREATE OR REPLACE FUNCTION corpus.norm_text(t text) RETURNS text "
                    "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS "
                    "$$ SELECT lower(public.unaccent('public.unaccent', t)) $$"
                )
            )
            await c.run_sync(Base.metadata.create_all)

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            # El `AsyncEngine` de la base descartable, para los tests que
            # necesitan armar OTRA sesión contra ella.
            #
            # `runner._persist_result` y `base._persist_event` abren su propia
            # sesión a propósito —la traza de un nodo que falló tiene que
            # sobrevivir a la transacción abortada— así que no se les puede
            # inyectar la del test. `session.get_bind()` devuelve el engine
            # SÍNCRONO y `async_sessionmaker` lo rechaza; esto deja el async a
            # mano sin cambiar la firma de la fixture.
            session.info["engine"] = engine
            yield session
    finally:
        await engine.dispose()
        admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
        async with admin.connect() as c:
            await c.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture(autouse=True)
def _rate_limit_limpio() -> None:
    """El rate limit del login vive en memoria del proceso; sin esto, los
    tests que hacen varios logins se limitarían ENTRE SÍ (todos comparten la
    IP "testclient") y el orden de ejecución decidiría cuáles pasan."""
    from tasador.v1.auth import _resetear_rate_limit

    _resetear_rate_limit()
