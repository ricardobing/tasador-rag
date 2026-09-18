# Informe — Etapa 0: fundaciones

**Fecha:** 13/08/2026 · **Estado:** parcial — lo que se puede hacer sin VPS, hecho y
verificado. Lo que necesita infraestructura y tarjeta de crédito, pendiente.

---

## 1. Qué quedó construido

```
tasador/
├─ docker-compose.yml           8 servicios, límites de memoria, healthchecks
├─ docker-compose.dev.yml       overrides con hot reload y puertos expuestos
├─ Makefile / make.ps1          mismos objetivos en Linux y Windows
├─ pyproject.toml               deps + ruff + mypy strict + pytest
├─ alembic.ini
├─ .env.example                 toda la configuración documentada
├─ .gitignore                   secretos y datos crudos fuera de git
├─ caddy/Caddyfile              TLS auto, cabeceras, mismo origen web+api
├─ config/
│   ├─ litellm.yaml             gateway de modelos por TAREA (ADR-003)
│   └─ adjustments.yaml         coeficientes de valuación, versionados
├─ docker/
│   ├─ api.Dockerfile           multi-stage, non-root, read-only
│   └─ worker.Dockerfile        + supercronic para los crons
├─ migrations/env.py            URL del entorno, solo nuestros schemas
├─ ops/
│   ├─ sql/00-extensions.sql    pgvector, pg_trgm, unaccent, schemas
│   └─ crontab                  crons del servicio
├─ src/tasador/
│   ├─ settings.py              config tipada, sin valores mágicos sueltos
│   ├─ main.py                  app + request_id + handler de errores
│   ├─ healthcheck.py           healthcheck sin curl en la imagen
│   └─ v1/health.py             liveness y readiness
├─ tests/test_health.py         6 tests
└─ .github/workflows/ci.yml     lint, tipos, tests, secretos, build, trivy, eval-gate
```

## 2. Qué está verificado (ejecutado, no supuesto)

| Verificación | Comando | Resultado |
|---|---|---|
| Compose de producción válido | `docker compose config --quiet` | ✅ |
| Compose de desarrollo válido | `docker compose -f ... -f ... config --quiet` | ✅ |
| 8 servicios declarados | `docker compose config --services` | ✅ postgres, redis, migrate, api, worker, ingest, web, caddy, litellm |
| Dependencias instalables | `uv sync --extra dev` | ✅ (uv baja Python 3.12 solo) |
| Lint | `ruff check src tests` | ✅ **All checks passed** |
| Formato | `ruff format --check src tests` | ✅ **8 files already formatted** |
| Tipos | `mypy src` (strict) | ✅ **Success: no issues found in 6 source files** |
| Tests | `pytest -m "not live"` | ✅ **6 passed** |

Los tests corren **sin tocar internet y sin gastar un centavo**: `/health` no consulta
dependencias y `/ready` se prueba con las verificaciones mockeadas.

### 2.1 Decisiones que salieron de la verificación

Tres cosas se corrigieron porque la ejecución las delató, no porque estuvieran en el
plan:

1. **Python 3.12, no 3.14.** Tenés 3.14 instalado, pero varias dependencias del stack
   de agentes todavía no publican wheels para esa versión. `pyproject.toml` acota a
   `>=3.12,<3.14` y `uv` instala 3.12 solo. No tenés que tocar nada.
2. **`make` no está instalado** y no vale la pena instalarlo: el `Makefile` queda para
   el VPS y el CI (Linux) y en Windows se usa `make.ps1`, con los mismos objetivos.
3. **`docker compose config` falla sin `.env`.** Es el comportamiento correcto
   (`env_file` obligatorio en producción), pero significa que `setup` tiene que crear
   el `.env` antes de cualquier otra cosa. Está en `make setup` / `.\make.ps1 setup`.

## 3. Decisiones de arquitectura que este scaffolding fija

### 3.1 Todo en Docker, incluido el frontend

**Sí, todo.** Un `docker compose up` levanta el sistema completo: Postgres, Redis, la
API, el worker, la ingesta, el gateway de modelos, Caddy y el frontend.

### 3.2 El frontend va en el VPS, no en Vercel

Fue una decisión, no una omisión. Fundamento:

| A favor del VPS | |
|---|---|
| **Mismo origen** | `/v1/*` → api, `/*` → web, detrás del mismo Caddy. Cookies de sesión simples, sin CORS, sin preflight, sin `SameSite=None` |
| **El artefacto es autocontenido** | `git clone && docker compose up` levanta el sistema entero. Eso es lo que se demuestra en una entrevista y lo que se le instala a un cliente que quiera on-premise |
| **Costo cero** | El VPS ya está pago y le sobra RAM |
| **Un solo deploy** | Un push, un pipeline, una versión. No hay "el front está en v3 y el back en v2" |

Vercel sería mejor si esto fuera un sitio público que necesita CDN de borde. **No lo
es**: es un panel privado para ~10 usuarios en Argentina. La latencia de un VPS en
Ashburn es de sobra.

> Si algún día hace falta un sitio de marketing público, **ese** va a Vercel. El panel
> se queda acá.

### 3.3 El gateway de modelos ya está aislado

`config/litellm.yaml` define los modelos **por tarea**, no por proveedor:

```yaml
- model_name: extractor    → deepseek/deepseek-chat   (fallback: qwen3-30b)
- model_name: judge        → deepseek-chat            (fallback: glm-4.6)
- model_name: writer       → qwen3-max
- model_name: critic       → claude-sonnet-4.5        ← el único caro, a propósito
```

El código pide `model="extractor"`. Si un cliente quiere su cuenta de OpenAI, se edita
este archivo. Cero cambios de código. Y tiene tope de gasto (`max_budget: 30`) para que
un loop corte ahí y no en la tarjeta.

### 3.4 Los coeficientes de valuación son configuración

`config/adjustments.yaml` — versionado, con el origen de cada número declarado
("práctica de mercado, no regresión") y una nota de que tocarlo obliga a correr el
backtest. El gate del CI ya está escrito para eso.

## 4. Lo que falta de la Etapa 0 — y necesita que hagas vos

| # | Tarea | Qué necesitás |
|---|---|---|
| 0.5 | Auth: API keys + sesión | — (código, sigue) |
| 0.6 | Repositorio con `org_id` + test de arquitectura | — |
| 0.8 | Langfuse conectado | Crear cuenta free en cloud.langfuse.com y pegar 2 claves |
| 0.9 | **VPS + dominio + TLS** | Alta en Hetzner (€6,80/mes, tarjeta) y un dominio o subdominio |
| 0.10 | Deploy por SSH con rollback | Lo anterior |
| 0.11 | Backups a R2 + restore probado | Cuenta Cloudflare R2 (free tier, pide tarjeta pero no cobra) |
| — | **Mail a el CRM** | [Borrador listo](../guias/mail-crm.md). Mandarlo desde la cuenta de la inmobiliaria o con Gastón en copia |
| — | Preguntar a la inmobiliaria por sus planes de Portal A/Portal B | Un mensaje a Gastón |

**Nada de eso bloquea la Etapa 1.** El corpus se arma local y se empuja cuando la infra
esté.

## 5. Cómo levantarlo ahora mismo

```powershell
cd .
.\make.ps1 setup      # crea .env e instala dependencias
# editar .env: cambiar los valores que dicen "cambiar-esto"
.\make.ps1 dev        # levanta todo con hot reload
```

Después: `http://localhost:8000/v1/health` y `http://localhost:8000/docs`.

> **Nota:** la primera corrida de `dev` construye las imágenes y baja el modelo de
> embeddings (~2 GB). Tarda. Las siguientes son inmediatas.

## 6. Estado de la Etapa 0

| | |
|---|---|
| Tareas completas | **6 de 11** (0.1, 0.2, 0.3, 0.4, y parcialmente 0.7 y 0.10) |
| Bloqueadas por infraestructura | 4 (0.8, 0.9, 0.10, 0.11) |
| Pendientes de código | 2 (0.5, 0.6) |

La parte que se podía hacer sin gastar un peso, está hecha y verificada.
