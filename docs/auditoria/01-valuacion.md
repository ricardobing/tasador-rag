# S1 — Núcleo de valuación

**Alcance:** `src/tasador/valuation/` (engine 252 líneas · adjustments 145 ·
models 143), `src/tasador/agents/nodes/valuation.py`, `config/adjustments.yaml`,
contra `docs/05-metodologia-de-valuacion.md`.
**Método:** ejecutar el motor y comparar con lo persistido en la base, no leer.

---

## 1. Lo primero: ADR-002 aguanta, y el número reproduce

Antes de los hallazgos, lo que **sí** está bien, porque es la parte que sostiene
todo lo demás.

**El precio no puede salir de un LLM, y el candado no se puede saltear.**
`NodeConfig._coherencia` rechaza cualquier `task` sobre `adjust_and_value`, y el
atajo por entorno pasa por la misma validación:

```
$ uv run pytest tests/test_agents_config.py -q -k "adr_002 or precio"
2 passed
```

Verifiqué también el camino de escritura: `report.value_mid` se escribe en
`runner.py:186` desde `state["valuation"]`, que produce el nodo 7. Ningún nodo
LLM escribe esas columnas (grep sobre `src/`), y la base tiene un CHECK que
exige `value_low <= value_mid <= value_high` (`models.py:770`).

**La matemática del informe reproduce.** Tomé los 5 informes SUCCEEDED más
recientes, leí sus comparables incluidos de `core.report_comparables`, y corrí
los pasos 4-6 del motor (mediana, winsorizado, percentiles, rango, cierre)
contra lo guardado en `core.reports`:

```
$ uv run python docs/auditoria/sondas/s1_reproduce.py
informe 4f3a88f7  n=23  conf=ALTA
    price_per_m2   calculado         3988  guardado      3988.00  OK
    value_mid      calculado       239280  guardado    239280.00  OK
    value_low      calculado       191424  guardado    191424.00  OK
    value_high     calculado       287136  guardado    287136.00  OK
    closing_low    calculado       203388  guardado    203388.00  OK
    closing_high   calculado       227316  guardado    227316.00  OK
informe 47a5f323  n=22 ... 6/6 OK
informe ad8e0c16  n=49 ... 6/6 OK
```

La implementación que corrió es la del repo (R2 verificado para este módulo) y
el número es auditable desde las filas.

Y el ajuste registrado en `report_comparables.adjustments->>'total'` coincide
hoy con el cociente realmente aplicado, en las 615 filas:

```
 filas | discrepan | min_delta | max_delta
   615 |         0 |   -0.0003 |    0.0004
```

Lo que sigue son los ocho lugares donde el motor y el documento que lo describe
no dicen lo mismo.

---

### H-01 · El motor ELIMINA comparables por p5–p95, y doc 05 dice que no elimina

**Sección:** S1 · **Severidad:** media

**Qué está mal:** `engine.py:130-145` corre un recorte por percentil 5–95 que
**descarta** comparables antes de winsorizar. Doc 05 §5 no menciona ese paso y
además argumenta explícitamente en contra: *«Por qué winsorizar en vez de
eliminar: con 8 comparables, eliminar los 2 extremos tira el 25% de la
muestra»*. Con 8 comparables, el motor elimina exactamente 2.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s1_probe.py
D. El paso p5-p95: ¿cuantos comparables saca?
  n=  7 -> usados   7  descartados por p5-p95: 0
  n=  8 -> usados   6  descartados por p5-p95: 2
  n= 10 -> usados   8  descartados por p5-p95: 2
  n= 21 -> usados  18  descartados por p5-p95: 3
  n= 41 -> usados  35  descartados por p5-p95: 6
  n= 60 -> usados  54  descartados por p5-p95: 6
```

Y no es teórico: en los informes ya emitidos es el segundo motivo de exclusión.

```sql
select exclusion_reason, count(*) from core.report_comparables
where not included group by 1 order by 2 desc;

 en_pozo_o_construccion | 239
 outlier_estadistico    |  88     <-- este paso
 ajuste_excede_el_tope  |  77
 permuta_o_financiacion |  25
 duplicado_de_cluster   |  24
```

**Por qué importa:** el documento que se le muestra a un martillero describe un
método que el código no ejecuta. Con n=8 —el piso realista de un barrio con
poca cobertura— se tira el 25% de la muestra que doc 05 §5 dice expresamente
que no se tira, y encima se winsoriza después, o sea que se aplica el recorte
dos veces. La justificación escrita de por qué el método es robusto deja de
describir el método.

**Qué hay que hacer:** es una decisión de producto, no un arreglo mecánico.
Elegir una de las dos y dejar el sistema y el documento diciendo lo mismo:

1. **Sacar el paso** (`engine.py:130-145`) y quedarse solo con el winsorizado
   de doc 05 §5. Correr el backtest antes y después: el MdAPE es la vara.
2. **Dejarlo y documentarlo** en doc 05 §5 como paso 4.0, con el fundamento y
   con la tabla de arriba, y renombrar el motivo de exclusión a
   `recorte_p5_p95` (`outlier_estadistico` se confunde con
   `usd_m2_fuera_de_rango`, que es otra cosa).

En los dos casos, agregar `tests/test_valuation.py::test_recorte_percentil`
con los conteos exactos por n, para que el comportamiento quede fijado.

**Cómo se verifica que quedó bien:**

```
uv run python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300
uv run python -m tasador.eval.run --history --dataset BADATA_2015_2020
```
Si se saca el paso, el MdAPE no debe empeorar más de 2 pp (la tolerancia de
doc 09 §5, calibrada contra los 0,6 pp de varianza entre semillas).

**Riesgo de tocarlo:** sacar el paso cambia el número de TODOS los informes
futuros y rompe la comparabilidad con la serie de `eval.backtest_runs`. El
`_hash` de `adjustments.yaml` no cambia porque el paso vive en el código: hay
que subir `method_version` a mano en `settings.py` o la serie histórica queda
mezclando dos métodos con la misma etiqueta.

---

### H-02 · La cochera del comparable no se descuenta antes de calcular su USD/m²

**Sección:** S1 · **Severidad:** media

**Qué está mal:** doc 05 §4.1 trata la cochera como valor absoluto
(+USD 12.000 al total, no al m²). El motor lo hace **solo del lado del
sujeto** (`engine.py:176-179`). Del lado del comparable, la cochera está
incluida en el precio publicado y nadie la saca antes de dividir por la
superficie: su USD/m² queda inflado y contamina la mediana.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s1_probe.py
A. ¿La cochera del COMPARABLE se descuenta antes de calcular USD/m2?
  sin cocheras en comparables : USD/m2 2500  mid 175000
  5 de 10 CON cochera         : USD/m2 2500  mid 175000
  -> la cochera del comparable NO se descuenta
  sujeto CON cochera vs comparables CON cochera: mid 187000  (+12000)
```

Cinco de diez comparables con cochera declarada y el USD/m² no se mueve un
peso. La distorsión por comparable es grande: 175.000 / 70 m² con cochera son
2.500 USD/m², y descontando la cochera serían 2.329 — **7,3% de inflación en
ese comparable**.

**Y el impacto agregado HOY es cero, medido.** Contrafactual sobre los 6
informes SUCCEEDED más recientes, descontando USD 12.000 a cada comparable con
cochera declarada:

```
$ uv run python docs/auditoria/sondas/s1_parking.py
4f3a88f7  n= 23  con cochera= 0  ... 239,280 ->  239,280  (+0.00%)
ad8e0c16  n= 49  con cochera= 9  ... 169,860 ->  169,860  (+0.00%)
1f2c2c04  n= 53  con cochera= 1  ... 230,564 ->  230,564  (+0.00%)
```

La razón es que la mediana es robusta y la cochera casi no se declara:

```sql
select count(*) filter (where nullif(raw->>'parking_spaces','0') is not null), count(*)
from corpus.listings where active;
 con_cochera | total
          92 |  8497      -- 1,1%
```

Y del lado del sujeto **nunca se dispara**: de las 56 propiedades cargadas,
cero declaran cochera.

**Por qué importa:** es exactamente la clase de supuesto latente que este
proyecto ya pagó tres veces. Hoy el sesgo es 0,00% porque el dato falta; el día
que el scraper traiga cocheras al 30% —Portal B ya las trae al 30%— el
comparable con cochera empieza a empujar la mediana hacia arriba **y encima se
le suma la cochera del sujeto**, que es el doble conteo completo.

**Qué hay que hacer:** en `engine.py`, antes de `crudo = comp.raw_price_per_m2`,
normalizar el precio del comparable:

```python
# precio del inmueble SIN cocheras, que es lo que mide el USD/m2
precio_sin_cochera = comp.price - parking_value(cfg, comp.prop.parking_spaces)
```
y usar ese precio para el USD/m² crudo. Requiere mover `raw_price_per_m2` de
`models.Comparable` al motor (hoy es una `@property` que no ve la config) o
pasarle el valor de la cochera. Guardar el crudo SIN normalizar en
`report_comparables.raw_price_per_m2` y agregar una clave `cochera` al detalle
de `adjustments`, para que el informe siga mostrando el precio publicado.

**Cómo se verifica que quedó bien:** un test nuevo con la forma de la sonda A —
10 comparables idénticos, 5 con cochera— donde el USD/m² del set con cocheras
tiene que bajar en `12000/superficie`. Y `uv run python docs/auditoria/sondas/s1_parking.py`
tiene que dar delta 0,00% después del arreglo (porque ya estaría normalizado).

**Riesgo de tocarlo:** cambia el número de cualquier informe con comparables con
cochera. `test_cochera_suma_valor_absoluto` protege el lado del sujeto y tiene
que seguir en verde. Correr el backtest: BA Data no trae cocheras, así que el
MdAPE no debería moverse — si se mueve, el arreglo tocó otra cosa.

---

### H-03 · El tope de ±25% se aplica antes del último coeficiente, y se puede violar

**Sección:** S1 · **Severidad:** baja (hoy latente) · **media si llegan fechas**

**Qué está mal:** `engine.py:109-117` calcula `capped` con
`compute_adjustments()` y **después** multiplica por `listing_age_coef()`. El
coeficiente que de verdad divide al precio nunca pasa por el tope, y el
`total` que se guarda en `report_comparables.adjustments` es el de antes de esa
multiplicación.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s1_probe.py
B. ¿El tope de +-25% se respeta DESPUES de listing_age_coef?
  compute_adjustments -> total 0.762600  capped=False
  listing_age_coef (400 dias) -> 0.97
  producto REAL aplicado al precio -> 0.73972200
  piso declarado en adjustments.yaml -> 0.75
  -> VIOLA el piso
```

**Hoy no ocurre**, y por un motivo que conviene decir: ningún aviso vigente
tiene fecha de publicación, así que `listing_age_coef` devuelve siempre 1,00.

```sql
select count(*) total, count(published_at) con_fecha,
       count(*) filter (where published_at < now() - interval '90 days') mayor_90
from corpus.listings where active;
 total | con_fecha | mayor_90
  8497 |         0 |        0
```

**Por qué importa:** son dos cosas a la vez. Una es el tope: doc 05 §4.2 regla 1
dice que fuera de `[0,75 · 1,25]` el comparable se descarta, y con el aviso
viejo el ajuste real llega a −26%. La otra es peor para un producto que se vende
por auditable: doc 05 §4.2 regla 3 dice *«todo ajuste queda registrado»*, y
`adjustments.total` no incluye el coeficiente de antigüedad del aviso. El día
que el scraper traiga `publication_date` —Portal B ya lo trae en el 76%— el JSON
que respalda el informe va a decir un número y el precio va a estar dividido por
otro.

**Qué hay que hacer:** mover `listing_age_coef` **adentro** de
`compute_adjustments()` en `adjustments.py:70`, como un `apply()` más
(`apply("antiguedad_aviso", comp.days_published, listing_age_coef(cfg, comp), D("1.00"))`),
para que entre al producto, al tope y al detalle en un solo lugar. Eso obliga a
pasarle el `Comparable` y no solo la `Property`; alternativa mínima: recalcular
`capped` en `engine.py` después de la multiplicación y agregar la clave al
detalle.

**Cómo se verifica que quedó bien:**

```python
# tests/test_valuation.py
def test_el_tope_incluye_la_antiguedad_del_aviso():
    # comparable en el borde del tope + aviso de 400 dias -> descartado
    ...
    assert a.exclusion_reason == "ajuste_excede_el_tope"

def test_el_total_registrado_es_el_que_divide_al_precio():
    assert D(a.adjustments["total"]) == a.raw_price_per_m2 / a.adjusted_price_per_m2
```
Y sobre la base, la consulta de arriba (`discrepan`) tiene que seguir dando 0.

**Riesgo de tocarlo:** hoy no cambia ningún número porque el coeficiente es 1,00
en el 100% de los avisos. El riesgo es futuro y al revés: no tocarlo.

---

### H-04 · Dos coeficientes de doc 05 §4.1 no existen en el código

**Sección:** S1 · **Severidad:** baja

**Qué está mal:** la tabla de coeficientes de doc 05 §4.1 y
`config/adjustments.yaml` declaran **expensas > 2× la mediana del barrio →
0,96** y **PB con patio → 1,02**. Ninguno de los dos se aplica nunca.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s1_probe.py
C. ¿Que coeficientes del YAML nunca se aplican?
  cfg['expenses'] referenciado en el codigo: False
  cfg['ground_with_patio'] referenciado en el codigo: False
  cfg['expenses'] = {'high_multiplier': 2.0, 'coef': 0.96}
  cfg['floor']['ground_with_patio'] = {'coef': 1.02}
```

`compute_adjustments()` aplica cinco factores: estado, antigüedad, orientación,
piso y amenities. `expenses_ars` se transporta hasta `Property` (se lee del
crudo en 3.674 avisos vigentes) y ahí muere. `_coef_floor()` tiene dos ramas y
una salida por defecto: `high_no_elevator`, `high_with_view`, `default`.

**Por qué importa:** el dato de expensas está en el 43% del corpus vigente y se
está pagando por transportarlo hasta un coeficiente que no existe. Y sobre todo:
el documento que justifica el método ante un cliente lista siete factores de
ajuste, y el sistema aplica cinco. No es un número mal calculado, es una
promesa que el código no cumple.

**Qué hay que hacer:** decidir y dejar una sola versión. O se implementan los
dos coeficientes en `adjustments.py` (expensas necesita la mediana del barrio,
que ya la calcula `corpus/coverage.py`; PB con patio necesita saber si hay
patio, que hoy no se extrae — o sea que ese es más caro de lo que parece), o se
sacan del YAML y de la tabla de doc 05 §4.1 con una nota de por qué.
Recomendación: sacar `ground_with_patio` (no hay dato de entrada) e implementar
`expenses` (el dato está).

**Cómo se verifica que quedó bien:** un test que recorra las claves de
`config/adjustments.yaml` y falle si alguna no aparece referenciada en
`src/tasador/valuation/`. Es el mismo patrón que
`test_todo_prompt_declarado_existe_como_archivo`, aplicado a los coeficientes.

**Riesgo de tocarlo:** implementar `expenses` cambia el número. Backtest antes y
después; BA Data no trae expensas, así que el MdAPE no lo va a mostrar — hay que
medirlo sobre el corpus vigente y decirlo como lo que es: sin validar.

---

### H-05 · El ancho mínimo de rango de doc 05 §6 es inalcanzable, y su test no puede fallar

**Sección:** S1 · **Severidad:** baja

**Qué está mal:** doc 05 §6 dice *«Ancho mínimo del rango: 8%. Si p25 y p75
quedan más cerca que eso, se fuerza a ±4% del medio»*. El código toma
`max(mid*8%/2, mid*20%)` (`engine.py:189`), o sea que el piso real es ±20% y el
±4% no gana nunca. Y el test que lleva el nombre de esa regla afirma un umbral
que no puede fallar.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s1_probe.py
E. El 'ancho minimo 8%' de doc 05 §6, ¿opera alguna vez?
  set perfectamente homogeneo -> ancho 40.0%  (doc 05 dice +-4% = 8%)
  min_range_width_pct=8  uncertainty_half_width_pct=20

$ uv run python docs/auditoria/sondas/s1e.py
  test_rango_ancho_minimo afirma ancho >= 0.079 sobre un ancho real de 40.0%
```

**Por qué importa:** es un gate que no mide (R3). El test se llama
`test_rango_ancho_minimo`, se lee como el que protege la regla de doc 05 §6, y
pasaría igual si alguien pusiera `min_range_width_pct: 0`. El único parámetro
que de verdad gobierna el ancho es `uncertainty_half_width_pct`, y no tiene
ningún test que lo fije.

**Qué hay que hacer:**
1. Cambiar el assert de `tests/test_valuation.py::test_rango_ancho_minimo` a la
   igualdad exacta contra `uncertainty_half_width_pct` leído del YAML
   (`ancho == 2 * unc`), que es el invariante real.
2. Corregir doc 05 §6: el piso es `max(min_range_width/2, uncertainty)` y hoy
   son ±20%.
3. Decidir si `min_range_width_pct` se queda. Sirve solo si algún día
   `uncertainty_half_width_pct` baja de 4% —lo cual pasaría si el MdAPE mejora
   mucho— así que dejarlo documentado como el piso de último recurso es
   razonable; borrarlo también.

**Cómo se verifica que quedó bien:** `uv run pytest tests/test_valuation.py -q`
y bajar `uncertainty_half_width_pct` a 1 en una copia del YAML: el test tiene
que fallar. Hoy no falla.

**Riesgo de tocarlo:** ninguno en el número. Es test y documentación.

---

### H-06 · La frescura y la completitud del score de confianza son constantes

**Sección:** S1 · **Severidad:** media

**Qué está mal:** dos de los cinco factores del score de confianza (doc 05 §7)
no discriminan nada hoy, y suman 0,25 de peso entre los dos.

- **`f_freshness` (peso 0,15)** devuelve el default 0,6 siempre, porque ningún
  aviso vigente tiene `published_at` y `_a_candidato` deja `days_published` en
  `None` (`retrieve.py:161`).
- **`f_completitud` (peso 0,10)** mide **los campos del sujeto**, y el sujeto
  casi nunca los trae.

**Cómo lo verifiqué:**

```sql
select count(*) total, count(condition) cond, count(orientation) ori,
       count(age_years) age, count(floor_number) piso
from core.subject_properties;
 total | cond | ori | age | piso
    56 |    3 |   2 |   1 |    2
```

```sql
select count(*) total, count(published_at) con_fecha from corpus.listings where active;
 total | con_fecha
  8497 |         0
```

```
$ uv run python docs/auditoria/sondas/s1_probe.py
F. Completitud: sujeto sin atributos -> 0.900 | con 4 atributos -> 1.000
   delta 0.100 = el peso entero
```

**Por qué importa:** el score de confianza es lo que decide si el informe sale
con advertencia y si el rango se ensancha 50%. De los cinco factores, dos
aportan una constante: 0,15×0,6 + 0,10×0,05 ≈ 0,095 fijo. La confianza que ve el
cliente la deciden en la práctica solo la cantidad de comparables y la
dispersión (0,65 de peso), con un piso constante de 0,095. La calibración del
backtest (ALTA 13,4% · MEDIA 20,6% · BAJA 66,2%) es monótona, así que **el
indicador sirve** — pero sirve por dos factores, no por cinco, y eso hay que
saberlo antes de tocar los pesos.

**Qué hay que hacer:**
1. `f_freshness`: hoy no hay de dónde. Usar `last_seen_at - first_seen_at` como
   proxy de "cuánto lleva publicado" —que sí está en la base— o dejarlo, pero
   **decirlo en el detalle del evento**: agregar a `report_events.detail` del
   nodo 7 los cinco factores por separado, no solo el score. Sin eso, nadie
   puede ver que dos están planchados.
2. `f_completitud`: es correcto que mire al sujeto (doc 05 §7 lo dice), pero
   hoy es una penalización fija porque el formulario de alta no pide esos
   campos. Es un problema de producto, no del motor: ver S6.
3. Documentar en doc 05 §7 cuáles factores están activos con los datos de hoy.

**Cómo se verifica que quedó bien:** después del cambio,

```sql
select detail->'factores' from core.report_events where node='adjust_and_value'
order by created_at desc limit 1;
```
tiene que devolver los cinco valores, y `count(*)` sobre 10 informes distintos
tiene que mostrar variación en `freshness`.

**Riesgo de tocarlo:** cambiar `f_freshness` mueve la confianza de todos los
informes y por lo tanto el ensanchado del rango. La calibración del backtest
(§3 de ESTADO) hay que volver a correrla: es el único chequeo de que el nivel de
confianza sigue significando algo.

---

### H-07 · Sin datos del sujeto, el motor lo trata como "muy bueno, lateral, planta baja"

**Sección:** S1 · **Severidad:** media

**Qué está mal:** `compute_adjustments` calcula `rel = coef_comp / coef_subj`.
Cuando el sujeto no declara un atributo, `coef_subj = 1,00`, que es el
coeficiente **de referencia**: `muy_bueno`, `lateral`, 6-15 años, sin piso alto.
O sea que la regla "sin dato, sin ajuste" se cumple del lado del comparable y
del lado del sujeto se convierte en "sin dato, se asume el promedio". Con 3 de
56 sujetos declarando estado, eso es lo que pasa **en casi todos los informes**.

**Cómo lo verifiqué:** la consulta de completitud de H-06 (3/56 · 2/56 · 1/56 ·
2/56), más la lectura de `adjustments.py:86-99`, más la sonda F: el sujeto
pelado y el sujeto completo dan el mismo `price_per_m2` y solo cambian de score.

**Por qué importa:** un departamento a estrenar y uno a refaccionar reciben hoy
**la misma valuación** si el usuario no cargó el estado, y la diferencia entre
los dos extremos de la tabla de doc 05 §4.1 es 1,15 / 0,82 = **40%**. El sistema
no está equivocado —hace lo único razonable con la información que tiene— pero
el informe no le dice al cliente que ese supuesto se tomó. Es un supuesto de
40% de amplitud, silencioso.

**Qué hay que hacer:** no cambiar la matemática. Hacerlo visible:
1. En `engine.py`, agregar a `v.notes` una línea por cada atributo del sujeto
   que quedó en `None` y para el que **algún comparable sí tenía dato** (si
   ninguno lo tenía, el coeficiente no se movió y no hay nada que declarar).
2. Que `prompts/writer/v1.jinja` esté obligado a mencionarlo cuando aparezca,
   igual que hace con la confianza baja.
3. En `web/src/app/informes/nuevo/page.tsx`, pedir estado y orientación como
   opcionales con un texto que diga qué se pierde si no se cargan (ver S6).

**Cómo se verifica que quedó bien:**

```python
def test_avisa_cuando_el_sujeto_no_declara_un_atributo_que_los_comparables_si():
    v = value(Property(surface_covered=D("70")),
              [comp(f"c{i}", "175000", "70", condition=Condition.A_ESTRENAR) for i in range(8)])
    assert any("estado" in n for n in v.notes)
```
Y sobre un informe real: `select methodology->'notes' from core.reports ...`
tiene que traer la nota.

**Riesgo de tocarlo:** no cambia ningún número. Puede disparar rechazos del
crítico si la nota entra al informe sin que el prompt del redactor la
contemple — por eso el punto 2 va junto con el 1, no después.

---

### H-08 · Las cifras publicadas no multiplican entre sí

**Sección:** S1 · **Severidad:** baja

**Qué está mal:** el informe publica `price_per_m2` redondeado al entero y
`weighted_surface` a un decimal, pero `value_mid` se calcula con los valores sin
redondear. El cliente que multiplica lo que ve no llega a lo que ve.

**Cómo lo verifiqué:**

```sql
select id, weighted_surface informada, price_per_m2, value_mid,
       round(value_mid/price_per_m2,4) superficie_implicita,
       value_mid - round(price_per_m2*weighted_surface) delta_usd
from core.reports where status='SUCCEEDED' and price_per_m2 > 0
order by abs(value_mid - round(price_per_m2*weighted_surface)) desc limit 3;

 47a5f323 | 60.0 | 4325.00 | 259470.00 | 59.9931 | -30.00
 99d5f005 | 60.0 | 3988.00 | 239251.00 | 59.9927 | -29.00
 e29fca61 | 60.0 | 3988.00 | 239251.00 | 59.9927 | -29.00
```

**Por qué importa:** USD 30 sobre 259.470 es 0,01% y no cambia ninguna decisión.
Importa por lo que el producto promete: el argumento de venta es *«salió de
estos avisos con estos ajustes»*, y la primera cuenta que hace cualquiera —m²
por USD/m²— no cierra. El crítico no lo marca porque su tolerancia es 1%
(`agents.yaml`, `numeric_tolerance_pct: 1.0`), así que el control que debería
verlo está calibrado justo por encima.

**Qué hay que hacer:** en `engine.py:193-194`, calcular `value_mid` a partir de
los valores **ya redondeados** que se publican, o publicar `weighted_surface`
con la precisión suficiente (2 decimales) y `price_per_m2` con 2 decimales. La
primera opción es la que hace que el informe cierre; cambia el valor en ±30 USD.

**Cómo se verifica que quedó bien:** la misma consulta de arriba tiene que dar
`delta_usd = 0` en todas las filas nuevas.

**Riesgo de tocarlo:** mueve `value_mid` unos pocos dólares en informes futuros
y por lo tanto rompe la comparación exacta contra corridas anteriores del
backtest (no el MdAPE, que se mueve en el cuarto decimal). `test_reproducibilidad`
y `test_caso_realista_belgrano` protegen la forma; ninguno fija el valor exacto.

---

### H-09 · `MIN_COMPARABLES` es una regla dura que una variable de entorno ablanda

**Sección:** S1 · **Severidad:** baja

**Qué está mal:** doc 05 §8 y doc 00 §2.2 presentan "menos de 5 comparables →
`INSUFFICIENT_DATA`" como la regla que hace al sistema presentable ante un
cliente. `settings.py:84` la define como `Field(default=5, ge=3)`, o sea que
`MIN_COMPARABLES=3` en el `.env` la baja sin que nada avise. ADR-002 tiene un
test que impide justamente ese tipo de puerta trasera
(`test_el_override_de_entorno_no_puede_saltear_el_adr_002`); esta regla no.

**Cómo lo verifiqué:** lectura de `settings.py:84` y de `valuation.py:226`
(`min_comparables=s.min_comparables`). No hay ningún test que fije el valor
efectivo:

```
$ uv run pytest tests -q -k "min_comparables"
no tests ran
```

**Por qué importa:** un informe con 3 comparables es exactamente lo que doc 05
§8 dice que el sistema no emite. La diferencia con ADR-002 es que ahí alguien se
tomó el trabajo de cerrar la puerta y acá no, y el motivo por el que importa es
el mismo.

**Qué hay que hacer:** o subir el piso del `Field` a `ge=5`, o —mejor— agregar
un test en `tests/test_valuation.py` que verifique que con la configuración del
repo `get_settings().min_comparables >= 5`, y que el `.env.example` no lo baje.

**Cómo se verifica que quedó bien:** `MIN_COMPARABLES=3 uv run pytest tests/test_valuation.py -q`
tiene que fallar.

**Riesgo de tocarlo:** ninguno. Es un test nuevo.

---

## 2. Qué NO pude verificar

- **Si los coeficientes son correctos.** No es auditable sin regresión hedónica;
  está declarado como supuesto en doc 05 §4.3 y en el informe al cliente, que es
  lo que corresponde. Queda como está.
- **El efecto real de H-01 sobre el MdAPE.** Requiere correr el backtest dos
  veces (300 casos), que son minutos de cómputo y no gasto de LLM. No lo corrí
  para no escribir en `eval.backtest_runs` sin autorización — es la primera
  medición que debería hacer la sesión 2.
- **El caso `test_caso_real_conocido` de doc 05 §10** ("una propiedad de
  la inmobiliaria con precio real"): no existe en `tests/test_valuation.py`. De los 9
  tests obligatorios de doc 05 §10 están los 8 restantes.

