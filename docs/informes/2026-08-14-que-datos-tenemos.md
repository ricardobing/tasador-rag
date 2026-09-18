# Qué datos tenemos de verdad — auditoría campo por campo

**14/08/2026, noche.** Pregunta que la disparó: *"de los datos nuevos de
Palermo, ¿están completos? ¿qué tenemos? ¿hay datos en los CSV/JSON que no
guardamos?"*

La respuesta corta: **el scraper entrega 47 campos, guardábamos el 100% del
contenido pero usábamos ~60%**, y había **4.100 avisos sin ingerir** porque el
scraper reescribió un archivo después de que lo procesamos.

---

## 1. Qué trae el scraper, y cuánto viene lleno

Fuente: los `.json` de `C:\datos\avisos` (los `.csv` y `.jsonl` traen
lo mismo, verificado). El porcentaje es **cuántos avisos traen el campo con
algún valor**.

| Campo | Portal A (8.586) | Portal B (59) | ¿Nos sirve? |
|---|---|---|---|
| `price` · `currency` | 98% | 98% | ⭐ es el dato |
| `covered_area` | 97% | 84% | ⭐ superficie ponderada |
| `total_area` | 0% | 100% | ⭐ |
| `address` · `street` | 100% | 100% | ⭐ dedup y geocoding |
| `street_number` | 88% | 93% | ⭐ (pero es la altura APROXIMADA) |
| `rooms` | **0,6%** ⚠️ | 100% | ⭐ ambientes |
| `bedrooms` | 76% | 72% | dormitorios |
| `bathrooms` | 37% | 84% | baños |
| `age` | 48% | 84% | ⭐ **coeficiente de ajuste** |
| `condition` *(es la orientación)* | 0% | 66% | ⭐ **coeficiente de ajuste** |
| `parking_count` | 0,07% | 30% | ⭐ **coeficiente de ajuste** |
| `expenses` | 42% | 52% | coeficiente de ajuste |
| `amenities` | 0% | 86% | coeficiente de ajuste |
| `description` | 99% | 100% | ⭐ es lo que lee el nodo 4 |
| `publication_date` | 0% | 76% | días publicado |
| `publisher_name` | 94% | 100% | informativo |
| `images` | 0% | 100% | no se usa |
| `orientation` *(punto cardinal)* | 0% | 54% | ❌ no está en doc 05 |
| `floor` · `apartment` · `toilets` | **0%** | **0%** | la columna existe, siempre vacía |
| `latitude` · `longitude` | **0%** | **0%** | ❌ por eso `distance_m` es siempre None |
| `city` · `province` · `publisher_type` | 0% | 0-100% | no se usa |

**Tres cosas que hay que leer de esa tabla:**

1. **Portal A casi no trae `rooms`** (56 de 8.586). Los ambientes de la tanda
   grande salen de `bedrooms` o del nodo 4 leyendo la descripción. Portal B sí
   los trae siempre.
2. **`floor` y las coordenadas nunca vienen.** El scraper tiene la columna y
   está vacía en el 100% de los casos. Por eso el nodo 2 no puede calcular
   distancias y el piso solo puede salir de la descripción.
3. **El scraper llama `condition` a la ORIENTACIÓN.** Los valores son
   `Frente`, `Contrafrente`, `Lateral`, `Interno` — que es exactamente nuestro
   vocabulario de orientación (doc 05 §4.1) — y usa `orientation` para el punto
   cardinal (`E`, `NO`), que no mueve ningún coeficiente. No es un error suyo:
   es como lo rotula Portal B.

---

## 2. Qué guardábamos, y qué no llegaba al motor

**Nada se perdía del contenido**: el JSON completo del scraper queda en
`listings.raw.attrs`, y reprocesar es gratis. El problema era otro: **dónde**
quedaba guardado.

El nodo 2 (`_a_candidato`) arma el candidato leyendo claves del **nivel
superior** de `listings.raw`. Lo que está anidado en `raw.attrs` no lo mira
nadie.

| Campo del archivo | Dónde quedaba | ¿Llegaba al motor? |
|---|---|---|
| `rooms`, `age`, `covered_area`, `total_area`, `expenses` | `raw` (nivel superior) | ✅ sí |
| `bedrooms`, `bathrooms` | `raw` (nivel superior) | ⚠️ se guardan, pero `Property` no tiene esos campos: **no ajustan nada hoy** |
| `parking_count` | **en ningún lado** | ❌ se parseaba en `Card.parking` y `card_to_listing_kwargs` no lo incluía |
| `condition` (= orientación) | `raw.attrs.condition`, en mayúscula | ❌ nadie lo lee, y `_enum(Orientation, "Frente")` da None |
| `orientation` (cardinal), `floor`, `amenities`, `publication_date`, `images` | `raw.attrs` | ❌ nadie los lee |

**El costo concreto:** se pagaba una extracción por LLM para adivinar de la
prosa un dato que venía declarado en el archivo. Medido: **31 avisos** traían
la orientación del portal y ninguno llegaba al motor; las cocheras declaradas
se descartaban en el 100% de los casos.

### Lo arreglado (14/08, noche)

- Las cocheras **sí estaban guardadas**, con el nombre `raw.parking` — y el
  nodo 2 busca `raw.parking_spaces`. No era un dato perdido: era un dato
  invisible por un renombre de una palabra. Ahora se guarda con la clave que
  el nodo lee.
- La orientación declarada → se normaliza al vocabulario de doc 05
  (`"Frente"` → `frente`) y se persiste como **`raw.orientation`**. Se leen las
  dos columnas del scraper, así que si mañana corrige el nombre sigue andando.
  El punto cardinal se descarta a propósito: llenar el campo con un valor que
  el motor ignora taparía el hueco real.
- **`CAMPOS_DEL_RAW`**: un solo diccionario declara con qué nombre se guarda
  cada campo. Era exactamente la clase de cosa que no se puede tener repartida.
- **El `content_hash` ahora cubre todo lo que se persiste y mueve un precio.**
  No lo hacía, y eso rompía dos cosas: un aviso al que el portal le cambiaba
  las cocheras se leía como "sin cambios", y reingerir para rellenar un mapeo
  corregido no escribía nada porque el upsert corta antes de tocar la fila.
- **Precedencia sin cambiar**: si el nodo 4 extrajo el campo, gana la
  extracción; el dato del portal LLENA EL HUECO. Invertirlo sería cambiar de
  qué depende un precio sin haberlo medido, y hoy no hay golden set que pueda
  decir cuál de los dos acierta más. Queda como pregunta abierta.

### El error del medio, que es el que más enseña

**Había TRES copias de "cómo se guarda una tarjeta"**: la de
`ingest/core.py`, la de `ingest/portal_a_ingest.py` y el mapeo suelto de
`csv_scan.py`. El primer arreglo se hizo en `portal_a_ingest` —con cinco
tests nuevos que pasaron en verde— y **el corpus no cambió un solo campo**:
esa copia la usa únicamente la captura por HTML, que está apagada. El camino
que carga el corpus de verdad, `scripts/ingest_csv.py`, nunca pasa por ahí.

Un test contra la implementación que no corre es un gate que no mide nada, y
se ve exactamente igual que uno que sí mide: verde. Lo que lo destapó no fue
releer el código, fue **contar filas en la base después de reingerir** y ver
un cero donde tenía que haber un número.

Ahora hay una sola implementación (`capture.ingest._raw` y `_content_hash`,
las que usa el camino real), `portal_a_ingest` delega en ella, y hay un test
—`test_hay_una_sola_implementacion_del_raw_y_del_hash`— que falla si vuelven
a separarse.

**`bedrooms` / `bathrooms` siguen sin usarse.** Están guardados y disponibles;
lo que falta es un coeficiente en `config/adjustments.yaml` que diga cuánto
valen, y eso es una medición (regresión hedónica), no un mapeo. Queda anotado
como deuda, no como bug.

---

## 3. Los 4.100 avisos que estaban afuera

`corpus.ingest_runs` registra el `sha256` de cada archivo procesado y por eso
la ingesta es idempotente. Al auditar, **el sha de la corrida más grande
(4.062 avisos) no coincidía con ningún archivo del disco**: el scraper
reescribió `output_caba/portal_a_...csv` con más datos después de que lo
procesamos.

```
archivo en disco hoy          8.586 filas  (8.184 usables)
lo que habíamos ingerido      4.062 filas
                              ─────────────
diferencia                   ~4.100 avisos que nunca entraron
```

**La idempotencia por hash funcionó exactamente como debía** —no reprocesó lo
mismo dos veces— pero nadie estaba mirando si el archivo había cambiado. La
lección es operativa y va al manual: **después de cada tanda del scraper hay
que correr la ingesta de nuevo**; es barata, es idempotente por contenido, y un
archivo reescrito es contenido nuevo.

---

## 3 bis. El corpus después de todo esto

```
                        ANTES (14/08 tarde)   DESPUÉS
avisos vigentes                    4.121        8.497
propiedades únicas (canónicos)     2.672        5.027
avisos agrupados por dedup         1.826        4.606
cocheras visibles para el motor         0           92
orientación declarada del portal        0           31
```

Y lo que **cada aviso trae**, medido sobre el corpus vigente:

| Campo | Portal A (8.199) | Portal B (298) |
|---|---|---|
| ambientes (`rooms`) | 37 ⚠️ | 298 |
| dormitorios | 6.266 | 237 |
| baños | 3.009 | 264 |
| antigüedad | 3.982 | 40 |
| cocheras | 7 | 85 |
| orientación declarada | 0 | 31 |

**Portal A casi no publica ambientes** (37 de 8.199) y es el 96% del corpus.
Los ambientes de esos avisos salen del nodo 4 leyendo la descripción, o de
`bedrooms` — que sí viene en el 76%. Es el hueco de datos más grande que
queda, y no se arregla con código: o lo trae el scraper, o lo infiere el
extractor.

---

## 3 ter. El prompt que medía bien y extraía cero

Con el corpus grande y las features recién extraídas, la cuenta no cerraba:
**5.016 avisos con features y solo 28 con estado y 37 con orientación.** Menos
del 1%, cuando el golden set decía 71% para el piso.

La forma de saber si el problema es el modelo o los datos es preguntarle a los
datos: cuántos avisos **dicen** el campo en el texto y quedaron en `null`.

```
2.704 avisos dicen la orientación  ->  extraída en 31     (98,9% omitidos)
  989 avisos dicen el piso         ->  extraído en 21     (97,9% omitidos)
```

Y no es que los textos sean sutiles: *"Desarrollado en piso alto con
disposición contrafrente y orientación noreste"*, *"excelente departamento de 3
ambientes en planta baja al contrafrente"*.

**Primera hipótesis, y era mía: el tamaño de lote.** La corrida masiva usó
`--lote 8` y la config dice 6, con un comentario que documenta que con 10 la
salida se pasaba de `max_tokens`. Experimento controlado, mismos avisos:

```
lote=6   orientación 2%      lote=8   orientación 0%
```

Las dos igual de mal: **la hipótesis era falsa.** Menos mal que se midió.

**Segunda hipótesis: el prompt.** 48 avisos que TODOS declaran la orientación,
mismo modelo, mismo lote, tres corridas cada uno:

```
                orientación (de 48)        piso (de 48)      citas falsas
extractor/v1     22 · 41 · 43              20 · 30 · 28        0 · 0 · 2
extractor/v2      0 ·  0 ·  0               0 ·  0 ·  0        0 · 0 · 0
```

**v2 devuelve null en todo.** No alucina —eso lo hubiera cazado la
verificación de citas— simplemente no extrae. Y las de v1 verifican: 41 de 43
con la cita exacta en el texto.

### Por qué esto pasó, que es lo que hay que llevarse

v2 se puso en producción el 13/08 **con evidencia**: midió 68% global contra
41% de v1 sobre el golden set. El golden set son 24 avisos de Portal B en
Belgrano. El corpus real es **96% Portal A**, y ahí v2 da cero.

No es que la medición del 13/08 estuviera mal hecha. Es que **un golden set de
un solo portal mide un solo portal**, y la decisión que salió de ahí se aplicó
al otro 96%. Es la misma lección que este proyecto ya tenía escrita para los
barrios —"un sistema probado sobre un solo barrio está probado sobre un solo
barrio"— repetida en la dimensión que no se había mirado.

Lo corregido: `config/agents.yaml` vuelve a `extractor/v1`, con los números
adentro, y `scripts/extraer_corpus.py --reextraer extractor/v2` reprocesa lo
que quedó mal (la columna `extractor_version` existe justamente para poder
corregir una decisión de prompt sin borrar la tabla).

**Resultado de reprocesar el corpus entero — 4.995 avisos, USD 1,38, 55 min.**
El número que vale es el recall sobre los avisos que SÍ declaran el campo:

```
                        CON v2            CON v1
orientación        31 / 2.704  (1,1%)   2.315 / 2.638  (87,8%)
piso               21 /   989  (2,1%)     746 /   964  (77,4%)
```

Y la verificación de citas hizo su trabajo: **108 avisos quedaron marcados
`needs_review`** porque el modelo citó algo que no está en el texto. Es el 2%,
se ven en `/comparables`, y son candidatos naturales al golden set.

Quedan 139 filas con features de v2. **Son todas duplicados no canónicos**
—verificado: 0 activas canónicas, 0 inactivas— así que ninguna llega al motor:
el nodo 2 trae un solo aviso por cluster. No hay nada que reprocesar.

Lo que NO se resolvió y queda anotado: **v2 puede seguir siendo mejor para
Portal B.** Saberlo pide un golden set con los dos portales — que es
exactamente lo que está preparado y sin anotar en
[la guía de anotación](../guias/anotar-golden-set.md).

---

## 4. Qué mirar de acá en adelante

`/admin/fuentes` ya muestra la última corrida por fuente y los conteos. Lo que
esta auditoría agrega como rutina:

```powershell
# 1. ¿Cambió algo en el disco desde la última vez? (barato, no escribe)
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos --dry-run

# 2. Ingerir. Idempotente por sha256 del archivo y por (source, source_id).
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos

# 3. Dedup de lo nuevo (33% del corpus de Palermo son republicaciones)
uv run python scripts/dedup_corpus.py --aplicar

# 4. Extraer features de lo nuevo (reanudable, ~USD 0,20 por 2.500 avisos)
uv run python scripts/extraer_corpus.py --paralelo 16 --lote 8
```

Y la pregunta que queda abierta, ahora que hay datos para responderla: **¿el
dato declarado por el portal le gana al que infiere el LLM de la descripción?**
Con la orientación se puede medir — hay avisos que traen las dos — y es
exactamente el tipo de decisión que el golden set debería resolver.
