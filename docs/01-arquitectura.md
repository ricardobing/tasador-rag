# 01 — Arquitectura

---

## 1. Vista general

```
┌───────────────────────────────────────────────────────────────────┐
│  PANEL PANEL  (repo ajeno · Next.js/Supabase/Vercel)           │
│  ─────────────────────────────────────────────────────────────    │
│  /tasaciones/[id]  →  [Generar informe]                           │
│  cron nocturno     →  empuja snapshot de propiedades el CRM        │
└───────────┬──────────────────────────────────┬────────────────────┘
            │ POST /v1/reports                 │ POST /v1/inventory/snapshot
            │ GET  /v1/reports/{id}            │ (1 vez/día)
            │ Bearer <api_key del tenant>      │
            ▼                                  ▼
╔═══════════════════════════════════════════════════════════════════╗
║  TASADOR — VPS propio · Docker Compose · repo propio              ║
║                                                                   ║
║  ┌─────────┐   caddy (TLS automático, rate limit, headers)        ║
║  └────┬────┘                                                      ║
║       ├──────────────┬────────────────────┐                       ║
║       ▼              ▼                    ▼                       ║
║  ┌─────────┐   ┌──────────┐        ┌────────────┐                 ║
║  │ web     │   │ api      │        │ worker     │                 ║
║  │ Next.js │   │ FastAPI  │──cola─▶│ LangGraph  │                 ║
║  │ UI      │   │          │        │ + CrewAI   │                 ║
║  └─────────┘   └────┬─────┘        └─────┬──────┘                 ║
║                     │                    │                        ║
║       ┌─────────────┴────────┬───────────┴──────────┐             ║
║       ▼                      ▼                      ▼             ║
║  ┌──────────┐         ┌───────────┐         ┌─────────────┐       ║
║  │ postgres │         │  redis    │         │  litellm    │       ║
║  │ +pgvector│         │ cola+cache│         │  gateway    │       ║
║  └──────────┘         └───────────┘         └──────┬──────┘       ║
║                                                    │              ║
║  ┌──────────────────────┐                          │              ║
║  │ ingest (cron)        │                          │              ║
║  │ Portal A/BA Data/…  │                          │              ║
║  └──────────────────────┘                          │              ║
╚════════════════════════════════════════════════════│══════════════╝
                                                     ▼
                             DeepSeek · Qwen · GLM · OpenRouter · OpenAI
                             (intercambiables por configuración)

  Observabilidad → Langfuse (cloud free tier al inicio)
```

---

## 2. Reglas de aislamiento respecto de la inmobiliaria

Son la razón por la que este proyecto es un producto y no un módulo.

> **Revisión del 13/08/2026.** La regla 1 original prohibía toda credencial de la
> Supabase de la inmobiliaria. **Se relaja a lectura opcional**: quien desarrolla este
> proyecto es también quien construyó ese sistema y tiene acceso legítimo, y la
> alternativa (llamar a la API de el CRM, o pedirle gestiones al dueño de la
> inmobiliaria) es peor. Lo que **no** se relaja es la dependencia. Ver §2.2.

| # | Regla | Fundamento |
|---|---|---|
| 1 | **Lectura opcional de la Supabase de la inmobiliaria, nunca obligatoria.** Rol de **solo lectura** sobre una **vista dedicada**, detrás de un feature flag. Sin esa fuente, el sistema arranca y funciona igual. | Aprovechar un dato que ya existe sin convertirlo en dependencia. |
| 2 | **Cero escrituras hacia sistemas ajenos.** Se garantiza con un rol de PostgreSQL que **solo tiene `SELECT`**. | Un bug nuestro no puede corromper su producción. No es disciplina: es un permiso que no existe. |
| 3 | **Cero llamadas a la API de el CRM.** Las 285 propiedades ya están en esa Supabase. | Elimina el riesgo de cuota sin pedirle nada a nadie. Ver [02 §2.3](02-fuentes-de-datos.md). |
| 4 | **Modelo de dominio propio.** El Tasador tiene `subject_properties`, no `appraisals`. La traducción vive en **un solo archivo**: `app/adapters/inbound/panel.py`. | *Anti-corruption layer*. Si cambian su schema, se rompe un archivo. |
| 5 | **Sin PII.** Cruzan dirección, atributos del inmueble y un `external_ref` opaco. Nombre, teléfono y email del propietario **nunca**. | Menos superficie legal, menos que proteger, y es argumento de venta. |
| 6 | **API versionada `/v1`, contrato congelado.** Cambios solo aditivos. | Podés deployar 20 veces por día sin coordinar con nadie. |
| 7 | **Degradación limpia en ambas direcciones.** Tasador caído → el panel muestra "informe no disponible". Panel caído → el Tasador funciona desde su propia UI. | Ninguno es punto único de falla del otro. |
| 8 | **Repos, CI, secretos, dominio y backups separados. Cero código compartido.** Del lado del panel son ~60 líneas de `fetch`, no un paquete. | Independencia real, no declarada. |
| 9 | **El Tasador funciona sin la inmobiliaria.** Login propio, UI propia, alta de tenants propia. | Es el producto. la inmobiliaria es el tenant #1, no el dueño. |

### 2.2 La base del panel de la inmobiliaria como fuente externa opcional

**Cómo se conecta** — un rol dedicado de **solo lectura** en el Postgres del panel,
con `SELECT` sobre **dos vistas** creadas para este fin y nada más: una con el
inventario (dirección, barrio, tipo, ambientes, superficie, precio, moneda,
descripción, disponibilidad) y otra con los casos de tasación (dirección y
atributos del inmueble, más el valor captado cuando existe). Ninguna de las dos
expone nombre, teléfono, email ni el mensaje original del contacto. El SQL que
las crea vive del lado del panel, no en este repo.

**Qué aporta:**

| Vista | Uso |
|---|---|
| inventario | Las propiedades con precio → dataset de backtest actual, y el dato "de estos comparables, N son de tu propia cartera" |
| tasaciones | Casos reales con dirección, tipo, ambientes y superficie → **y el valor captado cuando existe: un segundo ground truth** |

**Las cuatro garantías:**

1. **Solo lectura, por permisos.** El rol no tiene `INSERT` ni `UPDATE`. Aunque nuestro
   código tuviera un bug, no puede escribir.
2. **Sin PII.** Las vistas no exponen datos de contacto. La regla 5 sigue intacta.
3. **Detrás de una vista, no de las tablas.** Si cambian su schema, se ajusta la vista
   del lado de ellos y nuestro adaptador (`sources/panel_externo.py`) ni se entera. Es
   el *anti-corruption layer* materializado en SQL.
4. **Opcional de verdad.** `PANEL_SOURCE_ENABLED=false` y el sistema arranca igual.
   Un test verifica que con la fuente apagada el pipeline completo sigue produciendo
   informes. Si esa fuente desaparece mañana, se pierde un dataset de backtest, no el
   producto.

**Alternativa equivalente si no se quiere tocar su base:** una sincronización manual
cada 2-3 días (export a JSON → `POST /v1/inventory/snapshot`). El inventario cambia
2-3 propiedades por semana; no hace falta tiempo real. Ambos caminos están soportados
por el mismo adaptador.

### 2.1 Superficie de integración total

Todo lo que existe entre los dos sistemas:

```
Panel → Tasador:   POST /v1/reports              (al apretar el botón)
                   GET  /v1/reports/{id}         (polling de estado)
                   POST /v1/inventory/snapshot   (1×/día, propiedades el CRM)

Tasador → Panel:   nada.
```

Tres endpoints salientes desde el panel, cero entrantes. El panel es un **cliente**
del Tasador, igual que lo sería cualquier otra inmobiliaria.

---

## 3. Componentes

| Servicio | Tecnología | Responsabilidad |
|---|---|---|
| `caddy` | Caddy 2 | Reverse proxy, TLS automático (Let's Encrypt), rate limiting, cabeceras de seguridad |
| `web` | Next.js 15 (App Router) | UI propia: login, listado, alta, ficha de informe, admin |
| `api` | FastAPI + uvicorn | API `/v1`, auth por API key y por sesión, encola jobs, sirve PDFs |
| `worker` | Python + LangGraph + CrewAI | Ejecuta el grafo de generación de informes |
| `ingest` | Python + cron | Descubrimiento y descarga de avisos, carga de datasets oficiales |
| `postgres` | PostgreSQL 16 + `pgvector` + `pg_trgm` | Toda la persistencia: corpus, informes, checkpoints, evals |
| `redis` | Redis 7 | Cola de trabajos, caché de fetch, rate limiting distribuido |
| `litellm` | LiteLLM proxy | Gateway único a todos los proveedores de LLM |
| — | Langfuse (cloud) | Trazas, costos, versionado de prompts, evals |

**Por qué Python en el backend y no Node/TS** (que es donde tenés más recorrido):
el ecosistema de agentes, evals y datos —LangGraph, CrewAI, pandas, scikit, pydantic—
vive en Python, y es el lenguaje que piden los avisos de AI Engineer. La UI sigue en
Next.js/TypeScript, así que el proyecto muestra las dos cosas.

---

## 4. Decisiones de arquitectura (ADR)

### ADR-001 — LangGraph como orquestador principal, CrewAI acotado

**Contexto.** Los avisos piden CrewAI "o similares". Hay que elegir con qué se
construye el pipeline.

**Decisión.** El grafo principal se construye con **LangGraph**. **CrewAI** se usa en
**un solo nodo**: la investigación de contexto de mercado del barrio.

**Fundamento.**

- La generación de un informe es un proceso **con estado, con reintentos y con pasos
  que fallan**: el fetch de un portal se cae, un LLM devuelve JSON inválido, no hay
  suficientes comparables. Necesito *checkpointing durable*: si el proceso muere en el
  paso 6 de 11, retomar desde el 6, no desde cero. LangGraph lo da con persistencia en
  Postgres; CrewAI está pensado para colaboración autónoma entre roles, no para
  máquinas de estado confiables.
- El grafo tiene **ciclos condicionales** (el crítico rechaza → se regenera, máximo 2
  veces) y **salidas tempranas** (`insufficient_data`). Eso es un grafo, no una crew.
- La sub-tarea "¿qué está pasando en el mercado de Villa Urquiza?" **sí** es
  genuinamente abierta y multi-fuente: ahí una crew de CrewAI (investigador +
  analista) encaja bien y se usa por razones técnicas, no cosméticas.

**Consecuencia.** Se escribe un ADR público explicando esto. En una entrevista,
"evalué CrewAI y lo usé donde correspondía, el resto va en LangGraph porque necesitaba
checkpointing durable" es una respuesta más fuerte que "usé CrewAI".

**Alternativas descartadas:** CrewAI para todo (frágil ante fallos parciales);
orquestación a mano con funciones (pierdo checkpointing, reintentos y trazas gratis);
Pydantic AI (excelente, pero menos reconocido en los avisos y sin grafo con ciclos).

---

### ADR-002 — Los números no los produce un LLM

**Contexto.** Sería más simple pedirle al modelo "dame el precio".

**Decisión.** El valor y el rango salen de un cálculo determinístico en Python
(mediana de USD/m² ajustada, estadística robusta). El LLM nunca emite una cifra de
precio.

**Fundamento.**

- **Reproducibilidad:** el mismo set de comparables da el mismo número siempre.
  Requisito para auditar y para el backtest.
- **Auditabilidad:** ante el dueño de la propiedad, "salió de estos 8 avisos con
  estos ajustes" es defendible; "lo dijo la IA" no.
- **Costo:** la parte cara del cálculo es gratis.
- **Es lo correcto:** los LLM son malos en aritmética y peores en estadística. Usarlos
  para eso es un error de ingeniería, no una decisión de producto.

**Consecuencia.** El LLM queda confinado a: extracción estructurada, deduplicación,
juicio sobre comparabilidad, redacción y crítica. Ver
[05 — Metodología](05-metodologia-de-valuacion.md).

---

### ADR-003 — LiteLLM como gateway único de modelos

**Contexto.** Restricción explícita: gastar poco, poder usar modelos chinos, y poder
migrar al proveedor del cliente si el producto se vende.

**Decisión.** Ningún código del sistema importa un SDK de proveedor. Todo habla
OpenAI-compatible contra un LiteLLM self-hosted. El modelo de cada tarea se define en
`litellm-config.yaml`.

```yaml
model_list:
  - model_name: extractor        # tarea, no modelo
    litellm_params: { model: deepseek/deepseek-chat }
  - model_name: judge
    litellm_params: { model: openrouter/qwen/qwen3-max }
  - model_name: critic
    litellm_params: { model: anthropic/claude-sonnet-4-5 }
```

El código pide `model="extractor"`. Cambiar de proveedor es editar el YAML.

**Fundamento.** Aísla el costo y el proveedor en un solo lugar; da fallback automático
si un proveedor se cae; da presupuesto y tracking de costo por tenant; y permite el
escenario "se lo vendo a un cliente que quiere usar su cuenta de OpenAI" sin tocar
código.

**Sobre las credenciales disponibles:** **OpenRouter sirve** (es OpenAI-compatible,
entra directo). La API de **OpenCode probablemente no**: ese tipo de suscripción suele
estar atada a su cliente de coding y su ToS no habilita usarla como backend de una
aplicación. Se verifica en Etapa 0; no está en el camino crítico.

---

### ADR-004 — Postgres + pgvector, no una base vectorial dedicada

**Contexto.** El sistema necesita búsqueda semántica sobre descripciones de avisos.

**Decisión.** `pgvector` sobre el mismo Postgres. Nada de Pinecone, Qdrant o Weaviate.

**Fundamento.** La búsqueda real es **híbrida y mayormente estructurada**: barrio +
tipo + rango de superficie + ventana temporal, y *recién ahí* similitud semántica para
ordenar. Eso es un `WHERE` con un `ORDER BY embedding <=> $1`. Un servicio vectorial
aparte obligaría a mantener dos fuentes de verdad sincronizadas, sumaría un contenedor
más a un VPS de 4 GB y no aportaría nada a esta escala (decenas de miles de avisos).

**Embeddings locales** (`bge-m3` o `multilingual-e5-base` vía `fastembed`): costo cero,
sin llamadas de red, y suficiente para español rioplatense inmobiliario.

---

### ADR-005 — Cola de trabajos, no request/response

**Contexto.** Generar un informe tarda 60-180 segundos.

**Decisión.** `POST /v1/reports` encola y devuelve `202` con un `report_id`. El
worker procesa. El cliente hace polling de `GET /v1/reports/{id}`.

**Fundamento.** Un HTTP de 3 minutos se muere en cualquier proxy intermedio. Además la
cola da reintentos, límite de concurrencia (importante con 2 vCPU), visibilidad del
progreso paso a paso en la UI, y permite regenerar sin bloquear a nadie.

**Implementación:** Redis + `arq` (async nativo, liviano, encaja con FastAPI).
Se evaluó Celery: más conocido y mejor palabra en un CV, pero pesado y sincrónico.
Se elige la herramienta correcta y se documenta por qué.

---

### ADR-006 — Un solo Postgres para todo

**Contexto.** Tentación de separar el corpus de avisos de los datos operativos.

**Decisión.** Una sola base, con schemas lógicos (`core`, `corpus`, `eval`).

**Fundamento.** A esta escala, separar agrega operación (dos backups, dos
migraciones, dos monitoreos) sin ganancia. Y el join entre un informe y sus
comparables es constante: separarlos obligaría a resolverlo en aplicación.
Se revisa si el corpus supera ~5 millones de filas, cosa que no va a pasar en v1.

---

### ADR-007 — Multi-tenant desde la primera línea

**Contexto.** Hoy hay un solo cliente.

**Decisión.** `org_id` en toda tabla raíz, aislamiento verificado por tests, desde el
día 1.

**Fundamento.** Retrofittear multi-tenancy es de las refactorizaciones más caras y
peligrosas que existen. Agregarlo ahora cuesta casi nada. Y el objetivo declarado es
poder vender esto. Es exactamente el criterio que ya se aplicó, y funcionó, en el
panel de la inmobiliaria.

**Nota:** a diferencia del panel, acá el aislamiento **no** se apoya en RLS de
Postgres sino en un repositorio que exige `org_id` en toda query, con tests que
verifican que ninguna consulta puede omitirlo. Fundamento: no hay un cliente
autenticado hablando directo con la base (todo pasa por la API), así que RLS sería
defensa en profundidad sobre un vector que no existe, a cambio de complejidad real.

---

### ADR-008 — Observabilidad: Langfuse Cloud primero, self-hosted después

**Contexto.** Hace falta trazar cada corrida: qué prompt, qué modelo, cuántos tokens,
cuánto costó, qué devolvió cada nodo.

**Decisión.** **Langfuse Cloud (free tier)** en las etapas 0-4. Self-hosting solo si
el volumen lo justifica.

**Fundamento.** `VERIFICADO`: Langfuse v3 self-hosted **requiere ClickHouse** y su
documentación recomienda **4 vCPU / 8 GB de RAM como piso**, y 16 GB para producción.
Eso solo no entra en un VPS de 4 GB junto con Postgres, Redis, el worker y la API.
Meter Langfuse en el VPS chico significaría o pagar el doble de VPS o convivir con
OOM kills. El free tier de la nube cubre de sobra el volumen de v1.

**Instrumentación agnóstica:** se emite con OpenTelemetry/OpenInference, de modo que
migrar a Langfuse self-hosted, Phoenix o cualquier otro backend sea configuración.

---

### ADR-009 — El fetcher de portales es intercambiable

**Contexto.** `VERIFICADO`: Portal A devuelve 403 a IPs de datacenter; Portal B
bloquea todo, incluso el sitemap.

**Decisión.** Interfaz `Fetcher` con implementaciones `Direct`, `Zyte`, `Cached` y
`Fixture`, seleccionables por configuración y con degradación en cascada
(directo → proxy → caché → error explícito).

**Fundamento.** El acceso a portales es el riesgo #1 del proyecto y el más volátil.
Aislarlo detrás de una interfaz significa que un cambio de estrategia de acceso es
una clase nueva, no un refactor. `Fixture` además permite correr todos los tests y
el CI sin tocar internet ni gastar un centavo.

---

## 5. Flujo de una generación de informe

```
1. Usuario aprieta "Generar informe" (en la UI del Tasador o en el panel)
2. API valida el tenant, crea `reports` (status=queued), encola, devuelve 202
3. Worker toma el job → arranca el grafo LangGraph (checkpoint en Postgres)
4. Nodos 1..11 (ver doc 04). Cada uno emite un `report_events` + traza a Langfuse
5. Si el crítico rechaza → vuelve al redactor (máx. 2 veces)
6. Si hay < 5 comparables → status=insufficient_data y termina limpio
7. Render de PDF → guardado en disco + backup a R2
8. status=succeeded. El cliente lo ve en el siguiente poll
```

**Idempotencia:** `POST /v1/reports` acepta un header `Idempotency-Key`. Dos clicks
del mismo botón no generan dos informes ni cobran dos veces.

---

## 6. Qué NO está en la arquitectura, y por qué

| Ausente | Por qué |
|---|---|
| Kubernetes | 1 VPS, 8 contenedores. Compose alcanza y se opera de verdad. |
| Base vectorial dedicada | ADR-004. |
| Microservicios | El sistema tiene un solo dominio. Partirlo sería complejidad sin beneficio. |
| Kafka / event bus | Redis alcanza para una cola con este volumen. |
| Fine-tuning de modelos | Prompt + few-shot + validación tipada resuelve la extracción. El fine-tuning es la optimización que se hace cuando ya medís, no antes. |
| GraphQL | Un contrato REST chico y estable es más fácil de consumir desde el panel. |
| Autenticación federada / SSO | Login propio con sesión. SSO cuando haya un cliente que lo pida. |
