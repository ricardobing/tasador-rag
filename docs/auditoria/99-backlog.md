# Backlog — el plan de la sesión 2

> ## Estado al 15/08: **35 de 58 cerrados**
>
> Los bloques **A**, **B**, **C**, **G** y ahora **B4** y **B6** están hechos y
> verificados. El detalle de qué se midió antes y después está en
> [el cierre de la auditoría](../informes/2026-08-15-cierre-auditoria.md) y en
> [el informe de implementación](../informes/2026-08-15-implementacion.md).
>
> | Bloque | Estado | Hallazgos |
> |---|---|---|
> | B0 · Control de versiones | ✅ | H-46a — 14 commits, sin remoto todavía |
> | B1 · Desplegable | ✅ | H-50, H-49, H-10, H-32, H-47, H-51 · falta el remoto y el primer run del CI |
> | B2 · El número | ✅ | H-12, H-17, H-11, H-01, H-02, H-03, H-08, H-54 |
> | B3 · Seguridad | ⬜ | **postergado por decisión**: todo corre local hoy |
> | B4 · Datos | ✅ | H-21, H-23, H-22, H-16 · H-25 tiene el script y espera autorización · H-19 pendiente |
> | B5 · Traza y operación | ✅ | H-14, H-15, H-28, H-13 · H-20 pendiente |
> | B6 · API | ✅ | H-26 (RFC 7807), H-27 (la respuesta completa) · H-30 pendiente |
> | B7 · Frontend | ⚠️ | H-37, H-38, H-39 hechos · faltan H-36 (ESLint) y H-40 (los e2e no corren en el CI) |
> | B8 · Gates | ✅ | H-58, H-44, H-31, H-05, H-09 |
> | B9 · Rendimiento y deuda | ⚠️ | H-33 medido y **no confirmado**: ver abajo |
> | B10 · Documentación | ⚠️ | ESTADO.md actualizado; las 26 divergencias de S12 siguen |
> | G · Que conecte | ✅ | H-48 · imagen = repo, informe real de punta a punta, 19/19 |
>
> ### Lo que hay que saber antes de seguir
>
> **H-33 no está cerrado, y el hallazgo se equivocaba.** La columna
> `listings.surface_weighted` se agregó y devuelve el mismo conjunto de
> candidatos que el cast de JSONB, pero el índice propuesto **no cambia un solo
> buffer** y el pre-filtro propuesto **perdía 5 candidatos de 1.637**. Lo
> dominante es el bitmap heap scan trayendo filas anchas de `listings` —con el
> JSONB `raw` adentro— para devolver 60. Medición en
> `docs/auditoria/sondas/h33_superficie.py`.
>
> **H-25 espera una decisión.** El script existe, corrió en seco y da 11 filas
> migrables con 0 conflictos. Aplicarlo es un UPDATE sobre el corpus:
> `uv run python scripts/reparar_claves_del_raw.py --aplicar`.
>
> **Lo primero que haría la próxima sesión**, en este orden:
> 1. Crear el remoto y hacer que el CI corra una vez — sigue sin correr nunca.
> 2. H-59: las 26 divergencias de documentación, que ya crecieron con esta sesión.
> 3. H-19 (una re-extracción pisa con `None` un campo que ya tenía valor) y
>    H-20, los dos que quedan de B4 y B5.
> 4. H-30 (`/v1/usage`) y H-36 (ESLint), que son los últimos chicos.

Los **58 hallazgos** en orden de ejecución, no de severidad. Las dependencias
mandan. Cada ítem remite a su sección, donde están el comando que lo verificó,
el arreglo propuesto y cómo comprobar que quedó bien.

**Esfuerzo:** XS < 30 min · S 1-2 h · M medio día · L 1-2 días.
**Rompe:** qué puede romperse y qué test lo protege.

> **Las dos reglas de orden que no se pueden invertir:**
> 1. **B0 va primero.** 58 arreglos sobre un árbol sin control de versiones es la
>    peor forma posible de hacer este trabajo.
> 2. **H-12 va ANTES del redeploy (B1.7).** Reconstruir la imagen enciende en
>    producción el filtro de cluster del nodo 2, que hoy opera sobre clusters
>    encadenados. Desplegar primero es empeorar el número.

---

## B0 — Control de versiones · antes de tocar una línea

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 0.1 | **H-46a** · `git add -A && git commit`. Revisar antes `git status --ignored`: `.env` está bien ignorado, **`test-results/` de la raíz NO** (solo `web/test-results/`), y `data/raw/cdp-profile/` es un perfil de Chrome completo | S9 | alta | XS | Nada. Es el seguro de todo lo demás |

---

## B1 — Que el sistema se pueda desplegar

Nada de esto toca lógica. Es hacer que exista un "producción" del que hablar.

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 1.1 | **H-50** · Mover el comentario de `ENV=` a su propia línea en `.env.example` y `.env` + test que falle ante `^[A-Z_]+=.*\S+\s+#` | S9 | media | XS | Nada |
| 1.2 | **H-49** · `api.Dockerfile`: agregar `COPY scripts/` y `COPY ops/`. Mover `make backup` al contenedor `postgres` (tiene `pg_dump`) con `./ops:/ops:ro` | S9 | alta | S | `make backup` cambia de servicio; hay que probarlo |
| 1.3 | **H-10** · `settings.artifacts_dir = "/data/artifacts"` explícito; usarlo en `render.py:179`. Agregar `/data/raw` al `mkdir` de `api.Dockerfile` para que los dos servicios resuelvan igual | S2 | **alta** | S | Los 15 `report_artifacts` existentes apuntan a rutas viejas: decidir si se migran |
| 1.4 | **H-32** · Declarar `idx_listing_embeddings_hnsw` en `ListingEmbedding`, copiando los parámetros de la migración | S5 | media | XS | Ninguno sobre datos. Si los parámetros no coinciden, `alembic check` sigue en rojo |
| 1.5 | **H-47** · Cobertura: bajar el umbral a 55 **con fecha de vencimiento escrita en el YAML**, o subir `runner.py` y `eval/run.py` (que es B8.3). Recomiendo bajar ahora y subir en B8 | S9 | media | XS | Nada |
| 1.6 | **H-46b** · Crear el remoto, renombrar `master` → `main`, pushear. **Correr el CI y arreglar lo que salga**, no desactivarlo | S9 | alta | S | Trivy y gitleaks corren por primera vez: pueden encontrar algo |
| 1.7 | **H-48** · `ops/verificar_imagen.sh` (hash de cada `.py` de la imagen vs el repo) como paso del CI + `ARG GIT_SHA` → `ENV` → `GET /v1/health`. **Reconstruir e implantar recién después de B2.1** | S9 | **alta** | M | El redeploy enciende todo lo del 14/08 de golpe. **Después de H-12** |

**Verificación del bloque:** los cinco jobs del CI en verde en un run real,
`bash ops/backup.sh --verify` con los conteos coincidiendo, y
`curl localhost:8000/v1/health | jq .git_sha` devolviendo el commit.

---

## B2 — El número que recibe el cliente

Lo que produce o permite un valor equivocado en un informe entregado.

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 2.1 | **H-12** · Cortar el encadenamiento del dedup: validar cada componente (≥80% de pares que cumplen) o pasar a enlace completo, + tope duro de tamaño de cluster. Volver a correr `dedup_corpus.py --aplicar` limpiando lo anterior | S2 | **alta** | L | Cambia el universo de comparables de todo informe. `test_dedup.py` protege los casos borde; hace falta un test de cadena A-B-C-D |
| 2.2 | **H-17** · En `_aplicar`: limpiar clusters previos y marcar `match_method="LLM_JUDGE"` los que decidió el juez. Va junto con 2.1 | S2 | baja | S | Destructivo sobre el corpus: requiere autorización |
| 2.3 | **H-11** · Sacar la regla `v*100`, bajar `numeric_tolerance_pct` a 0,1, y verificación **posicional** de las cifras clave. **Y el test que mide el gate**, no que lo ejercita | S2 | **alta** | M | Sube la tasa de rechazo del crítico → más informes sin narrativa (hoy 11%). Medir sobre ≥5 informes antes de dar por bueno |
| 2.4 | **H-01** · Decidir: sacar el recorte p5–p95, o documentarlo en doc 05 §5 y renombrar el motivo a `recorte_p5_p95`. Test con los conteos por n | S1 | media | S | Sacarlo cambia el número de todos los informes y rompe la comparabilidad de `eval.backtest_runs`: subir `method_version` a mano |
| 2.5 | **H-02** · Normalizar la cochera del comparable antes del USD/m². Impacto medido hoy: **0,00%** (1,1% del corpus la declara) | S1 | media | S | `test_cochera_suma_valor_absoluto` protege el lado del sujeto |
| 2.6 | **H-03** · Mover `listing_age_coef` adentro de `compute_adjustments` para que entre al producto, al tope y al detalle | S1 | baja | S | Hoy no cambia nada (el coeficiente es 1,00 en el 100%). Después de B4.1 sí |
| 2.7 | **H-08** · Calcular `value_mid` desde los valores ya redondeados, para que el informe cierre | S1 | baja | XS | Mueve el valor ±30 USD |
| 2.8 | **H-54** · Correr el par `VIGENTES` / `VIGENTES_SIN_FEATURES` como **línea de base antes** de 2.4 y 2.5, y de nuevo después. Es lo que ESTADO §5.1 #5 da por pendiente de diseño y ya está implementado | S10 | media | S | Escribe una fila en `eval.backtest_runs`. No gasta LLM |

**Verificación del bloque:** `docs/auditoria/sondas/s2_critico.py` con la cobertura de
precios inventados < 10%; `docs/auditoria/sondas/s2_cluster.py` sin clusters por debajo del
80%; los dos backtests de 2.8 con su delta; y `docs/auditoria/sondas/s1_reproduce.py` en OK
sobre los informes nuevos.

---

## B3 — Seguridad

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 3.1 | **H-42** · Usar `X-Real-IP` (Caddy lo sobrescribe), acotar `--forwarded-allow-ips` a la red interna, y poner tope a `_intentos`. Test que rote la cabecera y exija el 429 | S7 | **alta** | S | Sin Caddy delante, `X-Real-IP` no existe: el fallback al peer real es obligatorio |
| 3.2 | **H-41** · `wrap_external` en las cuatro piezas de `dedup._payload_par` + **test que recorra todos los nodos** y verifique que todo campo derivado de `description`/`address` sale envuelto | S7 | **alta** | S | Cambia el prompt → se mueve `prompt_bundle_version`. Correr `dedup_corpus.py` sin `--aplicar` antes y después y comparar grupos |
| 3.3 | **H-29** · Chequeo de cuota mensual en `POST /v1/reports` con el `402` que doc 06 §2 ya documenta. Arrancar en modo aviso | S4 | media | M | Una cuota mal calculada bloquea a un cliente real |
| 3.4 | **H-43** · CSP en el Caddyfile, probada recorriendo las 10 rutas con la consola abierta | S7 | baja | S | Una CSP mal armada rompe la página **en silencio** |

---

## B4 — Los datos que no llegan al motor

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 4.1 | **H-21** · `publication_date` → `listings.published_at`, y agregarlo a `CAMPOS_DEL_HASH`. **Enciende de golpe H-03, H-06 y H-18** | S3 | media | S | Cambia el número (coeficiente + confianza) y provoca una reescritura masiva en la próxima ingesta. Backtest antes y después, y revisar la calibración de la confianza |
| 4.2 | **H-23** · Test que cruce `CAMPOS_DEL_RAW`/`CAMPOS_DEL_HASH` contra **todas** las clases `PortalCard`; completar `Card` (`age_years` y `orientation` faltan **del hash**) | S3 | media | S | Cambia el `content_hash` de los 298 avisos de Portal B |
| 4.3 | **H-22** · Borrar `upsert_card` y que `ingest_barrios` use `capture.ingest._upsert` (el otro **no escribe `neighborhood_id`**) | S3 | media | S | `test_portal_a.py` ejercita `upsert_card`: hay que reescribir esos tests |
| 4.4 | **H-16** · Una sola fuente para los umbrales de descarte (hoy en 3 lugares) + test que falle ante literales sueltos | S2 | media | S | Bajo: los valores no cambian |
| 4.5 | **H-25** · Los 11 avisos con `raw.parking` (clave vieja) | S3 | baja | XS | UPDATE sobre el corpus: coordinar con el humano |
| 4.6 | **H-19** · Que una re-extracción no pise con `None` un campo que ya tenía valor, salvo `--forzar` | S2 | media | S | Reprocesar para *corregir* un dato malo va a necesitar `--forzar`: documentarlo |

---

## B5 — Traza, costo y operación

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 5.1 | **H-14** · Que el consumo del tenant salga de `report_events` y no de `reports.cost_usd` (hoy subestima 7,5%) | S2 | media | S | Si se hace incremental, cuidar el reintento del mismo `report_id` |
| 5.2 | **H-15** + **H-28** · Persistir `degraded_nodes` **y** `market_context` en `methodology`. Es la misma línea de `_persist_result` | S2/S4 | media | XS | Ninguno: JSONB aditivo |
| 5.3 | **H-13** · Cosechador de informes colgados en `ops/crontab` (hoy hay 4 desde hace 8-13 h) + tiempo transcurrido en `GET /v1/reports/{id}` | S2 | media | S | Un umbral menor que `job_timeout` (1800 s) mata informes vivos |
| 5.4 | **H-20** · Serie "informes sin narrativa / total" en `/v1/calidad` | S2 | baja | XS | Ninguno |

---

## B6 — Contrato de la API

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 6.1 | **H-26** · Handlers de `HTTPException` y `RequestValidationError` que devuelvan `application/problem+json`, **sin** el `input` ni el `ctx` de pydantic | S4 | media | S | El front y los tests leen `detail`, que RFC 7807 conserva. Revisar `web/src/lib/api.ts` |
| 6.2 | **H-27** · Completar la respuesta: `comparables.items[]`, `pdf_url`, `confidence.notes`, `external_ref`, `market_context` (depende de 5.2) | S4 | media | M | Aditivo. Acotar `items` a los incluidos: con 60 comparables son ~40 KB |
| 6.3 | **H-30** · `/v1/usage`: implementarlo o marcarlo ⬜ en doc 06 §2 (recomiendo lo segundo) + test que cruce las rutas de doc 06 contra el `openapi.json` | S4 | baja | XS | Ninguno |
| 6.4 | **H-31** · Cerrar los huecos del gate estático: `sa.select`, `update`, `delete`, `session.get`, y las tablas hijas. Agregar el `join(Report)` con `org_id` a `reports.py:467` | S4 | baja | S | El punto de las tablas hijas da falso positivo sobre `reports.py:467` hasta que se le agregue el join |

---

## B7 — Frontend

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 7.1 | **H-37** · `error.tsx`, `not-found.tsx`, `loading.tsx`, y tratar el 422 como `notFound()` en `informes/[id]` (hoy da **500**) | S6 | media | S | Ninguno: archivos nuevos |
| 7.2 | **H-38** · Contraste del botón principal: 2,51:1 → ≥4,5:1 | S6 | baja | XS | Ninguno |
| 7.3 | **H-39** · `<label htmlFor>` en los 4 campos de admin y `scope="col"` en los 51 `<th>`; el test de labels tiene que recorrer **todas** las rutas | S6 | baja | S | Ninguno |
| 7.4 | **H-36** · Instalar y configurar ESLint (hoy `npm run lint` abre un wizard interactivo) + `web/package.json` a las fuentes de `test_entrypoints.py` | S6 | baja | S | ESLint nuevo encuentra cosas: arrancar con `--max-warnings` = lo que haya |
| 7.5 | **H-40/H-57** · Dar vuelta los skips de Playwright: sembrar el dato o **fallar**, nunca saltearse. Paso de CI que exija `E2E_CON_AUTH` | S6/S11 | media | M | Los tests pasan a depender de un sembrado que hay que mantener |

---

## B8 — Gates y evaluación

Después de B1-B7, porque varios de estos gates prueban los arreglos anteriores.

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 8.1 | **H-44** · `MINIMO_DE_CASOS` en el gate del backtest: hoy sale **verde con 0 casos** con los flags que usa el CI | S8 | media | XS | Un mínimo muy alto pone el gate en rojo permanente |
| 8.2 | **H-56** · Los cuatro gates que miden menos de lo que dicen (H-05, H-49, H-31, labels). Para cada uno: **introducir el defecto y comprobar que se pone en rojo** | S11 | media | M | Ninguno |
| 8.3 | **H-58** · `tests/test_runner.py`: `_persist_result` está al 0% y es donde viven H-14, H-15 y H-28 | S11 | media | M | Ninguno |
| 8.4 | **H-05** · El test del ancho de rango contra `uncertainty_half_width_pct` leído del YAML | S1 | baja | XS | Ninguno |
| 8.5 | **H-09** · Test que fije `min_comparables >= 5` (hoy `MIN_COMPARABLES=3` lo ablanda) | S1 | baja | XS | Ninguno |
| 8.6 | **H-45** · Que el eval de curaduría llame a `curate()` o que la métrica se llame `curator_judge` | S8 | baja | S | Cambia el número que se reporta como recall: correr 3 veces y comparar medianas |

---

## B9 — Rendimiento y deuda

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 9.1 | **H-33** · `listings.surface_weighted` como columna + índice `(neighborhood_id, active, surface_weighted)`. Hoy: 258 ms y 39.195 buffers por consulta, ×5 escalones | S5 | media | M | Verificar que sobre un informe conocido devuelva **el mismo conjunto** de `listing_id` |
| 9.2 | **H-04** · Implementar `expenses` (el dato está en el 43%) y sacar `ground_with_patio` (no hay dato de entrada) + test que cruce las claves del YAML contra el código | S1 | baja | M | Implementar `expenses` cambia el número y BA Data no lo trae: no se puede validar con el backtest |
| 9.3 | **H-06** + **H-07** · Exponer los cinco factores de la confianza en el detalle del nodo 7; avisar en `notes` cuando el sujeto no declara un atributo que los comparables sí | S1 | media | S | Cambiar `f_freshness` mueve la confianza de todos los informes: rehacer la calibración |
| 9.4 | **H-18** · `reglas_inaplicables` en el detalle del nodo 6 | S2 | baja | XS | Ninguno |
| 9.5 | **H-24** · Que `--dry-run` consulte `_ya_procesado` y diga cuántos archivos son NUEVOS; podar el recorrido (1.779 archivos para 8 que importan) | S3 | media | S | Ninguno: la sesión del dry-run tiene que ser de lectura |
| 9.6 | **H-34** + **H-35** · Sacar `api_key_hash_unique` (un unique sobre un hash salado no garantiza nada) y `idx_neighborhoods_aliases`; columna `source_sha256` en `ingest_runs` | S5 | baja | S | Migración con backfill |
| 9.7 | **H-52** + **H-53** + **H-55** · `argparse` en `bundle_lock.py` (hoy `--help` **ejecuta y escribe**); borrar `run_backtest.py` y `seed_org.py` | S10 | baja | S | Hay que actualizar ESTADO §4.4 y el informe de la Etapa 3 |
| 9.8 | **H-51** · Que `make lint/test/typecheck` corran lo mismo que el CI. Hoy `make test` saltea **58 de 368** en silencio | S9 | baja | S | Obliga a tener Postgres arriba para correr los tests. Es el costo correcto |

---

## B10 — Documentación

Al final a propósito: recién ahora se sabe qué va a decir.

| # | Ítem | Sec | Sev | Esf | Rompe |
|---|---|---|---|---|---|
| 10.1 | **H-59a** · Corregir las **26 divergencias** listadas en S12. Prioridad: doc 05 (es el que sale del equipo), doc 17 §3/§4/§6 (quedó en la Etapa 3), doc 06 §1/§2/§3, ESTADO §2/§5.2 | S12 | media | M | Ninguno |
| 10.2 | **H-59b** · Los tres chequeos automáticos: rutas de doc 06 vs `openapi.json`, claves de `adjustments.yaml` vs el código, tabla de coeficientes de doc 05 §4.1 vs el YAML | S12 | media | S | Acotarlos a rutas, claves y números: no intentar parsear prosa |
| 10.3 | Actualizar ESTADO.md con lo que quede después de todo esto, y escribir `docs/informes/<fecha>-cierre-auditoria.md` | — | — | S | — |

---

## Resumen de esfuerzo

| Bloque | Ítems | Esfuerzo |
|---|---|---|
| B0 · Control de versiones | 1 | XS |
| B1 · Desplegable | 7 | ~1 día |
| B2 · El número | 8 | ~2,5 días |
| B3 · Seguridad | 4 | ~1 día |
| B4 · Datos | 6 | ~1 día |
| B5 · Traza y operación | 4 | ~0,5 día |
| B6 · API | 4 | ~1 día |
| B7 · Frontend | 5 | ~1 día |
| B8 · Gates | 6 | ~1,5 días |
| B9 · Rendimiento y deuda | 8 | ~1,5 días |
| B10 · Documentación | 3 | ~0,5 día |
| | **56** | **~11 días** |

(H-46 y H-59 aparecen partidos en dos ítems cada uno: 58 hallazgos, 56 filas.)

**Si hay que cortar:** B0, B1 y B2 son irrenunciables — sin B1 no hay producción
y sin B2 el número que se entrega no está bien justificado. B3 va tercero porque
H-42 es explotable desde afuera. B9 y B10 pueden esperar; B8 no debería, porque
es lo que evita que todo esto vuelva.

---

## Lo que la sesión 2 tiene que medir antes de empezar

Tres números que hoy no existen y que son la línea de base contra la que se van
a comparar los arreglos:

```bash
# 1. El par que aísla el aporte del nodo 4 (H-54). No gasta en LLM.
docker compose run --rm worker python -m tasador.eval.run --dataset VIGENTES --sample 300
docker compose run --rm worker python -m tasador.eval.run --dataset VIGENTES_SIN_FEATURES --sample 300

# 2. La permeabilidad actual del crítico (H-11).
uv run python docs/auditoria/sondas/s2_critico.py     # hoy: 50,5% / 27,2% / 27,1%

# 3. La calidad actual de los clusters (H-12).
uv run python docs/auditoria/sondas/s2_cluster.py     # hoy: 7,59% / 12,15% / 47,83% / 20,33%
```

Las tres sondas están en el scratchpad de esta sesión; conviene moverlas a
`ops/` o a `tests/` antes de que se pierdan.

