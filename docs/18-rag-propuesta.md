# 18 — Propuesta: de recuperación estructurada a RAG completo

**Fecha:** 17/09/2026 · **Estado:** propuesta, nada implementado · **Rama:** `prep/publicacion`

> Este documento propone agregar al Tasador lo que un RAG "de manual" tiene y este
> sistema hoy no: embeddings, chunking, búsqueda híbrida, reranking, métricas de
> recuperación y una interfaz de preguntas con citas. Se hace sabiendo que la
> medición de agosto dijo que la similitud semántica no era el cuello de botella.
> **El objetivo declarado es doble: experiencia demostrable y una respuesta medida
> —no supuesta— a "¿aporta o no aporta?".** Si la respuesta vuelve a ser "no", el
> repo va a mostrar un RAG completo, evaluado, y apagado por configuración con los
> números al lado. Eso también es un resultado.

---

## 1. De dónde partimos (medido el 17/09, no recordado)

```
corpus.listings               93.544   (84.998 históricos BA Data, inactivos)
avisos vigentes                8.546   Portal A 8.248 · Portal B 298
  con descripción > 200 c      8.300   (97%)
  largo de descripción         p50 1.508 c · p90 2.921 c · p99 4.884 c · máx 10.252 c
corpus.listing_embeddings          0   Vector(1024) + HNSW coseno, migrado y vacío
corpus.listing_features       90.192
core.reports                      84   51 SUCCEEDED · 20 INSUFFICIENT_DATA · 8 FAILED
core.report_comparables        2.034   1.143 usados · 891 descartados · 462 avisos distintos
pgvector 0.8.6 · pg_trgm 1.6 · fastembed 0.8.0
```

Tres hechos de esa tabla definen la propuesta.

**1. Hoy, cuando sobran candidatos, elige la fecha.** El nodo 2 filtra y después
hace `ORDER BY last_seen_at DESC LIMIT 60`. En Palermo el primer escalón junta
entre 124 y 294 avisos con ficha (y 1.637 sin exigirla) para 60 lugares. *Cuáles*
60 entran lo decide la recencia, no el parecido con la propiedad. Ese es el hueco
real donde una señal semántica puede aportar: no en encontrar candidatos, sino en
**elegir cuáles ocupan el cupo**.

**2. Uno de cada seis lugares del cupo se desperdicia en algo que el texto
anunciaba.** De 2.034 comparables juzgados, **325 (16%) se descartaron por
`en_pozo_o_construccion`** y 36 por permuta o financiación. Cada uno ocupó un
lugar entre los 60, pagó su extracción por LLM y su turno con el juez, para
terminar afuera. Hipótesis medible: un ranking híbrido reduce esa fracción
*antes* de gastar en ella.

**3. El modelo que el diseño eligió no se puede cargar como el diseño decía.**
ADR-004 y `settings.py` dicen `BAAI/bge-m3` vía `fastembed`. Verificado contra la
librería instalada: **fastembed 0.8.0 no sirve bge-m3.** Los multilingües de 1024
dimensiones que sí sirve son `intfloat/multilingual-e5-large` y
`jinaai/jina-embeddings-v3`. Es el tipo de cosa que este proyecto ya aprendió a no
inferir: se prueba. Ver §3.1.

Y un cuarto, que es una oportunidad: **el texto libre del agente hoy no llega a
ningún lado** (`runner.py`: las notas no entran al grafo para que no viajen a un
proveedor de LLM). Un embedding **local** no tiene ese problema: las notas pueden
alimentar la consulta semántica sin salir de la máquina. 19 de los sujetos
cargados tienen notas. Es la primera vez que "balcón corrido, cocina a reciclar"
podría influir en qué comparables se eligen, sin romper la regla de privacidad de
doc 10 §3.

---

## 2. Qué se propone: tres capacidades

| | Capacidad | Dónde entra | Toca el precio |
|---|---|---|---|
| **R1** | **Recuperación híbrida de comparables**: filtro duro → denso + léxico → fusión | nodo 2 | indirectamente: cambia qué comparables llegan al motor |
| **R2** | **Reranking** con cross-encoder local sobre los finalistas | nodo 2 | ídem |
| **R3** | **"Preguntale al informe"**: preguntas en lenguaje natural sobre un informe, respondidas con citas a comparables y a la metodología, o rechazadas | API + ficha web | **no** — solo explica |

**Lo que NO cambia, y no se negocia:** ADR-002. El precio sigue saliendo del
nodo 7, determinístico. R1 y R2 deciden *qué avisos* se le presentan; nunca un
número. R3 no puede emitir una cifra que no esté en los datos del informe: reusa
la fase A del crítico (§5.3).

---

## 3. Decisiones de diseño — qué se elige, qué se descarta, y qué se mide

### 3.1 Modelo de embeddings → ADR-010

| Opción | Dim | Contexto | Licencia | Cómo se carga | Nota |
|---|---|---|---|---|---|
| **`intfloat/multilingual-e5-large`** | 1024 | **512 tokens** | MIT | fastembed (ONNX, CPU) | Pide prefijos `query:` / `passage:`. 2,2 GB |
| `jinaai/jina-embeddings-v3` | 1024 | 8.192 | **CC-BY-NC** | fastembed | No comercial: choca con "producto vendible" |
| `BAAI/bge-m3` | 1024 | 8.192 | MIT | sentence-transformers + torch | +2 GB de imagen y otra pila de inferencia |
| `paraphrase-multilingual-mpnet-base-v2` | 768 | 128-512 | Apache | fastembed | Obliga a migrar la columna; línea de base barata |

**Propuesta: e5-large.** Entra en el esquema ya migrado sin tocar la dimensión, es
MIT, corre con lo que ya está instalado y sin red. Su límite de 512 tokens **no es
un defecto para este plan: es lo que hace que el chunking sea una necesidad real y
no un adorno** (§3.2). bge-m3 queda como comparación en la Fase 3 si el tiempo da:
"mismo corpus, mismo eval, contexto largo sin chunking contra contexto corto con
chunking" es un experimento que vale la pena tener hecho.

Se corrige `settings.embedding_model` y ADR-004, que hoy prometen algo que no
carga.

### 3.2 Chunking → ADR-011

Con ~3,7 caracteres por token en castellano, la mediana de 1.508 caracteres son
~410 tokens y el p90 de 2.921 son ~790. **Estimado: entre un cuarto y un tercio de
los avisos no entra en 512 tokens.** La Fase 2 lo mide con el tokenizador real
antes de decidir nada; si diera menos del 5%, esta sección se achica y se dice.

Tres estrategias, las tres implementadas y comparadas en el mismo eval:

| | Estrategia | Por qué está |
|---|---|---|
| A | **Truncar a 512** | La línea de base honesta. Si A empata con C, el chunking no aportó |
| B | **Ventana fija** de 384 tokens, solape 64 | Lo que hace un tutorial. Corta frases por la mitad |
| C | **Por oraciones y párrafos**, objetivo 256-384 tokens, solape de una oración, **con encabezado estructurado** | La propuesta |

El encabezado de C es lo importante. Un fragmento que dice *"a reciclar, muy
luminoso, excelente ubicación"* no sabe de qué propiedad habla. Cada chunk se
embebe precedido de una línea armada con los campos estructurados:

```
passage: Departamento · 3 ambientes · 78 m² · Palermo · piso 4 · USD 2.650/m²
[texto del fragmento]
```

Es *contextual chunking* sin llamar a un LLM: el contexto ya lo tenemos, en
columnas.

**El aviso es el documento padre.** Se recuperan chunks, se devuelven avisos: el
puntaje de un aviso es el máximo de sus chunks (se compara contra el promedio en
el eval). Nunca se mezcla texto de dos avisos en una unidad, y el límite semántico
del dominio —un aviso es una propiedad— se respeta por construcción.

### 3.3 Dónde viven: `corpus.listing_chunks`

```sql
corpus.listing_chunks
  listing_id      uuid  → corpus.listings
  chunk_ix        smallint
  text            text             -- lo que se embebió, con encabezado
  token_count     smallint
  content_hash    char(64)         -- sha256 del texto: identidad del trabajo hecho
  chunker_version text             -- 'C-oraciones-v1'
  model           text             -- 'intfloat/multilingual-e5-large'
  embedding       vector(1024)
  tsv             tsvector GENERATED ALWAYS AS (to_tsvector('spanish', text)) STORED
  PRIMARY KEY (listing_id, chunk_ix, chunker_version, model)
```

Índices: GIN sobre `tsv`; HNSW coseno sobre `embedding`. La tabla vacía
`listing_embeddings` (un vector por aviso) se reemplaza en la misma migración.

**Por qué `model` y `chunker_version` están en la clave:** es la misma decisión que
separó `listings` (hecho) de `listing_features` (interpretación versionada). Un
embedding es una interpretación. Con la versión en la clave se pueden tener dos
modelos indexados a la vez y compararlos sin reindexar — que es justo lo que el
eval necesita.

### 3.4 Búsqueda: exacta sobre lo filtrado, y el HNSW medido aparte → ADR-012

El orden no cambia: **filtro duro primero**. Lo que cambia es el `ORDER BY`.

```
1. WHERE  barrio · USD · superficie ±30% · ambientes ±1 · visto < 120 d · canónico
          (la consulta de hoy, sin el LIMIT)            → ~120 a ~1.700 avisos
2. denso    coseno(consulta, chunk), máx por aviso      → ranking D
3. léxico   ts_rank_cd(tsv, consulta) en 'spanish'      → ranking L
4. fusión   RRF:  score = Σ 1/(60 + rank_i)             → top 60
5. (R2)     cross-encoder sobre los 60                  → orden final
```

Una decisión que hay que decir en voz alta porque contradice el reflejo: **a esta
escala el índice vectorial no hace falta.** Después del filtro quedan a lo sumo
~1.700 avisos (~4.000 chunks): la distancia exacta contra 4.000 vectores es
cuestión de milisegundos, tiene recall 100% y no sufre el problema conocido de
ANN + filtro (el índice devuelve los k más cercanos *del corpus entero* y el
filtro después deja menos de k). El HNSW se crea igual y se mide aparte —con
`hnsw.iterative_scan` de pgvector 0.8, que existe para ese caso— porque el
proyecto tiene una regla sobre esto: H-33 ya enseñó que un índice que "debería
ayudar" puede no usarse nunca. Se reporta: latencia y recall@60 de exacto contra
HNSW, con los parámetros reales de la escalera.

**Por qué híbrido y no solo denso:** el léxico agarra lo que el embedding
difumina —"cochera fija", "apto crédito", "Thames", "a estrenar"— y el denso
agarra lo que el léxico no ve —"para actualizar" ≈ "a reciclar" ≈ "necesita
refacción". RRF se elige sobre una suma ponderada porque no pide calibrar escalas
entre un coseno y un `ts_rank`: un hiperparámetro menos que ajustar contra un set
chico, que es donde este proyecto ya se quemó (17,6 pp de ruido).

**La consulta** se arma en `rag/queries.py` desde el sujeto: los campos
estructurados en el mismo formato del encabezado de los chunks, más las notas del
agente si existen. Se embebe localmente; las notas siguen sin salir de la máquina.

### 3.5 Reranking → dentro de ADR-012

Cross-encoder local sobre los 60 finalistas, vía `fastembed.TextCrossEncoder`.
Candidatos: `BAAI/bge-reranker-base` (MIT) y
`jinaai/jina-reranker-v2-base-multilingual` (mejor en castellano en los papeles,
**CC-BY-NC**: sirve para medir, no para vender). Se miden los dos; la licencia
entra en la decisión.

**La lección del commit V aplica entera acá.** Embeber y rerankear son CPU
síncrona, exactamente lo que dejó al worker "vivo y sordo" cuando WeasyPrint
bloqueó el event loop. Desde el primer commit: `asyncio.to_thread` (o un pool de
procesos), modelo cargado una vez por proceso, y un test que mida que el loop
sigue respondiendo mientras se embebe. No se descubre dos veces el mismo bug.

### 3.6 Caché

| Qué | Clave | Dónde |
|---|---|---|
| Embedding de un chunk | `content_hash` + `model` + `chunker_version` | la propia tabla: si la fila existe, no se recalcula |
| Embedding de una consulta | sha256 del texto de consulta + `model` | Redis, TTL 7 días |
| Reindexado | el sha256 del texto de un chunk no está en la tabla → se embebe ese chunk y nada más | `scripts/embed_corpus.py`, idempotente |

⚠️ **`listings.content_hash` NO sirve de disparador, y hay que saberlo antes de
colgarse de él.** Verificado en `ingest/core.py`: `CAMPOS_DEL_HASH` tiene
precio, superficie, ambientes, estado, dirección y fecha — **no la descripción**.
Es la lección del 14/08 ("el hash tiene que cubrir todo lo que se persiste") por
cuarta vez: un portal que reescribe el texto de un aviso se lee como "sin cambios".
Por eso la identidad del trabajo hecho es el sha256 **del texto del chunk**, igual
que el nodo 4 usa su propio `content_hash` de dirección + descripción. La Fase 2
decide, midiendo cuántos avisos cambiaron solo de texto entre tandas, si además
corresponde sumar `description` a `CAMPOS_DEL_HASH`.

---

## 4. Cómo se mide — y se mide ANTES de construir

Es la fase 1, no la última. El nodo 7 se construyó primero porque era lo único
medible sin gastar; acá vale lo mismo: **primero la vara, después lo que se va a
medir con ella.**

### 4.1 Juicios de relevancia que ya tenemos

`core.report_comparables` es un set de relevancia que nadie pensó como tal:
**2.034 juicios sobre 84 consultas reales** (cada informe es una consulta: un
sujeto y los avisos que se le presentaron), con motivo.

| Grado | Criterio | n |
|---|---|---|
| **2** — comparable | `included = true` | 1.143 |
| **1** — comparable marginal | `recorte_p5_p95`, `outlier_estadistico` | 162 |
| **0** — no comparable | pozo, permuta, tipología, promocional, inconsistente, vencido, duplicado, `ajuste_excede_el_tope` | 729 |

**El sesgo de ese set, dicho antes de que lo diga otro:** solo están juzgados los
avisos que el recuperador *actual* trajo. Un sistema nuevo va a traer avisos sin
juicio, y "sin juzgar" no es "irrelevante" (sesgo de *pooling*). Tres defensas:

1. Métricas sobre lista condensada (solo documentos juzgados) y **bpref**, que
   están diseñadas para juicios incompletos.
2. **Anotación humana dirigida:** para 30 consultas, el top-10 de cada sistema
   nuevo que no tenga juicio. Son ~150-300 avisos, con `/comparables` como
   herramienta —ya muestra el texto al lado de lo extraído—.
3. El juez del nodo 6 como anotador débil de los no juzgados, **marcado como tal**
   y nunca mezclado con los juicios humanos en la misma cifra.

### 4.2 Métricas

| Nivel | Métrica | Qué contesta |
|---|---|---|
| Recuperación | **nDCG@25**, Recall@60, MRR, bpref | ¿Los buenos quedan arriba? |
| Cupo | **% del top-60 que la curaduría descarta** (hoy ~16% solo por pozo) | ¿Se dejó de gastar en lo que se iba a tirar? |
| Informe | comparables usados, dispersión, confianza, `INSUFFICIENT_DATA` | ¿Llega mejor material al motor? |
| Valuación | MdAPE sobre `VIGENTES`, **3 semillas**, contra la amplitud de 1,83 pp ya medida | ¿Cambia el número? |
| Costo | ms por consulta, USD por informe, tiempo de indexado, MB de imagen | ¿Cuánto cuesta? |

### 4.3 La tabla de ablación que tiene que salir

```
                                   nDCG@25  Recall@60  bpref  %descartado  ms
A  SQL + recencia (hoy)
B  A + denso (truncar 512)
C  A + denso (chunks por oración + encabezado)
D  A + léxico (FTS spanish)
E  C + D fusionados con RRF
F  E + rerank (bge-reranker-base)
G  E + rerank (jina-v2-multilingual)
```

Con intervalo por *bootstrap* sobre las 84 consultas. Una diferencia que no supere
ese intervalo **no es una mejora y no se reporta como tal** — R6, la regla que ya
dio vuelta la hipótesis del nodo 4.

### 4.4 El criterio, escrito antes de medir

`use_embeddings: true` pasa a ser el default **solo si**: (a) E o F superan a A en
nDCG@25 por fuera del intervalo, (b) el % descartado baja, y (c) el MdAPE no
empeora por fuera del ruido entre semillas. Si no se cumple, queda implementado,
evaluado y apagado, con esta tabla en el README. Cualquiera de los dos finales es
publicable.

---

## 5. R3 — "Preguntale al informe"

El propietario pregunta *"¿por qué no usaron el de Thames?"* o *"¿por qué la
mediana y no el promedio?"*. Hoy la respuesta existe —está en
`report_comparables` y en doc 05— pero hay que saber dónde mirar. R3 es un RAG
clásico, chico y verificable, sobre un corpus que ya es nuestro.

### 5.1 Qué se indexa

| Fuente | Cómo se parte | Id de cita |
|---|---|---|
| Comparables del informe (usados y descartados, con ajustes y motivo) | un "hecho" por comparable, generado por plantilla desde la fila | `[C-07]` |
| La valuación y el contexto de mercado del informe | un hecho por bloque | `[V]`, `[M]` |
| `docs/05-metodologia-de-valuacion.md` | **por encabezado markdown**, con la ruta de títulos como prefijo del chunk | `[Met §4.2]` |

Acá el chunking por estructura sí es el de manual: un documento largo, con
secciones, donde partir por título conserva el sentido y partir cada 500
caracteres no.

### 5.2 Flujo

```
pregunta → embebido local → híbrido sobre (hechos del informe ∪ metodología)
        → top 8 → prompt con los fragmentos numerados
        → respuesta con citas obligatorias [C-07] [Met §4.2]
        → VERIFICACIÓN SIN LLM → respuesta | rechazo
```

### 5.3 La verificación, que es lo que lo hace de este proyecto

1. **Toda cita tiene que existir** entre los fragmentos que se le pasaron. Una cita
   a un id que no estaba en el contexto es rechazo. Schema con `citas: list[str]`
   **requerido** — la lección de `cita: str | None = None` ya está pagada.
2. **Toda cifra de la respuesta pasa por `cifras_no_trazables`**, la misma función
   de la fase A del crítico, contra los fragmentos recuperados. Se reusa, no se
   reescribe.
3. **Rechazo por falta de evidencia:** si el mejor puntaje de recuperación no
   supera un umbral —calibrado con las preguntas sin respuesta del golden set—, la
   respuesta es *"eso no está en este informe"*. Sin llamar al modelo.
4. El texto de los avisos viaja en `<contenido_externo>`, como en el nodo 4.

`POST /v1/reports/{id}/ask`, con `org_id` en el join (H-31), rate limit, y costo
registrado en `report_events`. En el link compartido del propietario queda
**apagado por defecto**: es una superficie nueva frente a un usuario sin sesión.

### 5.4 Eval de R3

Golden set de 30 preguntas sobre 5 informes: 20 con respuesta, 10 sin (*"¿cuánto
va a valer en dos años?"*, *"¿quién es el dueño del de Gorriti?"*). Métricas:
precisión de citas (determinística), exactitud de rechazo (determinística),
fidelidad con juez LLM — 3 corridas, mediana, como todo eval de componente acá.

---

## 6. Estructura propuesta

```
src/tasador/rag/
    __init__.py
    embedder.py        carga única del modelo, prefijos e5, lotes, fuera del event loop
    chunking.py        estrategias A, B y C; cada una con su `version`
    indexer.py         incremental por content_hash; reanudable lote a lote
    queries.py         sujeto (+ notas) → texto de consulta
    retriever.py       filtro SQL → denso + FTS → RRF
    rerank.py          cross-encoder, con tope de pares y de latencia
    qa.py              R3: recuperar, responder, verificar citas y cifras
src/tasador/eval/retrieval.py     qrels desde report_comparables, métricas, bootstrap
src/tasador/v1/ask.py             POST /v1/reports/{id}/ask
scripts/embed_corpus.py           --barrio --modelo --chunker --dry-run
scripts/eval_retrieval.py         --sistemas A,C,E,F --guardar
scripts/generar_corpus_demo.py    corpus sintético para el repo público (doc 19)
migrations/versions/…_listing_chunks.py
prompts/qa/v1.jinja
config/agents.yaml                retrieve_candidates.params.semantic: {…}
tests/
    test_chunking.py              límites de oración, solape, encabezado, tope de tokens
    test_retriever_hibrido.py     EJECUTA el SQL contra Postgres (lección del commit U)
    test_embedder_no_bloquea.py   el loop responde mientras se embebe (lección del V)
    test_eval_retrieval.py        métricas contra casos calculados a mano
    test_qa_citas.py              cita inexistente → rechazo; cifra huérfana → rechazo
    architecture/                 el nodo 7 sigue sin modelo; `rag/` no importa `valuation/`
docs/
    18-rag-propuesta.md           este documento
    adr/ADR-010 … ADR-013
    informes/…-rag-resultados.md  la tabla de §4.3, como salga
```

Todo lo configurable va a `agents.yaml`, no al código:

```yaml
- id: retrieve_candidates
  params:
    semantic:
      enabled: false                 # el default lo decide §4.4, no el entusiasmo
      model: intfloat/multilingual-e5-large
      chunker: C-oraciones-v1
      fusion: rrf
      rrf_k: 60
      usar_notas_del_agente: true    # local: no sale de la máquina
      rerank: {enabled: false, model: BAAI/bge-reranker-base, top_n: 60}
```

---

## 7. Fases

Cada fase termina con algo que corre y un número pegado. Esfuerzo en días de
trabajo concentrado, con el ritmo que tuvo el proyecto.

| # | Fase | Qué se hace | Se da por hecha cuando | Días |
|---|---|---|---|---|
| **0** | **Publicación** | Doc 19: sanear, decidir, subir | `ops/auditar_publicacion.py --historial` en verde sobre el repo que se sube | 1-1,5 |
| **1** | **La vara** | `eval/retrieval.py`, qrels desde `report_comparables`, métricas + bootstrap, **línea de base A medida** y guardada en `eval.component_runs` | Existe el número de A con su intervalo, y un test valida las métricas contra un caso hecho a mano | 1,5 |
| **2** | **Índice** | Medir tokens reales · migración `listing_chunks` · chunkers A/B/C · `embedder` fuera del loop · `embed_corpus.py` reanudable | 8.546 avisos indexados; tiempo y tamaño medidos; reindexar sin cambios escribe 0 filas | 1,5 |
| **3** | **Híbrido** | `retriever.py`, FTS, RRF, consulta desde el sujeto, flag en YAML, exacto contra HNSW | Filas B-E de la tabla; test que ejecuta el SQL; informe real de punta a punta con el flag encendido | 2 |
| **4** | **Rerank** | `rerank.py`, dos modelos, tope de latencia | Filas F-G; latencia por informe medida en el contenedor | 1 |
| **5** | **Veredicto** | Anotación dirigida del top-10 no juzgado · MdAPE con 3 semillas · decidir el default según §4.4 | Informe de resultados escrito, con la decisión y los números | 1,5 |
| **6** | **R3** | `qa.py`, endpoint, caja en la ficha, golden de 30 preguntas | Citas y rechazo al 100% en los checks determinísticos; fidelidad medida en 3 corridas | 2-3 |
| **7** | **Cierre** | ADRs 010-013, README con la tabla, corregir ADR-004 y `settings` | El README cuenta lo que se midió, no lo que se esperaba | 0,5 |

**Total: 11 a 13 días.** Las fases 1-5 son un bloque cerrado que ya justifica el
repo. R3 es independiente y se puede hacer antes si lo que más interesa mostrar es
"RAG con citas y rechazo".

---

## 8. Riesgos

| Riesgo | Qué se hace |
|---|---|
| **Que vuelva a dar "no aporta"** | Está previsto y es publicable (§4.4). El riesgo real es no poder distinguirlo del ruido: por eso bootstrap, 3 semillas y anotación dirigida |
| **Sesgo de pooling en los qrels** | §4.1: métricas condensadas, bpref, anotación humana del top no juzgado |
| **El modelo no entra en la imagen** | e5-large son 2,2 GB. Volumen `tasador_models` (ya existe) en vez de hornearlo en la imagen; se descarga en el primer arranque y el healthcheck lo espera |
| **CPU bloqueando el worker** | §3.5. Test desde el día uno |
| **Licencias NC** de los modelos Jina | Solo para comparar. El default tiene que ser MIT/Apache; queda escrito en ADR-010 |
| **El corpus real no se puede publicar** | Doc 19 §4: corpus sintético para el repo público. Los resultados se reportan sobre el real y se dice |
| **Que el RAG contamine el precio** | Test de arquitectura: `valuation/` no importa `rag/`; el nodo 7 sigue sin `task` |
| **Inflar la arquitectura** | Doc 00 §6 lo prohíbe y sigue vigente: nada de base vectorial dedicada, nada de framework de RAG. Son ~7 archivos sobre el Postgres que ya está |

---

## 9. Estado de implementación (17/09/2026, en curso)

| Fase | Estado | Dónde |
|---|---|---|
| 0 Publicación | ✅ | `ops/auditar_publicacion.py`, corpus demo, historial nuevo |
| 1 La vara | ✅ | `eval/retrieval.py` (nDCG, recall, MRR, bpref, bootstrap apareado) · `eval/juicios.py` (pooling juzgado por el pipeline) · `scripts/eval_retrieval.py` |
| 2 Índice | ✅ código · ⏳ indexando | `rag/chunking.py` (A/B/C + encabezado) · `rag/embedder.py` · `rag/indexer.py` · migración `listing_chunks` · `scripts/embed_corpus.py` |
| 3 Híbrido | ✅ código · ⏳ medición | `rag/retriever.py` · `rag/queries.py` · nodo 2 detrás de `semantic.enabled` |
| 4 Rerank | ✅ código · ⏳ medición | `rag/rerank.py` (bge-reranker-base MIT · jina-v2 CC-BY-NC) |
| 5 Veredicto | ⏳ | la tabla de §4.3, con la decisión de §4.4 |
| 6 R3 | ✅ código · ⏳ eval | `rag/qa.py` · `POST /v1/reports/{id}/ask` · caja en la ficha · `scripts/eval_qa.py` |
| 7 Cierre | ⏳ | ADR-010 a 013 escritos en doc 01 §4 |

### 9.1 Lo que la medición ya corrigió de esta propuesta

- **§3.1**: `bge-m3` no lo sirve `fastembed` 0.8.0. Y `multilingual-e5-large`,
  la alternativa propuesta, embebe a **0,4–0,8 pasajes por segundo en esta CPU**:
  indexar 17.500 chunks son entre 6 y 12 horas, y comparar tres chunkers deja de
  ser posible en una tarde. Se mide con `paraphrase-multilingual-MiniLM-L12-v2`
  (384 d, 128 tokens, ~13 pasajes/s, Apache) y el modelo queda en la clave del
  índice para poder comparar con e5-large sobre un subconjunto (ADR-010).
- **§3.3**: la columna `embedding` no tiene dimensión fija y no lleva HNSW:
  conviven modelos de 384 y 1024 d mientras se comparan, y la búsqueda es exacta
  sobre el pool filtrado (ADR-012). El HNSW vuelve como migración de tres líneas
  cuando se fije el modelo de producción.
- **§3.2, la estimación "entre un cuarto y un tercio no entra en 512 tokens"**:
  medido con el tokenizador real, sin truncar, sobre 8.514 avisos vigentes —
  **28,1% supera los 512 tokens** (p50 374 · p90 710 · p99 1.176 · máx 2.400).
  La estimación por caracteres estaba bien. La PRIMERA medición, en cambio, dio
  p90 = p99 = máx = 512: el tokenizador con el que `fastembed` embebe trunca, y
  contar con él responde "ninguno" por construcción. Se cuenta con una copia sin
  truncación.
- **§3.6**: `listings.content_hash` no cubre la descripción; la identidad del
  trabajo hecho es el hash del texto del chunk.
- **§4.1, los juicios de informes pasados**: son relativos al pool de aquel
  momento. La escalera cambió el 15/08 y el corpus creció: sobre 23 consultas,
  `juzgados@25` dio 0,27 y en 17 fue cero. Se pasó a **pooling juzgado por el
  propio pipeline** (nodos 4-7 sobre la unión del top-60 de los sistemas), con
  53 informes + 60 avisos del corpus como consultas (`eval/juicios.py`).
- **§3.5, el costo de contar tokens**: la primera versión del chunker re-tokenizaba
  el candidato entero en cada paso —O(n²)— y tardó 323 s solo en contar sobre
  8.500 avisos. Cada oración se cuenta una vez y el chunk es la suma.
- **§3.2 con 128 tokens de contexto**: el chunking deja de ser opcional. Con
  MiniLM el objetivo por chunk es 100 tokens (`chunk_objetivo`), y "truncar" (A)
  descarta la mayor parte del texto de casi todos los avisos. Es la comparación
  que la tabla de ablación tiene que mostrar.
