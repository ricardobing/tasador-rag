# 12 — Plan de desarrollo

**Modalidad:** part-time. Las semanas son de referencia, no un compromiso.
**Principio de ordenamiento:** cada etapa termina con **algo que funciona y se puede
mostrar**. Nada de "la etapa de backend" seguida de "la etapa de frontend".

**Regla de oro del orden:** los riesgos se atacan de mayor a menor. Las dos preguntas
que pueden matar el proyecto son *"¿puedo conseguir datos?"* y *"¿el método sirve?"*,
y las dos se responden antes de la Etapa 2. Si alguna da mal, se pierde una semana y
media, no dos meses.

---

## Etapa 0 — Fundaciones (1 semana)

**Objetivo:** un `/v1/health` respondiendo en un dominio real, con CI verde.

| # | Tarea | Done cuando |
|---|---|---|
| 0.1 | Repo, estructura, `pyproject`, ruff + mypy strict, pre-commit | `make lint` verde |
| 0.2 | Docker Compose completo (8 servicios) corriendo local | `make dev` levanta todo y los healthchecks pasan |
| 0.3 | Postgres + extensiones + Alembic + migración inicial de `core` | `alembic upgrade head` sin errores |
| 0.4 | FastAPI con `/v1/health` y `/v1/ready` | Responden con dependencias verificadas |
| 0.5 | Auth: API keys (argon2id) + sesión de usuario | Test de auth verde |
| 0.6 | Multi-tenancy: repositorio con `org_id` + test de arquitectura | El test falla si borro un filtro de `org_id` |
| 0.7 | LiteLLM configurado con DeepSeek + OpenRouter y fallback | Un ping a `extractor` responde |
| 0.8 | Langfuse Cloud conectado | La llamada de prueba aparece en el dashboard |
| 0.9 | VPS: alta, ufw, fail2ban, usuario de deploy, Caddy con TLS | `https://tasador.<dominio>/v1/health` → 200 |
| 0.10 | CI/CD: lint + test + build + deploy por SSH con rollback | Un push a main deploya solo |
| 0.11 | Backup nocturno a R2 + restore probado | Restore ejecutado con éxito una vez |

**Acciones fuera del código, en paralelo:**

- [ ] Crear el rol `tasador_ro` y las dos vistas sin PII en la Supabase de la inmobiliaria
      ([01 §2.2](01-arquitectura.md)). Lo hacés vos, no depende de nadie.
- [ ] Verificar si la API de OpenCode sirve como backend (ADR-003). Si no, seguir con
      OpenRouter.

> ~~Mail a soporte de el CRM~~ — **cancelado 13/08.** Requeriría gestión del titular de
> la cuenta y este proyecto es independiente del dueño de la inmobiliaria. Se lee la
> Supabase en su lugar.

**Entregable demostrable:** un servicio en producción con TLS, CI y backups. Vacío,
pero real.

---

## Etapa 1 — Datos y corpus (1,5 semanas) 🔴 *la etapa que decide el proyecto*

**Objetivo:** responder si hay datos suficientes. Sin IA todavía.

| # | Tarea | Done cuando |
|---|---|---|
| 1.1 | Migración de `corpus` + seed de `neighborhoods` con aliases | Los barrios de CABA y GBA Norte cargados con sus alias |
| 1.2 | **Descargar y perfilar BA Data "Departamentos en Venta" 2015-2020** | CSVs cargados en `listings` con `source='BADATA'`. Sé cuántos casos por año y por barrio |
| 1.3 | Cargar la serie oficial "Precio de venta de departamentos" en `market_index` | Puedo consultar el USD/m² oficial de cualquier barrio |
| 1.4 | ~~Probar fetch directo a Portal A~~ | ✅ **HECHO 13/08** — 200 desde IP residencial, 403 desde datacenter |
| 1.5 | ~~¿Hay JSON-LD?~~ | ✅ **HECHO** — no, pero la **tarjeta del listado trae 20 comparables completos** por request |
| 1.6 | Ingestor de listados de Portal A (sobre el prototipo ya validado) + normalización + caché + throttle | 400 avisos de 4 barrios en `listings`, con `ingest_runs` completo |
| 1.7 | `POST /v1/ingest/push` — la ingesta corre local y empuja al VPS | Un corpus armado en casa aparece en producción |
| 1.8 | Interfaz `Fetcher` con `Direct` / `Zyte` / `Cached` / `Fixture` (ADR-009) | Cambio de estrategia por variable de entorno |
| 1.9 | `ingest_runs` / `ingest_items` + cron nocturno | Una corrida deja traza completa |
| 1.10 | Adaptador **opcional** de la Supabase de la inmobiliaria (read-only, vistas sin PII) + `POST /v1/inventory/snapshot` | Las 285 propiedades y las tasaciones históricas en la base. **Y un test que verifica que con `PANEL_SOURCE_ENABLED=false` todo sigue funcionando** |
| 1.11 | Embeddings locales (`bge-m3`) + índice HNSW | kNN sobre 1.000 avisos responde en < 100 ms |
| 1.12 | **Reporte de cobertura por barrio** | Sé, con números, en qué barrios hay ≥ 20 avisos activos |

**🚦 Puerta de decisión al cierre de la Etapa 1:**

| Resultado | Qué se hace |
|---|---|
| ≥ 20 avisos activos en los barrios de la inmobiliaria | ✅ Seguir a la Etapa 2 |
| Cobertura insuficiente pero el fetch funciona | Ampliar el crawleo antes de seguir |
| Portal A bloquea y Zyte también | 🔴 **Replantear.** Opciones: apoyarse solo en BA Data + inventario propio (menos preciso pero funcional), o pedirle los datos a la inmobiliaria, o cambiar de proyecto. **Se decide acá, con una semana y media gastada, no con dos meses** |

**Entregable demostrable:** un corpus consultable y un reporte honesto de cuánta
cobertura hay. Sin una línea de IA.

---

## Etapa 2 — Valuación (1 semana) 🔴 *la segunda pregunta que puede matar el proyecto*

**Objetivo:** saber si el método funciona. **Todavía sin agentes.**

| # | Tarea | Done cuando |
|---|---|---|
| 2.1 | `normalize_subject`: normalización de dirección + Nominatim + resolución de barrio | 50 direcciones reales de la inmobiliaria geocodificadas correctamente |
| 2.2 | `retrieve_candidates`: SQL híbrido + relajación progresiva | Dada una propiedad, devuelve candidatos ordenados |
| 2.3 | **Motor de valuación completo** ([05](05-metodologia-de-valuacion.md)): ponderada, ajustes, mediana robusta, rango, confianza | Los 9 tests de [05 §10](05-metodologia-de-valuacion.md) verdes |
| 2.4 | `config/adjustments.yaml` versionado | Cambiar un coeficiente no requiere tocar código |
| 2.5 | Baseline `MEDIAN_NEIGHBORHOOD_M2` implementado | Devuelve un número para cualquier barrio |
| 2.6 | **Backtest v0 sobre BA Data**, sin extracción por LLM (usando los campos ya estructurados del dataset) | **Tengo MdAPE del sistema y del baseline sobre ~1.000 casos** |

**🚦 Puerta de decisión al cierre de la Etapa 2:**

| Resultado | Qué se hace |
|---|---|
| Sistema le gana al baseline | ✅ Seguir. Los agentes van a mejorar esto, no a salvarlo |
| Empata con el baseline | ⚠️ Revisar los coeficientes de ajuste antes de seguir. Puede ser que estén mal calibrados |
| Pierde contra el baseline | 🔴 El método o la selección de comparables está mal. **Arreglarlo acá**, donde es una función pura de 200 líneas, no después con 11 nodos encima |

Esta puerta es el momento más valioso de todo el plan: se valida la hipótesis central
del proyecto con **cero IA involucrada**, así que si falla, se sabe exactamente que la
culpa no es del modelo.

**Entregable demostrable:** un número. `MdAPE 14,2% vs baseline 19,1%`.

---

## Etapa 3 — El pipeline de agentes (2 semanas)

**Objetivo:** todo lo que las etapas 1-2 no podían hacer sin LLM.

| # | Tarea | Done cuando |
|---|---|---|
| 3.1 | Esqueleto LangGraph con checkpointer en Postgres | Un grafo de 2 nodos sobrevive matar el worker a mitad |
| 3.2 | Cola `arq` + `POST /v1/reports` (202) + `GET /v1/reports/{id}` | Un informe se encola y se sigue por polling |
| 3.3 | `report_events` + trazas a Langfuse por nodo | Veo cada nodo con su costo y latencia |
| 3.4 | **Nodo 4 — extracción** con `instructor` + Pydantic + batching + caché | Golden set de extracción > 92% |
| 3.5 | **Nodo 5 — dedup** en cascada de 3 capas | Golden set de dedup: precisión > 0,95 |
| 3.6 | **Nodo 6 — curaduría**: reglas duras + juez con motivos cerrados | Golden set de curaduría: recall > 0,90 |
| 3.7 | Salida `INSUFFICIENT_DATA` con detalle y sugerencias | Una propiedad en un barrio vacío devuelve el motivo correcto |
| 3.8 | **Nodo 8 — contexto** con CrewAI, con herramientas sobre nuestra base | El contexto de Belgrano cita el dato oficial y el nuestro |
| 3.9 | **Nodo 9 — redactor** con prompt que prohíbe calcular | Genera markdown con las secciones fijas |
| 3.10 | **Nodo 10 — crítico**: verificación determinística + crítica adversarial + ciclo | Un informe con un número plantado a mano es rechazado |
| 3.11 | `prompts/` versionado + `bundle.lock.json` | El hash del bundle se estampa en cada informe |
| 3.12 | Test de prompt injection (15 casos) | Verde en CI |
| 3.13 | Nodo 3 — ingesta on-demand con presupuesto | Un barrio sin cobertura dispara fetch acotado |
| **3.14** | **`parse-text`**: extraer campos de un aviso pegado a mano (reusa el nodo 4) | Pego el texto de un aviso de Portal B y salen los campos. **Es la respuesta a que Portal B bloquee** |
| **3.15** | **`Transcriber`** con `faster-whisper` local + `Fixture` ([15 §3.1](15-captura-por-voz.md)) | Un audio de 2 min transcribe en < 45 s, sin salir del servidor |
| **3.16** | Prompt `extract_subject`: transcripción → atributos **con la frase de origen** | De la nota de ejemplo del doc 15 salen los 7 campos |
| **3.17** | Redacción de PII en la transcripción, con test | Nombres, teléfonos y referencias personales no se persisten |

**Entregable demostrable:** el pipeline completo generando informes en markdown, con
trazas y costos visibles.

---

## Etapa 4 — Producto (1,5 semanas)

**Objetivo:** que lo pueda usar alguien que no sea yo.

| # | Tarea | Done cuando |
|---|---|---|
| 4.1 | Next.js + shadcn + auth por sesión + layout | `/login` funciona |
| 4.2 | `/informes` con DataTable, filtros y búsqueda | Lista y filtra informes reales |
| 4.3 | `/informes/nuevo` en dos bloques + autocompletado de dirección | Genero un informe desde el navegador |
| 4.4 | `/informes/[id]` progreso: stepper en castellano llano | Veo el avance en vivo |
| 4.5 | `/informes/[id]` resultado: encabezado, gráfico de dispersión, tabla de comparables expandible con excluidos, contexto, narrativa, limitaciones | Un agente entiende el informe sin que se lo expliquen |
| 4.6 | Pantalla de `INSUFFICIENT_DATA` con acciones | Ofrece reintentar con zona ampliada |
| 4.7 | **Nodo 11 — PDF** con WeasyPrint + plantilla con marca + disclaimers | El PDF sale bien impreso en A4 |
| 4.8 | `/comparables` explorador + "Reportar extracción incorrecta" | Puedo auditar el corpus |
| 4.9 | `POST /reports/{id}/regenerate` y `/share` | El flujo post-visita funciona |
| 4.10 | `/admin/organizacion`: marca, cuotas, API keys | Puedo dar de alta un tenant |
| **4.11** | **Grilla de carga manual de comparables** + pegar de Excel + importar CSV ([14 §9.2](14-carga-manual.md)) | Genero un informe con comparables 100% cargados a mano |
| **4.12** | **Overrides**: incluir/excluir/corregir con motivo + `report_overrides` + recalcular | Un cambio en el set recalcula el rango y queda auditado |
| **4.13** | Chips de origen por comparable + declaración en el PDF | El PDF dice "6 de portales, 3 cargados por Juan Pérez" |
| **4.14** | **`/informes/[id]/voz`**: grabador móvil (`MediaRecorder`), escuchar y regrabar | Grabo desde el celular y sube |
| **4.15** | Pantalla de confirmación campo por campo, con la cita de origen | Veo qué entendió y de qué frase, antes de que toque el precio |

**Entregable demostrable:** el producto completo. Se puede hacer una demo de punta a
punta.

---

## Etapa 5 — Medición (1,5 semanas)

**Objetivo:** que el sistema pruebe que funciona, y que lo siga probando solo.

| # | Tarea | Done cuando |
|---|---|---|
| 5.1 | Runner de backtest con leave-one-out temporal | Corre sobre `BADATA_2015_2020` de punta a punta |
| 5.2 | Todas las métricas: MdAPE, MAPE, PPE10/20, cobertura, hit rate, sesgo | Se guardan en `eval.backtest_runs` con las 3 versiones |
| 5.3 | Baseline 2 (`MEDIAN_COMPARABLES_RAW`) | Se calcula en la misma corrida |
| 5.4 | Segmentación por barrio, tipo, superficie y **nivel de confianza** | Chequeo de calibración disponible |
| 5.5 | Backtest sobre `CRM_INVENTORY` (las 285 de la inmobiliaria) | Tengo el número que le muestro a la inmobiliaria |
| 5.6 | Golden set completo (120 casos) + eval de componentes en CI | Verde en cada PR |
| 5.7 | Gate del CI: bloquear PR si el MdAPE empeora > 2 puntos | Probado con un cambio malo a propósito |
| 5.8 | `/calidad`: comparación con baseline, evolución, heatmap por barrio, peores casos | La pantalla existe y muestra datos reales |
| 5.9 | `/admin/fuentes` con salud y **chequeo de sesgo contra la serie oficial** | Alerta si un barrio se desvía > 15% |
| 5.10 | Cron semanal de backtest | Corre solo y publica |

**Entregable demostrable:** la pantalla `/calidad`. **Esta es la captura que va en el
CV y la que se abre en una entrevista.**

---

## Etapa 6 — Endurecimiento (1 semana)

| # | Tarea | Done cuando |
|---|---|---|
| 6.1 | Rate limiting en Caddy + Redis, probado | Un flood recibe 429 |
| 6.2 | Alertas completas ([08 §7](08-infra-y-despliegue.md)) | Cada una probada disparándola a mano |
| 6.3 | Checklist de seguridad de [10 §6](10-seguridad-y-legal.md) completo | Los 11 ítems tildados |
| 6.4 | Runbook `ops/RUNBOOK.md` + `ops/RESTORE.md` | Un restore ejecutado siguiendo el doc al pie de la letra |
| 6.5 | Cachear el contexto de barrio (TTL 7 días) | El costo del nodo 8 baja > 80% en el segundo informe del barrio |
| 6.6 | **Calibración empírica de coeficientes**: regresión hedónica sobre BA Data | Los coeficientes de `adjustments.yaml` salen de datos, no de criterio. Backtest confirma mejora |
| 6.7 | Página pública `/bot` con política de crawleo y contacto | Publicada, y la URL en el User-Agent |
| 6.8 | Documentación de operación y onboarding de un tenant nuevo | Doy de alta un tenant de prueba en < 15 min |

**Entregable demostrable:** un sistema que se puede dejar solo.

---

## Etapa 7 — Integración con el panel (medio día) ⬅️ *lo último, y opcional*

| # | Tarea |
|---|---|
| 7.1 | `src/lib/tasador/client.ts` en el repo del panel (~60 líneas) |
| 7.2 | Route handlers proxy (la API key nunca toca el browser) |
| 7.3 | `InformeButton` en `/tasaciones/[id]` con stepper y link al PDF |
| 7.4 | Push del snapshot de inventario en el cron nocturno que ya existe |
| 7.5 | Feature flag `TASADOR_ENABLED` |

**Por qué al final:** el Tasador ya funciona sin esto. Si la inmobiliaria no quiere
integrarlo, no se pierde nada. Y hacerlo último garantiza que el contrato `/v1` ya
esté estabilizado por el uso real.

---

## Resumen

| Etapa | Semanas | Riesgo que retira | Entregable |
|---|---|---|---|
| 0 Fundaciones | 1 | Operacional | Servicio en producción con CI y backups |
| **1 Datos** | **1,5** | **🔴 "¿hay datos?"** | Corpus + reporte de cobertura |
| **2 Valuación** | **1** | **🔴 "¿el método sirve?"** | MdAPE vs. baseline |
| 3 Agentes | 2 | Técnico | Pipeline completo con trazas |
| 4 Producto | 1,5 | De producto | UI + PDF usables |
| 5 Medición | 1,5 | De credibilidad | `/calidad` con números |
| 6 Endurecimiento | 1 | De producción | Sistema autónomo |
| 7 Integración | 0,5 | — | Botón en el panel |
| **Total** | **~10** | | |

**Los dos primeros riesgos se retiran en la semana 3,5.** Ese es el punto del orden:
las dos preguntas que pueden hacer inviable el proyecto se responden con dos semanas y
media de trabajo, y sin haber escrito una línea de código de agentes.

---

## Reglas de trabajo

Heredadas de lo que funcionó en `leads-ventas`:

1. **Nada se declara terminado sin verificación real.** Ni build verde ni "debería
   andar": una corrida contra datos reales con el output pegado en el doc.
2. **Un doc por etapa** con lo hecho, lo verificado y lo que quedó pendiente.
3. **Migraciones siempre aditivas**, numeradas, nunca destructivas en el mismo deploy.
4. **El backtest se corre antes de mergear** cualquier cambio de prompts, coeficientes
   o valuación.
5. **Los resultados se publican como salen.** Si el sistema no le gana al baseline,
   se dice.
6. **Si una etapa se estira más del 150% de lo estimado**, se para y se replantea el
   alcance en vez de seguir empujando.
