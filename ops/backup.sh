#!/usr/bin/env bash
#
# Backup de Postgres a almacenamiento externo (doc 08).
#
# `make backup` lo invocaba desde el 13/08 y el archivo no existía: fallaba con
# "No such file or directory". Un backup que se cree que corre es peor que uno
# que no existe — la diferencia se descubre el día que hace falta restaurar.
#
# Uso:
#     bash ops/backup.sh                    # a ./backups
#     BACKUP_DIR=/mnt/x bash ops/backup.sh  # a otro lado
#     bash ops/backup.sh --verify           # ademas RESTAURA y cuenta filas
#
# ⚠️ Un backup no verificado no es un backup. `--verify` restaura el dump en una
# base descartable y compara el conteo de filas de las tablas que importan. Es
# lento y por eso no es el default, pero es lo único que prueba que el archivo
# sirve. Doc 08 lo pide y la Etapa 6 lo tiene como pendiente explícito.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
VERIFY=0
[[ "${1:-}" == "--verify" ]] && VERIFY=1

: "${POSTGRES_USER:=tasador}"
: "${POSTGRES_DB:=tasador}"
# Dentro del contenedor `api` el host es `postgres`; desde el host, 127.0.0.1.
: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"

sello="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
destino="${BACKUP_DIR}/tasador-${sello}.dump"

echo ">> pg_dump  ${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB} -> ${destino}"

# Formato custom (-Fc): comprimido y restaurable por tabla. `--no-owner` para
# que el dump se pueda restaurar en una base con otro dueño, que es exactamente
# el caso de una verificación en una base temporal.
pg_dump \
  --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" \
  --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
  --format=custom --compress=9 --no-owner --no-privileges \
  --file="$destino"

bytes="$(wc -c <"$destino" | tr -d ' ')"
echo ">> ok  ${bytes} bytes"

# Un dump de 0 bytes es "exitoso" para pg_dump si la base está vacía. Acá no
# puede estarlo: si sale menos de 1 KB, algo se rompió antes.
if [[ "$bytes" -lt 1024 ]]; then
  echo "!! el dump pesa ${bytes} bytes: eso no es una base con datos" >&2
  exit 1
fi

sha256sum "$destino" | tee "${destino}.sha256"

if [[ "$VERIFY" == "1" ]]; then
  tmpdb="verify_${sello}"
  echo ">> verificando: restaurando en ${tmpdb}"
  createdb --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" \
           --username="$POSTGRES_USER" "$tmpdb"
  # `|| true`: pg_restore devuelve != 0 por avisos benignos (extensiones que ya
  # existen). Lo que decide si el backup sirve es el conteo de abajo, no esto.
  pg_restore --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" \
             --username="$POSTGRES_USER" --dbname="$tmpdb" \
             --no-owner --no-privileges "$destino" || true

  for tabla in corpus.listings core.reports core.report_events; do
    orig="$(psql --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" \
                 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
                 -tAc "select count(*) from ${tabla}")"
    copia="$(psql --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" \
                  --username="$POSTGRES_USER" --dbname="$tmpdb" \
                  -tAc "select count(*) from ${tabla}")"
    printf '   %-24s original=%-8s restaurado=%-8s' "$tabla" "$orig" "$copia"
    if [[ "$orig" == "$copia" ]]; then echo "OK"; else echo "MISMATCH"; fi
    [[ "$orig" == "$copia" ]] || { dropdb --host="$POSTGRES_HOST" \
        --port="$POSTGRES_PORT" --username="$POSTGRES_USER" "$tmpdb"; exit 1; }
  done

  dropdb --host="$POSTGRES_HOST" --port="$POSTGRES_PORT" \
         --username="$POSTGRES_USER" "$tmpdb"
  echo ">> verificado: el dump restaura y los conteos coinciden"
fi

# Retención. `-mtime +N` borra por fecha de modificación, no por nombre: si
# alguien toca un archivo viejo, se queda — y eso es lo que se quiere.
borrados="$(find "$BACKUP_DIR" -name 'tasador-*.dump*' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)"
echo ">> retención ${RETENTION_DAYS} días: ${borrados// /} archivos borrados"

# El envío a R2/S3 queda fuera a propósito: necesita credenciales que no están
# en el repo. Cuando se configure, va acá:
#   rclone copy "$destino" r2:tasador-backups/
if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  echo ">> copiando a ${BACKUP_REMOTE}"
  rclone copy "$destino" "$BACKUP_REMOTE"
  rclone copy "${destino}.sha256" "$BACKUP_REMOTE"
fi
