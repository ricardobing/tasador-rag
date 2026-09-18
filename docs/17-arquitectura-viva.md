# 17 — Arquitectura viva

**Cómo va quedando el proyecto de verdad**, no cómo se diseñó. Se actualiza cuando la
realidad difiere del plan.

Leyenda: ✅ construido y verificado · ⚠️ parcial · ⬜ falta

---

## 1. Vista de conjunto

```
                        ┌──────────────────────────────────────┐
                        │  ENTRADA DE LA PROPIEDAD A TASAR     │
                        │  · formulario  ⬜                     │
                        │  · nota de voz ⬜  (doc 15)           │
                        │  · API /v1/reports ⬜                 │
                        └──────────────────┬───────────────────┘
                                           ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                      GRAFO LangGraph  ✅                               │
   │  checkpointer en Postgres · reintentos · salida temprana · traza      │
   │  topología desde config/agents.yaml, no desde el código               │
   │                                                                       │
   │  1 normalize_subject ✅ ─▶ 2 retrieve ✅ ─▶ ¿pocos? ─▶ 3 capture ⬜    │
   │                                    │                                  │
   │  4 extract_features ✅ ◀───────────┘                                  │
   │      │  v4-flash · Pydantic+instructor · lotes paralelos              │
   │      │  caché por aviso · CITA TEXTUAL VERIFICADA                     │
   │      ▼                                                                │
   │  5 dedup_cluster ✅ ─▶ 6 curate ✅ ─▶ ¿≥5? ──no──▶ INSUFFICIENT_DATA  │
   │                                        │ sí                           │
   │                                        ▼                              │
   │  ╔═══════════════════════════════════════════════════════╗            │
   │  ║  7 adjust_and_value  ✅  DETERMINÍSTICO — SIN LLM      ║            │
   │  ║  superficie ponderada · ajustes · mediana robusta ·    ║            │
   │  ║  winsorizado · rango · confianza calibrada            ║            │
   │  ╚═══════════════════════════════════════════════════════╝            │
   │                                        │                              │
   │  8 market_context ✅ ────────▶ 9 write_report ✅ ─▶ 10 critic ✅     │
   │                                                    rechaza │ (máx 2)  │
   │                                        ┌───────────────────┘          │
   │                                        ▼                              │
   │                                  11 render_pdf ✅                      │
   │                                                                       │
   │  El ciclo crítico→redactor sale de agents.yaml (`reintenta_a`), no del │
   │  código. Máx. 2 rechazos; al tercero, informe SIN narrativa: los       │
   │  números son correctos, lo que falló es la prosa.                     │
   └───────────────────────────────────────┬───────────────────────────────┘
                                           ▼
                              informe + PDF + trazabilidad
```

**Los 11 nodos están construidos y el informe sale completo, con narrativa
verificada cifra por cifra.** El único apagado es el 3 (captura on-demand), y a propósito:
necesita Chrome real con IP residencial y hoy corre a mano desde Windows.

El orden en que se construyeron no fue el del diagrama: primero el 7, porque es
el que produce el número y el único que se puede medir sin gastar un centavo en
LLM. Todo lo demás es lenguaje y juicio alrededor de él.

---

## 2. Los agentes: quién hace qué y con qué modelo

| # | Nodo | Tipo | Modelo (tarea) | Costo/informe | Estado |
|---|---|---|---|---|---|
| 1 | `normalize_subject` | determinístico | — | $0 | ✅ |
| 2 | `retrieve_candidates` | SQL (+ embeddings, Fase 5) | — | $0 | ✅ |
| 3 | `ondemand_capture` | I/O (Playwright) | — | $0 | ⚠️ fetcher ✅, nodo ⬜ |
| **4** | **`extract_features`** | **LLM** | `extractor` | **0,0032 / $0 en caliente** | ✅ |
| **5** | **`dedup_cluster`** | **híbrido** | `judge` (solo dudas) | **0,001** | ✅ |
| **6** | **`curate`** | **reglas + LLM** | `judge` | **0,0004** | ✅ recall 100% |
| **7** | **`adjust_and_value`** | **determinístico** | **ninguno** | **$0** | ✅ |
| **8** | **`market_context`** | SQL + 2 agentes | `judge` | **0,0006** | ✅ |
| **9** | **`write_report`** | **LLM** | `writer` | **0,005** | ✅ |
| **10** | **`critic`** | **híbrido** | `critic` (el caro) | **0,037** | ✅ el 85% del costo |
| **11** | **`render_pdf`** | **determinístico** | — | **$0** | ✅ solo en contenedor |

**Medido hoy** (informe COMPLETO, con narrativa verificada):
**43 s / USD 0,042** aprobado al primer intento · 87 s / USD 0,081 con dos
rechazos del crítico. Doc 04 §4 estimaba USD 0,044: el número real es 0,042.

### 2.1 Dos capas de configuración, cero nombres de modelo en el código

```
config/agents.yaml    nodo del grafo  ->  TAREA + PROMPT + parámetros
config/litellm.yaml   TAREA           ->  PROVEEDOR
```

El código pide `node("extract_features")`; no nombra modelos ni proveedores.
Cambiar de modelo, subir la versión de un prompt, agrandar el batch, reordenar o
**apagar** un nodo es editar YAML (ADR-003). Para iterar sin tocar archivos
versionados: `TASADOR_TASK_<NODO>=flash`.

Hay dos tests que cruzan las capas y fallan si se desincronizan: que toda tarea
usada exista en el gateway, y que ningún fallback apunte a una tarea inexistente.

Estado al 13/08/2026, verificado con `scripts/check_models.py --structured`
(no solo "responde": **devuelve un objeto Pydantic validado**):

```
extractor        → openrouter/deepseek/deepseek-v4-flash        ✅  1.566 ms
extractor_backup → openrouter/qwen/qwen3-30b-a3b-instruct-2507  ✅    971 ms
judge            → openrouter/deepseek/deepseek-v4-flash        ✅  1.385 ms
judge_deep       → openrouter/z-ai/glm-4.6                      ✅ 44.869 ms ⚠️
writer           → openrouter/qwen/qwen3-max                    ✅  2.268 ms
critic           → openrouter/anthropic/claude-sonnet-4.5       ✅  2.722 ms
```

⚠️ **GLM-4.6 dejó de ser el `judge` de línea.** Es de razonamiento: gastó 1.180
tokens de salida y 45 segundos en la misma extracción trivial que v4-flash
resolvió en 1,5 s con 31 tokens. Los dos aciertan, pero 45 s por llamada no entra
en el presupuesto de doc 09 §4. Queda como `judge_deep`, para la escalada de la
zona gris del nodo 5. Cuál gana en precisión lo decide el golden set.

### 2.2 Dónde va CrewAI y por qué solo ahí

Solo el nodo 8. Es la única sub-tarea genuinamente abierta ("¿qué pasa en el mercado de
Villa Urquiza?"). El resto es una máquina de estados con reintentos, ciclos y salidas
tempranas: eso es LangGraph, no una crew (ADR-001).

**Restricción:** las herramientas de la crew consultan **nuestra base**, no la web
abierta. Un agente navegando libremente traería cifras imposibles de auditar, y el
nodo 10 las rechazaría.

---

## 3. Rutas HTTP

```
✅  GET  /v1/health              liveness — no toca dependencias
✅  GET  /v1/ready               readiness — Postgres, Redis, LiteLLM

✅  POST /v1/reports             encola, devuelve 202 + report_id (idempotente)
✅  GET  /v1/reports/{id}        progreso nodo a nodo, o resultado
⬜  GET  /v1/reports/{id}/pdf
⬜  POST /v1/reports/{id}/regenerate     flujo post-visita
⬜  POST /v1/reports/{id}/comparables    carga manual (doc 14)
⬜  POST /v1/reports/{id}/voice-note     nota de voz (doc 15)
⬜  POST /v1/capture/plan        qué falta capturar
⬜  POST /v1/capture/ingest      HTML → corpus
⬜  POST /v1/inventory/snapshot  el panel empuja su inventario
⬜  GET  /v1/usage               consumo del tenant
```

Contrato completo en [06](06-api-contrato.md). `/v1` no rompe nunca: solo cambios
aditivos.

---

## 4. Datos: de dónde salen y adónde van

```
  FUENTES                          CORPUS                        CONSUMO
┌──────────────────┐
│ Portal B      ✅ │──┐
│ Chrome+CDP       │  │
├──────────────────┤  │   ┌────────────────────────┐
│ Portal A     ❌ │  ├──▶│ corpus.listings        │──┐
│ WAF bloquea      │  │   │ 85.022 filas           │  │
├──────────────────┤  │   │  · 84.998 BADATA       │  │   ┌──────────────────┐
│ BA Data       ✅ │──┤   │  · 24 PORTAL_B  ◀ vivos│  ├──▶│ nodo 2: retrieve │
│ CSV del GCBA     │  │   └────────┬───────────────┘  │   │ filtros + kNN    │
├──────────────────┤  │            │                  │   └────────┬─────────┘
│ Supabase      ⚠️ │──┘   ┌────────▼───────────────┐  │            ▼
│ panel la inmobiliaria   │      │ listing_features    ⬜ │  │   ┌──────────────────┐
└──────────────────┘      │ ◀ lo que llena nodo 4  │  │   │ nodo 7: valuar ✅│
                          ├────────────────────────┤  │   └────────┬─────────┘
                          │ listing_embeddings  ⬜ │──┘            ▼
                          │ pgvector 1024 + HNSW   │      ┌──────────────────┐
                          ├────────────────────────┤      │ core.reports  ⬜ │
                          │ listing_snapshots   ✅ │      │ + comparables    │
                          │ historial de precio    │      │ + events + pdf   │
                          ├────────────────────────┤      └──────────────────┘
                          │ market_index        ✅ │
                          │ 2.771 pts oficiales    │──────▶ chequeo de sesgo ✅
                          └────────────────────────┘        Belgrano +7,1%
```

### 4.1 Las tres decisiones de datos que más importan

**`listing_features` es tabla aparte de `listings`.** `listings` es el HECHO (lo que
publicaron); `features` es una INTERPRETACIÓN con su versión de modelo y prompt.
Cuando mejore el extractor se reprocesa sin tocar el crudo, y se pueden comparar
versiones.

**`listings.org_id` es NULLABLE.** NULL = aviso público, compartido entre tenants (por
eso el costo marginal de un cliente nuevo es casi cero). Con valor = cargado a mano por
ese tenant, y **solo ese tenant lo ve**.

**`active=false` en los históricos de BA Data.** Nunca pueden aparecer como comparables
vigentes; solo sirven para el backtest. Sin ese flag, la cobertura daría 85.000 y sería
mentira.

---

## 5. Cómo entran los datos hoy

```
   scripts/ingest_csv.py --carpeta …          scripts/load_badata.py
         │                                          │
   ┌─────▼──────────────────┐              ┌────────▼─────────┐
   │ ARCHIVOS               │              │ API CKAN del GCBA│
   │ csv · json · jsonl     │              │ → CSV → corpus   │
   │ · escaneo recursivo    │              └──────────────────┘
   │ · reconoce por         │
   │   CONTENIDO, no ruta   │   De dónde salen los archivos es decisión de
   │ · idempotente: sha256  │   quien opera la instancia (doc 10 §2.2). El
   └─────┬──────────────────┘   producto solo conoce PORTAL_A / PORTAL_B.
         │  filas → Card
   ┌─────▼──────────────────┐
   │ ingest/core.py         │
   │ · DESCARTE TEMPRANO ⭐ │  sin_precio · precio_no_usd · sin_superficie
   │ · upsert idempotente   │  sin_direccion · usd_m2_fuera_de_rango
   │ · snapshot si cambió   │
   └────────────────────────┘
```

**El descarte temprano corre antes de tocar la base.** El costo de un aviso inservible
no es la fila: es la extracción por LLM que se le va a correr después y que nunca va a
servir.

---

## 6. Infraestructura

```
docker compose  (8 servicios, todo local hoy)

  caddy ⬜        TLS, reverse proxy          — sin usar hasta el VPS
  web ⬜          Next.js                     — Etapa 4
  api ✅          FastAPI /v1
  worker ⬜       arq + LangGraph             — Etapa 3
  ingest ⬜       cron interno
  migrate ✅      Alembic
  litellm ✅      gateway de modelos
  postgres ✅     16 + pgvector + pg_trgm
  redis ✅        cola y caché

Fuera del contenedor, a propósito:
  Chrome real ✅  la captura corre en Windows, con IP residencial
  Langfuse ⬜     cloud free tier (self-host pide 8 GB, ADR-008)
```

---

## 7. Diferencias entre el diseño y la realidad

| Diseñado | Realidad | Doc |
|---|---|---|
| Nodo 8 con CrewAI y herramientas | Las 4 consultas son SQL determinístico; CrewAI queda opcional porque **rompe la contabilidad de costo** (no acepta inyectar el cliente HTTP) | informes/etapa-3 |
| Nodo 2 con `JOIN listing_features` | INNER devolvía cero: las features las crea el nodo 4, que corre después. Es LEFT | 04 nodo 2 |
| Relajación por "barrios limítrofes" y "comuna" | No había ni un centroide ni adyacencia. Se geocodificaron los 59 y es radio en metros | 04 nodo 2 |
| `confidence` por campo declarada por el modelo | El modelo cita el fragmento del aviso y **se verifica sin LLM**. Los modelos son malos estimando su propia confianza | 04 nodo 4 |
| GLM-4.6 como juez | 45 s por llamada. Pasó a `judge_deep`; el juez de línea es v4-flash | informes/etapa-3 |
| Batch de 10 avisos en el extractor | Se pasaba de `max_tokens` y se perdía el lote. Son 6, en paralelo | 04 nodo 4 |
| MercadoLibre API como fuente principal | Cerrada desde abril 2025 (403 sin token) | 02 §5 |
| Portal A como fuente principal | Bloqueado por AWS WAF a Playwright | informes/playwright |
| Portal B descartado | **Es la fuente que funciona** | informes/playwright |
| Fetcher async | Síncrono — conflicto de event loops en Windows | `playwright_fetcher.py` |
| Rango = p25-p75 de comparables | Se agregó ±20% de incertidumbre: el p25-p75 mide el mercado, no nuestro error | 05 §6 |
| el CRM API para el inventario | Se lee la Supabase del panel: cero requests a el CRM | 01 §2.2 |
| Ingesta en el VPS | Corre en Windows: el VPS es IP de datacenter | informes/hallazgos |

---

## 8. Por dónde crece

**Hecho (Etapa 3, fases 0-3):** el grafo con checkpointer, la cola, los endpoints
y los nodos 1, 2, 4 y 7. **El nodo 4 encendió los coeficientes**: sobre los mismos
22 comparables de Belgrano, el valor pasó de USD 343.805 (sin features) a 284.715
(con features). Que se mueva un 17% no prueba que ahora sea correcto — prueba que
los ajustes operan sobre datos reales, que es lo que el backtest no pudo medir.

**Ahora:** nodos 5 (dedup) y 6 (curate). La dispersión de Belgrano —3,8× entre el
comparable más barato y el más caro del mismo barrio— es lo que esos dos nodos
tienen que recortar.

**Antes de declarar que el nodo 4 sirve:** el golden set de doc 09 §3.3. Que las
citas verifiquen prueba que el modelo **no inventa**; que acierte es otra cosa y
solo se mide contra anotación humana.

**Después:** UI + PDF (Etapa 4) · `/calidad` y evals en CI (Etapa 5).

**Lo que NO se agrega:** más agentes para mejorar el número. Lo medido dice que faltan
datos de entrada, no procesamiento.
