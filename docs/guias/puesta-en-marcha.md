# Guía de puesta en marcha — todo en Docker local

**Para:** Ricardo · **Estado:** todo corre en tu máquina, sin VPS todavía
**Tiempo:** ~30 minutos la primera vez, de los cuales 20 son esperas

---

## Índice

1. [Respuesta corta a lo de las APIs](#1-respuesta-corta-a-lo-de-las-apis)
2. [Qué cuentas necesitás y cuáles no](#2-qué-cuentas-necesitás-y-cuáles-no)
3. [Paso a paso: levantar el sistema](#3-paso-a-paso-levantar-el-sistema)
4. [Cargar los datos](#4-cargar-los-datos)
5. [Verificar que todo anda](#5-verificar-que-todo-anda)
6. [Comandos del día a día](#6-comandos-del-día-a-día)
7. [Cuando algo falla](#7-cuando-algo-falla)

---

## 1. Respuesta corta a lo de las APIs

Preguntaste tres cosas. Las respondo directo y después el detalle.

| Tu pregunta | Respuesta |
|---|---|
| **¿Mi API de OpenRouter sirve?** | **Sí, perfectamente.** Es lo único que necesitás para arrancar. |
| **¿Saco una de DeepSeek?** | **No hace falta ahora.** OpenRouter ya te da DeepSeek. |
| **¿Formato OpenAI o Anthropic?** | **OpenAI.** DeepSeek es OpenAI-compatible. Anthropic no entra en esta discusión. |

### 1.1 Por qué el formato no importa (y esto es a propósito)

Es la razón de ser del ADR-003. **Tu código nunca ve el formato de ningún proveedor.**

```
tu código  ──OpenAI-compatible──>  LiteLLM  ──traduce──>  DeepSeek  (OpenAI-compat)
                                                       ├─>  OpenRouter (OpenAI-compat)
                                                       ├─>  Anthropic  (formato propio)
                                                       └─>  OpenAI
```

LiteLLM traduce. Si mañana un cliente exige Anthropic nativo, cambiás una línea de
`config/litellm.yaml` y el código no se entera. **Por eso no tenés que decidir esto
ahora.**

### 1.2 Por qué OpenRouter solo, para empezar

`config/litellm.yaml` define cuatro modelos por tarea. Con **una sola clave de
OpenRouter** tenés los cuatro:

| Tarea | Modelo | ¿OpenRouter lo tiene? |
|---|---|---|
| `extractor` | DeepSeek Chat | ✅ `openrouter/deepseek/deepseek-chat` |
| `judge` | GLM / Qwen | ✅ |
| `writer` | Qwen3 Max | ✅ |
| `critic` | DeepSeek V4 Flash (desde el 18/09; antes Claude Sonnet 4.5) | ✅ `openrouter/deepseek/deepseek-v4-flash` |

**Una cuenta, una clave, una factura.** Sin OpenRouter necesitarías tres cuentas
distintas (DeepSeek + Z.ai + Anthropic).

### 1.3 Cuándo sí conviene DeepSeek directo

Solo por costo. DeepSeek directo es ~5% más barato que a través de OpenRouter (que
cobra su margen). A tu volumen:

| | Vía OpenRouter | DeepSeek directo | Ahorro |
|---|---|---|---|
| 50 informes/mes | ~USD 2,30 | ~USD 2,20 | **USD 0,10/mes** |

**Diez centavos por mes.** No vale la cuenta extra. Cuando el sistema haga 500
informes al mes, lo revisamos.

> Si igual la querés sacar: `platform.deepseek.com`, formato **OpenAI**, base URL
> `https://api.deepseek.com`. La ponés en `DEEPSEEK_API_KEY` y en
> `config/litellm.yaml` cambiás `openrouter/deepseek/deepseek-chat` por
> `deepseek/deepseek-chat`. Nada más.

---

## 2. Qué cuentas necesitás y cuáles no

### Para arrancar hoy (Docker local)

| Servicio | ¿Hace falta? | Costo | Para qué |
|---|---|---|---|
| **OpenRouter** | ✅ **Sí** — ya la tenés | ~USD 6/mes de uso | Todos los modelos |
| Langfuse Cloud | ⬜ Opcional pero recomendado | Gratis | Ver qué hizo cada agente, cuánto costó |
| Todo lo demás | ❌ No | — | Postgres, Redis y LiteLLM corren en Docker |

**No necesitás:** VPS, dominio, Cloudflare R2, Sentry, Healthchecks, ni cuenta de
DeepSeek, Anthropic u OpenAI. Todo eso es para cuando pases a producción.

### Cargar saldo en OpenRouter

Si tu clave es nueva, cargale **USD 10**. Alcanza para meses de desarrollo y para
correr el backtest completo varias veces.

> 💡 En [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) podés
> ponerle un **límite de gasto a la clave**. Poné USD 10. Es la red de seguridad
> contra un loop infinito: corta ahí y no en tu tarjeta.

### Langfuse (opcional, 3 minutos)

Cuenta gratis en [cloud.langfuse.com](https://cloud.langfuse.com) → nuevo proyecto →
copiás `pk-lf-...` y `sk-lf-...`. Sin esto el sistema funciona igual, pero no vas a
poder ver por qué un informe salió mal.

---

## 3. Paso a paso: levantar el sistema

### 3.1 Preparar la configuración

```powershell
cd .
.\make.ps1 setup
```

Eso crea `.env` desde la plantilla e instala las dependencias de Python (`uv` baja
Python 3.12 solo — no toques tu 3.14).

### 3.2 Editar `.env`

Abrí `.\.env` y cambiá **solo estas cinco líneas**:

```bash
# 1. Contraseñas locales — cualquier cosa sirve, no salen de tu máquina
POSTGRES_PASSWORD=<POSTGRES_PASSWORD>
DATABASE_URL=postgresql+psycopg://tasador:<POSTGRES_PASSWORD>@postgres:5432/tasador
REDIS_PASSWORD=<REDIS_PASSWORD>
REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/0

# 2. Secretos de la app — generalos con el comando de abajo
SECRET_KEY=<pegar>
ENCRYPTION_KEY=<pegar>

# 3. La clave del gateway — inventala, es interna
LITELLM_MASTER_KEY=<LITELLM_MASTER_KEY>

# 4. TU CLAVE DE OPENROUTER  ← la única que importa
OPENROUTER_API_KEY=sk-or-v1-...

# 5. Langfuse (si la sacaste)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Para generar los secretos:

```powershell
uv run python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48)); print('ENCRYPTION_KEY=' + secrets.token_urlsafe(48))"
```

> ⚠️ `.env` está en `.gitignore`. **Nunca lo commitees.** Si alguna vez lo hacés sin
> querer, rotá la clave de OpenRouter inmediatamente.

### 3.3 Ajustar el YAML de modelos

Como vamos con OpenRouter solo, editá `config/litellm.yaml` y dejá **una sola entrada
por tarea**, todas con prefijo `openrouter/`:

```yaml
model_list:
  - model_name: extractor
    litellm_params:
      model: openrouter/deepseek/deepseek-chat
      api_key: os.environ/OPENROUTER_API_KEY
      temperature: 0.0
  - model_name: judge
    litellm_params:
      model: openrouter/z-ai/glm-4.6
      api_key: os.environ/OPENROUTER_API_KEY
      temperature: 0.1
  - model_name: writer
    litellm_params:
      model: openrouter/qwen/qwen3-max
      api_key: os.environ/OPENROUTER_API_KEY
      temperature: 0.3
  - model_name: critic
    litellm_params:
      model: openrouter/deepseek/deepseek-v4-flash
      api_key: os.environ/OPENROUTER_API_KEY
      temperature: 0.0
```

El resto del archivo (router, caché, presupuesto) queda igual.

### 3.4 Levantar

```powershell
.\make.ps1 dev
```

**La primera vez tarda 5-10 minutos**: construye las imágenes y baja el modelo de
embeddings (~2 GB, una sola vez). Las siguientes arrancan en segundos.

Cuando veas que dejó de scrollear, en **otra terminal**:

```powershell
docker compose ps
```

Todos los servicios deben decir `Up` y los que tienen healthcheck, `(healthy)`.

---

## 4. Cargar los datos

Con el stack levantado, en otra terminal:

```powershell
cd .

# 1. Datasets oficiales del GCBA (~15 MB, gratis, sin cuenta)
uv run python scripts/fetch_badata.py --years 2020

# 2. Barrios + serie oficial + 85.000 avisos históricos al corpus
uv run python scripts/load_badata.py

# 3. El backtest: la prueba de que el motor funciona
uv run python scripts/run_backtest.py --sample 1500

# 4. Avisos vigentes: el corpus DEMO que viene en el repo (sintético, ver
#    scripts/generar_corpus_demo.py). Con esto ya se puede generar un informe.
$env:PORTALES="demo-a=PORTAL_A,demo-b=PORTAL_B"
uv run python scripts/ingest_csv.py --carpeta data/demo
```

El paso 2 tarda ~90 segundos. El 3, ~2 minutos. El 4, segundos.

**Avisos reales.** Este repo no incluye captura de portales (doc 10 §2.2). Si
tenés archivos de un recolector propio, `ingest_csv.py` los incorpora del mismo
modo; declarás qué portal es cada uno con `PORTALES=nombre=PORTAL_A,...` en el
`.env`. La ingesta es idempotente: se puede correr después de cada tanda.

**Lo que tenés que ver al final del paso 3:**

```
MdAPE                15.0%       16.2%
✅ Le gana al baseline por 7.6%
```

Si eso sale, el motor está bien y la base está bien cargada.

---

## 5. Verificar que todo anda

```powershell
# La API responde
curl http://localhost:8000/v1/health

# Y sus dependencias también
curl http://localhost:8000/v1/ready
```

`/ready` tiene que devolver `"status": "ok"` con los tres checks en `ok`. Si LiteLLM
dice `degraded`, revisá que la clave de OpenRouter esté bien en `.env`.

**Probar que los modelos responden:**

```powershell
curl -X POST http://localhost:4000/v1/chat/completions `
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>" `
  -H "Content-Type: application/json" `
  -d '{\"model\":\"extractor\",\"messages\":[{\"role\":\"user\",\"content\":\"Decí OK\"}]}'
```

Si contesta, la cadena entera funciona: tu código → LiteLLM → OpenRouter → DeepSeek.

**La suite completa:**

```powershell
.\make.ps1 test
```

Tiene que dar **54 passed**. No toca internet ni gasta un centavo.

---

## 6. Comandos del día a día

```powershell
.\make.ps1 dev        # levantar con hot reload
.\make.ps1 down       # bajar
.\make.ps1 logs       # ver qué está pasando
.\make.ps1 test       # tests (gratis, sin red)
.\make.ps1 lint       # ruff
.\make.ps1 typecheck  # mypy strict
.\make.ps1 psql       # consola de Postgres
.\make.ps1 migrate    # aplicar migraciones
.\make.ps1 revision   # crear una migración nueva
```

**Antes de commitear, siempre:**

```powershell
.\make.ps1 fmt ; .\make.ps1 lint ; .\make.ps1 typecheck ; .\make.ps1 test
```

### Consultas útiles a la base

```sql
-- ¿Qué hay en el corpus?
select source, count(*), count(*) filter (where active) as activos
from corpus.listings group by source;

-- Cobertura por barrio (los que importan)
select n.name, count(*) filter (where l.active) as activos
from corpus.neighborhoods n left join corpus.listings l on l.neighborhood_id = n.id
where n.name in ('Palermo','Belgrano','Núñez','Colegiales','Villa Urquiza')
group by n.name order by 2 desc;

-- La serie oficial de un barrio
select period, rooms, condition_group, usd_per_m2
from corpus.market_index mi join corpus.neighborhoods n on n.id = mi.neighborhood_id
where n.name = 'Belgrano' order by period desc limit 10;
```

---

## 7. Cuando algo falla

| Síntoma | Causa casi siempre | Solución |
|---|---|---|
| `docker compose config` falla con "env file not found" | No creaste `.env` | `.\make.ps1 setup` |
| `/ready` dice `litellm: degraded` | Clave de OpenRouter mal o sin saldo | Revisá `.env` y el saldo en openrouter.ai |
| `Psycopg cannot use the 'ProactorEventLoop'` | Corriste un script sin `tasador.cli.run()` | Usá siempre `run_async(main())`, ya está en todos los scripts |
| El worker se muere sin mensaje | Sin RAM. Docker Desktop viene con poca | Docker Desktop → Settings → Resources → **Memory ≥ 8 GB** |
| `alembic: Can't locate revision` | Borraste un archivo de migración con la base ya migrada | `.\make.ps1 clean` y volvés a empezar (borra la base) |
| Los tests fallan por red | `FETCHER_STRATEGY` no está en `fixture` | `.\make.ps1 test` lo pone solo |
| Puerto ocupado | Tenés otro Postgres local en 5432 | En dev usamos **5433** y **6380** justamente por eso |

**Conectarte a la base desde DBeaver o similar:**

```
Host: localhost   Puerto: 5433
Base: tasador     Usuario: tasador
Password: el POSTGRES_PASSWORD de tu .env
```

---

## 8. Lo que NO tenés que hacer todavía

Para que no pierdas tiempo:

- ❌ Contratar el VPS. Cuando el sistema esté completo.
- ❌ Comprar dominio.
- ❌ Cuentas de DeepSeek, Anthropic u OpenAI. OpenRouter alcanza.
- ❌ Configurar backups a R2. Es infra de producción.
- ❌ Correr el SQL de las vistas en la Supabase de la inmobiliaria. Cuando lleguemos a esa
  integración (`ops/sql/panel-readonly.sql` ya está listo para ese momento).
- ❌ Sacar la app de MercadoLibre. Es opcional y de baja prioridad (verificado el
  13/08: su API de búsqueda devuelve 403 sin token autorizado).

---

## 9. Resumen de lo único imprescindible

```powershell
cd .
.\make.ps1 setup
# editar .env: contraseñas + secretos + OPENROUTER_API_KEY
# editar config/litellm.yaml: dejar solo entradas openrouter/
.\make.ps1 dev
# en otra terminal:
uv run python scripts/fetch_badata.py --years 2020
uv run python scripts/load_badata.py
uv run python scripts/run_backtest.py --sample 1500
```

**Una clave: la de OpenRouter.** El resto es local.
