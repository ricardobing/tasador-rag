# syntax=docker/dockerfile:1.9
# Worker: corre el grafo de LangGraph y la ingesta.
# Comparte la base con la API pero suma supercronic (crons sin cron de root)
# y el cache de modelos de embeddings.

FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install -r pyproject.toml

FROM base AS runtime

ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_SHA=71b0d58cc53f6bd72cf2f293e09e294b79c666d8

# `postgresql-client` para `ops/backup.sh`: el servicio `ingest` corre
# supercronic con esta imagen y el cron del backup invoca `pg_dump`. Sin esto la
# línea del crontab fallaba en silencio dentro del contenedor — que es la peor
# forma posible de no tener backup, porque se cree que está.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
      libcairo2 libgdk-pixbuf-2.0-0 fonts-dejavu-core curl ca-certificates \
      postgresql-client \
    && curl -fsSLO "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && echo "${SUPERCRONIC_SHA}  supercronic-linux-amd64" | sha1sum -c - \
    && chmod +x supercronic-linux-amd64 \
    && mv supercronic-linux-amd64 /usr/local/bin/supercronic \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

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

# Ver la nota extensa en docker/api.Dockerfile: esta misma línea tenía un `\n`
# LITERAL y Docker se comía la instrucción entera. Ninguna de las cinco
# variables llegaba a la imagen, y el worker es justo el que corre CrewAI
# (nodo 8) y WeasyPrint (nodo 11) en producción.
#
# Una línea por variable a propósito: la continuación con `\` es lo que falló.
ENV XDG_CACHE_HOME=/tmp/cache
ENV XDG_DATA_HOME=/tmp/data
ENV HOME=/tmp/home
ENV CREWAI_DISABLE_TELEMETRY=true
ENV CREWAI_TRACING_ENABLED=false
COPY --chown=app:app scripts/ ./scripts/
# `ops/` entero y no solo el crontab: `ops/backup.sh` también se invoca desde un
# contenedor, y copiar un archivo suelto es cómo el de al lado quedó afuera.
COPY --chown=app:app ops/ ./ops/
ENV PYTHONPATH=/app/src

RUN mkdir -p /data/artifacts /data/models /data/raw && chown -R app:app /data
USER app

CMD ["arq", "tasador.worker.WorkerSettings"]
