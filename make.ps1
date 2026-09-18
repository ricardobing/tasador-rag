<#
  make.ps1 — equivalente del Makefile para Windows.

  `make` no está instalado en esta máquina y no vale la pena instalarlo:
  el Makefile existe igual para el VPS y el CI (Linux), y acá se usa este
  script. Mismos comandos, mismos nombres.

  Uso:  .\make.ps1 <objetivo>
  Ej.:  .\make.ps1 dev
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('help','setup','dev','up','down','logs','ps','test','lint','fmt',
                 'typecheck','migrate','revision','seed','eval','eval-hist','backup',
                 'build','clean','shell','psql','ci','ci-imagenes','e2e')]
    [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'
$COMPOSE_DEV = @('-f','docker-compose.yml','-f','docker-compose.dev.yml')

function Section($t) { Write-Host "`n>> $t" -ForegroundColor Cyan }

switch ($Target) {

    'help' {
        Write-Host @"
Tasador — objetivos disponibles

  setup       Instala dependencias y crea .env si no existe
  dev         Levanta el stack en modo desarrollo (hot reload)
  up          Levanta el stack como en produccion
  down        Baja el stack
  logs        Sigue los logs
  ps          Estado de los contenedores
  test        Corre los tests (sin tocar internet)
  ci          TODO el CI en local, contra el Postgres del stack (ops/ci-local.sh)
  ci-imagenes Lo mismo, mas las tres imagenes con trivy
  e2e         Playwright contra el stack levantado (WEB_PORT, E2E_CON_AUTH=1 para login real)
  lint        ruff check
  fmt         ruff format
  typecheck   mypy --strict
  migrate     Aplica migraciones (alembic upgrade head)
  revision    Nueva migracion:  .\make.ps1 revision   (pide el mensaje)
  seed        Datos iniciales (tenant + centroides de barrios)
  eval        Backtest local, y lo guarda en eval.backtest_runs
  eval-hist   Serie historica de backtests
  backup      Backup de Postgres (--verify para restaurarlo y contar filas)
  build       Construye las imagenes
  shell       Shell dentro del contenedor api
  psql        Consola de Postgres
  clean       Baja todo y borra volumenes  (DESTRUCTIVO)
"@
    }

    'setup' {
        Section 'Verificando herramientas'
        foreach ($c in @('docker','uv','node')) {
            if (-not (Get-Command $c -ErrorAction SilentlyContinue)) {
                throw "Falta '$c'. Instalalo antes de continuar."
            }
            Write-Host "  ok $c"
        }
        if (-not (Test-Path '.env')) {
            Section 'Creando .env desde .env.example'
            Copy-Item '.env.example' '.env'
            Write-Host '  .env creado — COMPLETAR los valores marcados como cambiar-esto' -ForegroundColor Yellow
        } else {
            Write-Host '  .env ya existe, no se toca'
        }
        Section 'Instalando dependencias de Python (uv usa 3.12 solo)'
        uv sync --extra dev
        Section 'Listo'
    }

    'dev'       { docker compose @COMPOSE_DEV up --build }
    'up'        { docker compose up -d --build; docker compose ps }
    'down'      { docker compose down }
    'ps'        { docker compose ps }
    'logs'      { docker compose logs -f --tail 100 }
    'build'     { docker compose build }

    'test' {
        Section 'Tests (FETCHER_STRATEGY=fixture — no se toca internet)'
        $env:ENV = 'test'; $env:FETCHER_STRATEGY = 'fixture'
        uv run pytest -m "not live"
    }

    # Git Bash viene con Git for Windows; el script es el mismo que corre el CI.
    'ci'          { bash ops/ci-local.sh }
    'ci-imagenes' { bash ops/ci-local.sh --imagenes }
    'e2e' {
        $port = if ($env:WEB_PORT) { $env:WEB_PORT } else { '3000' }
        $env:E2E_BASE_URL = "http://127.0.0.1:$port"
        Push-Location web; try { npx playwright test --project=escritorio } finally { Pop-Location }
    }

    'lint'      { uv run ruff check src tests scripts migrations ops }
    'fmt'       { uv run ruff format src tests scripts; uv run ruff check --fix src tests scripts }
    'typecheck' { uv run mypy src }

    'migrate'   { docker compose run --rm migrate }

    'revision' {
        $msg = Read-Host 'Mensaje de la migracion'
        if (-not $msg) { throw 'Hace falta un mensaje.' }
        docker compose run --rm api alembic revision --autogenerate -m "$msg"
    }

    'seed'      { docker compose run --rm api python scripts/seed.py }
    'eval'      { docker compose run --rm worker python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300 }
    'eval-hist' { docker compose run --rm worker python -m tasador.eval.run --history --dataset BADATA_2015_2020 }
    'backup'    { docker compose run --rm api bash ops/backup.sh }
    'shell'     { docker compose exec api /bin/bash }
    'psql'      { docker compose exec postgres psql -U tasador -d tasador }

    'clean' {
        Write-Host 'Esto borra TODOS los volumenes (base de datos incluida).' -ForegroundColor Red
        if ((Read-Host 'Escribi BORRAR para confirmar') -eq 'BORRAR') {
            docker compose down -v
        } else { Write-Host 'Cancelado.' }
    }
}
