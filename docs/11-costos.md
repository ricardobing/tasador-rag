# 11 — Costos

**Supuesto de volumen:** 50 informes/mes (la inmobiliaria hace ~40-60 tasaciones/mes).

---

## 1. Costo mensual total

| Concepto | Proveedor | Costo/mes |
|---|---|---|
| VPS CX32 (4 vCPU / 8 GB / 80 GB) | Hetzner | **€6,80** ≈ USD 7,40 |
| Snapshots semanales | Hetzner | €0,50 ≈ USD 0,55 |
| Dominio `.com.ar` (prorrateado) | NIC.ar | ~USD 0,80 |
| LLM — 50 informes | DeepSeek / OpenRouter | **USD 2,20** |
| LLM — backtests (4 semanales + PRs) | " | **USD 3,50** |
| Fetch de portales (1.500 req) | Zyte PAYG | **USD 0,20 – 2,00** |
| Embeddings | local (`bge-m3` en CPU) | **USD 0** |
| Observabilidad | Langfuse Cloud free tier | USD 0 |
| Errores | Sentry free tier | USD 0 |
| Uptime + crons | Healthchecks.io free | USD 0 |
| Backups | Cloudflare R2 free tier (10 GB) | USD 0 |
| Registry de imágenes | GHCR (público) | USD 0 |
| CI | GitHub Actions (2.000 min free) | USD 0 |
| **TOTAL** | | **USD 15 – 17 / mes** |

**El gasto más grande no es el LLM: es el VPS.** Y el segundo son los backtests, no
los informes. Eso dice algo bueno del diseño: la operación es barata y lo que cuesta
es la disciplina de medir.

---

## 2. Costo por informe

Del desglose de [04 §4](04-pipeline-de-agentes.md):

| Escenario | Costo | Cuándo |
|---|---|---|
| Corpus caliente (barrio ya cubierto) | **USD 0,044** | El caso normal, ~80% |
| Con ingesta on-demand | **USD 0,090** | Barrio nuevo o poco cubierto |
| Segundo informe del mismo barrio | **USD 0,030** | Features ya extraídas |
| Regeneración post-visita | **USD 0,025** | Reusa candidatos y features |

**Promedio ponderado: ~USD 0,044.** Objetivo de [00 §5](00-vision-y-alcance.md):
≤ 0,15. Hay 3× de margen.

**Contexto de negocio:** una tasación consume hoy 30-60 minutos de un agente. Aun
valuando esa hora conservadoramente, el costo actual por tasación es **tres órdenes de
magnitud** mayor que USD 0,044. El argumento de ROI no necesita exagerarse.

---

## 3. Reparto del costo de LLM por nodo

| Nodo | % del costo | Modelo | Optimizable |
|---|---|---|---|
| 10 crítico | 34% | caro, a propósito | No — es la garantía de calidad |
| 9 redactor | 23% | medio | Sí: prompt más corto, caché de secciones fijas |
| 8 contexto | 23% | medio | Sí: cachear el contexto de barrio por 7 días → **-90%** |
| 4 extracción | 9% | barato | Ya optimizado (batch de 10 + caché por hash) |
| 6 curaduría | 7% | barato | — |
| 5 dedup | 4% | barato | — |

**La optimización más obvia** es cachear el contexto de barrio: el análisis de
Belgrano no cambia entre el lunes y el miércoles. Con TTL de 7 días, el nodo 8 baja a
casi cero para el segundo informe del mismo barrio en adelante. Se implementa en la
Etapa 5, no antes: primero medir, después optimizar.

---

## 4. Sensibilidad

| Escenario | Impacto |
|---|---|
| **Volumen ×4** (200 informes/mes) | LLM USD 8,80 · total **~USD 23/mes**. El VPS aguanta con un segundo worker |
| **Bajar a CX22** (€3,79) | Ahorra USD 3,90/mes, pero sin margen de RAM. No vale la pena |
| **Zyte con render de browser** para todo | +USD 3-4/mes. Solo si el fetch directo y el simple fallan |
| **Backtest quincenal** en vez de semanal | Ahorra USD 1,75/mes. Ahorro chico, costo alto en visibilidad. **No recomendado** |
| **Cliente exige OpenAI GPT-4o** | El costo por informe sube a ~USD 0,50. Se factura al cliente. Un cambio en el YAML (ADR-003) |
| **Self-host de Langfuse** | +€6,80/mes de un segundo VPS. Solo si se supera el free tier |
| **Sumar Portal B** | +USD 2-5/mes de fetch. Se decide con el dato de cobertura, no antes |

---

## 5. Punto de equilibrio comercial

Si esto se vende a otras inmobiliarias:

| Escenario | Ingreso | Costo | Margen |
|---|---|---|---|
| Solo la inmobiliaria (gratis, es el caso de validación) | USD 0 | USD 16 | **−16** |
| 1 cliente pago a USD 40/mes | USD 40 | USD 18 | +22 |
| 5 clientes a USD 40/mes | USD 200 | USD 30 | +170 |
| 20 clientes a USD 40/mes | USD 800 | USD 75 (VPS más grande) | +725 |

**El costo marginal por cliente nuevo es casi cero**, porque el corpus se comparte
entre tenants ([03 §6](03-modelo-de-datos.md)): el segundo cliente en CABA usa avisos
que ya están descargados. Ese es el efecto de red del modelo de datos, y es
deliberado.

**Aclaración de expectativas:** el objetivo declarado del proyecto no es facturar. Es
resolver un problema real y construir experiencia demostrable. Que el modelo cierre
comercialmente es una consecuencia agradable, no la meta.

---

## 6. Presupuesto de la construcción

| Etapa | Semanas (part-time) | Gasto real |
|---|---|---|
| 0 — Infra | 1 | USD 8 (VPS del mes) |
| 1 — Datos y corpus | 1,5 | USD 10 |
| 2 — Pipeline | 2 | USD 25 (iteración de prompts) |
| 3 — UI y PDF | 1,5 | USD 10 |
| 4 — Backtest | 1,5 | USD 40 (varias corridas completas) |
| 5 — Hardening | 1 | USD 15 |
| **Total** | **~9 semanas** | **~USD 110** |

Menos de lo que sale una suscripción anual a cualquier herramienta de las que ya
usás.

---

## 7. Controles de costo implementados

No alcanza con estimar: hay que impedir la sorpresa.

| Control | Dónde |
|---|---|
| Cuota mensual de informes por tenant | `organizations.monthly_report_quota` → `402` al agotarse |
| Presupuesto de fetch por tenant | `organizations.fetch_budget_monthly` → degrada a caché |
| Presupuesto por API key en LiteLLM | Corta si se supera |
| Alerta si el costo diario > USD 2 | Healthchecks + email |
| Costo registrado por informe | `reports.cost_usd`, visible en `/informes` |
| Costo por nodo | `report_events.cost_usd`, visible en el detalle técnico |
| Costo de cada backtest | `eval.backtest_runs.cost_usd` |

Ninguna corrida puede gastar sin dejar rastro de cuánto gastó y en qué nodo.
