# S8 — Evaluación

**Alcance:** `src/tasador/eval/`, `scripts/eval_*.py`, `tests/golden/`, contra
`docs/09-evaluacion-y-backtest.md`.
**Método:** ejercitar el gate real con resultados construidos, sin escribir en
`eval.backtest_runs`.

---

## 1. Lo que está bien

**Los evals corren el mismo camino que producción**, que es la corrección más
importante de la Etapa 3 y sigue en pie:

```
$ rg -n "extraer_lotes|from tasador" scripts/eval_extraccion.py
27:from tasador.agents.nodes.extract import (
30:    extraer_lotes,          <- la MISMA función que usa el nodo 4
105:        resultados, _lotes = await extraer_lotes(
```
```
$ rg -n "from tasador" scripts/eval_curaduria.py
26:from tasador.agents.nodes.curate import LoteCurado, _payload, _resumen_sujeto, aplicar_veredicto
```

**El manejo de la varianza es correcto y es lo mejor de esta sección.**
`eval/componentes.py` compara contra la **mediana de las últimas 5** corridas y
reporta la amplitud al lado, con el número medido (17,6 pp) y el razonamiento
escritos en el código. Un gate que se dispara con el ruido enseña a ignorarlo, y
este no lo hace.

**La serie histórica guarda las tres versiones** y son NOT NULL:

```sql
select created_at::date, dataset, n_cases, sample, seed, round(mdape*100,1),
       engine_version, method_version, left(prompt_bundle_version,12)
from eval.backtest_runs order by created_at;

 2026-08-14 | BADATA_2015_2020      | 300 | 300 | 42 | 14.5 | 2026.08.1 | 2026.08.1+b721657c226b | 94c53f5750b5
 2026-08-14 | BADATA_2015_2020      | 300 | 300 |  7 | 15.0 | 2026.08.1 | 2026.08.1+b721657c226b | 94c53f5750b5
 2026-08-14 | VIGENTES_SIN_FEATURES | 300 | 300 | 42 | 21.8 | 2026.08.1 | 2026.08.1+b721657c226b | 8371d2df62e2
```

`anterior()` filtra por dataset, así que no se comparan peras con manzanas. Y
existe un dataset `VIGENTES_SIN_FEATURES` sobre el corpus vigente, que ESTADO
§5.1 no menciona.

---

### H-44 · El gate del backtest sale VERDE con cero casos, y el CI usa justo el flag que no protege

**Sección:** S8 · **Severidad:** media

**Qué está mal:** las tres condiciones de falla de `eval/run.py:227-248` exigen
`res.mdape is not None`. Con cero casos evaluados, `mdape` es `None` y ninguna
se evalúa: `fallas` queda vacío y el proceso devuelve 0. La única condición que
sí atrapa el caso vacío es `not res.beats_baseline`, que **solo se evalúa con
`--fail-on-regression`** — y el CI usa `--fail-if-mdape-worse-than 2.0`.

**Cómo lo verifiqué:** ejercitando el gate real con un `BacktestResult` de cero
casos (sin tocar la base: `run_backtest`, `guardar` y `anterior` reemplazados).

```
$ uv run python docs/auditoria/sondas/s8_gate.py
  CERO CASOS, como en un runner sin corpus  (flags del CI)   -> VERDE (exit 0)
  CERO CASOS, con una corrida previa buena  (flags del CI)   -> VERDE (exit 0)
  CERO CASOS, con --fail-on-regression                       -> ROJO  (exit 1)
        · no le gana al baseline (doc 09 §7)
  MdAPE 40% (25 pp peor)                    (flags del CI)   -> ROJO  (exit 1)
```

Y el comando del CI (`ci.yml:204`):

```yaml
uv run python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300 \
                                  --fail-if-mdape-worse-than 2.0
```

**Por qué importa:** es exactamente el escenario que el propio YAML dice querer
evitar. `ci.yml:192-196`:

> *«El backtest LEE EL CORPUS de la base: sin BA Data cargado no hay casos. Este
> paso está declarado y desactivado a propósito, con el motivo a la vista:
> correrlo contra una base vacía daría "0 casos evaluados" y un gate verde, que
> es peor que no tener gate.»*

El diagnóstico es correcto, la mitigación elegida —desactivar el paso— es
razonable, y **la causa quedó sin arreglar**. El día que alguien ponga
`EVAL_CORPUS_LISTO=true` con un corpus incompleto, el gate va a dar verde sin
medir, y esta vez con la casilla tildada.

Además, la falla es silenciosa en la otra dirección: el gate tampoco distingue
"300 casos evaluados" de "3 casos evaluados".

**Qué hay que hacer:** un chequeo previo a todos los demás, en
`eval/run.py:227`:

```python
MINIMO_DE_CASOS = 50   # por debajo de esto el MdAPE es ruido
if res.n_evaluated < MINIMO_DE_CASOS:
    fallas.append(f"solo se evaluaron {res.n_evaluated} casos de {args.sample} pedidos "
                  f"(mínimo {MINIMO_DE_CASOS}): el gate no puede medir")
```
Y lo mismo, en su escala, para `_golden_set`. Un gate tiene que fallar cuando no
puede medir, no cuando mide mal.

**Cómo se verifica que quedó bien:**

```
uv run python docs/auditoria/sondas/s8_gate.py
# las dos primeras líneas tienen que decir ROJO
```
Y un test en `tests/` que llame a `_correr` con un resultado vacío y afirme
`== 1`. Hoy ese test no existe.

**Riesgo de tocarlo:** si `MINIMO_DE_CASOS` queda por encima de lo que el corpus
puede dar, el gate se pone rojo permanente y se aprende a ignorarlo — que es el
mismo daño por el otro lado. 50 sobre un `--sample 300` es holgado; conviene
revisarlo contra la cobertura real antes de encender el paso del CI.

---

### H-45 · El eval de curaduría mide al juez, no al nodo

**Sección:** S8 · **Severidad:** baja

**Qué está mal:** `scripts/eval_curaduria.py` importa del nodo `_payload`,
`_resumen_sujeto`, `aplicar_veredicto` y el prompt —bien— pero **no llama a
`curate()`**: rearma el bucle. Le faltan dos cosas que el nodo sí hace:

1. `reglas_duras()` no corre. El nodo descarta por reglas ANTES de llamar al
   juez; el eval le manda todo.
2. La validación `if v.motivo not in permitidos` (`curate.py:267`) no está. El
   eval acepta un motivo que producción rechazaría.

**Cómo lo verifiqué:**

```
$ rg -n "reglas_duras|motivos_permitidos|aplicar_veredicto" scripts/eval_curaduria.py
127:        aplicado = aplicar_veredicto(cands[ref], v)
```
Solo la tercera de las tres etapas.

**Por qué importa:** es la misma familia del bug #4 de la Etapa 3 —*"el eval
medía un fragmento del sistema"*— en otro componente. Acá el impacto es más
chico y en parte deliberado: los cinco `motivos_permitidos` del golden set son
todos del juez, así que medir al juez solo tiene sentido. Lo que **no** tiene
sentido es que el número se reporte como "recall del nodo 6": el nodo también
descarta por `sin_superficie`, `precio_en_ars` y `duplicado_de_cluster`, y esos
descartes no se están midiendo contra nada.

**Qué hay que hacer:** o renombrar la métrica a `curator_judge` en
`eval.component_runs.componente` y decir en doc 09 §3.3 que mide el juez, o
—mejor— hacer que el eval llame a `curate()` con un `NodeConfig` armado a mano y
mida el nodo entero. Lo segundo también agregaría la validación del vocabulario.

**Cómo se verifica que quedó bien:** anotar en el golden set un descarte por
regla dura (un aviso en pesos) y comprobar que el eval lo cuenta.

**Riesgo de tocarlo:** cambia el número que se reporta como recall. Hay que
correr tres veces y comparar medianas, no una.

---

## 2. Lo que la evaluación NO cubre hoy

Esto no son hallazgos: son huecos declarados en ESTADO §5.1 que confirmo, con lo
que agregaría a cada uno.

| Hueco | Estado verificado |
|---|---|
| El golden set de curaduría tiene 6 descartes | Confirmado en `tests/golden/extraccion.yaml`. Con 6 positivos, cada caso vale 16,7 pp: no se puede medir recall |
| El golden set lo anotó Claude | `tests/golden/palermo-para-anotar.yaml` tiene 4.293 líneas con `revisado: false` y los evals los ignoran. El mecanismo está bien |
| Backtest con el nodo 4 activo | Existe `VIGENTES_SIN_FEATURES` (21,8% vs 22,9% de baseline). El nombre dice **sin** features: sigue faltando la corrida CON el nodo 4 |
| El motor de ajustes no está medido | Ver S1: `expenses` y `ground_with_patio` no se aplican, la cochera no se normaliza y el paso p5–p95 no está documentado. **Cualquier backtest que se corra hoy mide eso, no lo que doc 05 describe** |

Y agrego uno que no está en la lista: **no hay ningún eval del nodo 5 (dedup)**.
Es el nodo con el hallazgo más grave de esta auditoría (H-12: clusters de 198
avisos donde el 92% de los pares no cumplen el criterio) y no hay un solo número
que lo hubiera detectado. `tests/test_dedup.py` cubre casos borde conocidos; no
hay una métrica sobre el corpus.

## 3. Qué NO pude verificar

- **No corrí ningún eval de verdad.** `eval_extraccion.py` y `eval_curaduria.py`
  gastan en LLM y `--guardar` escribe en `eval.component_runs`; el backtest
  escribe en `eval.backtest_runs`. Ejercité el **gate**, que es lo que la
  pregunta de esta sección pedía, con el código real y sin efectos.
- **La comparabilidad de la serie de componentes**: solo hay 5 filas y no pude
  consultarlas (mi consulta asumía una columna `dataset` que no existe). La
  lógica de `resumen()`/`ultimas()` la leí y es correcta.
- **Si el MdAPE de 15,0% sigue valiendo.** El motor cambió desde esa corrida (el
  bundle pasó de `94c53f5750b5` a `eeaae70d00a5`); para el backtest eso es
  metadato —no usa prompts— pero cualquier arreglo de S1 sí lo va a mover.

