# S10 — Scripts operativos

**Alcance:** los 19 scripts de `scripts/`.
**Método:** ejecutarlos todos.

---

## 1. Ninguno está roto

```
$ for s in scripts/*.py; do uv run python $s --help; echo "exit=$?"; done

bundle_lock.py             exit=0  bundle.lock.json ya estaba al día  (eeaae70d00a5)
capture.py                 exit=0  usage: capture.py [-h] --barrio BARRIO …
check_models.py            exit=0  usage: check_models.py …
crear_usuario.py           exit=0  usage: crear_usuario.py …
dedup_corpus.py            exit=0  usage: dedup_corpus.py …
eval_curaduria.py          exit=0  usage: eval_curaduria.py …
eval_extraccion.py         exit=0  usage: eval_extraccion.py …
extraer_corpus.py          exit=0  usage: extraer_corpus.py …
fetch_badata.py            exit=0  usage: fetch_badata.py …
geocode_neighborhoods.py   exit=0  usage: geocode_neighborhoods.py …
golden_set.py              exit=0  usage: golden_set.py …
ingest_portal_a.py        exit=0  usage: ingest_portal_a.py …
ingest_csv.py              exit=0  usage: ingest_csv.py …
load_badata.py             exit=0  usage: load_badata.py …
render_pdf.py              exit=0  usage: render_pdf.py …
run_backtest.py            exit=0  usage: run_backtest.py …
run_report.py              exit=0  usage: run_report.py …
seed.py                    exit=0  usage: seed.py …
seed_org.py                exit=0  usage: seed_org.py …
```

Los 19 importan, parsean argumentos y salen en 0. Es un resultado mejor del que
esperaba: el 14/08 había siete comandos documentados que no existían, y
`tests/architecture/test_entrypoints.py` cerró esa clase de error.

Lo que sigue son cuatro cosas que el `--help` no dice.

---

### H-52 · `bundle_lock.py --help` no muestra ayuda: ejecuta

**Sección:** S10 · **Severidad:** baja

**Qué está mal:** es el único de los 19 que no usa `argparse`. `--help` no lo
detiene: corre y **escribe `prompts/bundle.lock.json`**.

**Cómo lo verifiqué:** la primera línea de su salida no es un `usage:` sino el
resultado de la ejecución:

```
bundle_lock.py             exit=0  bundle.lock.json ya estaba al día  (eeaae70d00a5)
```

**Por qué importa:** poco hoy, porque es idempotente y el lock ya estaba al día.
Importa como principio: `--help` es lo que uno escribe **para no ejecutar nada**,
y acá escribe un archivo versionado. Si el bundle hubiera estado desactualizado,
mi `--help` habría modificado el repo — que es justo lo que esta auditoría no
tiene que hacer.

**Qué hay que hacer:** `argparse` con `--verificar` (que salga 1 si el lock está
viejo, sin escribir) y `--escribir` para el comportamiento actual. El primero
sirve además para el CI.

**Cómo se verifica que quedó bien:** `uv run python scripts/bundle_lock.py --help`
imprime `usage:` y `git status` queda limpio.

**Riesgo de tocarlo:** ninguno.

---

### H-53 · Dos caminos para el backtest, con distinto nombre de dataset y solo uno persiste

**Sección:** S10 · **Severidad:** media

**Qué está mal:** hay dos entradas a la misma medición:

| | `scripts/run_backtest.py` | `python -m tasador.eval.run` |
|---|---|---|
| dataset por defecto | `BADATA_2020` | `BADATA_2015_2020` |
| guarda en `eval.backtest_runs` | **no** | sí |
| compara contra la corrida anterior | no | sí |
| gate (`--fail-*`) | `exit 2` si no gana al baseline | sí |

**Cómo lo verifiqué:** `scripts/run_backtest.py:23` declara
`--dataset BADATA_2020`, y `eval/run.py:333` usa `BADATA_2015_2020`. En
`backtest.run_backtest` el nombre **no cambia qué casos se cargan** —solo se
mira `dataset.startswith("VIGENTES")`— pero **sí es la clave con la que
`anterior()` busca la corrida previa**:

```python
# eval/run.py
previa = await anterior(session, args.dataset)
```

Y en la base hay tres filas, todas escritas por el segundo camino:

```sql
select dataset, count(*) from eval.backtest_runs group by 1;
 BADATA_2015_2020      | 2
 VIGENTES_SIN_FEATURES | 1
```

**Por qué importa:** ESTADO §4.4 lista `run_backtest.py` como "el script que mide
el MdAPE end-to-end", y es el que **no deja rastro**. Alguien que corra el
documentado obtiene un número en la consola y nada en la serie — que es
exactamente el defecto #25 que se arregló el 14/08 ("ningún backtest quedaba
registrado"), sobreviviendo en el otro script. Y si algún día se le agrega
persistencia con su default, la serie se parte en dos etiquetas para el mismo
dataset y `anterior()` deja de encontrar nada.

**Qué hay que hacer:** borrar `scripts/run_backtest.py` y dejar
`python -m tasador.eval.run` como único camino (ya está en el Makefile, en el CI
y en ESTADO §8). Si se quiere conservar el nombre, que sea un alias de tres
líneas que invoque el módulo.

**Cómo se verifica que quedó bien:** `rg -n "run_backtest.py" .` sin resultados
fuera de la doc histórica, y `test_entrypoints.py` en verde.

**Riesgo de tocarlo:** el informe de la Etapa 2 y ESTADO §4.4 lo nombran; hay que
actualizarlos (S12).

---

### H-54 · El backtest "con el nodo 4 activo" que ESTADO da por pendiente está a un comando

**Sección:** S10 · **Severidad:** media (es una oportunidad, no un defecto)

**Qué está mal:** ESTADO §5.1 ítem 5 dice *«Backtest con el nodo 4 activo. Sin
esto, "el nodo 4 mejora el MdAPE" es una hipótesis. BA Data no tiene los
atributos, así que hay que pensar el dataset»*. **El dataset ya está
implementado.**

**Cómo lo verifiqué:**

```
$ rg -n "VIGENTES" src/tasador/eval/backtest.py
166:  el par de datasets VIGENTES / VIGENTES_SIN_FEATURES existe para medir
195:  correr el par VIGENTES / VIGENTES_SIN_FEATURES aísla cuánto aporta el motor
315:  if dataset.startswith("VIGENTES"):
320:      con_features=dataset != "VIGENTES_SIN_FEATURES",
```

Y en la base está corrida **solo la mitad**:

```sql
 BADATA_2015_2020      | 300 | 14.5% | base 16.2%
 BADATA_2015_2020      | 300 | 15.0% | base 16.2%
 VIGENTES_SIN_FEATURES | 300 | 21.8% | base 22.9%     <- la mitad SIN features
```

Falta `--dataset VIGENTES`, que es la otra mitad del par.

**Por qué importa:** es la medición que ESTADO nombra como bloqueante #5 y que
más veces se pospuso, y el trabajo de diseñar el dataset ya está hecho. El par
`VIGENTES` / `VIGENTES_SIN_FEATURES` aísla exactamente lo que se quiere saber.

**Ojo con una cosa antes de correrlo:** hoy ese número mediría el motor **con
los defectos de S1** (el recorte p5–p95 no documentado, la cochera sin
normalizar, `expenses` sin aplicar). Correrlo ahora da una línea de base honesta
de "el sistema como está"; correrlo después de S1 da "el sistema como doc 05 lo
describe". Valen los dos, en ese orden.

**Qué hay que hacer:**

```bash
docker compose run --rm worker python -m tasador.eval.run --dataset VIGENTES --sample 300
docker compose run --rm worker python -m tasador.eval.run --history --dataset VIGENTES
```
Y comparar contra la fila de `VIGENTES_SIN_FEATURES` **a mano**: `anterior()`
filtra por dataset, así que no las compara solas.

**Cómo se verifica que quedó bien:** dos filas en `eval.backtest_runs`, una por
dataset, con el mismo `sample` y la misma `seed`. La diferencia de MdAPE entre
las dos **es** el aporte del nodo 4, y hay que leerla contra los 0,6 pp de
varianza entre semillas.

**Riesgo de tocarlo:** escribe en `eval.backtest_runs` (una fila) y tarda
minutos. No gasta en LLM: el backtest no llama a ningún modelo.

---

### H-55 · `seed_org.py` quedó cuando `seed.py` lo absorbió

**Sección:** S10 · **Severidad:** baja

**Qué está mal:** `scripts/seed.py` crea el tenant **y** los centroides de
barrio. `scripts/seed_org.py` crea solo el tenant. El informe de la Etapa 3 §13
sigue indicando el segundo.

**Cómo lo verifiqué:** los dos existen y responden; `seed.py --help` ofrece
`--offline`, `seed_org.py` no. `ESTADO §8` documenta `seed.py`; el informe de la
Etapa 3 §13 documenta `seed_org.py` seguido de `geocode_neighborhoods.py`, que es
lo que `seed.py` ya hace junto.

**Por qué importa:** poco. Es prolijidad, y sobre todo que dos documentos del
proyecto indican dos caminos distintos para lo mismo.

**Qué hay que hacer:** borrar `seed_org.py` y corregir la referencia en el
informe de la Etapa 3 (o dejar el informe como documento histórico y anotarlo,
que es lo que corresponde con un informe fechado).

**Cómo se verifica que quedó bien:** `test_entrypoints.py` en verde después de
borrarlo — si algún archivo de comandos lo seguía nombrando, el test lo dice.

**Riesgo de tocarlo:** ninguno.

---

## 2. Duplicación con `src/`: lo que revisé

| Script | ¿Reimplementa lógica? |
|---|---|
| `dedup_corpus.py` | **No.** Importa `_capa_1`, `_capa_2`, `_zona_gris`, `_componentes`, `elegir_canonico` del nodo 5. Bien resuelto y con el motivo escrito |
| `eval_extraccion.py` | **No.** Usa `extraer_lotes`, la misma del nodo 4 |
| `eval_curaduria.py` | **Parcial.** Usa `_payload`, `_resumen_sujeto` y `aplicar_veredicto`, pero rearma el bucle y le faltan `reglas_duras` y la validación del vocabulario (ver H-45 en S8) |
| `ingest_csv.py` | **No.** Todo pasa por `csv_scan`, que a su vez usa `capture.ingest` |
| `run_backtest.py` | **Sí**, duplica la entrada de `tasador.eval.run` (H-53) |
| `seed_org.py` | **Sí**, subconjunto de `seed.py` (H-55) |
| `render_pdf.py` | **No.** Usa `informe_html` / `html_a_pdf` del nodo 11 |

## 3. Qué falta

Dos scripts que no existen y que la operación pide:

1. **Cosechar informes colgados** (H-13): hoy hay 4 informes en `RUNNING`/`QUEUED`
   desde hace 8-13 horas y no hay forma de limpiarlos que no sea SQL a mano.
2. **Verificar que la imagen desplegada es el código del repo** (H-48). La sonda
   que escribí para esta auditoría debería vivir en `ops/`.

## 4. Qué NO pude verificar

**No ejecuté ninguno de los 19 más allá de `--help`.** Todos escriben en la base,
gastan en LLM, o las dos cosas. `ingest_csv.py --dry-run` es la única excepción y
sí la corrí (S3): 1.779 archivos, cero escrituras, verificado contando filas
antes y después.
