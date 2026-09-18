# 08 — Infraestructura y despliegue

---

## 1. El servidor

| | |
|---|---|
| **Proveedor** | Hetzner Cloud |
| **Plan** | **CX32** — 4 vCPU, 8 GB RAM, 80 GB SSD — **€6,80/mes** `VERIFICADO` |
| **Ubicación** | Ashburn (US-East) — la más cercana a Argentina con buena latencia |
| **SO** | Ubuntu 24.04 LTS |
| **Snapshots** | Semanal, ~€0,50/mes |

**Por qué CX32 y no el CX22 de €3,79.** El CX22 (2 vCPU / 4 GB) alcanza para Postgres
+ Redis + API, pero el worker corriendo embeddings locales (`bge-m3` en CPU) y
WeasyPrint necesita aire. Y con 4 GB no hay margen para un pico. Tres euros de
diferencia no justifican convivir con OOM kills. Se empieza en CX32 y se baja si las
métricas dicen que sobra.

---

## 2. Docker Compose

```yaml
services:
  caddy:        # TLS automático, reverse proxy, rate limit
  web:          # Next.js — UI
  api:          # FastAPI — /v1
  worker:       # arq + LangGraph
  ingest:       # cron interno (supercronic)
  postgres:     # pgvector/pgvector:pg16
  redis:        # redis:7-alpine
  litellm:      # gateway de modelos
```

**Reglas de los contenedores:**

- Todos con `user: nonroot`, `read_only: true` y `tmpfs` para lo escribible.
- Todos con `healthcheck` y `restart: unless-stopped`.
- Límites de memoria explícitos (`deploy.resources.limits`) para que un worker
  desbocado no tumbe Postgres.
- Solo `caddy` publica puertos (80/443). El resto vive en la red interna.
- Imágenes pinneadas por digest, no por tag `latest`.

**Presupuesto de memoria (8 GB):**

| Servicio | Límite |
|---|---|
| postgres | 2,0 GB |
| worker | 2,5 GB (embeddings + WeasyPrint) |
| api | 768 MB |
| web | 512 MB |
| litellm | 512 MB |
| redis | 512 MB |
| ingest | 512 MB |
| caddy | 128 MB |
| **Reservado para el SO** | ~1 GB |

---

## 3. Caddy

```caddyfile
tasador.{$DOMAIN} {
  encode gzip zstd
  header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
    X-Content-Type-Options nosniff
    X-Frame-Options DENY
    Referrer-Policy strict-origin-when-cross-origin
    -Server
  }
  handle /v1/* { reverse_proxy api:8000 }
  handle      { reverse_proxy web:3000 }
}
```

TLS automático vía Let's Encrypt, renovación sola. Rate limiting con el módulo
`caddy-ratelimit`.

---

## 4. Secretos

- **Nunca en git.** `.env` en el servidor con `chmod 600`, propiedad de root.
- `.env.example` versionado con todas las claves y valores dummy.
- En CI, GitHub Actions Secrets.
- La API key de el CRM de un tenant se guarda **cifrada en la base** (`pgcrypto`), con
  la clave maestra en el entorno. Ni siquiera un `pg_dump` la expone en claro.
- Rotación documentada en el runbook.

---

## 5. CI/CD

**`.github/workflows/ci.yml`** — en cada push y PR:

```
lint         ruff + mypy --strict + eslint
test         pytest (unit + integración con Postgres de servicio)
arch-test    verifica que ninguna query omita org_id
build        docker build de las 4 imágenes
scan         trivy sobre las imágenes (falla en HIGH/CRITICAL)
```

**`deploy.yml`** — en push a `main` con los checks verdes:

```
1. build & push a ghcr.io con tag = SHA del commit
2. ssh al VPS (clave de deploy dedicada, sin sudo)
3. docker compose pull && docker compose up -d
4. espera healthchecks; si alguno falla → rollback al tag anterior
5. smoke test: GET /v1/ready debe responder 200
6. notificación
```

**Migraciones:** Alembic corre en un contenedor `init` antes de levantar `api` y
`worker`. Solo migraciones aditivas; nunca `DROP COLUMN` en el mismo deploy que
cambia el código que la usaba (patrón expand/contract, en dos releases).

**Regla dura:** el CI corre **el backtest** cuando cambia algo en `prompts/`,
`config/adjustments.yaml` o el módulo de valuación. Si el MdAPE empeora más de 2
puntos, el PR se bloquea. Ver [09](09-evaluacion-y-backtest.md).

---

## 6. Backups

| Qué | Cómo | Frecuencia | Retención | Destino |
|---|---|---|---|---|
| Postgres | `pg_dump -Fc` comprimido y cifrado con `age` | Diario 04:00 ART | 30 diarios + 12 mensuales | Cloudflare R2 (free tier: 10 GB) |
| PDFs generados | `rclone sync` | Diario | Indefinida | R2 |
| `.env` | Manual, cifrado | Al cambiar | — | Gestor de contraseñas |
| VPS completo | Snapshot de Hetzner | Semanal | 4 | Hetzner |

**Restore probado, no supuesto.** Una vez por mes, un job del CI levanta el último
dump en un contenedor efímero, corre las migraciones y ejecuta una query de sanidad.
Si falla, alerta. Un backup que nunca se restauró no es un backup.

**RTO objetivo:** 1 hora. **RPO:** 24 horas. Para este sistema es aceptable: los
informes están respaldados y el corpus se puede reconstruir.

---

## 7. Observabilidad

| Capa | Herramienta | Qué |
|---|---|---|
| **LLM** | Langfuse Cloud (free tier) | Traza por corrida, tokens, costo, prompt, latencia por nodo (ADR-008) |
| **Aplicación** | Logs JSON a stdout → `docker compose logs` + rotación | `request_id`, `org_id`, `report_id` en cada línea |
| **Errores** | Sentry (free tier: 5k eventos/mes) | Excepciones con contexto, sin PII |
| **Uptime** | Healthchecks.io (free) | Ping desde afuera cada 5 min a `/v1/health` |
| **Crons** | Healthchecks.io | Cada cron reporta inicio/fin. Si no reporta, alerta |
| **Métricas de negocio** | Tablas propias + `/calidad` | Informes/día, costo/informe, cobertura, MdAPE, % bloqueado por fuente |

**Alertas que importan** (a email + Telegram):

| Alerta | Umbral |
|---|---|
| `/v1/health` caído | 2 fallos seguidos |
| Cola de trabajos creciendo | > 20 pendientes por 10 min |
| Tasa de bloqueo de una fuente | > 20% en una corrida |
| Costo diario de LLM | > USD 2 |
| Desvío del corpus vs. serie oficial | > 15% en algún barrio |
| Backup fallido | cualquiera |
| Disco | > 80% |

---

## 8. Seguridad de la infraestructura

- `ufw`: solo 22 (con IP restringida), 80, 443.
- SSH: solo clave, sin root, sin password. `fail2ban` activo.
- Actualizaciones de seguridad desatendidas.
- Postgres y Redis **no** exponen puertos al host.
- Redis con `requirepass`.
- Usuario de deploy separado, sin sudo, solo puede correr `docker compose`.
- Trivy en CI corta el build ante vulnerabilidades HIGH/CRITICAL.
- Dependabot para dependencias.

---

## 9. Entorno de desarrollo

```bash
make dev        # levanta compose con overrides de dev, hot reload
make test       # pytest + vitest
make eval       # corre el backtest local contra el dataset chico
make seed       # carga BA Data + 200 avisos de fixture
make lint       # ruff + mypy + eslint
```

**Los tests no tocan internet.** El `Fetcher` está en modo `Fixture` (ADR-009) y el
gateway de LLM apunta a respuestas grabadas (VCR). Consecuencia: el CI es rápido,
gratis, determinístico y funciona sin claves.

Para probar contra modelos reales: `make test-live`, que sí gasta y no corre en CI.

---

## 10. Runbook

| Situación | Qué hacer |
|---|---|
| **Los informes fallan todos** | `docker compose logs api worker --tail 200`. Verificar `/v1/ready`. 90% de las veces es LiteLLM sin saldo en un proveedor |
| **Una fuente empezó a bloquear** | Ver `/admin/fuentes`. Cambiar `FETCHER_STRATEGY=zyte` en `.env` y reiniciar `ingest`. El sistema sigue con el corpus existente mientras tanto |
| **La cola se llenó** | `docker compose up -d --scale worker=2` (hay RAM para 2). Si persiste, revisar si un informe entró en loop |
| **Postgres lleno** | Correr la política de retención de [03 §7](03-modelo-de-datos.md). El candidato es siempre `raw_html_path` |
| **Rollback** | `docker compose pull && TAG=<sha-anterior> docker compose up -d`. Las migraciones son aditivas, así que el schema nuevo funciona con el código viejo |
| **Restore completo** | Documentado paso a paso en `ops/RESTORE.md`, probado mensualmente |
| **Un tenant reporta un informe mal** | Buscar por `report_id` → `report_events` → `trace_id` → traza completa en Langfuse con cada prompt y respuesta |
| **Se filtró una API key** | Revocar en `/admin/organizacion`, generar una nueva. `revoked_at` la mata al instante |

---

## 11. Escalado (cuándo preocuparse)

| Señal | Umbral | Acción |
|---|---|---|
| Informes/día | > 200 | Segundo worker (ya hay RAM) |
| Corpus | > 2 M avisos | Particionar `listings` por fecha |
| Tenants | > 10 | Considerar Postgres gestionado |
| RAM | > 85% sostenido | CX42 (8 vCPU / 16 GB) |
| Volumen de trazas | > free tier de Langfuse | Self-host en un VPS aparte |

**Nada de esto se hace antes de tiempo.** Está escrito para saber qué mirar, no para
construirlo ahora.
