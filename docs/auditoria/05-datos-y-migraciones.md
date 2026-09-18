# S5 — Modelo de datos y migraciones

**Alcance:** `src/tasador/db/models.py` (20 tablas), `migrations/`.
**Método:** correr las migraciones desde cero en una base descartable y comparar
el esquema resultante contra la base real, columna por columna.

---

## 1. Lo que está bien, y es lo más importante de esta sección

**Las migraciones corren desde cero y producen exactamente el esquema de la base
real.** Creé una base descartable, corrí `ops/sql/00-extensions.sql` y
`alembic upgrade head`, y comparé:

```
$ docs/auditoria/sondas/s5_migraciones.ps1
=== 3. alembic upgrade head ===
  Running upgrade  -> 1e4a025d6104, corpus inicial
  Running upgrade 1e4a025d6104 -> cf54ac6bc9a5, informe: usuarios, api keys, subject, reports…
  Running upgrade cf54ac6bc9a5 -> a1c7f2e40d18, eval.backtest_runs
  Running upgrade a1c7f2e40d18 -> c93e1b7d4a52, eval.component_runs

=== 6. comparar contra la base REAL ===
  tablas en la base real : 20
  tablas desde cero      : 20
  identicas

=== 7. columnas que difieren entre las dos bases ===
  identicas
```

Cero deriva de tablas y columnas. Los CHECK están todos y son sensatos: el
vocabulario cerrado de `source`, `operation`, `currency`, `condition`,
`orientation`, `property_type`, `rooms between 1 and 20`, `price > 0`, y el que
más vale — `manual_tiene_org`, que hace imposible un aviso manual sin dueño.

No encontré ningún CHECK que la aplicación viole ni ninguno que impida un estado
legítimo.

---

### H-32 · `alembic check` falla siempre: el índice HNSW existe en la migración y no en el modelo

**Sección:** S5 · **Severidad:** media

**Qué está mal:** la migración crea `idx_listing_embeddings_hnsw` y
`db/models.py` no lo declara. `alembic check` lo detecta como un índice a
eliminar, y falla. Pasa igual sobre la base real y sobre una recién migrada.

**Cómo lo verifiqué:** sobre la base real:

```
$ uv run alembic check
Detected removed index 'idx_listing_embeddings_hnsw' on 'listing_embeddings'
FAILED: New upgrade operations detected: [('remove_index', Index('idx_listing_embeddings_hnsw', ...))]
```

Y sobre una base creada desde cero con `alembic upgrade head`, en el mismo paso
que corre el CI:

```
=== 4. alembic check (el paso del CI) ===
FAILED: New upgrade operations detected: [('remove_index', Index('idx_listing_embeddings_hnsw', ...))]
```

**Por qué importa:** el job `Migraciones desde cero` del CI corre
`alembic upgrade head && alembic check`. **Ese paso no puede pasar.** Junto con
el gate de cobertura (S11), son dos pasos del CI que están rotos de fábrica — y
nadie lo sabe porque el CI nunca corrió (ver S9).

El síntoma de fondo también importa: `alembic check` es el mecanismo que
detecta que alguien cambió `models.py` y se olvidó la migración. Si falla
siempre, deja de decir nada; y cuando alguien lo arregle a los apurones
sacándolo del CI, se pierde el control de verdad.

**Qué hay que hacer:** declarar el índice en el modelo. En `ListingEmbedding`:

```python
Index("idx_listing_embeddings_hnsw", "embedding",
      postgresql_using="hnsw",
      postgresql_with={"m": 16, "ef_construction": 64},
      postgresql_ops={"embedding": "vector_cosine_ops"}),
```
(los parámetros exactos hay que copiarlos de `migrations/versions/…corpus_inicial.py`).

**Cómo se verifica que quedó bien:**

```
uv run alembic check
# "No new upgrade operations detected."
```
Y correr `docs/auditoria/sondas/s5_migraciones.ps1` de nuevo: el paso 4 tiene que salir
limpio.

**Riesgo de tocarlo:** ninguno sobre los datos — se declara un índice que ya
existe. Si los parámetros declarados no coinciden con los de la migración,
`alembic check` va a seguir marcando diferencia, así que hay que copiarlos.

---

### H-33 · El filtro de superficie del nodo 2 no puede usar índice: se castea texto en 8.383 filas

**Sección:** S5 · **Severidad:** media

**Qué está mal:** la consulta del nodo 2 filtra por
`coalesce(features.surface_covered, features.surface_total, (listings.raw->>'surface_weighted')::numeric)`.
Ese `coalesce` sobre un cast de JSONB **no es indexable**, así que Postgres trae
todos los avisos del barrio, hace un nested loop contra `listing_features` fila
por fila, y recién después filtra por superficie.

**Cómo lo verifiqué:** `EXPLAIN (ANALYZE, BUFFERS)` de la consulta real
(Palermo, 60–100 m², 120 días):

```
Limit  (actual time=257.993..258.007 rows=60)
  Buffers: shared hit=39195
  ->  Nested Loop Left Join  (actual time=8.100..248.436 rows=1881)
        Filter: (COALESCE(f.surface_covered, f.surface_total,
                 (NULLIF((l.raw ->> 'surface_weighted'), ''))::numeric) …)
        Rows Removed by Filter: 6502
        ->  Bitmap Heap Scan on listings l  (rows=8383)     <- estimaba 183
              Recheck Cond: ((neighborhood_id = $0) AND active)
        ->  Index Scan using pk_listing_features  (loops=8383)
              Buffers: shared hit=31347                     <- 80% del trabajo
Execution Time: 258.198 ms
```

Dos cosas: **8.383 iteraciones del nested loop** para devolver 60 filas, y una
**estimación de 183 filas contra 8.383 reales** (45× de error), que es lo que
lleva al planner a elegir nested loop.

**Por qué importa:** 258 ms por informe no es un problema hoy. Lo es la forma:
el costo crece **linealmente con el stock del barrio**, y el corpus está
diseñado para crecer. Con Palermo en 8.400 avisos ya son 39.000 buffers por
consulta; el nodo 2 la corre hasta **cinco veces** por informe (la escalera de
relajación), así que un informe hace ~200.000 accesos a buffer solo para elegir
candidatos. Y `listings` acumula 58.773 recorridos secuenciales sobre 121 MB.

**Qué hay que hacer:** la superficie ponderada es un dato del aviso, no una
interpretación: tiene que ser una **columna**, no un cast de JSONB.

1. Agregar `surface_weighted numeric(8,1)` a `corpus.listings`, que
   `capture.ingest._upsert` ya calcula (`card.surface_weighted`) y hoy guarda
   como texto en `raw`.
2. Índice compuesto: `(neighborhood_id, active, surface_weighted)`.
3. En `retrieve._consulta`, usar `coalesce(f.surface_covered, f.surface_total,
   l.surface_weighted)` — que sigue sin ser indexable del todo, pero permite al
   planner filtrar `listings` antes del join si se reordena la condición.
   Alternativa más efectiva: filtrar primero por `l.surface_weighted` en un rango
   más ancho (±10 pp sobre el pedido) y dejar el `coalesce` como refinamiento.
4. `ANALYZE corpus.listings` después de cada ingesta grande — la estimación de
   183 contra 8.383 reales sugiere estadísticas viejas.

**Cómo se verifica que quedó bien:**

```sql
explain (analyze, buffers) <la misma consulta>;
-- "Buffers: shared hit" tiene que bajar de 39.195 a menos de 5.000
-- y "Execution Time" de 258 ms a decenas de ms
```

**Riesgo de tocarlo:** una columna nueva y un índice, más una migración. El
riesgo real es que cambie **qué comparables devuelve** el nodo 2 si el filtro se
reescribe con otra semántica; hay que verificar que sobre un informe conocido
devuelva el mismo conjunto de `listing_id`.

---

### H-34 · Tres índices que nunca se usaron, y uno que no puede usarse

**Sección:** S5 · **Severidad:** baja

**Qué está mal:**

```sql
select relname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
from pg_stat_user_indexes where idx_scan = 0 and schemaname in ('core','corpus','eval');

 core.api_keys            | api_key_hash_unique             | 0 | 16 kB
 core.reports             | idx_reports_status              | 0 | 16 kB
 corpus.neighborhoods     | idx_neighborhoods_aliases       | 0 | 24 kB
 corpus.listing_embeddings| idx_listing_embeddings_hnsw     | 0 | 16 kB
 corpus.listings          | idx_listings_org                | 0 | 8192 bytes
```

- **`api_key_hash_unique` no puede usarse nunca.** El hash es argon2 con sal:
  dos hasheos de la misma clave dan strings distintos, así que la unicidad no
  restringe nada y la búsqueda es por `prefix` (`auth.py:91`). Es un índice que
  parece una garantía y no lo es.
- **`idx_reports_status`** (`where status in ('QUEUED','RUNNING')`) está para un
  cosechador de informes colgados que no existe (H-13). Cuando exista, se usa.
- **`idx_listing_embeddings_hnsw`** sobre una tabla vacía: correcto, es la
  Fase 5.
- **`idx_neighborhoods_aliases`** no se usa porque `_neighborhood_ids` carga
  las 59 filas a un dict en memoria. Está bien resuelto; el índice sobra.

**Por qué importa:** poco en performance (son kilobytes). Importa el primero:
un `UniqueConstraint` sobre un hash salado se lee como "no puede haber dos
claves iguales" y no garantiza nada.

**Qué hay que hacer:** sacar `api_key_hash_unique` y `idx_neighborhoods_aliases`
en una migración. Dejar los otros dos con un comentario de por qué existen antes
de usarse.

**Cómo se verifica que quedó bien:** `alembic check` limpio y la consulta de
arriba sin esos dos.

**Riesgo de tocarlo:** bajo. Hay que confirmar que ninguna consulta futura
planeada dependa del unique.

---

### H-35 · `ingest_runs` se recorre entero en cada archivo del scan

**Sección:** S5 · **Severidad:** baja

**Qué está mal:** `csv_scan._ya_procesado` busca por
`IngestRun.detail['sha256'].astext == sha`. No hay índice sobre esa expresión
JSONB, así que cada consulta es un seq scan.

**Cómo lo verifiqué:**

```sql
select relname, n_live_tup, seq_scan, idx_scan from pg_stat_user_tables …;
 ingest_runs | 37 | 6950 | 42
```

6.950 recorridos secuenciales sobre 37 filas.

**Por qué importa:** con 37 filas no cuesta nada. Con una ingesta diaria durante
un año son ~400 filas y 1.800 archivos por corrida: 720.000 comparaciones. Sigue
siendo barato, pero el dato debería estar en una columna: **el sha256 del
archivo procesado es identidad, no detalle.**

**Qué hay que hacer:** o un índice de expresión
(`create index on corpus.ingest_runs ((detail->>'sha256'))`), o —mejor— una
columna `source_sha256 text` con índice único parcial. Lo segundo también hace
imposible registrar dos corridas OK del mismo archivo.

**Cómo se verifica que quedó bien:** el `seq_scan` de `ingest_runs` deja de
crecer entre dos corridas de `ingest_csv.py`.

**Riesgo de tocarlo:** una migración con backfill desde `detail`. Bajo.

---

## 2. Otras mediciones

**Uso de las tablas** (`pg_stat_user_tables`, desde el último reinicio):

```
 tabla               |  filas | tamaño  | seq_scan | idx_scan
 listings            | 93.495 | 121 MB  |   58.773 |  350.925
 listing_features    | 90.153 |  27 MB  |       32 |  336.232
 listing_clusters    |  1.627 | 264 kB  |   11.523 |   47.904
 neighborhoods       |     59 | 136 kB  |  359.746 |    4.499
 ingest_runs         |     37 |  64 kB  |    6.950 |       42
```

`neighborhoods` con 359.746 recorridos sobre 59 filas es ruido barato
(`Barrios.cargar` y `_neighborhood_ids` lo hacen a propósito, y está bien:
59 filas en memoria es más rápido que ir a la base por cada aviso).

**El listado paginado de informes está bien**: 0,33 ms, top-N heapsort sobre 60
filas. El seq scan es la elección correcta a esta escala; el índice
`idx_reports_org_lista` va a entrar solo cuando la tabla crezca.

## 3. Qué NO pude verificar

- **El comportamiento de las migraciones sobre una base con DATOS.** Las corrí
  sobre una vacía. Una migración que agregue una columna NOT NULL a
  `corpus.listings` (93.495 filas) puede tomar minutos y bloquear; ninguna de
  las cuatro actuales lo hace, pero no hay ningún chequeo que lo impida en el
  futuro.
- **`listing_embeddings`** está vacía: no pude medir el índice HNSW ni el kNN.
- **Los 28 FKs de ESTADO §4.3.** No los conté uno por uno; las 20 tablas sí
  coinciden.

