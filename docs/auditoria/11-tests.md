# S11 — Tests

**Alcance:** `tests/` (320 funciones `test_*`, 368 casos con parametrización) +
`web/e2e/` (74 casos).
**Método:** cobertura real, y buscar tests que pasen sin medir.

---

## 1. El número, y lo que esconde

```
$ uv run pytest -m "not live" --cov=tasador --cov-report=term-missing
366 passed, 2 skipped in 181.26s
TOTAL   6025 stmts   2671 miss   56%
```

**55,67%**, contra el `--cov-fail-under=60` del CI. Ver H-47 (S9).

Y sin `DATABASE_URL` —que es como corre `make test`— se saltean **58 de 368**:

```
$ uv run pytest -m "not live" --junitxml=…      # sin DATABASE_URL
tests=368  skipped=58  failures=0  errors=0
```

**Lo bueno primero:** revisé las 320 funciones buscando tests sin afirmación.
No hay ninguna. Las 6 que mi primer chequeo marcó (`test_el_nodo_del_precio_no_
puede_tener_modelo` y compañía) usan `with pytest.raises(...)`, que mi detector
de AST no reconocía; corregido el detector, la lista queda vacía. **Todo test de
este repo afirma algo.**

---

## 2. Dónde no hay ninguna prueba

Los módulos en 0%, ordenados por lo que duele:

| Módulo | Stmts | Cobertura | Qué es |
|---|---|---|---|
| `agents/runner.py` | 102 | **0%** | Carga el informe, corre el grafo y **persiste el resultado**. Es donde se escriben `value_mid`, el costo y los comparables |
| `eval/run.py` | 142 | **0%** | El **gate de calidad**. Ver H-44: sale verde con 0 casos y no hay un test que lo diga |
| `eval/backtest.py` | 201 | **0%** | Produce el MdAPE |
| `eval/componentes.py` | 71 | **0%** | La mediana de 5 y la amplitud |
| `sources/playwright_fetcher.py` | 232 | **0%** | Captura (apagada) |
| `corpus/neighborhoods.py` | 33 | **0%** | Carga de barrios |
| `worker.py` | 56 | 50% | El job y `_marcar_fallado` |
| `agents/nodes/normalize.py` | 84 | **14%** | Nodo 1: geocoding y barrio |
| `agents/nodes/market.py` | 157 | **17%** | Nodo 8 |
| `agents/nodes/curate.py` | 135 | **25%** | Nodo 6 |
| `agents/nodes/retrieve.py` | 87 | **29%** | Nodo 2: **la consulta que elige los comparables** |

Contra los que sí están bien:

```
db/models.py           386  100%
security.py             71   99%
valuation/models.py     96   99%
valuation/adjustments.py 73  95%
valuation/engine.py    145   94%
```

**El patrón es el de siempre y conviene decirlo con estos números al lado:** la
cobertura siguió a donde es fácil testear (funciones puras, sin base, sin red) y
no a donde el sistema se rompe. El motor de valuación está al 94% y **no tiene
un test que cubra el paso p5–p95** (H-01). `runner.py` está en 0% y es el que
escribe el número en la base.

---

### H-56 · Cuatro gates verifican algo distinto de lo que su nombre dice

**Sección:** S11 · **Severidad:** media

**Qué está mal:** no son tests flojos: son tests que **pasan en verde sobre una
pregunta más chica que la que enuncian**. Los cuatro están detallados en su
sección; acá van juntos porque el patrón es el mismo y la corrección también.

| Gate | Dice que verifica | Verifica de verdad | Dónde |
|---|---|---|---|
| `test_rango_ancho_minimo` | el mínimo de ±4% de doc 05 §6 | que el ancho sea ≥ 7,9%, sobre un ancho real de **40%** | H-05 |
| `test_los_shell_scripts_invocados_existen` | que `make backup` pueda correr | que `ops/backup.sh` exista **en el repo** — el contenedor que lo corre no lo tiene | H-49 |
| `test_ninguna_consulta_de_la_api_pierde_el_filtro_de_tenant` | que ninguna consulta pierda el `org_id` | solo `select(...)` literal; no ve `sa.select`, `session.get`, `update`, `delete` ni las tablas hijas | H-31 |
| `los campos del formulario tienen labels reales` (Playwright) | que los formularios tengan labels | solo `/informes/nuevo`; `/admin/usuarios` y `/admin/organizacion` no los tienen | H-39 |

**Cómo lo verifiqué:** cada uno en su sección, con el comando y la salida.

**Por qué importa:** es R3 en su forma más cara. Un gate ausente se nota; uno que
mide una versión reducida de la pregunta **ocupa el lugar del que hacía falta** y
además da confianza. Los cuatro pasan hoy, y los cuatro dejan pasar el bug que
existían para atajar.

**Qué hay que hacer:** cada uno tiene su arreglo en su sección. Lo transversal:
`test_aislamiento.py` ya trae el patrón correcto —**el test del test**
(`test_la_heuristica_detecta_una_consulta_sin_filtro`)— y ese patrón es el que
falta en los otros tres. Un gate que no se prueba a sí mismo con un caso que
DEBE fallar no es un gate.

**Cómo se verifica que quedó bien:** para cada uno, introducir el defecto a mano
y comprobar que se pone en rojo. Hoy ninguno de los cuatro lo hace.

**Riesgo de tocarlo:** ninguno. Son tests.

---

### H-57 · Los tests de Playwright se saltean solos según el estado de la base

**Sección:** S11 · **Severidad:** media

Detallado en **H-40** (S6). Va acá porque es el mayor hueco de la estrategia de
tests, no del frontend: siete tests deciden en tiempo de ejecución no verificar
nada si el dato que necesitan no está.

```
$ npx playwright test --reporter=line
  11 skipped
  63 passed (4.5m)
```

`tests/conftest.py` resolvió exactamente esta pregunta del lado de pytest, y su
docstring es la especificación de cómo debería resolverse acá: *«sin
DATABASE_URL → se saltea, y se dice por qué; con DATABASE_URL → corre de verdad,
o falla ruidosamente»*.

---

### H-58 · No hay ningún test de los módulos que persisten

**Sección:** S11 · **Severidad:** media

**Qué está mal:** `agents/runner.py` está al 0%. No hay un test que verifique que
el resultado del grafo se escribe bien: ni que `status` se derive correctamente,
ni que el costo se sume de la traza, ni que los comparables se persistan una sola
vez.

**Cómo lo verifiqué:** la tabla de cobertura, y la ausencia de un
`tests/test_runner.py`:

```
$ ls tests/
test_admin.py  test_agents_config.py  test_portal_a.py  test_auth.py
test_dedup.py  test_extract.py  test_geocoding.py  test_golden.py
test_graph.py  test_health.py  test_ingest_csv.py  test_inventory.py
test_panel_externo.py  test_llm.py  test_render.py  test_reports_api.py
test_resolve.py  test_retrieve_clusters.py  test_valuation.py  test_portal_b.py
architecture/  golden/
```

Y tres hallazgos de esta auditoría viven justamente ahí:

- **H-14** (el costo de un informe que no termina queda en 0) — `_persist_result`.
- **H-15** (`degraded_nodes` no se persiste) — `_persist_result`.
- **H-28** (`market_context` se calcula y se tira) — `_persist_result`.

Los tres son la misma función, la misma línea del código, y ninguno tiene un test
que los hubiera visto.

**Por qué importa:** `runner._persist_result` es la frontera entre "el grafo
calculó algo" y "el cliente lo ve". Todo lo que se pierda ahí es invisible desde
los tests del grafo (que miran el estado) y desde los de la API (que miran filas
que alguien tuvo que escribir).

**Qué hay que hacer:** `tests/test_runner.py` con la fixture `db`, que arme un
`ReportState` final a mano y llame a `_persist_result`, afirmando:
1. `status` derivado en los tres casos (con valor, sin valor, con `error_code`).
2. `cost_usd` == suma de `report_events`, incluyendo el caso de dos corridas.
3. `methodology` con `degraded_nodes` y `market_context` (después de H-15/H-28).
4. `report_comparables` sin duplicar al re-persistir el mismo informe.

**Cómo se verifica que quedó bien:** la cobertura de `agents/runner.py` pasa de
0% a >70%, y los cuatro puntos de arriba tienen un test que falla si se rompen.

**Riesgo de tocarlo:** ninguno; son tests nuevos con base descartable.

---

## 3. Lo que la suite sí cubre bien, y hay que preservarlo

- **`tests/architecture/`** es el mejor material del repo. Tres archivos que
  verifican invariantes en vez de comportamiento: aislamiento multi-tenant (dos
  capas), invariantes de las imágenes (el `\n` literal, el `USER`, los `COPY`), y
  que todo comando documentado exista. Sus huecos (H-31, H-49) son de alcance,
  no de concepto.
- **`tests/conftest.py`** con la política de skip explícita y argumentada.
- **`test_agents_config.py`** cierra ADR-002 por los dos lados (YAML y variable
  de entorno) y cruza las dos capas de configuración.
- **`test_valuation.py`** implementa 8 de los 9 tests obligatorios de doc 05 §10
  (falta `test_caso_real_conocido`) y fija los invariantes contra-intuitivos con
  el motivo escrito, para que nadie los "arregle".
- **`db/models.py` al 100%**, que con 20 tablas y sus CHECK no es trivial.

## 4. Qué NO pude verificar

- **`pytest --cov` por módulo con y sin base**: corrí las dos, pero no crucé qué
  líneas específicas cubren los 58 tests que dependen de `DATABASE_URL`.
- **Los tests marcados `live`**: gastan. No los corrí ni los conté.
- **Si algún test prueba una implementación que no corre (R2).** Revisé los
  casos que la doc ya señalaba (el `_raw`/`_content_hash` consolidado, el eval
  compartiendo `extraer_lotes`) y están bien. No hice el barrido completo: para
  hacerlo bien haría falta cobertura por test, no agregada.
