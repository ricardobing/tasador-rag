# Desplegar una demo online (para que la inmobiliaria lo pruebe)

Qué hace falta, cuánto cuesta y qué conviene, escrito el 19/09/2026 con los tamaños
reales medidos en la máquina de desarrollo. La decisión de diseño de doc 08 (un VPS
Hetzner con Docker Compose, Caddy con TLS automático, sin Kubernetes ni servicios
administrados) sigue siendo la correcta para una demo: es lo que el repo ya sabe hacer.

## 1. Lo que hace falta, sea donde sea

| Qué | Detalle | Ya está |
|---|---|---|
| Un dominio o subdominio | p. ej. `tasador.<tu-dominio>`; un registro A al servidor. Caddy saca el certificado solo | Caddyfile en `caddy/` |
| Una clave de modelos con tope de gasto | OpenRouter (u otro compatible con OpenAI) con **límite mensual**: USD 10 alcanzan para ~300 informes | `config/litellm.yaml` |
| Secretos nuevos | `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `SECRET_KEY`, `ENCRYPTION_KEY`, `LITELLM_MASTER_KEY`, `CRON_SECRET` generados con `secrets.token_urlsafe(48)`; `.env` con `chmod 600` | `.env.example` |
| El corpus | **Con datos reales**: un `pg_dump` de la base local (480 MB en disco; `listing_chunks` 263 MB) y restaurarlo, o correr `ingest_csv` + `embed_corpus` en el servidor (~1 h de CPU). **Con la demo sintética**: `seed` + `ingest_csv --carpeta /data/raw/demo` en dos minutos | `ops/backup.sh`, `scripts/` |
| Los modelos locales | Solo MiniLM (~470 MB) se usa hoy (QA «Preguntale al informe» y el indexador); el reranker está apagado. Se bajan solos al primer uso al volumen `models`. No copiar los 4,5 GB de la carpeta local: son los modelos que se midieron y se descartaron | volumen `models` |
| Usuarios | `crear_usuario.py --email ... --rol owner` por cada persona; la inmobiliaria entra con email y contraseña | `scripts/crear_usuario.py` |
| Backups | `ops/backup.sh` con `pg_dump` (y `--verify`); a R2 si se configura, o al disco + snapshot semanal del proveedor | cron en `ops/crontab` |
| Disco | Imágenes: api 2,0 GB + worker 2,1 GB + web 0,35 GB (las dos de Python comparten capas); base 0,5-1 GB; modelos 0,5 GB. **~6 GB** | — |
| RAM real en uso (modo léxico) | postgres ~200 MB · redis ~30 · litellm ~700 · api 300-900 (sube al embeber una pregunta) · worker 500-1.500 (extracción de 60 avisos, PDF) · web ~400 · caddy ~10. **~3-3,5 GB en uso normal, picos de 4** | límites en `docker-compose.yml` |

Sin Kubernetes, sin base administrada, sin colas externas: Docker Compose y un `git
pull && docker compose up -d --build` para actualizar. El mantenimiento real es: mirar
`docker compose ps` de vez en cuando, el gasto en el proveedor de modelos, y el backup.

## 2. Las tres opciones, comparadas

| | VPS propio nuevo (Hetzner) | El VPS de TomaNota (compartido) | Railway |
|---|---|---|---|
| Costo | CX22 (2 vCPU / 4 GB) **€3,79/mes**; CX32 (4 vCPU / 8 GB) €6,80 | **€0** extra | 6 servicios facturados por uso: estimado **USD 15-30/mes**, más si el worker queda activo |
| Trabajo de puesta en marcha | 1-2 horas: crear, DNS, clonar, `.env`, restaurar dump, `up` | 1 hora, pero **hay que tocar el Caddy de TomaNota** (vhost nuevo y red compartida) y el `.env` | Medio día: 6 servicios a mano, Postgres con pgvector desde template, volúmenes para `/data` y modelos, sin `read_only`, LiteLLM como servicio aparte |
| Mantenimiento | Casi nulo (snapshots semanales, `actualizar` cuando haya cambios) | Igual, pero cada cambio del borde de TomaNota puede afectar la demo y viceversa | Bajo, pero los Dockerfiles tienen supuestos de Compose (volúmenes, red interna, healthchecks) que hay que adaptar y mantener |
| Riesgo para otros proyectos | Ninguno | **RAM**: 8 GB compartidos con Evolution/WhatsApp, runtimes y Redis; un OOM kill puede caerle al bot de un cliente. **CPU**: extraer 60 avisos o embeber un informe son ráfagas de 1-5 min | Ninguno |
| Datos privados (corpus) | En tu servidor | En tu servidor | En un tercero (Postgres administrado) |
| Veredicto | **La opción limpia.** CX22 alcanza para la demo en modo léxico si el índice se restaura de un dump en vez de calcularse ahí; CX32 si querés reindexar o encender el híbrido | Solo si `free -m` en el VPS muestra **≥ 3,5 GB disponibles de forma estable** y aceptás acoplar los dos stacks | La menos conveniente para este repo: más piezas, más costo, y el corpus fuera de tu control |

**Recomendación:** un CX22 nuevo (€3,79/mes) con el dump del corpus real, TLS por
Caddy, un usuario por persona de la inmobiliaria, y tope de gasto en OpenRouter. Es
lo más barato que no te obliga a mirar dos proyectos a la vez, y si la demo no
prospera se borra el servidor y no queda rastro en el otro.

Si igual querés usar el VPS de TomaNota, primero medí; con esto se decide en dos minutos:

```bash
free -m                                   # "available" ≥ 3.500 MB de forma estable → viable
docker stats --no-stream                  # cuánto usa cada contenedor de TomaNota/wa-kit hoy
df -h /                                   # ≥ 8 GB libres
docker network ls | grep -i tomanota      # el nombre de la red donde vive el Caddy del borde
```

Y las tres adaptaciones que hacen falta ahí (no las hagas sin medir antes):

1. **No levantar el `caddy` de Tasador** (`docker compose up -d --scale caddy=0` o un
   override que lo saque): el de TomaNota ya tiene 80/443. Agregar al Caddyfile del borde
   un bloque `tasador.<dominio>` que haga `reverse_proxy tasador-api-1:8000` para `/v1/*`
   y `reverse_proxy tasador-web-1:3000` para el resto, y conectar `web` y `api` a la red
   externa del borde (`networks: { edge: { external: true, name: <red-de-tomanota> } }`).
2. **Bajar los límites de memoria** en un override: worker 1.536 MB, api 768, litellm 512,
   postgres 1.024. Con eso el peor caso de Tasador son ~4 GB y el OOM killer tiene a quién
   matar antes que a Evolution.
3. **`COMPOSE_PROJECT_NAME=tasador`** y puertos sin publicar (el compose de producción ya
   no publica ninguno salvo Caddy).

## 3. Paso a paso en un VPS nuevo (Hetzner CX22, Ubuntu 24.04)

```bash
# 1. Servidor: usuario no root con sudo, Docker + Compose plugin, ufw 22/80/443
apt update && apt install -y docker.io docker-compose-v2 ufw && ufw allow 22,80,443/tcp && ufw enable

# 2. DNS: tasador.<dominio> → IP del servidor (esperar a que resuelva)

# 3. Código y configuración
git clone https://github.com/ricardobing/tasador-rag.git /opt/tasador && cd /opt/tasador
cp .env.example .env && chmod 600 .env
# editar .env: ENV=production, DOMAIN=tasador.<dominio>, ACME_EMAIL, contraseñas y claves
# generadas, OPENROUTER_API_KEY, PORTALES=<como en tu máquina>

# 4. Levantar (producción: sin override; Caddy publica 80/443 y saca el certificado)
docker compose up -d --build && docker compose ps

# 5a. Corpus real: restaurar el dump hecho en tu máquina
#     local:  docker exec tasador-postgres-1 pg_dump -U tasador -Fc tasador > tasador.dump
#     scp tasador.dump al servidor y:
docker compose exec -T postgres pg_restore -U tasador -d tasador --clean --if-exists < tasador.dump
# 5b. …o la demo sintética
docker compose run --rm api python scripts/seed.py --offline
docker compose run --rm api python scripts/ingest_csv.py --carpeta /data/raw/demo

# 6. Usuarios
docker compose run --rm api python scripts/crear_usuario.py --email agente@inmobiliaria.com.ar --rol owner --generar-password

# 7. Probar: https://tasador.<dominio> → login → Nuevo informe (Gorriti 5000, 3 amb, 70 m²)
# 8. Backup: docker compose exec postgres bash /ops/backup.sh --verify  (y snapshot semanal del VPS)
```

Actualizar después: `cd /opt/tasador && git pull && docker compose up -d --build`. El
CI ya verificó lint, tests, migraciones desde cero, imágenes sin vulnerabilidades y las
pantallas antes de que el commit llegue a `main`.

## 4. Lo que le mostraría a la inmobiliaria, y lo que no

- **Sí:** el flujo completo con direcciones de Palermo (guía de pruebas, §2.3 bis), la
  tabla de comparables con descartes explicados, el PDF, «Preguntale al informe», y el
  caso sin datos que explica por qué.
- **Decir antes:** cobertura Palermo (densa) y Belgrano (escasa); el corpus es una foto
  de una fecha y no se actualiza solo (el repo no captura portales); no es una tasación
  con validez legal.
- **No:** prometer tiempos de otros barrios ni carga automática de avisos.
