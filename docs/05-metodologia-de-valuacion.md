# 05 — Metodología de valuación

**Nodo 7 del pipeline. Determinístico, sin LLM, testeado con casos fijos.**

Este documento es el que se puede mostrar a un martillero, a un cliente escéptico o
en una entrevista técnica. Todo lo que hace el sistema para llegar a un número está
acá.

---

## 1. Método: comparación de mercado

Es el método estándar del rubro para inmuebles residenciales: se toman propiedades
similares publicadas en la misma zona, se ajusta cada una por sus diferencias con la
propiedad a tasar, y se resume el conjunto.

`VERIFICADO` — el criterio habitual en Argentina es tomar **5 a 10 comparables** del
mismo barrio y tipología, descartar el mayor y el menor, y promediar el USD/m² del
resto. Este sistema hace eso, con tres mejoras:

1. Usa **estadística robusta** (mediana y MAD) en vez de promedio, que es sensible a
   outliers.
2. **Ajusta cada comparable** antes de resumir, en vez de comparar peras con manzanas.
3. **Cuantifica la incertidumbre** en vez de dar un número seco.

---

## 2. Paso 1 — Superficie ponderada

Comparar por superficie total castiga a los departamentos sin balcón y premia a los
que tienen patio enorme. El criterio de mercado:

```
superficie_ponderada = cubierta + 0.5 × (total − cubierta)
```

Cuando el aviso solo declara superficie total (caso frecuente), se usa esa y se marca
`quality_flags += ['surface_total_only']`, lo que resta confianza.

---

## 3. Paso 2 — USD/m² crudo de cada comparable

```
usd_m2_crudo = precio_usd / superficie_ponderada
```

Solo entran avisos en USD. Los publicados en pesos se descartan (nodo 6): convertir a
un tipo de cambio arbitrario en un mercado con múltiples cotizaciones introduce más
error del que resuelve, y el mercado de venta de inmuebles en Argentina opera en
dólares de hecho.

---

## 4. Paso 3 — Ajustes

Cada comparable se ajusta multiplicativamente para llevarlo a las condiciones de la
propiedad sujeto. El coeficiente responde a: *"¿cuánto más/menos vale por m² el
comparable respecto del sujeto por esta característica?"*, y luego se divide.

```
usd_m2_ajustado = usd_m2_crudo / Π(coeficientes)
```

### 4.1 Tabla de coeficientes (v1)

| Factor | Condición | Coeficiente | Fuente / criterio |
|---|---|---|---|
| **Estado** | a_estrenar | 1,15 | Práctica de mercado. Calibrable |
| | excelente | 1,08 | |
| | muy_bueno | 1,00 | ← referencia |
| | bueno | 0,94 | |
| | a_refaccionar | 0,82 | |
| **Antigüedad** | 0-5 años | 1,06 | Decae hasta estabilizar; un edificio de 40 y uno de 60 valen casi igual |
| | 6-15 | 1,00 | ← referencia |
| | 16-30 | 0,96 | |
| | 31-50 | 0,92 | |
| | > 50 | 0,90 | |
| **Orientación** | frente | 1,03 | |
| | lateral | 1,00 | ← referencia |
| | contrafrente | 0,98 | (en zonas ruidosas puede invertirse: parametrizable por barrio) |
| | interno | 0,93 | |
| **Piso / ascensor** | ≥ 3° sin ascensor | 0,88 | El castigo más grande y más real del mercado porteño |
| | PB con patio | 1,02 | |
| | ≥ 8° con vista | 1,04 | |
| | resto | 1,00 | ← referencia |
| **Cochera** | incluida | +USD 12.000 al valor total, no al m² | Es un valor absoluto, no proporcional |
| **Amenities** | edificio con amenities completos | 1,05 | SUM, pileta, gimnasio, seguridad |
| | básicos | 1,00 | ← referencia |
| **Expensas** | > 2× la mediana del barrio | 0,96 | Expensas altas deprimen el precio de venta |
| **Antigüedad del aviso** | > 90 días publicado | 0,97 | Precio de oferta no convalidado por el mercado |

### 4.2 Reglas sobre los ajustes

1. **Tope acumulado: ±25%.** Si el producto de coeficientes sale de `[0,75 · 1,25]`,
   el comparable se descarta por "demasiado distinto". Ajustar un 40% no es ajustar,
   es inventar.
2. **Sin dato, sin ajuste.** Si `condition` es NULL, el coeficiente es 1,00. Nunca se
   imputa un valor por defecto pesimista u optimista.
3. **Todo ajuste queda registrado** en `report_comparables.adjustments`:
   ```json
   {"estado": {"valor":"a_refaccionar","coef":0.82},
    "piso":   {"valor":"4_sin_ascensor","coef":0.88},
    "total":  0.7216, "capped": true}
   ```
4. **Los coeficientes son configuración, no código** (`config/adjustments.yaml`),
   versionados. Cambiarlos incrementa `method_version` y **obliga a correr el
   backtest**.

### 4.3 Sobre la honestidad de estos números

Los coeficientes de v1 salen de práctica de mercado y criterio, no de una regresión
sobre datos. **Esto está dicho explícitamente en el informe y acá.**

El plan para hacerlos empíricos está en la Etapa 6: con el dataset de BA Data
(miles de avisos con precio, m², antigüedad, ambientes y ubicación) se puede correr
una regresión hedónica y **derivar los coeficientes de los datos**. Hasta que eso
esté medido, son un supuesto declarado, no un hecho.

---

## 5. Paso 4 — Estadística robusta

Sobre el vector de `usd_m2_ajustado` de los comparables que sobrevivieron:

```python
mediana = np.median(v)
mad     = np.median(np.abs(v - mediana))       # desviación absoluta mediana
dispersion = mad / mediana                      # coeficiente de variación robusto

# Winsorizado: los extremos se recortan, no se eliminan
v_w = np.clip(v, np.percentile(v, 10), np.percentile(v, 90))

usd_m2_final = np.median(v_w)
p25, p75     = np.percentile(v_w, [25, 75])
```

**Por qué mediana y MAD en vez de media y desvío estándar:** un solo aviso con un
precio absurdo mueve la media varios puntos porcentuales; a la mediana no la mueve. En
un corpus alimentado por scraping de avisos cargados a mano, los valores absurdos no
son la excepción, son el clima.

**Por qué winsorizar en vez de eliminar:** con 8 comparables, eliminar los 2 extremos
tira el 25% de la muestra. Recortarlos conserva la información de que hay valores
altos y bajos, sin dejar que dominen.

---

## 6. Paso 5 — El rango

```
valor_medio     = usd_m2_final × superficie_ponderada_sujeto
valor_minimo    = p25 × superficie_ponderada_sujeto
valor_maximo    = p75 × superficie_ponderada_sujeto
+ cochera:        + USD 12.000 por cochera incluida
```

**Ancho mínimo del rango: 8%.** Si p25 y p75 quedan más cerca que eso, se fuerza a
±4% del medio. Fundamento: un rango de ±1% transmite una precisión que este método no
tiene, y sería deshonesto.

### 6.1 Rango esperado de cierre

`VERIFICADO` — los precios publicados en portales están típicamente **5-15% por
encima del precio de cierre real**.

```
cierre_alto = valor_medio × 0.95
cierre_bajo = valor_medio × 0.85
```

Se presenta **separado** del precio de publicación sugerido, y con la fuente citada.
Esta es la sección que más le sirve al dueño de la propiedad: le explica por qué
publicar a X no significa cobrar X.

---

## 7. Paso 6 — Confianza

No es un adorno: determina si el informe se muestra con advertencias y cuánto se
puede confiar en él.

```python
score = (
    0.35 * f_cantidad(n_comparables)      # 5→0.3, 8→0.7, 12+→1.0
  + 0.30 * f_dispersion(mad/mediana)      # <0.10→1.0, 0.25+→0.0
  + 0.15 * f_frescura(dias_medianos)      # <30d→1.0, 120d+→0.2
  + 0.10 * f_relajacion(pasos_relajados)  # 0→1.0, 4→0.2
  + 0.10 * f_completitud(campos_del_sujeto)
)

ALTA  si score ≥ 0.75
MEDIA si score ≥ 0.50
BAJA  si score <  0.50
```

**Confianza BAJA no bloquea el informe**, pero:

- El PDF lleva un recuadro de advertencia visible en la primera página.
- El rango se ensancha un 50% adicional.
- La narrativa está obligada a mencionar la limitación concreta.

---

## 8. Salidas honestas

| Condición | Salida |
|---|---|
| < 5 comparables tras curar | `INSUFFICIENT_DATA` + qué faltó + sugerencia (ampliar zona, esperar) |
| Dispersión > 0,35 | Informe con confianza BAJA y advertencia explícita de que el mercado local es muy heterogéneo |
| Barrio sin cobertura en el corpus | `INSUFFICIENT_DATA` con el motivo "zona fuera de cobertura" |
| Geocodificación fallida | Falla en el nodo 1, antes de gastar un peso |

**El sistema nunca produce un número cuando no tiene con qué.** Esto no es una
limitación: es la característica que lo hace utilizable frente a un cliente.

---

## 9. Qué NO hace este método (y hay que decirlo)

| Limitación | Por qué |
|---|---|
| No conoce precios de cierre reales | No existe registro público en Argentina. El descuento 5-15% es una estimación de mercado, no un dato |
| No ve la propiedad | No evalúa calidad de terminaciones, vista real, ruido, olores, vecinos. El agente sí, y por eso el informe es un **insumo** para su criterio, no un reemplazo |
| Coeficientes no calibrados en v1 | Ver §4.3. Plan de calibración en Etapa 6 |
| Sesgo del corpus | Si Portal A sobre-representa cierto segmento, el corpus lo hereda. Por eso existe la validación contra la serie oficial del GCBA |
| No es una tasación legal | La firma un martillero matriculado. Ver [10](10-seguridad-y-legal.md) |

Estas limitaciones van **impresas en el PDF**, no escondidas en un footer de 6pt.

---

## 10. Tests obligatorios de este módulo

Como es determinístico, se testea como cualquier función pura:

| Test | Verifica |
|---|---|
| `test_superficie_ponderada` | Casos con y sin descubierta, y con solo total |
| `test_ajustes_topados` | Un comparable con 4 factores adversos queda capado en 0,75 y marcado |
| `test_sin_dato_sin_ajuste` | `condition=None` → coeficiente 1,00, no un default |
| `test_mediana_resiste_outlier` | Inyectar un comparable a USD 50.000/m²: el resultado se mueve < 2% |
| `test_rango_ancho_minimo` | Set muy homogéneo → rango forzado a ±4% |
| `test_menos_de_5` | Devuelve `INSUFFICIENT_DATA`, no un valor |
| `test_reproducibilidad` | El mismo input 100 veces da exactamente el mismo output |
| `test_confianza_monotona` | Más comparables nunca baja la confianza; más dispersión nunca la sube |
| `test_caso_real_conocido` | Una propiedad de la inmobiliaria con precio real: el resultado cae dentro del rango |
