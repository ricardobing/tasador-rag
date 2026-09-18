# Informe — cierre de la auditoría (sesión de implementación)

**15/08/2026** · Ejecución del backlog de `docs/auditoria/99-backlog.md`.
**22 de 58 hallazgos cerrados**, todos con verificación ejecutada.

> Los resultados se publican como salen. Dos de los números medidos acá
> contradicen lo que este proyecto venía dando por bueno, y están abajo.

---

## 1. Lo que quedó cerrado

| Bloque | Hallazgos | Verificado con |
|---|---|---|
| **A · Desplegable** | H-32, H-50, H-49, H-10, H-51, H-46a | La imagen de producción reconstruida |
| **B · El número** | H-12, H-17, H-11, H-01, H-02, H-03, H-08, H-54 | Sondas sobre informes reales + backtest |
| **C · Traza** | H-14, H-15, H-28, H-13, H-58 | `tests/test_runner.py` (0% → 64%) |
| **G · Que conecte** | H-48 | Un informe completo de punta a punta |

```
ruff check src tests scripts migrations   ✅ All checks passed
ruff format --check                       ✅ 119 files
mypy --strict                             ✅ 66 archivos
alembic check                             ✅ No new upgrade operations detected
pytest -m "not live"                      ✅ 407 tests, 3 skipped, 0 fallas
cobertura                                    57%  (era 55,67%)
```

Suite: **368 → 407 tests**. `tests/architecture`: **20 → 47**.

---

## 2. Los dos números que hay que decir

### 2.1 El nodo 4 no mejora el MdAPE de forma medible

Es el **bloqueante #5 de ESTADO §5.1** desde el principio: *"sin esto, «el nodo
4 mejora el MdAPE» es una hipótesis"*. El dataset ya estaba implementado
(`VIGENTES` / `VIGENTES_SIN_FEATURES`); lo que faltaba era correrlo.

Tres semillas cada uno, 300 casos, mismo baseline:

```
$ uv run python docs/auditoria/sondas/aporte_nodo4.py

  dataset                  semillas        MdAPE mediana   rango        PPE20
  VIGENTES                 [7, 13, 42]       22.5%      21.5-23.3    44.6%
  VIGENTES_SIN_FEATURES    [7, 13, 42]       22.2%      22.1-22.7    44.7%

  aporte del nodo 4 (mediana sin - mediana con) : -0.33 pp
  amplitud entre semillas del mismo dataset     :  1.83 pp
  -> NO SE DISTINGUE DEL RUIDO
```

**Con una sola semilla (42) el nodo 4 daba +0,6 pp a favor. Con tres, la mediana
va para el otro lado.** Es exactamente la lección que este proyecto tiene escrita
sobre las corridas únicas, aplicada a su propia hipótesis central.

Dos cosas más que salen de ahí y no estaban:

- **La amplitud entre semillas de `VIGENTES` es 1,83 pp**, tres veces la de
  BADATA (0,6 pp). La tolerancia de 2 pp de doc 09 §5 está *apenas* por encima
  del ruido para este dataset: un gate calibrado contra BADATA no sirve acá.
- Esto **no dice que el nodo 4 no sirva**. Dice que con 300 casos y este corpus
  no se puede medir. El nodo 4 sigue teniendo su justificación por otro lado —el
  efecto sobre el valor medio (−17%) que la Etapa 3 midió— pero eso es "los
  coeficientes operan", no "el resultado es más exacto".

### 2.2 El corpus tenía 6.204 propiedades únicas, no 5.027

El dedup agrupaba por transitividad sobre una relación con tolerancia. Medido
sobre los clusters que había escrito:

```
  n=198  pares posibles 19.503  pares que MATCHEAN 1.480 →  7,6%
```

Un cluster de 198 avisos de 23 a 73 m² y de USD 111.000 a 436.900, del que el
nodo 2 tomaba **un** candidato.

| | Antes | Después |
|---|---|---|
| cluster más grande | 198 | **8** |
| % de pares que matchean en el mayor | 7,6% | **100%** |
| clusters con dispersión de precio >10% | 202 | **0** |
| clusters con el precio al doble | 47 | **0** |
| clusters huérfanos | 488 | **0** |
| propiedades únicas | 5.027 | **6.204** |
| comparables visibles en Palermo 60-100 m² | 1.225 / 1.890 | **1.407 / 1.890** |

---

## 3. El crítico: de 50,5% a 4,3%

La fase A —la verificación sin LLM, la que sostiene el argumento del producto—
dejaba pasar **la mitad** de los precios inventados. Medido sobre informes
reales, precios al azar entre USD 100.000 y 400.000 que el control acepta:

```
  informe 4f3a88f7 (23 comparables)   50,5%  ->  4,3%
  informe e29fca61 ( 7 comparables)   27,2%  ->  1,4%
  informe 99d5f005 ( 7 comparables)   27,1%  ->  1,4%
```

Y el `USD 312.000` que el propio docstring de `critic.py` pone como ejemplo de
lo que no puede pasar: **PASA → RECHAZA** en los tres.

Tres cambios: se saca la regla `v*100` (convertía cada USD/m² del set en un
precio permitido), la tolerancia baja de 1,0% a 0,1% con la tabla de
sensibilidad escrita en el YAML, y se agrega una verificación **posicional** —
cuando el texto afirma un valor, tiene que ser EL valor, no cualquier número de
la tabla.

### 3.1 Y el primer informe con eso puesto salió sin narrativa

Vale más que el arreglo. El control nuevo rechazó tres veces por la cifra
`3.839`, que es el **USD/m² mediano del barrio** que el nodo 8 calcula y el
redactor cita legítimamente. El regex `precio … USD <n>` matcheaba la frase
sobre los comparables.

Es el costo asimétrico de la Etapa 3 §10.3 —**rechazar de más cuesta el
producto**— con este control como causa. Se acotó: el patrón solo matchea frases
que afirman el valor del sujeto, exige una cifra del orden de un inmueble
(≥ USD 50.000) y descarta la oración si habla de comparables o de m². Quedaron
dos tests, uno por cada lado del filo.

**Y solo apareció corriendo un informe de verdad.** Los 12 tests del archivo
pasaban.

---

## 4. Lo que corre en producción es lo que está escrito

Era el hallazgo más grande de la auditoría: la imagen desplegable estaba 12
archivos atrás y le faltaban cinco endpoints. Reconstruida y verificada:

```
$ docker run --rm --entrypoint python …tasador-api:dev -c "<hash de cada .py>"
  repo 66 .py  ·  imagen 66 .py
  AUSENTES : 0   DISTINTOS: 0
  -> LA IMAGEN ES EL REPO
```

Y en la imagen de producción, con `read_only: true`:

```
  data_path      = /data/raw          existe= True
  artifacts_path = /data/artifacts    existe= True
  scripts/ = True   ops/ = True
  --env-file .env.example -> env='development'   (antes: ValidationError)
```

---

## 5. Un informe completo, después de todo

`Gorriti 5000, Palermo`, 3 amb, 60 m², en el contenedor:

```
seq  NODO                 ESTADO       ms        USD
  1  normalize_subject    OK           56          —
  2  retrieve_candidates  OK          293          —
  4  extract_features     OK         1675   0.000000
  5  dedup_cluster        OK          320   0.001395
  6  curate               OK          199   0.007982
  7  adjust_and_value     OK           34          —   ALTA, 50/60 comparables
  8  market_context       OK         5892   0.001016
  9  write_report         OK          136   0.009744
 10  critic               OK        18043   0.060282   aprobado al PRIMER intento
 11  render_pdf           OK         3472          —

USD 137.472 — 171.840 — 206.208   ·   30,7 s   ·   USD 0,080
```

Verificado contra la base y contra la API:

| Qué | Antes | Ahora |
|---|---|---|
| narrativa | (3 rechazos) | **3.646 caracteres, 0 rechazos** |
| ruta del PDF | `/data/raw/artifacts/…` | **`/data/artifacts/…`** (el volumen compartido) |
| `GET /v1/reports/{id}/pdf` | 404 en producción | **200 · application/pdf · 30.101 bytes** |
| `market_context` | se calculaba y se tiraba | **Palermo, stock 8.383, USD/m² 3.839, desvío GCBA +12,1%** |
| `degraded_nodes` | no se guardaba | presente |
| `reports.cost_usd` vs la traza | 7,5% menos | **idénticos (0,080419)** |
| `value_mid` vs `price_per_m2 × superficie` | −30 USD | **0,00** |
| motivo de exclusión | `outlier_estadistico` | **`recorte_p5_p95`** |

El crítico costó USD 0,060 de los USD 0,080 — el 75%, y a propósito.

---

## 6. Lo que NO se hizo, y por qué

Quedan **36 de 58 hallazgos**. El backlog sigue siendo la referencia; lo que
cambia es que los bloques A, B, C y G están tachados.

**No se tocó por decisión del humano** (todo corre local hoy):
H-42 (el rate limit del login es evadible con `X-Forwarded-For`), H-43 (sin CSP),
H-29 (sin cuota por tenant).

**No se llegó**, en orden de lo que yo haría primero:

| Bloque | Qué queda |
|---|---|
| **D · Datos** | H-21 (`publication_date` no llega a la columna y apaga tres mecanismos), H-23 (el contrato tarjeta→`raw` por `getattr`, con `Card` atrás), H-22 (los dos `upsert`), H-16 (umbrales en tres lugares), H-25, H-19 |
| **E · Gates** | H-44 (el gate del backtest sale verde con 0 casos), H-05, H-09, H-31, H-56 parcial |
| **F · API y front** | H-26 (RFC 7807), H-27 (la respuesta no trae `comparables.items` ni `pdf_url`), H-37 (sin `error.tsx`; un id malformado da 500), H-38, H-39, H-36, H-40 |
| **B9 · Deuda** | H-33 (el filtro de superficie sin índice: 258 ms y 39.195 buffers), H-04, H-06, H-07, H-24, H-34, H-35, H-52, H-53, H-55 |
| **B10 · Docs** | H-59: las 26 divergencias siguen ahí. Doc 05 §5 ahora describe mal el recorte por otro motivo (el parámetro se movió al YAML) |

**Lo que no se pudo verificar y sigue pendiente:**

- **La restauración de un backup.** `make backup` ahora puede correr —el
  contenedor tiene los archivos y `pg_dump`— pero **no lo ejecuté**: `--verify`
  crea y borra una base en el Postgres real. Es el primer ítem que yo correría,
  a mano y mirando.
- **El CI.** El repo ya tiene commits y el disparador incluye `master`, pero no
  hay remoto: sigue sin correr una sola vez. Los cinco jobs son, todavía, un
  archivo YAML — con dos pasos menos rotos que ayer.
- **`ops/verificar_imagen.sh`**, el gate que compara la imagen con el fuente. La
  sonda existe (`docs/auditoria/sondas/s9_imagen.py`) y hoy da limpio; falta
  convertirla en un paso del CI. Sin eso, nada impide que la imagen vuelva a
  quedarse atrás.

---

## 7. Lo que aprendimos, esta vez

1. **Un test puede afirmar el bug.** `test_si_a_es_b_y_b_es_c_los_tres_son_el_mismo`
   fijaba la transitividad como si fuera una propiedad de diseño. Este proyecto
   tiene tres antecedentes de "el test tenía razón y el código estaba bien"; este
   es el inverso, y la única forma de distinguirlos fue medir sobre el corpus.

2. **Un gate nuevo puede romper el producto el primer día.** El control
   posicional del crítico rechazó tres veces un informe correcto y lo dejó sin
   narrativa. Los 12 tests del archivo pasaban. Lo destapó correr un informe.

3. **El self-test de un gate encuentra al gate.** El chequeo nuevo de "todo
   comando de contenedor tiene su archivo en la imagen" pedía `compose` en
   minúscula y no matcheaba **ni una línea** del Makefile, que usa `$(COMPOSE)`.
   Habría pasado en verde sobre cero comandos. Ahora falla si no encuentra
   ninguno.

4. **Un arreglo destapa el bug de al lado.** El test de la cochera falló por un
   motivo distinto del que probaba: el recorte por percentil comparaba el valor
   REDONDEADO contra percentiles sin redondear, y con diez avisos idénticos a
   2.328,57 USD/m² excluía los diez.

5. **Tres semillas dicen lo contrario que una.** El aporte del nodo 4 pasó de
   +0,6 pp a −0,33 pp al pasar de una corrida a tres. R6 no es una formalidad.

6. **Poner un piso por encima del suelo no es exigencia.** El gate de cobertura
   estaba en 60 con la nota "es lo que hay hoy más un poco", y lo que había era
   55,67%. Un gate que no puede pasar es un gate que se desactiva.
