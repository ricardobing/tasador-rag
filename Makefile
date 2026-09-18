# Makefile — para el VPS (Linux) y el CI.
# En Windows usar .\make.ps1 con los mismos objetivos.

COMPOSE     := docker compose
COMPOSE_DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.DEFAULT_GOAL := help
.PHONY: help setup dev up down logs ps test lint fmt typecheck migrate seed eval build verificar-imagen clean shell psql backup

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup:      ## Instala dependencias y crea .env
	@command -v uv >/dev/null || { echo "falta uv"; exit 1; }
	@test -f .env || { cp .env.example .env; echo ".env creado — completar valores"; }
	uv sync --extra dev

dev:        ## Stack en desarrollo con hot reload
	$(COMPOSE_DEV) up --build

up:         ## Stack como en producción
	$(COMPOSE) up -d --build && $(COMPOSE) ps

down:       ## Baja el stack
	$(COMPOSE) down

logs:       ## Sigue los logs
	$(COMPOSE) logs -f --tail 100

ps:         ## Estado de los contenedores
	$(COMPOSE) ps

# ⚠️ EXIGE `DATABASE_URL`. Sin ella se saltean 58 de 368 tests —entre ellos todo
# el aislamiento multi-tenant en runtime y el contrato HTTP— y el comando sale
# VERDE. El CI tiene un paso que falla si hubo skips; acá la protección es
# negarse a correr.
test:       ## Tests — sin tocar internet (fixtures + VCR). Necesita DATABASE_URL
	@test -n "$$DATABASE_URL" || { \
	  echo "falta DATABASE_URL: sin ella se saltean 58 tests y esto sale verde sin probarlos."; \
	  echo "  export DATABASE_URL=postgresql+psycopg://tasador:<PASS>@127.0.0.1:5433/tasador"; \
	  exit 1; }
	ENV=test FETCHER_STRATEGY=fixture uv run pytest -m "not live"

# ⚠️ Los tres tienen que correr EXACTAMENTE lo mismo que .github/workflows/ci.yml.
# `migrations/` estaba afuera del lint del Makefile —el CI sí lo incluye desde
# el 14/08— y ahí es donde se habían acumulado 11 errores que nadie veía.
lint:       ## ruff check (mismo alcance que el CI)
	uv run ruff check src tests scripts migrations ops

fmt:        ## Formatea y autocorrige
	uv run ruff format src tests scripts migrations ops && uv run ruff check --fix src tests scripts migrations ops

typecheck:  ## mypy --strict
	uv run mypy src

migrate:    ## Aplica migraciones
	$(COMPOSE) run --rm migrate

seed:       ## Datos iniciales (tenant + centroides de barrios)
	$(COMPOSE) run --rm api python scripts/seed.py

eval:       ## Backtest local, y lo guarda en eval.backtest_runs
	$(COMPOSE) run --rm worker python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300

eval-hist:  ## Serie histórica de backtests
	$(COMPOSE) run --rm worker python -m tasador.eval.run --history --dataset BADATA_2015_2020

build:      ## Construye las imágenes y verifica que sean el repo
	$(COMPOSE) build
	uv run python ops/verificar_imagen.py

verificar-imagen: ## ¿Lo que se desplegaría es el código del repo?
	uv run python ops/verificar_imagen.py

shell:      ## Shell en el contenedor api
	$(COMPOSE) exec api /bin/bash

psql:       ## Consola de Postgres
	$(COMPOSE) exec postgres psql -U tasador -d tasador

# En `postgres` y no en `api`: la imagen de la API no trae `pg_dump`, y acá el
# `pg_dump` es el de la misma versión del servidor. `--verify` restaura en una
# base descartable y compara conteos: un backup no verificado no es un backup.
backup:     ## Backup verificado (restaura en una base descartable y compara)
	$(COMPOSE) exec -T -e BACKUP_DIR=/backups -e POSTGRES_HOST=localhost -e POSTGRES_PORT=5432 \
	  postgres bash /ops/backup.sh --verify

clean:      ## DESTRUCTIVO: baja todo y borra volúmenes
	@read -p "Escribí BORRAR para confirmar: " c && [ "$$c" = "BORRAR" ] && $(COMPOSE) down -v
