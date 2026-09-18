# 09 — Evaluación y backtest

> Este es el documento que convierte el proyecto en algo demostrable.
> Sin números, "hice un sistema de tasación con IA" es una afirmación.
> Con números, es un resultado.

---

## 1. La pregunta que hay que poder responder

**"¿Cómo sabés que funciona?"**

La respuesta no puede ser "probé tres casos y daban bien". Tiene que ser:

> *"MdAPE de 11,4% sobre 1.850 casos reales, contra 18,9% del baseline. El 71% de las
> predicciones cae dentro del 20% del valor real. Cobertura del 78%. Y acá está la
> pantalla donde lo podés correr vos."*

---

## 2. El baseline — lo que hay que batir

**Baseline: `MEDIAN_NEIGHBORHOOD_M2`.**

```sql
valor_predicho = (
  SELECT usd_per_m2 FROM corpus.market_index
  WHERE neighborhood_id = :barrio AND property_type = :tipo
  ORDER BY period DESC LIMIT 1
) * superficie_ponderada
```

Una consulta. Sin LLM, sin agentes, sin ajustes, sin embeddings. Costo: cero.

**Por qué esto es el centro de la evaluación.** Todo el pipeline de agentes existe
para agregar valor sobre esta línea. Si el sistema completo no le gana claramente, la
conclusión honesta es que el pipeline no se justifica y hay que rediseñar o admitirlo.

Un segundo baseline, más exigente, se agrega en la Etapa 4:
**`MEDIAN_COMPARABLES_RAW`** — la mediana de USD/m² de los comparables **sin ajustar y
sin curar**. Ese aísla el valor específico de los nodos 4-7 (extracción, dedup,
curaduría y ajustes) frente a "simplemente buscar avisos parecidos".

```
Baseline 1: mediana del barrio          → mide el valor de TODO el sistema
Baseline 2: mediana de comparables cruda → mide el valor de la inteligencia del sistema
Sistema completo
```

Si el sistema le gana al 1 pero no al 2, significa que el valor está en buscar bien,
no en el procesamiento — y eso también es un hallazgo publicable y honesto.

---

## 3. Los datasets

### 3.1 `BADATA_2015_2020` — el dataset principal

`VERIFICADO` — [BA Data · Departamentos en Venta](https://data.buenosaires.gob.ar/dataset/departamentos-venta)

- Relevamiento muestral de avisos reales de departamentos en venta en CABA.
- Campos: **valor de publicación, m², antigüedad, ambientes, ubicación**
  (con shapefile georreferenciado hasta 2019).
- Cobertura **2001-2020**, un archivo por año. Licencia CC-BY-2.5-AR.

**Cómo se usa (protocolo leave-one-out temporal):**

1. Se toma un año, por ejemplo 2019.
2. Se carga como corpus **todo** el año.
3. Para cada caso de una muestra de ~2.000: se lo **quita del corpus**, se lo trata
   como propiedad sujeto, y se corre el pipeline completo.
4. Se compara la predicción contra su precio de publicación real.

**Por qué este dataset es tan bueno para esto:**

- Es real, es grande, es gratis y es legal.
- **Permite validar la metodología antes de tener un solo aviso scrapeado.** El riesgo
  #1 del proyecto (acceso a portales) no bloquea la evaluación del riesgo #2 (que el
  método sirva).
- Es de una época distinta a la actual, lo que obliga a que el método sea robusto y no
  esté sobreajustado al mercado de hoy.

**Limitación declarada:** es de 2015-2020, un mercado con dinámica distinta (crédito
UVA, otro tipo de cambio). Un buen resultado acá valida **el método**, no garantiza el
mismo número en 2026. Por eso existe el dataset 3.2.

### 3.2 `CRM_INVENTORY` — el dataset actual

Las **285 propiedades reales de la inmobiliaria**, con su precio de publicación actual.

- Mismo protocolo leave-one-out.
- Chico (285 casos) pero **actual y del mercado exacto donde opera el cliente**.
- Es el dataset que se le muestra a la inmobiliaria: *"tasamos tus propias propiedades sin
  mirarlas y esto es lo que dio"*.

**Limitación declarada:** son precios de publicación fijados por los propios agentes
de la inmobiliaria. Predecirlos bien mide consistencia con el criterio de la casa, no
exactitud contra el mercado. **Las dos limitaciones juntas son la razón de usar los
dos datasets**: uno grande y neutral, otro chico y relevante.

### 3.2 bis `VIGENTES` / `VIGENTES_SIN_FEATURES` — el experimento del motor de ajustes *(agregado 14/08)*

Leave-one-out sobre el **corpus vigente** (Portal A + Portal B, solo canónicos
de cluster para no predecir copiándose a sí mismo). Los dos datasets cargan los
MISMOS casos con la MISMA semilla; lo único que cambia es si los comparables
llevan las features del nodo 4 (`scripts/extraer_corpus.py` las produce para
todo el corpus). **La resta de los dos MdAPE es el aporte del motor de
ajustes** — la medición que BA Data no puede dar porque no trae estado,
antigüedad, orientación ni piso.

**Limitación declarada:** el "precio real" es el de publicación, no el de
cierre, y el stock vigente de un barrio es más heterogéneo que BA Data — los
números NO son comparables con los de `BADATA_2015_2020`; cada dataset se
compara solo contra su propia serie.

### 3.3 `GOLDEN_SET` — evaluación de componentes

~120 casos curados a mano, con la respuesta correcta anotada por mí:

| Sub-set | Qué mide | Métrica |
|---|---|---|
| Extracción (60 avisos) | `listing_features` correcto vs. anotación humana | Exactitud por campo. Objetivo: > 92% en `rooms`, `surface`, `condition` |
| Deduplicación (30 pares) | Mismo inmueble sí/no | Precisión y recall. Objetivo: precisión > 0,95 (un falso positivo borra un comparable legítimo) |
| Curaduría (30 avisos) | Descartar lo que hay que descartar | Recall > 0,90 sobre los que deben descartarse |

Este set **crece con los errores reales**: cada "Reportar extracción incorrecta" desde
`/comparables` es candidato a entrar. Así el sistema no puede volver a equivocarse
igual sin que el CI lo detecte.

---

## 4. Métricas

| Métrica | Definición | Por qué |
|---|---|---|
| **MdAPE** ⭐ | Mediana de \|pred − real\| / real | **Métrica principal.** Robusta a outliers, a diferencia del MAPE |
| MAPE | Media del error porcentual | Se reporta para comparabilidad con literatura, pero no se decide con ella |
| **PPE10 / PPE20** ⭐ | % de casos con error < 10% / < 20% | Estándar de la industria de AVM. Mide consistencia, no promedio |
| **Cobertura** ⭐ | % de casos que produjeron informe (no `INSUFFICIENT_DATA`) | Un sistema con MdAPE 5% y cobertura 12% es inútil. Siempre se leen juntas |
| **Hit rate del rango** | % de casos donde el precio real cayó entre `low` y `high` | Mide si el rango es honesto. Objetivo: 70-85%. Más de 90% significa rangos demasiado anchos |
| **Sesgo** | Mediana del error **con signo** | Detecta si el sistema tasa sistemáticamente alto o bajo. Objetivo: \|sesgo\| < 3% |
| Costo / informe | USD | Objetivo: ≤ 0,15 |
| Latencia p50/p95 | segundos | Objetivo: p95 ≤ 180 s |
| Alucinación de cifras | Nº de informes con un número no trazable | **Objetivo: 0. No negociable** |

### 4.1 Por qué MdAPE y no MAPE

Con 2.000 casos, tres propiedades atípicas (un PH mal cargado, un dúplex de 400 m²)
pueden mover el MAPE varios puntos. La mediana no se mueve. Reportar MAPE como
métrica principal en tasación es un error clásico y se evita a propósito.

### 4.2 Segmentación obligatoria

El número global miente. Toda corrida reporta también el desglose por:

- **Barrio** — dónde funciona y dónde no. Alimenta la decisión de sumar Portal B.
- **Tipo de propiedad**.
- **Rango de superficie** — es esperable que falle más en > 200 m² (pocos comparables).
- **Nivel de confianza declarado** — **este es el chequeo de calibración**: los casos
  que el sistema declaró confianza ALTA deben tener MdAPE claramente menor que los
  BAJA. Si no, el score de confianza está mal y es peor que no tenerlo, porque le
  miente al usuario.

---

## 5. Cuándo se corre

| Momento | Dataset | Bloquea |
|---|---|---|
| Cambio en `prompts/`, `config/adjustments.yaml` o el módulo de valuación | Muestra de 300 casos | **Sí** — el PR no mergea si el MdAPE empeora > 2 puntos |
| Merge a `main` | 500 casos | No, pero notifica |
| Semanal (domingo 05:00) | Completo, ambos datasets | No, publica en `/calidad` |
| Manual desde `/calidad` | El que se elija | No |
| Golden set | En **cada** PR | **Sí** — es rápido y barato |

**Todo resultado se guarda en `eval.backtest_runs` con las tres versiones**
(motor, prompts, método). Sin eso, la serie histórica de `/calidad` no significa nada.

### 5.1 Costo de correr los backtests

| Corrida | Casos | Costo |
|---|---|---|
| Golden set (cada PR) | 120 | ~USD 0,05 |
| PR con cambio de prompts | 300 | ~USD 4 |
| Semanal completo | 2.285 | ~USD 25 |

El semanal completo es el gasto más grande del proyecto. Mitigaciones: el corpus está
caché (nodo 3 no se dispara), las features ya están extraídas, y se usa el modelo
barato en todos los nodos salvo el crítico. Si aun así molesta, se baja a quincenal.
Es un gasto que **vale la pena**: es la única fuente de verdad sobre si el sistema
mejora o empeora.

---

## 6. Evaluación de componentes con LLM-as-judge

Para lo que no tiene ground truth numérico (la narrativa), se evalúa con un modelo
juez sobre una rúbrica fija:

| Dimensión | Criterio | Escala |
|---|---|---|
| Fidelidad | ¿Toda afirmación se sostiene en los datos provistos? | 1-5, objetivo ≥ 4,5 |
| Completitud | ¿Cubre las secciones obligatorias? | 1-5 |
| Claridad | ¿Lo entendería un propietario sin formación técnica? | 1-5 |
| Calibración | ¿El tono coincide con el nivel de confianza calculado? | 1-5 |

**Precaución explícita:** LLM-as-judge es ruidoso y sesgado hacia textos largos y
elaborados. Se usa como **señal de regresión** (¿empeoró respecto de la versión
anterior?), no como medida absoluta de calidad. Las decisiones importantes se toman
con MdAPE, que es un número duro.

---

## 7. Lo que se hace si los resultados son malos

Escenarios previstos, con su plan. Escribirlos ahora evita racionalizar después.

| Resultado | Interpretación | Acción |
|---|---|---|
| MdAPE > 25% | El método o los datos no alcanzan | Revisar en orden: (1) calidad de la extracción con el golden set, (2) dedup, (3) coeficientes. **No** agregar más agentes |
| Sistema ≈ baseline | El pipeline no aporta | Hallazgo válido. Se documenta, se simplifica el sistema a lo que sí aporta, y se dice públicamente. Es mejor ingeniería que inflar el número |
| Sistema le gana al baseline 1 pero no al 2 | El valor está en buscar bien, no en procesar | Simplificar los nodos 4-7 y quedarse con la recuperación. Documentarlo |
| Cobertura < 50% | Corpus insuficiente | Ahí sí se justifica sumar Portal B. **Con este dato, no antes** |
| Buen MdAPE, mala calibración de confianza | El score de confianza miente | Recalibrar los pesos de [05 §7](05-metodologia-de-valuacion.md) contra el error observado |
| Sesgo > 5% | Ajustes mal calibrados o corpus sesgado | Regresión hedónica sobre BA Data para derivar coeficientes empíricos (Etapa 6) |

**El compromiso:** los resultados se publican como salgan. Un backtest que solo se
muestra cuando da bien no es un backtest, es marketing.
