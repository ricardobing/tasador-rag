# 04 — Pipeline de agentes

**Orquestador:** LangGraph con checkpointer en Postgres (ADR-001).
**Regla madre:** el LLM extrae, clasifica, juzga y redacta. **Nunca calcula el precio.**

---

## 1. El grafo

```
              ┌──────────────────────┐
              │ 1. normalize_subject │  determinístico + geocoding
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ 2. retrieve_candidates│ SQL híbrido (filtros + kNN)
              └──────────┬───────────┘
                         ▼
                  ¿candidatos ≥ 25?
                    │           │
                 no │           │ sí
                    ▼           │
        ┌──────────────────────┐│
        │ 3. ondemand_ingest   ││  fetch a portal, acotado
        └──────────┬───────────┘│
                   └────────────┤
                                ▼
              ┌──────────────────────┐
              │ 4. extract_features  │  LLM barato · batch · tipado
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ 5. dedup_cluster     │  reglas → embeddings → LLM
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ 6. curate            │  reglas duras + LLM juez
              └──────────┬───────────┘
                         ▼
                  ¿válidos ≥ 5?  ──no──▶ ┌──────────────────┐
                         │ sí            │ INSUFFICIENT_DATA│
                         ▼               └──────────────────┘
              ┌──────────────────────┐
              │ 7. adjust_and_value  │  ⚙ DETERMINÍSTICO — sin LLM
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ 8. market_context    │  CrewAI (crew de 2)
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ 9. write_report      │  LLM redactor
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │ 10. critic           │  LLM adversarial + verificación dura
              └──────────┬───────────┘
                    ¿aprueba?
                  no │      │ sí
      (máx 2) ◀──────┘      ▼
                  ┌──────────────────────┐
                  │ 11. render_pdf       │
                  └──────────┬───────────┘
                             ▼
                        SUCCEEDED
```

**Checkpointing:** el estado se persiste después de cada nodo
(`langgraph.checkpoint.postgres`). Si el worker muere en el nodo 6, al reintentar
retoma en el 6. Los nodos 2-6 son los caros; no repetirlos vale plata real.

**Estado del grafo** (`ReportState`, un `TypedDict` de Pydantic): `subject`,
`candidates`, `features`, `clusters`, `curated`, `valuation`, `context`, `draft`,
`critique`, `attempts`, `costs`, `errors`.

---

## 2. Nodo por nodo

### Nodo 1 — `normalize_subject`

**Tipo:** determinístico · **LLM:** no · **Costo:** $0

| | |
|---|---|
| **Entrada** | `SubjectProperty` cruda (dirección en texto libre + atributos) |
| **Salida** | `NormalizedSubject`: dirección desglosada, `neighborhood_id`, lat/lng, superficie ponderada |

**Qué hace:**

1. Normaliza la dirección: mayúsculas/tildes fuera, abreviaturas expandidas
   (`AV`/`AVDA` → `Avenida`, `Gral.` → `General`). **Se reusa el criterio del
   `property-matcher` del panel**, que ya resolvió este problema con datos reales.
2. Geocodifica con **Nominatim (OSM)** — gratis, self-hosteable, sin API key.
   Fallback a Google Geocoding solo si Nominatim falla y hay clave configurada.
3. Resuelve el barrio: primero por polígono si hay coordenadas, si no por match
   contra `neighborhoods.aliases` con trigram.
4. Calcula **superficie ponderada** = `cubierta + 0.5 × (total − cubierta)`.

**Falla si:** no puede resolver un barrio. Sin barrio no hay comparables, y es mejor
fallar acá con un mensaje claro ("no pudimos ubicar la dirección") que producir un
informe sobre la zona equivocada.

---

### Nodo 2 — `retrieve_candidates`

**Tipo:** determinístico · **LLM:** no (sí embeddings locales) · **Costo:** $0

| | |
|---|---|
| **Entrada** | `NormalizedSubject` |
| **Salida** | Lista de `listing_id` candidatos con `similarity_score` y `distance_m` |

**Estrategia: filtro duro primero, semántica después.** El error clásico de un RAG mal
hecho es buscar por embedding y filtrar después; acá la mayor parte de la señal es
estructurada.

> ⚠️ **CORREGIDO el 13/08/2026 — el JOIN era INNER y devolvía cero.**
> Las features las produce el nodo 4, que corre DESPUÉS de este. Medido: los
> avisos vigentes del corpus tienen **0 filas** en `listing_features`, así que
> con un INNER JOIN todo informe sobre mercado vigente devolvía cero
> candidatos. Era un huevo-gallina en el diseño escrito.
> El JOIN es **LEFT**: lo que haya de features enriquece y ordena, lo que falte
> no excluye. El filtro duro por atributos vive en el **nodo 6**, que es donde
> ya estaba diseñado y donde las features ya existen.
> Implementación real: `src/tasador/agents/nodes/retrieve.py`.

```sql
WITH duros AS (
  SELECT l.id, lf.*
  FROM corpus.listings l
  LEFT JOIN corpus.listing_features lf ON lf.listing_id = l.id   -- LEFT, no INNER
  WHERE l.active
    AND l.operation = 'SALE'
    AND l.currency = 'USD'
    AND NOT l.price_on_request
    AND lf.property_type = :tipo
    AND l.neighborhood_id = ANY(:barrio_y_limitrofes)
    AND lf.surface_covered BETWEEN :m2 * 0.70 AND :m2 * 1.30
    AND (lf.rooms BETWEEN :amb - 1 AND :amb + 1 OR lf.rooms IS NULL)
    AND l.last_seen_at > now() - interval '120 days'
)
SELECT * FROM duros
ORDER BY (SELECT embedding FROM corpus.listing_embeddings 
          WHERE listing_id = duros.id) <=> :subject_embedding
LIMIT 60;
```

**Relajación progresiva:** si con esos filtros hay < 25 candidatos, se relajan por
etapas y **se registra qué se relajó** (impacta la confianza del informe):

| Paso | Se relaja | Penalización de confianza |
|---|---|---|
| 0 | filtros base | — |
| 1 | superficie ±30% → ±40% | leve |
| 2 | ventana 120d → 180d | leve |
| 3 | barrios limítrofes → comuna | media |
| 4 | ambientes ±1 → ±2 | media |

Si tras el paso 4 sigue habiendo menos de 25, va al nodo 3.

> ⚠️ **CORREGIDO el 13/08/2026 — dos cosas.**
>
> **1. "Limítrofes" y "comuna" no tenían de dónde salir.** Medido: 0 de 59
> barrios con centroide, 0 con `parent_id`, 0 avisos con coordenadas. La
> alternativa de escribir a mano una tabla de adyacencia se descartó por ser un
> dato inventado e imposible de verificar. Se geocodificaron los 59 centroides
> (`scripts/geocode_neighborhoods.py`) y el alcance pasó a ser **radio en
> metros**: `barrio` = 0, `barrio_y_limitrofes` = 3.500 m, `comuna` = 7.000 m.
> Los metros salen de la medición: barrios que se tocan quedan a 1.500-3.100 m.
> Es además mejor semántica — dos barrios pueden lindar por una punta y tener
> sus centros a 4 km.
>
> **2. La penalización se cobraba de más.** Se reportaba el último escalón
> *intentado*, no el que produjo el set devuelto. En Belgrano los 5 escalones
> daban los mismos 22 avisos —no hay más en la base— y el informe salía con la
> penalización máxima. Ahora refleja la relajación que **sirvió**. Efecto
> medido: confianza BAJA → MEDIA sin que cambie el valor.
>
> La escalera vive en `config/agents.yaml`, no en el código.

---

### Nodo 3 — `ondemand_ingest` (condicional)

**Tipo:** I/O · **LLM:** no · **Costo:** ~USD 0,01-0,05

Se dispara solo cuando el corpus no alcanza. Consulta los sitemaps de Portal A
filtrados por el barrio, baja hasta **N fichas nuevas** (N configurable, default 40),
las normaliza y las inserta en `listings`.

**Restricciones duras:** respeta `fetch_budget_monthly` del tenant; máximo 1 request
cada 2 segundos con jitter; si el presupuesto está agotado o el portal bloquea,
**no falla el informe** — sigue con lo que hay y anota `quality_flags`.

---

### Nodo 4 — `extract_features`

**Tipo:** LLM · **Modelo:** `extractor` (DeepSeek-chat o similar barato) · **Costo:** ~USD 0,004 por informe

| | |
|---|---|
| **Entrada** | Avisos candidatos sin `listing_features` o con `extractor_version` vieja |
| **Salida** | Una fila de `listing_features` por aviso, validada con Pydantic |

**Por qué hace falta un LLM acá.** El dato duro de un aviso viene incompleto y lo
importante está en la prosa:

> *"Excelente 3 ambientes al frente en Villa Urquiza. Muy luminoso. Necesita
> refacción en cocina y baño. Expensas $85.000. Apto crédito. Cochera cubierta
> opcional. 4to piso por escalera."*

De ahí hay que sacar: `orientation=frente`, `condition=a_refaccionar`,
`expenses_ars=85000`, `credit_eligible=true`, `parking_spaces=0` (opcional ≠
incluida), `floor_number=4`, `has_elevator=false`. Ninguna regex hace eso bien, y
cada uno de esos campos mueve el precio.

**Cómo se hace bien:**

- **Salida tipada obligatoria** con `instructor` + Pydantic. Si no valida, reintenta
  con los errores de validación como feedback; si vuelve a fallar, `needs_review=true`
  y el aviso queda sin features (no entra como comparable).
- **Batching:** 6 avisos por llamada, en lotes paralelos.
  `AJUSTADO el 13/08:` con 10 la salida (16 campos + 5 citas por aviso) se pasaba
  de `max_tokens` y se perdía el lote entero. Y los lotes van en paralelo: 253 s
  → 145 s sobre 22 avisos.
- **`confidence` por campo, VERIFICADA y no declarada.** `IMPLEMENTADO distinto
  del diseño original, y a propósito.` No se le pregunta al modelo qué tan seguro
  está —los modelos son malos estimando su propia confianza. Se le exige, para
  cada uno de los cinco campos que mueven el precio (`condition`, `orientation`,
  `floor_number`, `has_elevator`, `age_years`), **el fragmento textual del aviso
  que lo justifica**, y ese fragmento se busca en el texto original sin LLM:

  | Situación | Confianza | ¿Ajusta el precio? |
  |---|---|---|
  | Citó y la cita está en el aviso | 1,00 | sí |
  | Dio un valor sin citar (infirió) | 0,40 | no |
  | Citó algo que NO está en el aviso | 0,00 | no, y `needs_review=true` |

  Medido sobre los 22 avisos reales de Belgrano: **0 citas falsas**.
- **Chequeo de omisiones:** un modelo al que se le mandan 6 avisos puede
  devolver 4 y la salida valida igual contra el schema. Se comparan los `ref`
  devueltos contra los enviados y los faltantes se reintentan una vez.
  `MEDIDO:` de 22 avisos volvían 17 sin ninguna señal de error.
- **Caché por `content_hash`:** un aviso ya extraído no se vuelve a procesar nunca.
- **Defensa contra prompt injection:** el texto del aviso va delimitado y el system
  prompt dice explícitamente que el contenido es *datos a extraer*, jamás
  instrucciones. Ver [10 — Seguridad](10-seguridad-y-legal.md).

---

### Nodo 5 — `dedup_cluster`

**Tipo:** híbrido · **Modelo:** `judge` solo en casos dudosos · **Costo:** ~USD 0,002

**El nodo más importante para la corrección del precio.** Si el mismo departamento
está en Portal A y en Portal B y ambos entran como comparables, pesa doble en la
mediana. Con tres portales, triple.

**Cascada de tres capas, de barata a cara:**

| Capa | Método | Decide |
|---|---|---|
| 1 | Dirección normalizada exacta + superficie ±2 m² + ambientes iguales | Mismo inmueble, sin dudas |
| 2 | Trigram sobre dirección > 0,85 **y** similitud de embedding > 0,92 **y** precio dentro del ±5% | Mismo inmueble, alta confianza |
| 3 | LLM juez, solo para los pares que quedaron en zona gris | Devuelve `same/different/unknown` con motivo |

Un cluster aporta **un solo comparable**: el canónico, que se elige por completitud de
datos y luego por recencia.

**Caso borde tratado:** dos unidades distintas del mismo edificio (mismo `address_raw`,
distinto piso/depto). La capa 1 exige superficie **y** ambientes; si el aviso declara
`unit` distinta, no se agrupan. Los que quedan ambiguos van a la capa 3.

---

### Nodo 6 — `curate`

**Tipo:** híbrido · **Modelo:** `judge` · **Costo:** ~USD 0,003

**Reglas duras primero** (sin LLM, sin discusión):

| Se descarta | Motivo |
|---|---|
| `price_on_request` o precio nulo | No hay dato |
| Precio en ARS | El mercado de venta opera en USD; convertir agrega ruido cambiario |
| USD/m² fuera del rango [300, 12.000] | Error de carga o valor imposible |
| USD/m² fuera de p5–p95 del set | Outlier estadístico |
| Superficie ausente o < 15 m² | No se puede calcular USD/m² |
| `last_seen_at` > 180 días | Precio desactualizado |
| Aviso del propio tenant sobre la misma dirección | Auto-referencia: tasar con tu propio aviso es circular |
| Cluster ya representado | Duplicado |

**Después, el LLM juez** sobre lo que sobrevivió, con la propiedad sujeto como
referencia. Solo puede marcar `descartar` con uno de estos motivos cerrados:

- `permuta_o_financiacion` (el precio no es de venta contado)
- `en_pozo_o_construccion` (no comparable con usado terminado)
- `descripcion_inconsistente` (los datos se contradicen)
- `tipologia_distinta` (dice "departamento" pero describe un local)
- `precio_promocional` (remate, urgencia, sucesión)

**El juez no puede inventar motivos ni descartar "porque sí".** Cada descarte queda
en `report_comparables.exclusion_reason` y se muestra en el informe.

**Regla dura de salida:** si quedan **menos de 5**, el grafo corta con
`INSUFFICIENT_DATA` y `insufficient_reason` explicando qué faltó. No se estima igual.

---

### Nodo 7 — `adjust_and_value` ⚙️

**Tipo:** DETERMINÍSTICO · **LLM:** ninguno · **Costo:** $0

El corazón del sistema. Toda la matemática está en
[05 — Metodología de valuación](05-metodologia-de-valuacion.md).

| | |
|---|---|
| **Entrada** | Comparables curados + `NormalizedSubject` |
| **Salida** | `Valuation`: rango, USD/m², dispersión, confianza, y el **detalle completo de cada ajuste** |

Es Python puro, testeado con casos fijos. Dado el mismo set, devuelve siempre lo
mismo. Esto es lo que hace el backtest posible y el informe auditable.

---

### Nodo 8 — `market_context`

**Tipo:** CrewAI · **Modelo:** `judge` · **Costo:** ~USD 0,01

El único nodo donde una crew tiene sentido: la pregunta es abierta y multi-fuente.

**Crew de 2 agentes:**

| Rol | Tarea | Herramientas |
|---|---|---|
| `analista_de_zona` | "Caracterizá el mercado de {barrio} para {tipo} de {ambientes} amb" | Query a `market_index` (serie GCBA), query al corpus (stock activo, mediana, dispersión), query a `listing_snapshots` (% que bajó de precio), query a `listings.delisted_at` (tiempo medio de publicación) |
| `redactor_de_contexto` | Convierte los hallazgos en 2 párrafos citables | — |

**Restricción crítica:** las herramientas devuelven **datos de nuestra base**, no
búsquedas web abiertas. El contexto tiene que ser verificable; un agente navegando
internet libremente traería cifras imposibles de auditar. Esto es deliberado: se usa
CrewAI por lo que aporta (coordinación de una tarea abierta) sin heredar lo que no
queremos (fuentes no trazables).

**Degradación:** si este nodo falla, el informe sale igual, sin la sección de
contexto. No es bloqueante.

---

### Nodo 9 — `write_report`

**Tipo:** LLM · **Modelo:** `writer` · **Costo:** ~USD 0,01

| | |
|---|---|
| **Entrada** | `Valuation` (con todos los números ya calculados) + `MarketContext` + datos del sujeto |
| **Salida** | Markdown estructurado en secciones fijas |

**El prompt recibe los números ya hechos y su única tarea es explicarlos.** Se le
prohíbe explícitamente:

- Calcular, promediar o estimar cualquier cifra.
- Mencionar un número que no esté en el input.
- Usar adjetivos de certeza no justificados ("sin dudas", "garantizado").
- Prometer tiempos de venta que no salgan de `delisted_at`.

Secciones: resumen ejecutivo · la propiedad · metodología (en castellano llano) ·
comparables utilizados · contexto del barrio · rango recomendado y qué esperar en cada
escenario · limitaciones y alcance.

**Tono:** configurable por tenant (formal / cercano). Default: profesional y directo,
sin marketing.

---

### Nodo 10 — `critic` 🛡️

**Tipo:** híbrido · **Modelo:** `critic` (el único caro) · **Costo:** ~USD 0,015

**Este nodo es la diferencia entre un demo y un producto.**

**Fase A — verificación determinística** (sin LLM, no negociable):

1. Extrae **todos los números** del markdown con regex (precios, m², porcentajes,
   cantidades).
2. Verifica que cada uno exista en `Valuation`, `MarketContext` o
   `report_comparables`, con tolerancia de redondeo.
3. Verifica coherencia estructural: `low ≤ mid ≤ high`; el USD/m² citado × superficie
   ≈ el valor medio; la cantidad de comparables mencionada coincide con la real.

**Cualquier número no trazable = rechazo automático.** Sin apelación, sin LLM
opinando.

**Fase B — crítica adversarial** (LLM, instruido para *buscar problemas*):

- ¿Afirma algo que los datos no sostienen?
- ¿Omite una limitación relevante (pocos comparables, alta dispersión, datos viejos)?
- ¿El tono promete algo que no se puede prometer?
- ¿Contradice el nivel de confianza calculado?

**Ciclo:** rechazo → vuelve al nodo 9 con la crítica como feedback. **Máximo 2
reintentos.** Al tercero, el informe se emite **sin narrativa**: solo la tabla de
comparables y el rango, con una nota. Fundamento: los números son correctos (salieron
del nodo 7, determinístico); lo que falló es la redacción. Entregar los datos sin
prosa es mejor que no entregar nada y mucho mejor que entregar prosa no verificada.

---

### Nodo 11 — `render_pdf`

**Tipo:** determinístico · **Costo:** $0

Markdown + datos → HTML con plantilla Jinja2 (marca del tenant) → PDF con
**WeasyPrint**. Se elige WeasyPrint sobre Playwright: no necesita un Chromium de
300 MB en el contenedor, y para un documento de texto y tablas alcanza de sobra.

Se guarda en disco con hash SHA-256, se registra en `report_artifacts` y se respalda
a R2 en el backup nocturno.

---

## 3. Manejo de errores

| Situación | Comportamiento |
|---|---|
| LLM devuelve JSON inválido | Reintento con el error de validación como feedback (máx 2). Después, ese ítem se marca y el pipeline sigue |
| Proveedor de LLM caído | LiteLLM hace fallback automático al siguiente modelo configurado |
| Portal bloquea (403) | Se registra en `ingest_items.blocked`, se sigue con el corpus existente, se marca `quality_flags` |
| Timeout de un nodo | Reintento con backoff exponencial (máx 3). Después → `FAILED` con `error_code` |
| Worker muere | Al reiniciar, LangGraph retoma desde el último checkpoint |
| Comparables < 5 | `INSUFFICIENT_DATA` — es una respuesta válida, no un error |
| Crítico rechaza 3 veces | Informe sin narrativa (degradación, no falla) |

**Principio general:** degradar antes que fallar; fallar explícito antes que inventar.

---

## 4. Costo y latencia por informe (estimado)

| Nodo | Modelo | Tokens aprox. | Costo USD | Latencia |
|---|---|---|---|---|
| 1 normalize | — | — | 0 | 1-3 s |
| 2 retrieve | embeddings locales | — | 0 | < 1 s |
| 3 ingest (condicional) | — | — | 0,01-0,05 | 30-90 s |
| 4 extract (×40, batch de 10) | extractor | ~30k | 0,004 | 15 s |
| 5 dedup | judge (parcial) | ~8k | 0,002 | 5 s |
| 6 curate | judge | ~12k | 0,003 | 6 s |
| 7 value | — | — | 0 | < 1 s |
| 8 context | judge (crew) | ~15k | 0,010 | 20 s |
| 9 write | writer | ~10k | 0,010 | 15 s |
| 10 critic | critic | ~12k | 0,015 | 12 s |
| 11 pdf | — | — | 0 | 2 s |
| **Total (corpus caliente)** | | | **~USD 0,044** | **~75 s** |
| **Total (con ingesta on-demand)** | | | **~USD 0,09** | **~165 s** |

Con caché de features tibia, un segundo informe en el mismo barrio cuesta ~USD 0,03.

---

## 5. Versionado de prompts

Los prompts viven en `prompts/` como archivos versionados en git, no en el código:

```
prompts/
  extractor/v3.jinja
  dedup_judge/v2.jinja
  curator/v4.jinja
  writer/v5.jinja
  critic/v3.jinja
  bundle.lock.json      ← qué versión usa cada nodo hoy
```

`bundle.lock.json` se hashea y ese hash es el `prompt_bundle_version` que se estampa
en cada `report` y en cada `backtest_run`. Sin esto, "el sistema mejoró" es una
opinión; con esto, es una medición.

Cambiar un prompt **obliga** a correr el backtest antes de mergear (ver
[09](09-evaluacion-y-backtest.md)).
