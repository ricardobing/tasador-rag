# syntax=docker/dockerfile:1.9
# Imagen de la API. Multi-stage: las herramientas de build no llegan a la
# imagen final.

FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# ── build ────────────────────────────────────────────────────────────────
FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app

# Capa de dependencias separada del código: cambiar una línea de la app no
# reinstala 40 paquetes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install -r pyproject.toml

# ── runtime ──────────────────────────────────────────────────────────────
FROM base AS runtime

# WeasyPrint necesita estas libs de sistema para renderizar el PDF.
# `upgrade` antes de instalar: la imagen base de Python trae Debian con
# parches de seguridad pendientes (18/09/2026: tres HIGH en pcre2) y trivy
# corta el CI por eso. Lo que se instala tiene que ser lo que Debian ya arregló.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
      libcairo2 libgdk-pixbuf-2.0-0 fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

# Nunca root.
RUN groupadd -r app && useradd -r -g app -u 10001 app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --chown=app:app src/ ./src/
COPY --chown=app:app config/ ./config/
COPY --chown=app:app prompts/ ./prompts/
# La plantilla del PDF (nodo 11). Faltaba, y el nodo fallaba con
# TemplateNotFound solo dentro del contenedor: en Windows el path
# resolvia al repo y parecia andar.
COPY --chown=app:app templates/ ./templates/
# La metodología es corpus del QA (doc 18 §5); el resto de docs/ no viaja.
COPY --chown=app:app docs/05-metodologia-de-valuacion.md ./docs/

# WeasyPrint (fontconfig) y CrewAI (appdirs) escriben en $HOME, que en esta
# imagen no es escribible. Las XDG son la palanca real —`CREWAI_STORAGE_DIR`,
# pese al nombre, es el NOMBRE de la app y no la ruta— y `HOME` cubre el resto,
# porque CrewAI toca $HOME aunque la telemetría esté apagada.
#
# ⚠️ Esta línea tenía un `\n` LITERAL en el medio (`... HOME=/tmp/home \n
# CREWAI_...`). Docker no lo lee como salto de línea: se comía la instrucción
# entera y NINGUNA de las cinco variables quedaba en la imagen. Verificado el
# 14/08 con `docker image inspect --format '{{json .Config.Env}}'`.
#
# Pasaba desapercibido porque `docker-compose.dev.yml` las declara igual en su
# bloque `environment`. O sea: en desarrollo andaba y en PRODUCCIÓN volvían los
# tres bugs de la Etapa 3 §11.3 y §12.2 — fontconfig sin caché, CrewAI con
# PermissionError en $HOME, y la telemetría de CrewAI ENCENDIDA, que es lo que
# doc 10 §3 no permite.
#
# Una línea por variable a propósito: la continuación con `\` es exactamente lo
# que falló.
ENV XDG_CACHE_HOME=/tmp/cache
ENV XDG_DATA_HOME=/tmp/data
ENV HOME=/tmp/home
ENV CREWAI_DISABLE_TELEMETRY=true
ENV CREWAI_TRACING_ENABLED=false
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app alembic.ini ./
# ⚠️ `scripts/` y `ops/` NO estaban, y los comandos documentados los invocan
# DESDE ESTE CONTENEDOR:
#
#     make seed    -> compose run api python scripts/seed.py
#     make backup  -> compose run api bash ops/backup.sh
#     ESTADO §8    -> compose exec api python scripts/seed.py
#
# Los tres fallaban con "No such file or directory". El gate que existía para
# esto —`test_los_shell_scripts_invocados_existen`— verificaba que el archivo
# estuviera EN EL REPO, no que el contenedor que lo corre lo tuviera: el gate
# comprobaba lo que no fallaba. Ahora hay un test que cruza cada comando con lo
# que el Dockerfile de ese servicio copia.
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app ops/ ./ops/
ENV PYTHONPATH=/app/src

# `/data/raw` además de `/data/artifacts`: `settings.data_path` lo autodetecta y
# sin él la API resolvía a `/app/data`, que con `read_only: true` no es
# escribible. Es el `PermissionError: '/app/data'` que quedó en report_events.
# Los dos Dockerfile tienen que crear el MISMO conjunto, y hay un test.
RUN mkdir -p /data/artifacts /data/raw && chown -R app:app /data
USER app

EXPOSE 8000
CMD ["uvicorn", "tasador.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
