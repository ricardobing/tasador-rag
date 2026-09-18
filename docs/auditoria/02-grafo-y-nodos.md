# S2 — El grafo y los 11 nodos

**Alcance:** `src/tasador/agents/` (graph, runner, state, base, 11 nodos).
**Método:** la traza real. 61 informes en la base, 619 filas en
`core.report_events`, y verificación en el contenedor de PRODUCCIÓN, no en el
override de dev.

---

## 1. El estado del grafo, medido

```sql
select node, status, count(*) n, round(avg(duration_ms)) ms_prom, max(duration_ms) ms_max,
       round(sum(cost_usd)::numeric,6) usd
from core.report_events group by node, status order by min(seq), status;

 normalize_subject   | FAILED |  2 |    25 |     30 |
 normalize_subject   | OK     | 59 |   177 |  1.854 |
 retrieve_candidates | OK     | 59 |   109 |    323 |
 extract_features    | OK     | 58 | 18.346| 252.903| 0.090753
 dedup_cluster       | OK     | 58 |  6.424| 180.569| 0.025013
 curate              | OK     | 58 | 32.021| 445.548| 0.098407
 adjust_and_value    | FAILED |  1 |    30 |     30 |
 adjust_and_value    | OK     | 57 |    17 |     60 |
 market_context      | FAILED |  3 |  3.759|  5.646 |
 market_context      | OK     | 36 |  5.151| 25.766 | 0.010735
 write_report        | OK     | 56 | 11.745| 24.658 | 0.270896
 critic              | OK     | 56 |  6.254| 19.240 | 1.069341
 render_pdf          | FAILED |  4 |  1.200|  2.391 |
 render_pdf          | OK     | 15 |  3.584|  7.128 |
```

**Lo que está bien y hay que decirlo:** la traza cumple lo que promete. Cada
degradación deja su motivo legible en `detail->>'error'` —el bug 20 de la
Etapa 3 está cerrado— y el modelo que **realmente** atendió queda en
`detail->'llm'->'modelos'`:

```sql
select node, detail->'llm' from core.report_events where detail ? 'llm' limit 3;
 critic       | {"modelos": ["openrouter/anthropic/claude-sonnet-4.5"], "llamadas": 2, "degradado": false}
 write_report | {"modelos": ["openrouter/qwen/qwen3-max"], "llamadas": 1, "degradado": false}
 market_context | {"modelos": ["crewai:judge"], "llamadas": 1, "degradado": false}
```

```sql
select node, status, left(detail->>'error',80), detail->>'on_error' from core.report_events where status='FAILED';
 adjust_and_value | FAILED | TypeError: '<=' not supported between 'str' and 'int'    | fail
 render_pdf       | FAILED | NodeError: WeasyPrint no pudo cargar sus librerías...    | degrade
 normalize_subject| FAILED | PermissionError: [Errno 13] Permission denied: '/app/data'| fail
 market_context   | FAILED | ImportError: Invalid `http_client` argument...           | degrade
 market_context   | FAILED | InvalidRequestError: This session is provisioning...     | degrade
 normalize_subject| FAILED | NodeError: No pudimos ubicar 'Av. Cabildo 2530'...       | fail
```

Y el reparto de costo es el declarado: el crítico es USD 1,07 de USD 1,56
totales (**68%**), consistente con la decisión de doc 04.

Lo que sigue son once cosas que la traza también dice y que nadie estaba
mirando.

---

### H-10 · El PDF se escribe fuera del volumen: en producción no se puede descargar

**Sección:** S2 · **Severidad:** ALTA

**Qué está mal:** el nodo 11 guarda el PDF en `settings.data_path / "artifacts"`
(`render.py:179`). `data_path` autodetecta `/data/raw`, y el volumen compartido
entre la API y el worker está montado en **`/data/artifacts`**. En desarrollo no
se nota porque el override monta `./data:/data/raw` en los dos servicios. En
producción el PDF queda en la capa efímera del worker y la API no lo ve.

**Cómo lo verifiqué:** en el contenedor de producción, sin el override de dev.

```
$ docker compose -f docker-compose.yml config   # volúmenes de PRODUCCIÓN
api:    artifacts->/data/artifacts
worker: artifacts->/data/artifacts, models->/data/models
```

```
$ docker run --rm --read-only --tmpfs /tmp -v tasador_artifacts:/data/artifacts \
    --entrypoint python ghcr.io/ricardobrossard/tasador-worker:dev -c \
    "from tasador.settings import get_settings; print(get_settings().data_path)"
data_path = /data/raw
artifacts destino = /data/raw/artifacts        <-- NO es el volumen
/data/artifacts existe = True                  <-- el volumen, vacío
```

Y del lado de la API, peor:

```
$ docker run --rm --read-only --tmpfs /tmp -v tasador_artifacts:/data/artifacts \
    --entrypoint python ghcr.io/ricardobrossard/tasador-api:dev -c "..."
data_path = /app/data
existe = False
escribible = False
```

`api.Dockerfile` hace `mkdir -p /data/artifacts` y **no crea `/data/raw`**;
`worker.Dockerfile` sí lo crea. Por eso los dos servicios resuelven `data_path`
a lugares distintos.

**Por qué importa:** `GET /v1/reports/{id}/pdf` (`reports.py:353`) hace
`Path(artefacto.pdf_path).exists()` y devuelve 404 si no está. En producción el
`pdf_path` guardado apunta a un archivo que vive dentro del worker: **el botón
"Descargar PDF" y el link compartido del propietario devuelven 404 siempre**, y
falla limpio, o sea que el síntoma es "no anda" sin ninguna pista. Además el PDF
se pierde al recrear el contenedor, y doc 03 promete lo contrario: *"un informe
entregado en septiembre tiene que ser byte por byte el mismo en diciembre"*.

Es el mismo patrón del bug 23 (`\n` literal en el `ENV`) y de los cinco arreglos
que vivían solo en dev: **lo que corre en producción se verifica en el
contenedor de producción**.

**Qué hay que hacer:**
1. Que el destino de los artefactos sea explícito y no derivado de `data_path`.
   Agregar a `settings.py` un `artifacts_dir: str = "/data/artifacts"` y usarlo
   en `render.py:179`, con fallback a `data_path/"artifacts"` solo si ese
   directorio no existe (para Windows).
2. `api.Dockerfile`: agregar `/data/raw` al `mkdir -p` para que la API resuelva
   `data_path` igual que el worker, y que el caché de geocoding no reviente.
3. Extender `tests/architecture/test_contenedor.py` con un test que verifique
   que los dos Dockerfile crean el MISMO conjunto de directorios bajo `/data`.

**Cómo se verifica que quedó bien:**

```bash
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml exec worker python -c \
  "from tasador.settings import get_settings; print(get_settings().artifacts_dir)"
# tiene que imprimir /data/artifacts
docker compose -f docker-compose.yml exec api ls /data/artifacts
# después de generar un informe, el .pdf tiene que estar acá
curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer tsk_..." \
  http://localhost:8000/v1/reports/<id>/pdf
# 200, no 404
```

**Riesgo de tocarlo:** los 15 `report_artifacts` existentes apuntan a rutas
viejas. Hay que decidir si se migran (copiar los archivos y hacer UPDATE) o si
se aceptan como históricos. En dev nada cambia porque `./data` está montado en
los dos lados.

---

### H-11 · La fase A del crítico deja pasar la mitad de los precios inventados

**Sección:** S2 · **Severidad:** ALTA

**Qué está mal:** `critic.py` declara que la fase A es *"la única defensa real
contra la alucinación de cifras"* y pone un ejemplo: *"si el redactor escribió
'USD 312.000' y ese número no está en la valuación ni en la tabla, el informe no
sale"*. **Ese número exacto pasa**, en los tres informes reales que probé.
`_valores_permitidos` acepta cualquier cifra que aparezca en cualquier parte de
los datos, le agrega `v*100` y `v/1000`, y compara con ±1% de tolerancia. Con
23 comparables eso cubre la mitad del espacio de precios plausibles.

**Cómo lo verifiqué:** reconstruí `datos_del_informe` desde
`reports.methodology` de informes SUCCEEDED reales y medí la cobertura del
conjunto aceptado con 20.000 precios al azar entre USD 100.000 y 400.000.

```
$ uv run python docs/auditoria/sondas/s2_critico.py
=== informe 4f3a88f7  (23 comparables usados) ===
  cifras distintas aceptadas como 'trazables': 190
  precios inventados entre 100.000 y 400.000 que la fase A DEJA PASAR: 10098/20000 = 50.5%
    cifra inventada USD    312.000 -> PASA
    cifra inventada USD    999.999 -> RECHAZA
    cifra correcta   USD    239.280 -> pasa

=== informe e29fca61  (7 comparables usados) ===
  cifras distintas aceptadas: 81
  precios inventados que DEJA PASAR: 5435/20000 = 27.2%
    cifra inventada USD    312.000 -> PASA
```

Y aislando qué regla abre el agujero:

```
$ uv run python docs/auditoria/sondas/s2_critico2.py
informe 4f3a88f7 (23 comparables)
  solo las cifras de los datos       66 valores  cobertura  29.1%
  + la regla v/1000                 106 valores  cobertura  30.0%
  + la regla v*100                  132 valores  cobertura  49.7%
  las dos (lo que corre hoy)        172 valores  cobertura  49.0%

  tolerancia 0.1%  -> cobertura   7.3%
  tolerancia 0.5%  -> cobertura  30.0%
  tolerancia 1.0%  -> cobertura  49.2%
  tolerancia 2.0%  -> cobertura  70.5%
```

**Por qué importa:** es un gate que puede pasar sin medir (R3), y está en el
lugar donde más duele. Todo el argumento del producto —"el LLM no produce
cifras, y si inventa una la agarramos sin depender de otro LLM"— descansa en
esta función. Hoy la fase A rechaza cifras absurdas (USD 999.999) y **no
distingue** un valor inventado plausible del correcto. La causa es estructural,
no un descuido: al aceptar *cualquier* número de los datos, y siendo los precios
de los 23 comparables números del mismo orden de magnitud que el valor del
sujeto, el conjunto satura el rango. La regla `v*100` lo empeora porque convierte
cada USD/m² (2.000–5.000) en un precio permitido (200.000–500.000).

**Qué hay que hacer:** la verificación tiene que ser **posicional**, no un
conjunto global.

1. Sacar la regla `permitidos.add(v * 100)` de `critic.py:145`. Existe para la
   dispersión en porcentaje: en su lugar, agregar explícitamente los derivados
   que sí se quieren (`dispersion*100`, `closing_discount*100`) al armar
   `datos_del_informe`.
2. Bajar `numeric_tolerance_pct` de 1,0 a 0,1 en `config/agents.yaml`. Con 0,1%
   la cobertura cae a 7,3% y sigue cubriendo el redondeo real del redactor
   (USD 182.000 por 181.950 es 0,03%).
3. Agregar una verificación de las cifras CLAVE por posición: extraer del
   markdown la cifra que sigue a "valor medio / sugerido / estimado" y exigir
   igualdad exacta contra `value_mid`; ídem para el rango y el USD/m². Es lo que
   de verdad protege el número que lee el cliente.
4. **Y un test que mida el gate**, no que lo ejercite: `tests/test_critic.py`
   con la forma de la sonda de arriba, afirmando que la cobertura de precios al
   azar queda por debajo de un umbral (p. ej. 10%). Sin eso, cualquier cambio
   futuro puede volver a abrirlo sin que nada avise.

**Cómo se verifica que quedó bien:**

```
uv run python docs/auditoria/sondas/s2_critico.py
# "precios inventados que DEJA PASAR" tiene que quedar por debajo del 10%
# y "USD 312.000 -> RECHAZA" en los tres informes
uv run pytest tests/test_critic.py -q
```

**Riesgo de tocarlo:** bajar la tolerancia sube los rechazos, y al tercer rechazo
el informe sale sin narrativa — que hoy ya pasa en el 11% de los casos (H-20).
Hay que hacer los dos cambios juntos y medir la tasa de rechazo sobre al menos
5 informes antes de dar por bueno. El aprendizaje de Etapa 3 §10.3 aplica:
**rechazar de más cuesta el producto.**

---

### H-12 · El dedup encadena: un cluster de 198 avisos donde el 92% de los pares no son duplicados

**Sección:** S2 · **Severidad:** ALTA

**Qué está mal:** `_componentes()` agrupa por transitividad —"si A=B y B=C, los
tres son el mismo inmueble"—. Eso es cierto para una identidad y **falso para
una relación con tolerancia**: con ±5% de precio y ±2 m² de superficie, una
cadena A≈B≈C≈D une extremos que no se parecen en nada. Es clustering de enlace
simple, y encadena.

**Cómo lo verifiqué:** los clusters que ya están escritos en la base, y qué
fracción de sus pares internos cumple de verdad el criterio.

```
$ uv run python docs/auditoria/sondas/s2_cluster.py
    n   pares posibles   pares que MATCHEAN        %  direccion
  198           19.503                1.480    7.59%  Humboldt 2300
   94            4.371                  531   12.15%  Avenida Santa Fe 5000, Piso 1
   74            2.701                1.292   47.83%  Godoy Cruz 2500, Piso 1
   74            2.701                  549   20.33%  Avenida Cerviño 3900
```

Y la dispersión adentro de los clusters, que es la señal de que se fusionó lo
que no era:

```sql
-- 1.138 clusters con más de un miembro
 precio_mas_10pct | precio_mas_25pct | precio_el_doble | superficie_mas_15pct
              202 |              130 |              47 |                  173
```

```
 n  |  pmin   |  pmax   | ratio | smin  | smax   | direccion
  3 |  54.000 | 530.000 |  9.81 | 23.0  | 183.0  | Honduras 3700, Piso 5
  7 | 105.000 | 888.000 |  8.46 | 22.25 | 187.18 | Beruti 4500
  9 | 135.000 | 730.000 |  5.41 | 50.0  | 254.01 | Cabello 3900
198 | 111.000 | 436.900 |  3.94 | 23.55 |  73.01 | Humboldt 2300
```

**Por qué importa:** el nodo 2 toma **un solo candidato por cluster** (el
canónico). Medido:

```sql
-- Palermo, 60-100 m2, condiciones del nodo 2
 sin_filtro_de_cluster | con_filtro
                  1890 |       1225      -- 35% del universo suprimido
```

```sql
-- 58 clusters de 10+ miembros suprimen 1.460 avisos ellos solos
 clusters_grandes | suprimidos_por_grandes | suprimidos_total
               58 |                  1.460 |            3.470
```

Tres consecuencias concretas:

1. **3.470 avisos vigentes son invisibles para todo informe.** Hoy no falta
   cobertura en Palermo, pero sí cambia *cuáles* 60 comparables se eligen.
2. **El número que ESTADO §2 publica es incorrecto**: "8.497 avisos → 5.027
   propiedades únicas" cuenta como una sola propiedad a un cluster de 198 avisos
   de 23 a 73 m² y de USD 111.000 a 436.900.
3. Y es exactamente el riesgo que el propio módulo declara evitar: el criterio
   dice *"ante la duda, DISTINTO"* porque **fusionar borra un comparable
   legítimo**. La transitividad lo da vuelta.

**Qué hay que hacer:**
1. **Acotar el encadenamiento.** La solución mínima y verificable: después de
   armar cada componente con `_componentes`, validar el grupo entero —si la
   fracción de pares internos que cumplen `_capa_1 or _capa_2` está por debajo
   de un umbral (p. ej. 0,80), partirlo. Alternativa más correcta: enlace
   completo (todos contra todos dentro del grupo) en vez de simple.
2. **Tope duro de tamaño de cluster.** Un edificio real republicado tiene 2-5
   avisos; 198 es imposible. Un cluster mayor a N (p. ej. 8) es un bug y tiene
   que quedar registrado como tal, no aplicarse.
3. **Volver a correr `scripts/dedup_corpus.py --aplicar`** limpiando los
   `cluster_id` y `listing_clusters` previos (ver H-17).

**Cómo se verifica que quedó bien:**

```
uv run python docs/auditoria/sondas/s2_cluster.py
# ningun cluster con menos de 80% de pares que matchean
```
```sql
select max(n) from (select cluster_id, count(*) n from corpus.listings
                    where cluster_id is not null group by 1) t;
-- <= 8
select count(*) from corpus.listings where active
  and (cluster_id is null or ...canonical...);
-- tiene que SUBIR desde 5.027
```

**Riesgo de tocarlo:** cambia el universo de comparables de todo informe futuro
y por lo tanto los números. Hay que correr el backtest antes y después.
`tests/test_dedup.py` protege los casos borde conocidos (Zabala 93 vs 105 m²) y
tiene que seguir en verde; hace falta un test nuevo con una cadena A-B-C-D que
hoy se fusiona y no debería.

---

### H-13 · Informes colgados en RUNNING y QUEUED, sin nadie que los coseche

**Sección:** S2 · **Severidad:** media

**Qué está mal:** cuatro informes llevan entre 8 y 13 horas sin terminar y sin
fallar. `worker._marcar_fallado` cubre el caso "el job levantó una excepción",
pero no el caso "el proceso murió" ni "el job nunca se tomó". No hay ningún
proceso que recoja huérfanos.

**Cómo lo verifiqué:**

```sql
select r.id, r.status, r.created_at, r.started_at, count(e.id) eventos
from core.reports r left join core.report_events e on e.report_id=r.id
where r.status in ('RUNNING','QUEUED') group by 1,2,3,4;

 bbae82f9 | RUNNING | 2026-08-14 05:22:12 | 2026-08-14 05:22:13 |  2
 e405cfe1 | RUNNING | 2026-08-14 05:17:39 | 2026-08-14 05:20:21 | 22
 480a04aa | QUEUED  | 2026-08-14 00:16:12 |                     |  0
 af595645 | QUEUED  | 2026-08-14 00:17:48 |                     |  0
```

Los dos QUEUED nunca fueron tomados por el worker (cero eventos, sin
`started_at`). `e405cfe1` tiene 22 eventos: el grafo corrió **dos veces enteras**
sobre el mismo `thread_id` y terminó después del crítico sin llegar a
`render_pdf`.

**Por qué importa:** desde el front, "encolado" y "roto" se ven igual — es
textualmente lo que dice el docstring de `_marcar_fallado`, y el caso que cubre
no es el que está pasando. El usuario mira un stepper que no termina nunca.
Y el trabajo se pagó: `e405cfe1` gastó USD 0,109 en 22 eventos.

**Qué hay que hacer:**
1. Un job periódico en `ops/crontab` (el servicio `ingest` ya corre supercronic)
   que marque como `FAILED` con `error_code=ABANDONADO` todo informe en
   `RUNNING` con `started_at` anterior a `job_timeout * 2`, y en `QUEUED` con
   `created_at` anterior a, digamos, 1 hora.
2. Que `GET /v1/reports/{id}` devuelva el tiempo transcurrido para que el front
   pueda mostrar "esto está tardando más de lo normal" en vez de un spinner.
3. Limpiar los cuatro que están hoy (decisión del humano: es escritura en la
   base, no la hago).

**Cómo se verifica que quedó bien:**

```sql
select count(*) from core.reports
where status='RUNNING' and started_at < now() - interval '1 hour';
-- 0
```

**Riesgo de tocarlo:** un reaper demasiado agresivo mata informes que están
corriendo de verdad. El primer informe de un barrio nuevo tarda ~5 minutos y
`job_timeout` es 1800 s: el umbral tiene que ser mayor que eso, no menor.

---

### H-14 · El costo del informe se subestima: lo que no termina gasta y registra cero

**Sección:** S2 · **Severidad:** media

**Qué está mal:** `runner._persist_result` suma `report_events.cost_usd` al
final de la corrida. Un informe que no llega al final deja sus eventos —con su
costo real— y `reports.cost_usd` en cero.

**Cómo lo verifiqué:**

```sql
select o.slug, sum(r.cost_usd) segun_reports,
       (select sum(e.cost_usd) from core.report_events e
        join core.reports r2 on r2.id=e.report_id where r2.org_id=o.id) segun_traza
from core.organizations o join core.reports r on r.org_id=o.id group by o.id, o.slug;

 inmo-demo | 1.455851 | 1.565145      -- 7,5% menos
```

```sql
select id, status, cost_usd, tokens_in from core.reports
where id='e405cfe1-7bf9-4caa-8d28-0b0f03360710';
 e405cfe1 | RUNNING | 0.000000 | 0     -- y sus 22 eventos suman USD 0,109
```

**Por qué importa:** `/admin/organizacion` muestra "consumo del mes" y la cuota
del tenant. El gasto real es 7,5% mayor que el declarado, y la diferencia crece
con la tasa de informes que no terminan. Además rompe la propiedad que el propio
código declara: *"el costo del informe se SUMA de la traza, no se estima. Es la
misma tabla que ve el usuario: si no coinciden, mentimos"*.

**Qué hay que hacer:** que el consumo del tenant se calcule **desde
`report_events`** y no desde `reports.cost_usd` (ver `v1/admin.py`), o —mejor—
actualizar `reports.cost_usd` de forma incremental en `_persist_event`, que ya
escribe cada fila. La segunda opción también arregla el desglose en vivo.

**Cómo se verifica que quedó bien:** la consulta de arriba tiene que dar los dos
números iguales, y

```sql
select count(*) from core.reports r
where r.cost_usd is distinct from
  (select coalesce(sum(e.cost_usd),0) from core.report_events e where e.report_id=r.id);
-- 0
```

**Riesgo de tocarlo:** si se actualiza incrementalmente hay que cuidar el
reintento del mismo `report_id` (los eventos se acumulan). Sumar desde la tabla
es idempotente; incrementar no.

---

### H-15 · `degraded_nodes` se calcula y nunca llega al informe

**Sección:** S2 · **Severidad:** media

**Qué está mal:** `ReportState` tiene `degraded_nodes` y `base.instrument` lo
llena cuando un nodo con `on_error: degrade` falla. `runner._persist_result`
guarda `report.methodology = v`, donde `v` es **solo el dict de valuación**. La
lista de nodos degradados se descarta.

**Cómo lo verifiqué:**

```sql
select count(*) filter (where methodology ? 'degraded_nodes') con_lista, count(*)
from core.reports where status='SUCCEEDED';
 con_lista | count
         0 |    37
```

Y sin embargo hubo degradaciones reales: `market_context` falló 3 veces y
`render_pdf` 4.

**Por qué importa:** un informe al que le faltó la sección de contexto de
mercado es indistinguible, a nivel de informe, de uno completo. Para saberlo hay
que hacer un JOIN con `report_events` y filtrar por `status='FAILED'`. La API
(`GET /v1/reports/{id}`) devuelve la traza, así que el dato *existe*, pero no hay
un campo que diga "este informe salió degradado y por esto". Doc 04 §3 dice
"degradar antes que fallar"; la contraparte de esa política es que la
degradación se vea.

**Qué hay que hacer:** en `runner._persist_result`, agregar
`report.methodology = {**v, "degraded_nodes": final.get("degraded_nodes", [])}`,
y exponerlo en la respuesta de `GET /v1/reports/{id}` y en el PDF (una línea al
pie: "este informe salió sin la sección de contexto de mercado").

**Cómo se verifica que quedó bien:** correr un informe con
`TASADOR_TASK_MARKET_CONTEXT=inexistente` (fuerza la degradación del nodo 8) y

```sql
select methodology->'degraded_nodes' from core.reports order by created_at desc limit 1;
-- ["market_context"]
```

**Riesgo de tocarlo:** ninguno; es un campo aditivo en un JSONB.

---

### H-16 · Los umbrales de descarte están escritos tres veces

**Sección:** S2 · **Severidad:** media

**Qué está mal:** el rango plausible de USD/m² vive en tres lugares
independientes, con el mismo valor y sin nada que los sincronice.

**Cómo lo verifiqué:**

```
$ rg "MIN_USD_M2|MAX_USD_M2|outlier_usd_m2" src config
src/tasador/ingest/core.py:40:      MIN_USD_M2 = Decimal("300")
src/tasador/ingest/core.py:41:      MAX_USD_M2 = Decimal("12000")
src/tasador/agents/nodes/curate.py:57: MIN_USD_M2 = Decimal("300")
src/tasador/agents/nodes/curate.py:58: MAX_USD_M2 = Decimal("12000")
config/adjustments.yaml:95:            outlier_usd_m2_min: 300
config/adjustments.yaml:96:            outlier_usd_m2_max: 12000
src/tasador/ingest/badata.py:42:       MIN_USD_M2, MAX_USD_M2 = Decimal("200"), Decimal("20000")
```

(El de `badata.py` es distinto **a propósito** —datos históricos— y está bien
que lo sea; los otros tres son el mismo criterio duplicado.)

Lo mismo con la superficie mínima: `curate.MIN_SUPERFICIE = 15` y el
`superficie_implausible` de `ingest/core.py`.

**Por qué importa:** es el patrón que más daño hizo en este proyecto (R2). Doc
05 dice que los coeficientes y umbrales son configuración versionada; cambiar
`config/adjustments.yaml` de 12.000 a 15.000 movería el nodo 7 y dejaría a la
ingesta y al nodo 6 descartando con el valor viejo, en silencio. Nadie fallaría.

**Qué hay que hacer:** una sola fuente. Los tres son el mismo criterio de
"aviso plausible": moverlos a `config/adjustments.yaml` (donde ya está uno) y que
`ingest/core.py` y `curate.py` los lean con `load_config()`. Y un test que
falle si vuelven a aparecer literales: el mismo patrón que
`test_hay_una_sola_implementacion_del_raw_y_del_hash`.

**Cómo se verifica que quedó bien:**

```
uv run pytest tests/test_ingest_csv.py -q -k umbrales
rg "Decimal\(\"12000\"\)|Decimal\(\"300\"\)" src   # sin resultados fuera de badata.py
```

**Riesgo de tocarlo:** bajo. Los valores no cambian; cambia de dónde salen.
`tests/test_ingest_csv.py` cubre los motivos de descarte.

---

### H-17 · 488 clusters huérfanos y un `match_method` que no distingue lo que decidió un LLM

**Sección:** S2 · **Severidad:** baja

**Qué está mal:** dos cosas de `scripts/dedup_corpus.py::_aplicar`:
no borra los clusters de una corrida anterior, y escribe
`match_method="EXACT_ADDR"` para **todos** los grupos, incluidos los que armó el
juez — cuando su propio comentario dice lo contrario: *"Los que decida el juez se
marcarán distinto y así se puede auditar cuáles dependieron de un modelo"*.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s2_cluster.py
--- clusters: metodo de match declarado y huerfanos
    EXACT_ADDR 1627
    filas en listing_clusters: 1627
    cluster_id distintos en listings: 1139
    clusters SIN ningun miembro (huerfanos): 488
```

**Por qué importa:** los 488 huérfanos son basura acumulada (no rompen nada
hoy). El `match_method` sí importa: es el campo que permitiría responder
"¿cuántas de las 5.027 propiedades únicas dependen de que un modelo dijera que
dos avisos eran el mismo?", y hoy la respuesta es "no se puede saber".

**Qué hay que hacer:** en `_aplicar`, (a) limpiar `listings.cluster_id` y borrar
los `listing_clusters` del alcance antes de escribir, y (b) recibir qué pares
vinieron del juez y marcar esos grupos con `match_method="LLM_JUDGE"`.
`corpus.listing_clusters.match_method` ya está en el modelo.

**Cómo se verifica que quedó bien:**

```sql
select match_method, count(*) from corpus.listing_clusters group by 1;
-- EXACT_ADDR y LLM_JUDGE, con números distintos de cero si se corrió con --juez
select count(*) from corpus.listing_clusters cl
 where not exists (select 1 from corpus.listings l where l.cluster_id = cl.id);
-- 0
```

**Riesgo de tocarlo:** borrar clusters es destructivo sobre el corpus. Va con
`--aplicar` explícito y después de H-12, no antes.

---

### H-18 · Dos reglas del nodo 6 no pueden dispararse nunca

**Sección:** S2 · **Severidad:** baja

**Qué está mal:** `reglas_duras` descarta por `aviso_vencido` si
`days_published > 180`. `_a_candidato` deja `days_published = None` salvo que el
aviso tenga `published_at`, y ningún aviso vigente lo tiene.

**Cómo lo verifiqué:**

```sql
select count(*) total, count(published_at) con_fecha from corpus.listings where active;
 total | con_fecha
  8497 |         0
```

```sql
select exclusion_reason, count(*) from core.report_comparables where not included group by 1;
-- `aviso_vencido` no aparece en ninguna de las 619 filas
```

Es la misma causa que apaga `listing_age_coef` y `f_freshness` (H-03 y H-06):
**la fecha de publicación no llega nunca al motor**, y tres controles distintos
dependen de ella.

**Por qué importa:** por sí sola es deuda. Junta con H-03 y H-06 es un patrón:
un campo ausente apaga en silencio tres mecanismos en tres capas distintas, y
ningún gate lo dice. Portal B trae `publication_date` en el 76%; el mapeo
existe en `CAMPOS_DEL_RAW` pero no llega a la columna `published_at`.

**Qué hay que hacer:** ver S3. La corrección es de ingesta: mapear
`publication_date` a `listings.published_at`. Acá lo que corresponde es dejar un
chequeo que avise: agregar al detalle del nodo 6
`"reglas_inaplicables": [...]` con las que no pudieron evaluarse por falta de
dato, para que se vea en la traza.

**Cómo se verifica que quedó bien:**

```sql
select count(published_at) from corpus.listings where active and source='PORTAL_B';
-- > 0 después del arreglo de S3
```

**Riesgo de tocarlo:** al encenderse la regla empiezan a descartarse avisos que
hoy entran. Puede bajar el número de comparables de un informe. Medir antes y
después.

---

### H-19 · Una re-extracción puede borrar features buenas

**Sección:** S2 · **Severidad:** media · **NO EJECUTADO** (leído en el código)

**Qué está mal:** `extract._persistir` construye `datos` con
`confiable(campo)` —que devuelve `None` si la confianza no llega al umbral— y
después hace `setattr(fila, k, v)` para **todas** las claves. Si un aviso ya
tenía `condition='a_refaccionar'` con cita verificada y una re-extracción
posterior devuelve el mismo valor sin cita (confianza 0,4), el `None` pisa el
dato bueno.

**Cómo lo verifiqué:** lectura de `extract.py:249-287`. **No lo ejecuté**: para
provocarlo hay que correr dos extracciones reales sobre el mismo aviso con
salidas distintas del modelo, y eso implica gasto y escritura en el corpus, que
esta sesión no hace. Lo que sí es verificable sin costo es la forma del bug:
`datos` no distingue "el modelo no lo dijo" de "el modelo lo dijo flojo", y el
`else` es un UPDATE completo.

**Por qué importa:** `scripts/extraer_corpus.py --reextraer` existe justamente
para reprocesar, y ya se usó (el paso de v2 a v1 el 14/08). Con la varianza
medida del extractor —17,6 pp de amplitud entre corridas idénticas— que una
segunda corrida devuelva menos que la primera no es hipotético: es lo esperable
en una fracción de los avisos.

**Qué hay que hacer:** en el camino de UPDATE, no pisar con `None` un campo que
ya tenía valor, salvo que la nueva extracción venga de una versión de prompt
distinta y se pida explícitamente (`--forzar`). Concretamente, en
`_persistir`, filtrar `datos` antes del `setattr`:
`if v is None and getattr(fila, k) is not None and not forzar: continue`.

**Cómo se verifica que quedó bien:** un test con una sesión de base que
persista dos veces el mismo `listing_id`, la segunda con confianza por debajo
del umbral, y afirme que el valor original sobrevive. Y en producción:

```sql
select count(*) from corpus.listing_features where condition is not null;
-- no debe BAJAR después de una re-extracción
```

**Riesgo de tocarlo:** hace que reprocesar con un prompt peor no destruya datos,
pero también que reprocesar para *corregir* un dato malo necesite `--forzar`.
Hay que documentarlo en la guía.

---

### H-20 · Un informe de cada nueve sale sin narrativa, y nadie lo mide

**Sección:** S2 · **Severidad:** baja

**Qué está mal:** al tercer rechazo del crítico el informe sale sin prosa. Está
diseñado así y está bien. Lo que no hay es una métrica: nadie sabe con qué
frecuencia pasa hasta que un cliente lo pregunta.

**Cómo lo verifiqué:**

```sql
select r.critic_rejections, count(*) informes, count(*) filter (where r.narrative_md is null) sin_narrativa
from core.reports r where r.status='SUCCEEDED' group by 1 order by 1;
 0 | 27 | 8      <- historicos, anteriores al arreglo
 1 |  5 | 0
 2 |  1 | 0
 3 |  4 | 4      <- el comportamiento de hoy
```

Sobre los 14 informes más recientes, los dos que llegaron a 3 rechazos son
exactamente los dos sin narrativa: **11%**.

**Por qué importa:** 11% de los informes entregados no tienen la sección que
justifica el número. Doc 04 lo declara como degradación aceptable, y lo es —
pero es también la métrica que dice si el prompt del redactor o la calibración
del crítico están bien, y no está en `/calidad` ni en ningún lado. Y va a
empeorar si se endurece el crítico (H-11).

**Qué hay que hacer:** agregar a `GET /v1/calidad` la serie "informes sin
narrativa / total" por semana, junto a las de MdAPE y evals. Es una consulta
sobre `core.reports`, no hace falta tabla nueva.

**Cómo se verifica que quedó bien:** `/calidad` muestra el porcentaje y
`curl -s localhost:8000/v1/calidad | jq .sin_narrativa_pct` devuelve un número.

**Riesgo de tocarlo:** ninguno; es lectura.

---

## 2. Contrato entre nodos: lo que verifiqué

| Pregunta | Respuesta |
|---|---|
| ¿Un nodo que falla deja el informe coherente? | Sí para `on_error: fail` (corta y persiste FAILED con código). **No** para el caso "el proceso murió": queda RUNNING para siempre (H-13). |
| ¿La traza permite reconstruir qué pasó? | Sí a nivel de evento (motivo, modelo real, costo, duración). **No** a nivel de informe: `degraded_nodes` no se guarda (H-15). |
| ¿Hay costo que no se contabiliza? | Sí, 7,5% (H-14). El del nodo 8 con CrewAI es exacto pero agregado, y está declarado. |
| ¿El ciclo crítico→redactor puede quedar en bucle? | No: el tope vive en el nodo (`_rechazo`) y no en el router, y `rehacer` se apaga en las dos ramas. Verificado leyendo `critic._rechazo` y `write.write_report` (`updates={"rehacer": False}`). |
| ¿ADR-002 se respeta en el grafo? | Sí. Ver S1 §1. |

## 3. Qué NO pude verificar

- **El nodo 3 (`ondemand_capture`) está apagado** y no lo probé. Correcto que lo
  esté (necesita Chrome con IP residencial).
- **H-19** no se ejecutó, por lo dicho ahí.
- **La concurrencia de `market_context`**: el `InvalidRequestError` de sesión
  compartida aparece una vez en la traza y el código ya se corrigió a
  secuencial (`metricas_del_barrio`). No pude reproducir la condición de
  carrera, así que no sé si queda algún camino concurrente.
- **El comportamiento con `max_jobs: 4` bajo carga.** Todos los informes de la
  base se corrieron de a uno.

