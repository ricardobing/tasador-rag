#!/usr/bin/env bash
# El CI, pero en tu máquina: los MISMOS pasos que .github/workflows/ci.yml, en el
# mismo orden, contra el Postgres del stack de desarrollo. Pushear es confirmar,
# no descubrir.
#
#   bash ops/ci-local.sh              # lint · formato · tipos · tests · migraciones · aislamiento · gitleaks · auditoría
#   bash ops/ci-local.sh --imagenes   # además construye las tres imágenes, verifica que sean el repo y las pasa por trivy
#   bash ops/ci-local.sh --rapido     # solo lint · formato · tipos · tests (sin base desde cero ni gitleaks)
#
# Necesita: uv, docker, el stack de desarrollo levantado (postgres y redis al
# menos) y un .env con POSTGRES_PASSWORD y REDIS_PASSWORD. En Windows se corre
# desde Git Bash (`.\make.ps1 ci` lo hace por vos).
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGENES=0; RAPIDO=0
for a in "$@"; do
  case "$a" in
    --imagenes) IMAGENES=1 ;;
    --rapido) RAPIDO=1 ;;
    *) echo "opción desconocida: $a"; exit 2 ;;
  esac
done

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
PG_PORT="${PG_PORT:-5433}"
REDIS_PORT="${REDIS_PORT:-6380}"
PG_PASS="$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
REDIS_PASS="$(grep -E '^REDIS_PASSWORD=' .env | cut -d= -f2-)"
[ -n "$PG_PASS" ] || { echo "falta POSTGRES_PASSWORD en .env"; exit 1; }

# Los mismos valores de entorno que el job del CI, sobre una base APARTE
# (`tasador_test`) para no tocar la de desarrollo.
export ENV=test FETCHER_STRATEGY=fixture
export DATABASE_URL="postgresql+psycopg://tasador:${PG_PASS}@127.0.0.1:${PG_PORT}/tasador_test"
export REDIS_URL="redis://:${REDIS_PASS}@127.0.0.1:${REDIS_PORT}/1"
export SECRET_KEY="test-secret-para-ci-solamente"
export ENCRYPTION_KEY="test-encryption-para-ci-solamente"
export LITELLM_MASTER_KEY="sk-test"
export MSYS_NO_PATHCONV=1
export PYTHONUTF8=1

paso() { printf '\n\033[36m== %s ==\033[0m\n' "$1"; }
t0=$(date +%s)

paso "Lint"
uv run ruff check src tests scripts migrations ops

paso "Formato"
uv run ruff format --check src tests scripts migrations ops

paso "Tipos"
uv run mypy src

base_vacia() {
  # La base de tests se recrea vacía cada vez, con las extensiones que en el
  # contenedor pone el init script y en el CI un `psql -f`.
  $COMPOSE exec -T postgres psql -U tasador -d postgres -q -c "DROP DATABASE IF EXISTS tasador_test" -c "CREATE DATABASE tasador_test" >/dev/null
  $COMPOSE exec -T postgres psql -U tasador -d tasador_test -q < ops/sql/00-extensions.sql >/dev/null
}

paso "Base de tests vacía (tasador_test en el Postgres del stack)"
$COMPOSE ps --format '{{.Name}} {{.Status}}' | grep -q 'postgres-1 Up' || { echo "el stack no está levantado: make dev / .\\make.ps1 dev"; exit 1; }
base_vacia

paso "Tests (con cobertura mínima, como el CI)"
uv run pytest -m "not live" --cov=tasador --cov-report=term-missing --cov-fail-under=57 -q

paso "Que los tests de base NO se hayan salteado"
uv run pytest tests/test_reports_api.py tests/architecture -q | tee /tmp/ci-local-salida.txt
if grep -qE '[1-9][0-9]* skipped' /tmp/ci-local-salida.txt; then
  echo "hay tests salteados con DATABASE_URL definida"; exit 1
fi

if [ "$RAPIDO" = 0 ]; then
  paso "Migraciones desde cero"
  base_vacia
  uv run alembic upgrade head
  uv run alembic check

  paso "Aislamiento entre tenants"
  uv run pytest tests/architecture -q

  paso "Secretos filtrados (gitleaks, la misma imagen que el CI)"
  docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/repo" ghcr.io/gitleaks/gitleaks:latest \
    detect --source /repo --config /repo/.gitleaks.toml --no-banner --redact --exit-code 1

  paso "Auditoría de publicación (nada del cliente ni del scraping)"
  uv run python ops/auditar_publicacion.py
fi

if [ "$IMAGENES" = 1 ]; then
  paso "Imágenes: build + la imagen es el repo + trivy"
  docker build -f docker/api.Dockerfile --target runtime -t tasador-api:ci . -q
  docker build -f docker/worker.Dockerfile -t tasador-worker:ci . -q
  docker build -f docker/web.Dockerfile --target runtime -t tasador-web:ci ./web -q
  uv run python ops/verificar_imagen.py --imagen tasador-api:ci
  uv run python ops/verificar_imagen.py --imagen tasador-worker:ci
  for img in tasador-api:ci tasador-worker:ci tasador-web:ci; do
    docker run --rm -v //var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache/ \
      aquasec/trivy:latest image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --scanners vuln -q "$img"
  done
fi

printf '\n\033[32mCI local en verde\033[0m · %ss\n' "$(( $(date +%s) - t0 ))"
