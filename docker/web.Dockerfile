# syntax=docker/dockerfile:1.9
#
# Imagen del front (doc 07, doc 08).
#
# Este archivo faltaba desde la Etapa 0. El servicio `web` del compose lo
# referenciaba igual, así que `docker compose build` —y con él `make up`,
# `make dev` y la instrucción de arranque de ESTADO §8— fallaba entero con
# "failed to read dockerfile". No fallaba el front: fallaba el stack.
#
# El contexto de build es `./web` (así lo declara docker-compose.yml), por eso
# todos los COPY son relativos a esa carpeta y no a la raíz del repo.
#
# Dos targets, porque el compose los usa:
#   dev     -> docker-compose.dev.yml, con `npm run dev` y el código montado
#   runtime -> producción, standalone

FROM node:22-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

# ── deps ─────────────────────────────────────────────────────────────────
FROM base AS deps
COPY package.json package-lock.json* ./
# `npm ci` si hay lock, `npm install` si todavía no. El lock se versiona apenas
# exista: sin él la imagen no es reproducible.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ── dev ──────────────────────────────────────────────────────────────────
# El código llega por volumen (docker-compose.dev.yml), no por COPY: eso es lo
# que da hot reload.
FROM base AS dev
ENV NODE_ENV=development
COPY --from=deps /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ── build ────────────────────────────────────────────────────────────────
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NODE_ENV=production
RUN npm run build

# ── runtime ──────────────────────────────────────────────────────────────
FROM base AS runtime
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0

# Nunca root, igual que api y worker.
RUN addgroup -g 10001 -S app && adduser -u 10001 -S app -G app

# Sin npm en la imagen final. El runtime es `node server.js` y no lo usa, y el
# npm que trae la imagen base arrastra sus propias dependencias (`tar`,
# `brace-expansion`) con CVEs que trivy marca como HIGH/CRITICAL en el CI
# (18/09/2026). Lo que no corre no se escanea: se borra.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx     /usr/local/lib/node_modules/corepack /usr/local/bin/corepack

# `output: standalone` deja un server.js con solo lo que hace falta. Importa
# acá porque el servicio corre con `read_only: true` y un tmpfs chico.
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
COPY --from=builder --chown=app:app /app/public ./public

USER app
EXPOSE 3000
# El healthcheck del compose pega a /api/health con `fetch`. Si esa ruta no
# existe el contenedor queda unhealthy para siempre y `depends_on` no arranca.
CMD ["node", "server.js"]
