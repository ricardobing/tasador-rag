# Migraciones desde CERO en una base descartable, igual que el job del CI.
$ErrorActionPreference = "Continue"
$BASE = "tasador_auditoria_s5"
# Las claves salen del entorno, nunca del archivo: cargar antes POSTGRES_PASSWORD y
# REDIS_PASSWORD con los valores del .env local.
$PGPASS = $env:POSTGRES_PASSWORD; $RDPASS = $env:REDIS_PASSWORD
if (-not $PGPASS -or -not $RDPASS) { throw "Faltan POSTGRES_PASSWORD / REDIS_PASSWORD en el entorno" }
$ADMIN = "postgresql+psycopg://tasador:${PGPASS}@127.0.0.1:5433/tasador"

Write-Output "=== 1. crear base descartable $BASE ==="
docker exec tasador-postgres-1 psql -U tasador -d tasador -c "drop database if exists $BASE" | Out-Null
docker exec tasador-postgres-1 psql -U tasador -d tasador -c "create database $BASE" | Out-Null

Write-Output "=== 2. extensiones (lo que hace el CI con ops/sql/00-extensions.sql) ==="
Get-Content .\ops\sql\00-extensions.sql | docker exec -i tasador-postgres-1 psql -U tasador -d $BASE 2>&1 | Select-Object -Last 4

$env:DATABASE_URL = "postgresql+psycopg://tasador:${PGPASS}@127.0.0.1:5433/$BASE"
$env:SECRET_KEY = "x"; $env:ENCRYPTION_KEY = "x"; $env:LITELLM_MASTER_KEY = "x"
$env:REDIS_URL = "redis://:${RDPASS}@127.0.0.1:6380/0"

Write-Output "=== 3. alembic upgrade head ==="
uv run alembic upgrade head 2>&1 | Select-Object -Last 6

Write-Output "=== 4. alembic check (el paso del CI) ==="
uv run alembic check 2>&1 | Select-String -Pattern "FAILED|No new upgrade|ERROR|Detected" | ForEach-Object { $_.Line }

Write-Output "=== 5. tablas creadas ==="
docker exec tasador-postgres-1 psql -U tasador -d $BASE -tAc "select table_schema||'.'||table_name from information_schema.tables where table_schema in ('core','corpus','eval') order by 1" 2>&1

Write-Output "=== 6. comparar contra la base REAL ==="
docker exec tasador-postgres-1 psql -U tasador -d tasador -tAc "select table_schema||'.'||table_name from information_schema.tables where table_schema in ('core','corpus','eval') order by 1" > "$env:TEMP\real.txt"
docker exec tasador-postgres-1 psql -U tasador -d $BASE -tAc "select table_schema||'.'||table_name from information_schema.tables where table_schema in ('core','corpus','eval') order by 1" > "$env:TEMP\nueva.txt"
$real = Get-Content "$env:TEMP\real.txt" | Where-Object { $_ }
$nueva = Get-Content "$env:TEMP\nueva.txt" | Where-Object { $_ }
Write-Output "  tablas en la base real : $($real.Count)"
Write-Output "  tablas desde cero      : $($nueva.Count)"
$d = Compare-Object $real $nueva
if ($d) { $d | ForEach-Object { "  $($_.SideIndicator) $($_.InputObject)" } } else { Write-Output "  identicas" }

Write-Output "=== 7. columnas que difieren entre las dos bases ==="
$q = "select table_schema||'.'||table_name||'.'||column_name||' '||data_type from information_schema.columns where table_schema in ('core','corpus','eval') order by 1"
docker exec tasador-postgres-1 psql -U tasador -d tasador -tAc $q > "$env:TEMP\creal.txt"
docker exec tasador-postgres-1 psql -U tasador -d $BASE -tAc $q > "$env:TEMP\cnueva.txt"
$dc = Compare-Object (Get-Content "$env:TEMP\creal.txt" | Where-Object { $_ }) (Get-Content "$env:TEMP\cnueva.txt" | Where-Object { $_ })
if ($dc) { $dc | ForEach-Object { "  $($_.SideIndicator) $($_.InputObject)" } } else { Write-Output "  identicas" }

Write-Output "=== 8. limpiar ==="
docker exec tasador-postgres-1 psql -U tasador -d tasador -c "drop database if exists $BASE" | Out-Null
Write-Output "  base descartable eliminada"
