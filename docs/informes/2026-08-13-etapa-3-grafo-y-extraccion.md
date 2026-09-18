# Informe — Etapa 3, fases 0 a 3: el grafo y el nodo 4

**Fecha:** 13/08/2026 · **Alcance:** infraestructura del grafo + nodos 1, 2, 4, 6, 7, 9 y 10
**Gasto total de la sesión:** USD 0,60

> Los resultados se publican como salen. Un backtest que solo se muestra
> cuando da bien no es un backtest, es marketing (doc 09 §7). Lo mismo vale
> para una sesión de desarrollo.

---

## 1. Qué quedó funcionando

```
POST /v1/reports -> 202 QUEUED
  worker arq lo toma -> LangGraph con checkpointer en Postgres
  1 normalize_subject   OK      21 ms          geocodifica y resuelve barrio
  2 retrieve_candidates OK      89 ms          22 candidatos, 0 relajaciones
  4 extract_features    OK 144.133 ms  USD 0,0032   22/22 extraídos
  7 adjust_and_value    OK      13 ms          17 comparables, MEDIA
GET /v1/reports/{id} -> 200, progreso nodo a nodo
```

Cuatro nodos reales de once. Los otros siete corren como stubs **marcados como
tales en `core.report_events`**: un stub que devuelve datos inventados es la
forma más rápida de creer que el sistema funciona cuando no funciona.

```
ruff check           ✅ All checks passed
ruff format --check  ✅ 63 files
mypy --strict        ✅ 50 archivos
pytest               ✅ 172 tests   (eran 72)
```

---

## 2. El número que importa: el nodo 4 mueve el precio

Misma propiedad, mismos 22 comparables de Belgrano, misma matemática. Lo único
que cambia es cuántos coeficientes de ajuste están encendidos:

```
                        SIN FEATURES    CON FEATURES
valor medio (USD)          343.805         284.715      −17,2%
confianza                    MEDIA           MEDIA
comparables usados           19/22           17/22
```

**El motor de ajustes dejó de estar apagado.** De los 22 avisos, 8 aportaron
`condition`, 10 `floor_number`, 6 `orientation`, 5 `age_years` y 2
`has_elevator`. El resto no los declara y quedaron en null — "sin dato, sin
ajuste" (doc 05 §4.2).

Que el valor se mueva un 17% **no prueba que ahora sea correcto.** Prueba que
los coeficientes están operando sobre datos reales, que es lo que el backtest
de la Etapa 2 no pudo medir. La validación de si mejora el MdAPE necesita el
backtest, y el backtest necesita un dataset con esos atributos anotados. Eso
está pendiente y es lo próximo que hay que medir.

### 2.1 Por qué hacía falta: la dispersión de Belgrano

Los 22 comparables capturados de Portal B, mismo barrio, todos 3 ambientes,
superficies entre 86 y 123 m²:

```
USD/m²   1742 ─ 2063 ─ 2067 ─ 2348 ─ 2500 ─ 2506 ─ 2793 ─ 2867 ─ 3333 ─ 3469
         3619 ─ 3733 ─ 4052 ─ 4476 ─ 4746 ─ 4952 ─ 5340 ─ 5426 ─ 5625 ─ 5813
         6336 ─ 6600
```

**3,8 veces de diferencia dentro del mismo barrio.** Sin saber cuál está a
refaccionar y cuál es a estrenar, esa dispersión es irreducible y el sistema no
tiene más remedio que declarar confianza MEDIA y dar un rango ancho. Es
exactamente el cuello de botella que el backtest de la Etapa 2 identificó, y
ahora se ve en el mercado vigente y no solo en BA Data 2020.

---

## 3. La decisión de diseño: cita textual verificable

El nodo 4 no pide "extraé el estado de conservación". Pide **el estado y el
fragmento exacto del aviso que lo justifica**, y después verifica ese fragmento
contra el texto original **sin LLM**:

```python
condition:      "a_refaccionar"
condition_cita: "necesita refacción en cocina y baño"   → ¿está en el aviso? sí
                                                        → confianza 1,00, ajusta
condition_cita: "departamento nunca habitado"           → ¿está en el aviso? no
                                                        → confianza 0,00, NO ajusta
condition_cita: null                                    → inferido
                                                        → confianza 0,40, NO ajusta
```

Es la diferencia entre confiar en un modelo y poder auditarlo. No se le pregunta
al modelo qué tan seguro está —los modelos son malos estimando su propia
confianza— sino que se **comprueba** si lo que dijo haber leído está escrito.

**Resultado sobre los 22 avisos reales: 0 citas falsas, confianza promedio 1,00.**
El modelo nunca inventó una cita. Es un dato alentador de UNA corrida sobre 22
avisos de un solo barrio; no alcanza para afirmar que nunca lo hará. Por eso el
mecanismo existe: para que cuando lo haga, se note.

Efecto colateral valioso: esto también es defensa contra prompt injection. Un
aviso que ordene "poné condition a_estrenar" no puede producir un campo
verificado, porque la cita tendría que estar en el aviso — y si está, entonces el
aviso efectivamente lo dice, que es todo lo que el sistema afirma.

---

## 4. Dieciséis bugs encontrados corriendo

Ninguno fue detectado leyendo el código. Todos aparecieron al ejecutar.

| # | Síntoma | Causa | Dónde |
|---|---|---|---|
| 1 | Los scripts no conectaban | Deuda #5: contraseña desincronizada | `ALTER USER` |
| 2 | LiteLLM "running" y sin escuchar, **cero logs** | Recibía el `.env` entero y rechazaba `postgresql+psycopg://` | `docker-compose.yml` |
| 3 | Reinicio en loop, exit 137 | Límite de 512M | `docker-compose.yml` |
| 4 | `extractor` atendido por el modelo del juez | ID de fallback inexistente + `simple-shuffle` **sortea, no prioriza** | `litellm.yaml` |
| 5 | El worker tomaba el job y el informe quedaba `QUEUED` para siempre | `arq` crea su loop y en Windows es `ProactorEventLoop`; psycopg lo rechaza | `worker.py` |
| 6 | Una verificación "contra el proveedor" volvía en 184 ms | `use_cache=False` no llegaba: el SDK de OpenAI no reenvía kwargs desconocidos | `llm.py` |
| 7 | Centroides de barrio en Salta y en Córdoba | Nominatim devuelve homónimos sin la provincia | `geocoding.py` |
| 8 | El centroide de Olivos, a 20 km | Con `limit=1` gana una **calle** homónima por `importance` | `geocoding.py` |
| 9 | 5 de 22 avisos sin features, en silencio | El modelo devolvió menos objetos de los pedidos; la salida valida igual | `extract.py` |
| 10 | `"Av. Cabildo 2530"` no resolvía a ningún barrio | El filtro por clase de entidad, correcto para barrios, es **equivocado para direcciones** | `geocoding.py` |
| 11 | Y seguía sin resolver después de arreglarlo | El negativo estaba cacheado: era culpa nuestra, no de OSM | `geocoding.py` |

Los tres que más vale registrar:

**El #4 es el más peligroso de todos**, porque no rompía nada. El sistema
funcionaba: devolvía respuestas correctas, a un costo y una latencia distintos de
los declarados. Se descubrió porque la contabilidad registra
`x-litellm-attempted-fallbacks` y el modelo que **realmente** atendió, no el que
se pidió.

**El #8 tiene una vuelta de tuerca incómoda.** Al agregar el filtro por
provincia, el centroide "bueno" de Olivos que teníamos antes resultó ser **la
Clínica Olivos** — un hospital que casualmente cae en la zona correcta. El dato
estaba bien por casualidad. Hicieron falta tres capas para arreglarlo de verdad:
provincia en la consulta, `featureType=settlement` con filtro por clase de
entidad OSM, y una caja del AMBA. Ninguna alcanza sola.

**El #10 es la contracara del #8 y muestra que el criterio se invierte.** Ese
mismo filtro por clase de entidad, que era la solución para los barrios,
rechazaba direcciones normales: el único registro de OSM para "Avenida Cabildo
2530" es un local de comidas rápidas (`class=amenity`) — pero **está en Avenida
Cabildo 2530**, y sus coordenadas y su `address` lo confirman.

La lección es que la verificación depende de qué se preguntó:

| Se busca | La verificación correcta es | Porque |
|---|---|---|
| un **barrio** | el TIPO de entidad | un growshop llamado "Olivos" no es el barrio Olivos |
| una **dirección** | la CALLE y la ALTURA | un comercio en Cabildo 2530 sí está en Cabildo 2530 |

Y el #11 es su corolario: el caché guardaba el "no encontré" del #10, así que
arreglar la lógica no alcanzaba. Los negativos ahora llevan la versión de la
lógica de selección y caducan cuando esa lógica cambia. Los positivos no
caducan nunca: una coordenada no se mueve.

---

## 5. Correcciones al diseño escrito

### 5.1 El SQL del nodo 2 devolvía cero (doc 04)

El diseño hacía `JOIN corpus.listing_features`. Medido:

```
  source   | listings | con_features | activos
-----------+----------+--------------+---------
 BADATA    |    84998 |        84998 |       0
 PORTAL_B  |       24 |            0 |      24
```

Los avisos vigentes tienen **cero** features, porque las produce el nodo 4 — que
corre *después* del 2. Con un INNER JOIN, todo informe sobre mercado vigente
devuelve cero candidatos, hoy y siempre. Era un huevo-gallina en el diseño.

**Corregido:** el JOIN es LEFT. Lo que haya de features enriquece; lo que falte
no excluye. El filtro duro por atributos vive en el nodo 6, que es donde doc 04
ya lo tenía y donde las features ya existen.

### 5.2 La escalera de relajación no tenía datos (doc 04)

Los escalones `limítrofes` y `comuna` requieren saber qué barrio está cerca de
cuál. Medido: **0 de 59 barrios con centroide, 0 con `parent_id`, 0 avisos con
coordenadas.**

La opción fácil era escribir a mano la tabla de adyacencia. Se descartó: sería un
dato inventado de memoria e imposible de verificar. Se geocodificaron los 59
centroides con Nominatim y "cercano" pasó a ser distancia real — que además es
mejor semántica, porque dos barrios pueden lindar por una punta y tener sus
centros a 4 km.

Validado con números, no a ojo:

```
BARRIOS QUE SE TOCAN            EXTREMOS DE CABA
Belgrano - Núñez      1.847 m   Núñez - Villa Riachuelo   16.241 m
Belgrano - Colegiales 1.519 m   Puerto Madero - Liniers   14.881 m
Palermo - Recoleta    3.116 m   Belgrano - Villa Lugano   12.959 m

Barrio de CABA más lejos del centro geográfico: La Boca, 9,7 km
(CABA mide ~19 km de punta a punta)
```

### 5.3 La penalización de confianza se cobraba de más

`relaxation_steps` reportaba el último escalón *intentado*, no el que produjo el
set devuelto. En Belgrano los 5 escalones daban los mismos 22 avisos —no hay más
en la base— y el informe salía con la penalización máxima. Corregido: la
penalización refleja la relajación que **sirvió**. Efecto medido: confianza BAJA
→ MEDIA, rango de ±45% → ±30%, **sin que cambie el valor medio**.

---

## 6. Costo y latencia medidos

| Escenario | Latencia | Costo |
|---|---|---|
| Corpus frío (22 avisos a extraer) | 145 s | USD 0,0032 |
| Corpus caliente (features ya extraídas) | **4,6 s** | **USD 0,000000** |

El caché por aviso es lo que hace viable el producto: el segundo informe del
mismo barrio no paga extracción. Doc 04 §4 estimaba ~USD 0,03 para ese caso; el
número real, con los nodos 4 y 7 activos, es cero.

La latencia en frío bajó de 253 s a 145 s al paralelizar los lotes, y el batch
pasó de 10 a 6 avisos porque con 10 la salida se pasaba de `max_tokens` y se
perdía el lote entero.

### 6.1 El juez es 32× más lento que el extractor

Medido con `check_models.py --structured`, misma extracción trivial, mismo schema:

```
deepseek-v4-flash     1.566 ms      31 tok salida   USD 0,000013
z-ai/glm-4.6         44.869 ms   1.180 tok salida   USD 0,002080
```

Los dos aciertan. GLM-4.6 es de razonamiento: piensa antes de responder y lo
cobra en tokens y en latencia. 45 s por llamada no entra en el presupuesto de
doc 09 §4 (p95 ≤ 180 s por informe **completo**): el nodo 5 solo, con sus 15
pares dudosos, se lo comería once veces.

**Decidido:** el juez de línea es v4-flash; GLM-4.6 queda como `judge_deep` para
la escalada de casos genuinamente ambiguos, que es la capa 3 de la cascada de
doc 04. Cuál gana en precisión lo decide el golden set en la Fase 4.

---

## 7. Lo que NO está hecho y hay que decirlo

- **Nodos 5, 6, 8, 9, 10 y 11**: stubs. El informe todavía no tiene narrativa,
  ni deduplicación, ni curaduría, ni crítico.
- **Sin golden set.** Doc 09 §3.3 pide 60 avisos anotados a mano para medir la
  exactitud de la extracción por campo. Hoy la única evidencia de que el nodo 4
  extrae bien es que las citas verifican — que prueba que **no inventa**, no que
  **acierta**. Son cosas distintas y solo la segunda se mide con anotación humana.
- **Sin backtest del nodo 4.** No se puede afirmar que mejora el MdAPE hasta
  correrlo, y el dataset de BA Data no tiene los atributos que el nodo extrae.
- **Cobertura: 1 barrio de 6.** Los 22 comparables son todos de Belgrano.
- **Autenticación.** El tenant se resuelve por header `X-Org-Slug`; la auth real
  es Etapa 4.
- **Embeddings.** El nodo 2 usa filtros duros; el kNN con pgvector es Fase 5.

---

## 8. El gasto, tomado de la base y no de una estimación

```sql
select round(sum(cost_usd)::numeric, 6), count(distinct report_id), count(*)
  from core.report_events;
--   0.019759 USD   |   10 informes   |   82 eventos
```

Más ~USD 0,009 de las corridas de `check_models.py --structured`, que no pasan
por el grafo. **Total: USD 0,028.**

Que este número salga de una consulta y no de una planilla es el punto de que
`report_events` exista: el costo que ve el usuario en el desglose es el mismo
que se pagó, sumado de las mismas filas.

---

## 9. El golden set: lo que el nodo 4 realmente acierta

Se anotaron a mano los 24 avisos de Belgrano (`tests/golden/extraccion.yaml`) y
se midió con `scripts/eval_extraccion.py`. **El resultado corrige la impresión
optimista de la §3.**

```
CAMPO              N  ACIERTO  ALUCIN  OMITE  ERRA
condition         21     71%       0      6     0
floor_number      17     71%       0      5     0
orientation       11     18%       0      9     0
age_years         10    100%       0      0     0
parking_spaces    10     60%       0      4     0
has_elevator       3    100%       0      0     0
credit_eligible    2    100%       0      0     0
---------------------------------------------------
TOTAL             74     68%       0     24     0
```

**68%, contra el objetivo de ≥ 92% de doc 09 §3.3.** El nodo 4 NO está listo, y
sin el golden set lo habría dado por bueno: las citas verificaban, no había
alucinaciones, el pipeline corría. Todo cierto y todo insuficiente.

### 9.1 El error es de un solo tipo, y es el menos grave

**Cero alucinaciones y cero valores equivocados en las seis corridas.** Los 24
fallos son omisiones: el aviso lo dice y el modelo lo deja en null. Eso importa
porque las consecuencias son distintas — una omisión apaga un coeficiente
(pierde precisión), una alucinación lo enciende con un dato inventado (miente).
El mecanismo de la cita está haciendo exactamente lo que se diseñó.

`orientation` al 18% es el peor y es donde hay que trabajar.

### 9.2 Cinco hipótesis probadas, cuatro falsas

| Hipótesis | Resultado |
|---|---|
| El lote de 6 es muy grande | **Falsa.** Con lote de 2 bajó a 69%; con lote de 1, a 41% |
| Un modelo instruct puro es mejor | **Falsa.** qwen3-30b: estable pero 54%, y 2 alucinaciones |
| El razonamiento de v4-flash lo rompe | **Parcial.** Apagarlo bajó los tokens de ~5.000 a ~150, pero la precisión cayó a 59% |
| El problema es el prompt | **Cierta.** v1 → v2: `floor_number` 29% → 71%, global 41% → 68% |
| El eval mide el sistema | **Falsa, y era mi bug** — ver abajo |

La cuarta es la que valió: **v1 estaba sobre-corregido hacia la cautela.**
Machacaba con "si no está, es null" y el modelo dejaba vacío lo que el aviso sí
decía. v2 agrega que **el error es simétrico** —omitir cuesta lo mismo que
inventar— y una lista de los patrones concretos que se estaban perdiendo.

### 9.3 Dos errores míos que el golden set destapó

**El eval no medía el sistema.** Llamaba a `structured()` pelado, sin el
reintento de omitidos que el nodo sí tiene. Medía un fragmento y no avisaba: la
cobertura aparente saltaba entre 51 y 74 avisos según la corrida, y yo leía eso
como varianza del modelo. Ahora nodo y eval comparten `extraer_lotes()`, una
sola implementación.

**El golden set estaba mal anotado.** Puse `parking_spaces: 0` en un aviso que
no menciona cochera, violando la convención del propio archivo (si no está, es
null). El modelo respondía `null` —correcto— y se le contaba como omisión.
**Un golden set mal anotado miente con la misma cara que un modelo.**

### 9.4 Qué sigue con esto

1. `orientation` al 18%: es un solo campo y arrastra el promedio.
2. Correr la evaluación N veces para separar señal de varianza. Los números de
   arriba son **una corrida**; no alcanzan para declarar una mejora chica.
3. Que Ricardo revise la anotación. Está hecha por quien escribió el extractor,
   leyendo el mismo texto: no es ground truth independiente y está declarado.

---

## 10. Los nodos 6, 9 y 10: el informe completo

### 10.1 El nodo 6 hizo lo que tenía que hacer

Medido contra los descartes anotados en el golden set:

```
                    descartó  no descartó
debía descartar            6            0
debía quedarse             0           18

Recall 100% · Precisión 100%   (doc 09 §3.3 pide recall > 90%)
```

Y el efecto sobre el número, con los 22 comparables reales de Belgrano:

```
                        SIN NODO 6      CON NODO 6
valor medio (USD)          284.715         283.385     −0,5%
techo del rango            429.400         373.326    −13,1%
comparables usados           17/22           12/22
```

**Recortó la cola cara sin correr el centro**, que es exactamente lo que debía
pasar: los 5 descartes por `en_pozo_o_construccion` eran emprendimientos cuyo
precio incluye plazo de obra, no usados terminados.

**La verificación de cita evitó un falso positivo.** El juez intentó descartar
el aviso de Av. Monroe citando *"quartier bajo belgrano desarrollo de
Argencons"* — un texto que **reconstruyó, no copió**. No se aplicó. Ese aviso
estaba anotado en el golden set como *no descartar*.

### 10.2 El crítico encontró un bug que 175 tests no vieron

Primera corrida del informe completo, rechazo en fase B:

> *"El rango de cierre esperado es 240.877-269.216 USD, pero el mínimo de
> publicación sugerido es 239.899 USD. Nadie publica por debajo de lo que
> espera cerrar."*

Tenía razón en que **se lee mal**. La causa: `closing_*` se ancla en el valor
medio (doc 05 §6.1) mientras que `value_low` se ensancha por incertidumbre
(±20%), y cuando esa banda supera al descuento de cierre (5-15%), el piso de
publicación queda por debajo del piso de cierre.

**Se intentó arreglarlo en el motor y se revirtió.** Anclar el cierre en el
rango rompía el invariante que sí importa —el cierre esperado siempre por
debajo del precio sugerido— y lo detectó `test_caso_realista_belgrano`, que ya
existía. Van tres veces en este proyecto que el test tenía razón y el código
estaba bien.

El defecto real estaba en el **informe**, que presentaba dos rangos como si
fueran comparables. La corrección vive en `prompts/writer/v1.jinja`, y quedaron
dos tests nuevos que fijan cuál es el invariante y cuál es la consecuencia
esperada, para que nadie —yo incluido— lo "arregle" de nuevo.

### 10.3 Después hubo que calibrar al crítico para abajo

Con la corrección puesta, el crítico siguió rechazando. Uno de sus hallazgos
`alta`: que el informe *"sugiere plural"* al escribir **«1 incluía condiciones
especiales»**. El texto dice 1.

Rechazar de más tiene un costo asimétrico y concreto: **al tercer rechazo el
propietario recibe el informe sin ninguna narrativa.** El prompt ahora enumera
las cuatro cosas que son `alta` y dice explícitamente que un matiz de redacción
nunca lo es, con el costo del rechazo escrito.

### 10.4 El último defecto era mío, no del modelo

El informe aprobado tenía un párrafo que se contradecía solo, tratando de
reconciliar dos conteos de descartados. **Y el redactor tenía razón en
confundirse:** yo le estaba pasando `descartados: 10` y
`descartados_por_criterio: 6` sin decir cómo se relacionaban.

Con los descartes desglosados por etapa y la suma explícita, el informe salió
**aprobado al primer intento**, con esta sección:

> Descartamos 10 avisos en total. Por criterio profesional (curaduría), se
> eliminaron 6: 5 corresponden a unidades en pozo o en construcción —su precio
> incluye el plazo de obra y no refleja el valor de una propiedad lista para
> habitar—, y 1 ofrecía permuta o financiación especial. Por razones
> estadísticas, se descartaron otros 4.

**Si los datos son ambiguos, la prosa sale ambigua.** El problema no era del
modelo.

### 10.5 Costo y latencia del informe completo

```
informe aprobado al primer intento    43 s    USD 0,042
informe con 2 rechazos y 3 reescrituras  87 s    USD 0,081
```

El crítico (claude-sonnet-4.5) es el 85% del costo. Es caro a propósito: es lo
que garantiza que no salga una cifra inventada. Doc 04 §4 estimaba USD 0,044
por informe; el número real con corpus caliente es **USD 0,042**.

---

## 11. Los nodos 5, 8 y 11: el pipeline completo

Con estos tres, **los 11 nodos del grafo están construidos**. El único apagado
es el 3 (captura on-demand), y a propósito.

```
1 normalize_subject    OK      24 ms      8 market_context   OK     137 ms
2 retrieve_candidates  OK      82 ms      9 write_report     OK     122 ms
4 extract_features     OK   1.202 ms     10 critic           OK     123 ms
5 dedup_cluster        OK     211 ms     11 render_pdf       OK   3.539 ms
6 curate               OK     150 ms
7 adjust_and_value     OK      28 ms     6,0 s total  ·  USD 0,018  ·  PDF 24.903 bytes
```

### 11.1 Nodo 5: no fusiono dos unidades del mismo edificio

231 pares evaluados deterministicamente, **solo 4 llegaron al juez**
(USD 0,001). Cero clusters, y es correcto: los dos avisos de Zabala son del
mismo edificio *Zeta Belgrano* pero **unidades distintas** (93 vs 105 m2, otra
orientacion). Es el caso borde que doc 04 marca como el mas confundible.

El criterio ante la duda es explicito y asimetrico: `no_se` se trata como
"distinto". Fusionar dos inmuebles que no lo son **borra un comparable
legitimo** y puede dejar el informe sin datos; no fusionar dos que si lo son
deja uno de mas, que la mediana diluye.

### 11.2 Nodo 8: por que CrewAI quedo como opcion y no como default

Doc 04 seccion 8 plantea una crew con herramientas que consultan la base. Se
implemento distinto, por dos razones medidas:

**Las cuatro consultas son SQL deterministico y corren ANTES.** Dejar que un
modelo decida si llama o no a una herramienta agrega latencia, costo y
no-determinismo para conseguir datos que *siempre* queremos. Lo abierto
—interpretar esos numeros— es lo que queda para los agentes.

**CrewAI rompe la contabilidad de costo.** Construye sus propios clientes HTTP
y no acepta que se le inyecte el nuestro: pide un `httpx.Client` y un
`httpx.AsyncClient` con el mismo parametro `client_params`, asi que no hay
forma de cubrir los dos. Sin nuestro cliente no vemos los headers de LiteLLM, y
el nodo 8 seria **el unico del grafo cuyo gasto no aparece en
`report_events`** — el costo por informe dejaria de ser una suma para volver a
ser una estimacion.

Con las herramientas ya movidas a SQL, la "crew" son dos llamadas encadenadas:
un analista que lee los numeros y un redactor que los cuenta. Eso son 15 lineas
con el cliente propio, **con contabilidad completa**. Por eso el default es
`engine: secuencial` y CrewAI queda disponible en `agents.yaml`: si manana la
crew crece a un trabajo con delegacion real entre roles, el trade-off puede
darse vuelta, y esa decision es del que opera.

El contexto que produce cita solo lo medido, y su +7,1% reproduce exactamente
el chequeo de sesgo de la Etapa 1 calculado por otro camino.

### 11.3 Nodo 11: tres bugs que solo existen en el contenedor

WeasyPrint no corre en Windows —falta `libgobject-2.0-0`— asi que el nodo esta
partido: `informe_html()` es pura y testeable en cualquier lado, y
`html_a_pdf()` es la que necesita GTK. **Verificarlo dentro del contenedor
destapo tres cosas que en Windows parecian andar:**

| # | Bug | Consecuencia si no se probaba en el contenedor |
|---|---|---|
| 12 | `templates/` no estaba en NINGUNA de las dos imagenes | El nodo 11 fallaba en produccion con `TemplateNotFound` |
| 13 | El cache de geocodificacion escribia en `/app`, que es **solo lectura** | `PermissionError` en el nodo 1: el informe no arrancaba |
| 14 | Sin `XDG_CACHE_HOME`, fontconfig no tenia donde cachear | Re-escaneo de fuentes en cada PDF |

El 13 se resolvio centralizando donde se escribe: `settings.data_path`
autodetecta el volumen montado. La misma suposicion estaba duplicada en dos
lugares y ahora esta en uno.

Y dos que encontraron los tests antes que un informe:

| # | Bug | |
|---|---|---|
| 15 | `"Luis Maria Campos 1.400"` no colisionaba con `"1400"` | El punto de miles partia el numero y dos avisos de la misma direccion se veian distintos |
| 16 | `MarkdownIt("commonmark")` deja pasar **HTML crudo** | Un `<script>` en el markdown llegaba entero al PDF |

### 11.4 El PDF

25 KB, con la advertencia de confianza baja como recuadro visible en la primera
pagina (doc 05 seccion 7), la tabla de comparables **incluidos los descartados
con su motivo**, y la trazabilidad impresa al pie: las tres versiones y el id
del informe. `core.report_artifacts` guarda el SHA-256, y
`GET /v1/reports/{id}/pdf` sirve el archivo guardado — **no lo regenera**: un
informe entregado en septiembre tiene que ser byte por byte el mismo en
diciembre.

---

## 12. CrewAI encendido: qué cuesta y donde se justifica

Se encendio `engine: crewai` en el nodo 8. Comparacion directa, mismo trabajo,
mismos modelos, mismos datos:

| motor | llamadas | tok in | tok out | latencia | costo |
|---|---|---|---|---|---|
| **crewai** | **4** | 1.814 | 1.062 | 8,6 s | USD 0,000551 |
| **secuencial** | **2** | 836 | 1.818 | 1,0 s | USD 0,000626 |

El doble de llamadas y 2,2x los tokens de entrada: ese delta es el andamiaje
—prompts de rol, backstory, coordinacion entre tareas. Sale levemente mas
barato porque produce menos salida.

### 12.1 Como se mide una libreria que no deja ver sus respuestas

CrewAI no acepta que se le inyecte el cliente HTTP, asi que no llegan los
headers de LiteLLM con el costo. La solucion no fue estimar sino **declarar los
precios en el gateway**: `model_info.input_cost_per_token` en `litellm.yaml`
para las 8 tareas, leidos desde `/model/info`. El costo de la crew se calcula
con sus propios `usage_metrics` y esos precios.

Sigue habiendo una diferencia real y hay que decirla: es **exacto pero
agregado**. No hay desglose por llamada como en el resto del grafo.

Efecto colateral util: ahora LiteLLM conoce el precio de todos los modelos, no
solo de los que trae en su tabla interna.

### 12.2 Cuatro bugs mas, todos del entorno

| # | Bug | |
|---|---|---|
| 17 | CrewAI escribia su almacen en `$HOME` | `PermissionError` en el contenedor. **La palanca NO es `CREWAI_STORAGE_DIR`**: pese al nombre es el NOMBRE de la app, no la ruta (`crewai_core/paths.py`). La ruta la resuelve `appdirs` via `XDG_DATA_HOME` |
| 18 | Su telemetria escribe un token de auth en `$HOME` y manda trazas afuera | Apagada con `CREWAI_DISABLE_TELEMETRY`. **No es solo el permiso**: este sistema procesa direcciones y datos de clientes, y doc 10 seccion 3 fija que sale y hacia donde. La telemetria de una libreria no esta en esa lista |
| 19 | Aun con telemetria apagada seguia tocando `$HOME` | `HOME=/tmp/home` en la imagen y en compose |
| 20 | El nodo degradaba **sin decir por que** | El motivo iba al contexto y no al detalle del evento. `con_narrativa: false` y ni una pista, que es lo contrario de para que existe `report_events` |

El 20 vale mas que los otros tres: los tres primeros se arreglan una vez, pero
un nodo que degrada en silencio deja la traza inservible justo cuando mas se la
necesita.

Y uno mio, fuera del codigo: un script de parcheo imprimio "crewai encendido"
**sin verificar que el reemplazo hubiera ocurrido**. No ocurrio, y corri un
informe entero creyendo que medía CrewAI cuando medía el motor secuencial. Es
exactamente el no-op silencioso que el codigo evita por diseno.

---

## 13. Reproducirlo

```powershell
.\make.ps1 dev
uv run python scripts/seed_org.py
uv run python scripts/geocode_neighborhoods.py
uv run python scripts/check_models.py --structured
uv run python scripts/run_report.py --direccion "Av. Cabildo 2530, Belgrano" --amb 3 --m2 95
```
