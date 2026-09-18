# S3 — Ingesta y fuentes

**Alcance:** `src/tasador/ingest/`, `src/tasador/capture/`, `src/tasador/sources/`,
`scripts/ingest_csv.py`.
**Método:** contar filas en la base y ejercitar el contrato, no leer el código.

---

## 1. Lo que quedó bien de la consolidación del 14/08

`csv_scan` no reimplementa nada: importa `motivo_descarte`, `_upsert`,
`_neighborhood_ids` de `capture.ingest`, y `portal_a_ingest` delega `_raw` y
`_content_hash` en la misma implementación. Hay un test que lo impone.

La idempotencia por archivo funciona y la verifiqué contando filas:

```
$ uv run python -c "... select count(*) from corpus.listings; ... ingest_runs"
93495 37                       <- ANTES

$ uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos --dry-run
DRY RUN — 1779 archivos, no se escribe nada
...
93495 37                       <- DESPUÉS: el dry-run no escribió nada
```

```sql
select mode, status, count(*), sum(discovered), sum(created), sum(updated)
from corpus.ingest_runs group by 1,2;
 BACKFILL | OK      | 32 | 32386 | 8472 | 9412
 DISCOVER | OK      |  3 |    50 |   24 |    0
 DISCOVER | PARTIAL |  2 |     0 |    0 |    0
```

Lo que sigue son cinco cosas que la consolidación no alcanzó a cubrir.

---

### H-21 · La fecha de publicación nunca llega a la columna, y eso apaga tres mecanismos

**Sección:** S3 · **Severidad:** media

**Qué está mal:** `csv_scan.fila_a_card` mete `publication_date` en
`raw_attrs` (`csv_scan.py:191`) y **nadie lo baja a `listings.published_at`**.
`_upsert` no escribe esa columna.

**Cómo lo verifiqué:**

```sql
select count(*) total, count(published_at) en_la_columna,
       count(*) filter (where nullif(raw->'attrs'->>'publication_date','') is not null) en_raw_attrs
from corpus.listings where active;
 total | en_la_columna | en_raw_attrs
  8497 |             0 |           49
```

El dato está guardado, en el crudo, y la columna está vacía. **Es el mismo
patrón exacto que el bug de las cocheras del 14/08** (`raw.parking` guardado,
`raw.parking_spaces` leído): un renombre entre capas que no falla, no rompe, y
apaga algo.

**Por qué importa:** `published_at` es la entrada de **tres** controles
distintos, y los tres están apagados hoy:

| Control | Dónde | Qué debería hacer | Qué hace |
|---|---|---|---|
| `listing_age_coef` | `adjustments.py:137` | −3% a un aviso de >90 días | siempre 1,00 |
| `f_freshness` | `engine.py:252` | 15% del score de confianza | constante 0,6 |
| `aviso_vencido` | `curate.py:130` | descartar avisos de >180 días | nunca dispara |

Tres capas, un solo campo, cero señales. `_a_candidato` (`retrieve.py:161`)
calcula `days_published` solo si `listing.published_at is not None` — que es
nunca. Doc 05 §4.1 declara el coeficiente de antigüedad del aviso como parte
del método.

Hoy son 49 avisos (Portal B). La auditoría de datos midió que Portal B trae
`publication_date` en el **76%**: cuando entre una tanda grande de ese portal,
los tres controles se encienden de golpe y nadie va a saber por qué cambiaron
los números.

**Qué hay que hacer:**
1. En `csv_scan.fila_a_card`, parsear `publication_date` a `datetime.date` y
   agregarlo a `Card` como `published_at`.
2. En `capture.ingest._upsert`, agregar `"published_at": card.published_at` a
   `datos`, y agregar `published_at` a `CAMPOS_DEL_HASH` (si el portal corrige
   la fecha, es un cambio real).
3. Como el dato entra **de a poco**, dejar en el detalle del nodo 7 cuántos
   comparables tenían fecha, para que el efecto sobre la confianza sea
   atribuible.

**Cómo se verifica que quedó bien:**

```bash
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos --forzar
```
```sql
select count(published_at) from corpus.listings where active and source='PORTAL_B';
-- > 0, y del orden de los 49 que hoy están en raw.attrs
```
Y un informe nuevo tiene que mostrar `days_published` distinto de `null` en
`report_comparables`.

**Riesgo de tocarlo:** enciende tres mecanismos a la vez y **cambia el número
de los informes** (el coeficiente 0,97 y la confianza). Hay que correr el
backtest y volver a mirar la calibración de la confianza. Agregar
`published_at` a `CAMPOS_DEL_HASH` provoca una reescritura masiva en la próxima
ingesta (8.497 avisos "actualizados"), que es lo correcto pero hay que
esperarlo.

---

### H-22 · Quedan dos `upsert`, y divergen en un campo que decide si el aviso existe

**Sección:** S3 · **Severidad:** media

**Qué está mal:** `capture.ingest._upsert` (el camino real, CSV) y
`portal_a_ingest.upsert_card` (captura por HTML) hacen lo mismo con
diferencias. La que importa: **`upsert_card` nunca escribe
`neighborhood_id`.**

**Cómo lo verifiqué:** lectura comparada de las dos funciones, y la evidencia
en la base de que el camino que sí lo escribe funciona:

```sql
select source, count(*) filter (where neighborhood_id is null) sin_barrio, count(*)
from corpus.listings where active group by 1;
 PORTAL_B |  6 |  298
 PORTAL_A|  1 | 8199
```

```sql
select count(title) con_titulo, count(*) from corpus.listings where active;
 con_titulo | count
          0 |  8497       -- `upsert_card` escribe `title`; `_upsert` no
```

La tabla completa de divergencias:

| | `_upsert` (real) | `upsert_card` (HTML, apagado) |
|---|---|---|
| `neighborhood_id` | sí (`_match_neighborhood`) | **no lo escribe** |
| `title` | no lo escribe | sí |
| `price_on_request` | `False` fijo | `card.price is None` |
| `quality_flags` | solo `surface_total_only` | + `sin_superficie`, `sin_precio` |
| filtro de entrada | `motivo_descarte()` | `card.is_usable` |
| primer snapshot | siempre | solo si hay precio |

**Por qué importa:** el nodo 2 filtra por `Listing.neighborhood_id.in_(...)`.
Un aviso sin barrio **no existe** para ningún informe. Si mañana se enciende la
captura por HTML —que es el plan para el nodo 3— cargaría avisos invisibles, y
el síntoma sería "capturé 200 avisos y los informes no mejoraron". Es
exactamente la forma del bug que costó una tanda entera el 14/08: dos copias,
se arregla la que no corre, y el corpus no cambia un campo.

`card.is_usable` vs `motivo_descarte()` es la otra: dos reglas de descarte
distintas ensucian el corpus de dos formas distintas según por dónde entró el
aviso.

**Qué hay que hacer:** borrar `upsert_card` y que `ingest_barrios` llame a
`capture.ingest._upsert`, que ya recibe `source` como parámetro y sirve para
los dos portales. Lo único que hay que mover es `title` (agregarlo a `_upsert`)
y decidir si `quality_flags` lleva `sin_precio`/`sin_superficie` — hoy esos
avisos ni entran, así que probablemente no.
Y un test del mismo tipo que el que ya existe:

```python
def test_hay_un_solo_upsert():
    from tasador.ingest import portal_a_ingest
    assert not hasattr(portal_a_ingest, "upsert_card")
```

**Cómo se verifica que quedó bien:**

```
uv run pytest tests/test_portal_a.py tests/test_ingest_csv.py -q
rg "async def upsert" src   # una sola coincidencia
```

**Riesgo de tocalo:** `tests/test_portal_a.py` ejercita `upsert_card`; hay que
reescribir esos tests contra `_upsert`. La captura por HTML está apagada, así
que en producción no cambia nada hoy.

---

### H-23 · El contrato tarjeta→`raw` se resuelve con `getattr(..., None)`: lo que falta se ignora en silencio

**Sección:** S3 · **Severidad:** media

**Qué está mal:** `_raw` y `_content_hash` recorren `CAMPOS_DEL_RAW` y
`CAMPOS_DEL_HASH` con `getattr(card, attr, None)`. Un atributo que la tarjeta
no tenga —por un typo, o porque una clase de tarjeta se quedó atrás— **no falla:
simplemente no se guarda y no entra al hash**. Y `Card` está atrás.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s3_contrato.py
Card:
  atributos de CAMPOS_DEL_RAW que la tarjeta NO tiene : ['raw_features']
  atributos de CAMPOS_DEL_HASH que la tarjeta NO tiene: []

Card:
  atributos de CAMPOS_DEL_RAW que la tarjeta NO tiene : ['surface_semi', 'surface_uncovered',
                                                         'age_years', 'orientation', 'publisher']
  atributos de CAMPOS_DEL_HASH que la tarjeta NO tiene: ['age_years', 'orientation']

_raw sobre una tarjeta minima -> {}
_content_hash -> 103223adc63630a6
(ningun error)
```

**Por qué importa:** el 14/08 se arregló precisamente esto —*"el `content_hash`
tiene que cubrir todo lo que se persiste"*— y el arreglo llegó a
`Card` y no a `Card`. Para un aviso de Portal B capturado por
HTML, el hash **no cubre antigüedad ni orientación**: el portal las cambia y
nosotros leemos "sin cambios", que es el bug de la lección 3 de ESTADO §9,
todavía abierto para el otro portal. Hoy no muerde porque la captura por HTML
está apagada y el camino de CSV construye siempre un `Card`, pero es
una bomba con la mecha puesta.

Y `raw_features` en `CAMPOS_DEL_RAW` no corresponde a ningún atributo de
ninguna tarjeta: es una clave muerta que el diccionario declara como contrato.

**Qué hay que hacer:** convertir el contrato en algo que falle.
1. Un test que recorra `CAMPOS_DEL_RAW` y `CAMPOS_DEL_HASH` contra **todas** las
   clases que implementan `PortalCard` y falle si falta alguno. Es la sonda de
   arriba, convertida en `tests/test_ingest_csv.py::test_toda_tarjeta_cumple_el_contrato_del_raw`.
2. Completar `Card` con `age_years`, `orientation`, `publisher`,
   `surface_semi`, `surface_uncovered` (aunque queden en `None`).
3. Sacar `raw_features` de `CAMPOS_DEL_RAW`, o agregarlo a las tarjetas.

**Cómo se verifica que quedó bien:**

```
uv run python docs/auditoria/sondas/s3_contrato.py
# las dos listas vacías para las dos clases
uv run pytest tests/test_ingest_csv.py -q -k contrato
```

**Riesgo de tocarlo:** agregar campos a `Card` cambia su
`content_hash` y provoca una reescritura de los 298 avisos de ese portal en la
próxima ingesta. Es correcto y hay que esperarlo.

---

### H-24 · El `--dry-run` no contesta la pregunta para la que existe

**Sección:** S3 · **Severidad:** media

**Qué está mal:** la rutina operativa que dejó la auditoría de datos
(`2026-08-14-que-datos-tenemos.md` §4) empieza con: *"¿Cambió algo en el disco
desde la última vez? (barato, no escribe)"* y prescribe
`ingest_csv.py --dry-run`. **El dry-run no consulta `_ya_procesado`**: imprime
todos los archivos como si fueran nuevos y nunca dice cuál ya está ingerido.
Tampoco imprime un total.

**Cómo lo verifiqué:**

```
$ uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos --dry-run
DRY RUN — 1779 archivos, no se escribe nada
...
[1.744 líneas "(ignorado: no tiene esquema de avisos)"]
[8 archivos con sha256 + filas + usables]
[FIN — sin resumen, sin "ya estaban", sin "nuevos"]
```

Cero apariciones de "ya estaba" o "ya procesado" en 1.779 archivos. El camino
sin `--dry-run` sí imprime `archivos nuevos` / `ya estaban`
(`ingest_csv.py:99-101`); el dry-run no.

**Por qué importa:** es el control que existe para no repetir el error de los
**4.100 avisos que estuvieron en el disco sin ingerir** mientras se daba el
corpus por completo. La lección quedó escrita, el comando quedó documentado, y
el comando no responde eso. Es un gate que pasa sin medir (R3), en la rutina
operativa.

Y hay un segundo problema: `_archivos_a_ingerir` recorre **1.779 archivos**
porque el escaneo recursivo entra a los perfiles de Chrome del scraper. 1.744
de ellos se descartan por contenido. Tarda minutos y produce 5.000 líneas de
salida para 8 archivos que importan.

**Qué hay que hacer:**
1. En la rama `dry_run` de `scripts/ingest_csv.py`, abrir una sesión de solo
   lectura y llamar a `_ya_procesado(session, sha)`; imprimir `NUEVO` o
   `ya ingerido` por archivo.
2. Imprimir el mismo bloque de resumen que el camino real, con
   `archivos nuevos` arriba de todo.
3. No imprimir una línea por archivo ajeno: contarlos y decir el total al final.
4. Podar el recorrido: saltear directorios que contengan un `Default/` o
   `state*/` de perfil de Chrome, o —más simple y robusto— saltear cualquier
   archivo de menos de N bytes antes de abrirlo.

**Cómo se verifica que quedó bien:**

```
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos --dry-run
# la última línea tiene que decir cuántos archivos son NUEVOS.
# Corriendo la ingesta de verdad después, ese número tiene que coincidir con
# `archivos nuevos` del resumen.
```

**Riesgo de tocarlo:** ninguno. El dry-run no escribe y hay que mantenerlo así:
la sesión que abra tiene que ser de lectura y sin `commit`.

---

### H-25 · Once avisos conservan la clave vieja `raw.parking`

**Sección:** S3 · **Severidad:** baja

**Qué está mal:** residuo del renombre del 14/08. Once avisos vigentes tienen
la cochera guardada bajo `raw.parking` y no bajo `raw.parking_spaces`, que es
lo que lee el nodo 2.

**Cómo lo verifiqué:**

```sql
select count(*) filter (where raw ? 'parking') clave_vieja,
       count(*) filter (where raw ? 'parking_spaces') clave_nueva,
       count(*) filter (where raw ? 'parking' and not raw ? 'parking_spaces') solo_vieja
from corpus.listings where active;
 clave_vieja | clave_nueva | solo_vieja
          11 |          92 |         11
```

**Por qué importa:** once cocheras invisibles sobre 103. En sí es despreciable;
importa porque **el arreglo del 14/08 se dio por completo y no lo estaba**, y
porque no hay ninguna consulta ni test que verifique que el corpus no tiene
claves fuera de `CAMPOS_DEL_RAW`.

**Qué hay que hacer:** reingerir con `--forzar` los archivos que originaron
esas filas (`select distinct source, source_id ...`), o —más simple— un
`UPDATE` puntual. Y agregar a `scripts/` o al test suite un chequeo de que
`jsonb_object_keys(raw)` no contiene claves fuera del conjunto declarado.

**Cómo se verifica que quedó bien:**

```sql
select count(*) from corpus.listings where active and raw ? 'parking';
-- 0
```

**Riesgo de tocarlo:** es un UPDATE sobre el corpus. Requiere autorización
(datos), y el humano está tocando el corpus en paralelo: coordinarlo.

---

## 2. Lo que verifiqué y está bien

- **Descarte temprano compartido.** `csv_scan` llama a `motivo_descarte` de
  `capture.ingest`; hay un test de identidad de función.
- **Idempotencia por archivo (sha256) y por aviso (`content_hash`).** Verificada
  contando filas antes y después de un dry-run: 93.495 → 93.495.
- **Detección por contenido y no por ruta.** `parece_de_avisos` descartó 1.744
  archivos ajenos sin producir un solo `IngestRun` falso, que era el objetivo
  declarado.
- **`_dec("")` devuelve `None` y no `0`.** Está bien y está comentado por qué.
- **La orientación del portal se lee de `condition` Y de `orientation`**, así
  que si el scraper corrige el nombre no se rompe.

## 3. Qué NO pude verificar

- **La captura por HTML** (`sources/playwright_fetcher.py`, 0% de cobertura, 232
  líneas). Está apagada y requiere Chrome real con IP residencial. No la corrí.
- **`sources/panel_externo.py`** con la fuente real: `PANEL_SOURCE_ENABLED=false`
  y no tengo credenciales. El test que verifica que apagada no rompe nada sí
  corre (`tests/test_panel_externo.py`).
- **Si hay tandas del scraper sin ingerir hoy.** El dry-run no lo dice (H-24) y
  el corpus lo maneja el humano en paralelo: **bloqueado por datos**, a
  propósito.

