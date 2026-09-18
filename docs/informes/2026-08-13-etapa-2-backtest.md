# Informe — Etapa 2: el motor de valuación y su primer backtest

**Fecha:** 13/08/2026 · **Dataset:** BADATA_2020, 1.500 casos, leave-one-out
**Método:** `2026.08.1+b721657c226b`

> Los resultados se publican como salen. Un backtest que solo se muestra cuando
> da bien no es un backtest, es marketing (doc 09 §7).

---

## 1. El número

```
                   SISTEMA    BASELINE
──────────────────────────────────────────
MdAPE                15.0%       16.2%
PPE20                60.6%       59.0%
PPE10                35.8%           —
MAPE                 21.4%           —
Cobertura            99.9%      100.0%
Hit rate             61.0%           —
Sesgo                -0.0%           —
──────────────────────────────────────────
✅ Le gana al baseline por 7.6%
```

**Criterios de éxito de [00 §5](../00-vision-y-alcance.md):**

| Criterio | Objetivo | Resultado | |
|---|---|---|---|
| Le gana al baseline | sí | +7,6% | ✅ |
| MdAPE | ≤ 15% | **15,0%** | ✅ (justo) |
| PPE20 | ≥ 65% | **60,6%** | ❌ |
| Cobertura | ≥ 70% | 99,9% | ✅ |
| \|Sesgo\| | < 3% | **0,0%** | ✅ |

---

## 2. Lectura honesta

### 2.1 Le gana al baseline, pero por poco

7,6% de mejora sobre una consulta SQL de una línea es un resultado **modesto**.
Sería deshonesto presentarlo como una validación entusiasta del pipeline.

**Pero hay un atenuante fuerte, y es el más importante de este informe:**

> **Este backtest NO mide el motor de ajustes. Lo mide apagado.**
>
> BA Data trae dirección, superficie, precio y ambientes. **No trae estado de
> conservación, antigüedad, orientación ni piso** — que son exactamente las
> entradas de los coeficientes de [05 §4](../05-metodologia-de-valuacion.md).
> En las 1.500 corridas, *todos* los coeficientes valieron 1,00.
>
> Lo que se midió es: **selección de comparables por superficie/ambientes +
> estadística robusta**, contra la mediana del barrio. Que eso solo ya rinda
> 7,6% es una señal razonable, y significa que la parte que debería aportar
> más está sin evaluar.

Esto ya estaba anticipado en [09 §3.1](../09-evaluacion-y-backtest.md) como
limitación declarada del dataset. Se confirma.

**Consecuencia práctica:** la vía para mejorar el MdAPE no es tocar la
estadística, es **conseguir los atributos que faltan**. Y eso es exactamente lo
que hace la [captura por voz](../15-captura-por-voz.md): el agente dicta 90
segundos y de ahí salen estado, orientación, piso y ascensor. El doc 15 decía
que sin eso "la mitad de la metodología de valuación es decorativa". El backtest
lo confirma con números.

### 2.2 El sesgo es excelente

**0,0%.** El sistema no tasa sistemáticamente alto ni bajo. Es de las cosas más
difíciles de conseguir y de las más importantes: un sesgo del 5% sería invisible
en el uso diario y arruinaría todas las tasaciones en la misma dirección.

### 2.3 La confianza está bien calibrada — el mejor resultado del informe

```
CALIBRACIÓN (MdAPE por nivel declarado)
  ALTA         13.4%
  MEDIA        20.6%
  BAJA         66.2%
```

Monótono y con separación grande. **El sistema sabe cuándo no sabe.** Eso es lo
que hace que la promesa de "sabe decir no sé" sea real y no un eslogan: cuando
declara confianza BAJA, efectivamente se equivoca 5 veces más.

Si esto no se cumpliera, el score de confianza sería peor que no tenerlo, porque
le mentiría al usuario.

### 2.4 El hit rate: un defecto real, corregido a medias

**Primera corrida: 36,6%** contra un objetivo de 70-85%.

**Diagnóstico:** el rango salía del p25-p75 de los comparables, que mide **la
dispersión del mercado**, no **la incertidumbre de nuestra estimación**. Son
cosas distintas. Con un MdAPE del 15%, un rango de ±4-8% falla 2 de cada 3 veces.
Es un error de metodología, no de calibración.

**Corrección:** el rango ahora toma el mayor entre el p25-p75 y una semiamplitud
calibrada contra el error observado (±20%). **Hit rate: 36,6% → 61,0%.**

**Por qué no se siguió ensanchando hasta llegar al 75%:** haría falta ±30%, y un
rango de "entre USD 126.000 y USD 234.000" sobre una propiedad de 180.000 es
inútil como consejo. Seguir ensanchando hasta que el indicador se ponga verde
sería ajustar al test.

**La lectura correcta es incómoda y hay que decirla:** con los datos actuales el
modelo no es lo bastante preciso como para dar un rango que sea simultáneamente
angosto y confiable. El camino no es ensanchar el rango, es mejorar las entradas
(§2.1).

### 2.5 Dónde falla

| Mejores | | Peores | |
|---|---|---|---|
| Villa Santa Rita | 6,1% | Mataderos | 33,3% |
| Parque Avellaneda | 8,5% | Villa Lugano | 29,9% |
| San Cristóbal | 9,0% | Nueva Pompeya | 29,8% |
| Colegiales | 9,3% | Vélez Sársfield | 27,0% |
| Villa Pueyrredón | 9,4% | La Boca | 24,6% |

Los peores son barrios de mercado heterogéneo, donde el precio depende mucho más
del estado y la cuadra que del barrio. Coherente con §2.1: son justamente los
casos donde los ajustes que no pudimos evaluar más falta hacen.

**Buena noticia para el caso de uso real:** Colegiales aparece entre los mejores,
y los barrios donde opera la inmobiliaria (Palermo, Belgrano, Núñez, Villa Urquiza) no
están entre los peores.

---

## 3. Lo construido

| Componente | Verificación |
|---|---|
| `valuation/models.py` — tipos desacoplados del ORM | Acepta comparables de cualquier origen (doc 14 §3.1) |
| `valuation/adjustments.py` — coeficientes desde YAML | Ajuste **relativo** sujeto↔comparable, tope ±25% |
| `valuation/engine.py` — motor determinístico | 24 tests, incluidos los 9 de doc 05 §10 |
| `eval/backtest.py` — leave-one-out + baseline + métricas | Corrido sobre 1.500 casos reales |
| `sources/panel_externo.py` — fuente externa opcional | 6 tests, incluido "apagada no rompe nada" |
| `ops/sql/panel-readonly.sql` — rol y vistas sin PII | Listo para correr |

```
ruff check              ✅ All checks passed
ruff format --check     ✅ 36 files already formatted
mypy --strict           ✅ 27 archivos
pytest                  ✅ 54 tests
```

### 3.1 Dos veces el test estuvo mal, no el código

Vale registrarlo porque es un patrón:

1. **`test_ajuste_es_relativo_al_sujeto`**: asumí que comparables en mejor estado
   dan un valor **mayor**. Es al revés — si los comparables son mejores que el
   sujeto, el sujeto vale **menos** que ellos. El motor estaba bien.
2. **`test_precio_no_se_confunde_con_expensas`** (Etapa 1): asumí precio ≠
   expensas. Hay un aviso real con USD 180.000 y $180.000 de expensas.

En ambos casos el reflejo de "el código está mal" habría metido un bug.

---

## 4. Qué hacer con esto

| # | Acción | Por qué | Impacto esperado |
|---|---|---|---|
| 1 | **Captura por voz** (doc 15) | Es lo que genera estado, orientación y piso — las entradas que hoy están vacías | **Alto.** Es lo que puede mover el MdAPE de verdad |
| 2 | Backtest sobre `CRM_INVENTORY` | Mercado actual y real del cliente, no 2020 | Medio — valida en el contexto que importa |
| 3 | Recalibrar `uncertainty_half_width_pct` cuando mejore el MdAPE | Hoy ±20% es un parche honesto | Medio |
| 4 | Regresión hedónica sobre BA Data para los coeficientes (tarea 6.6) | Hoy son criterio, no medición | Medio |
| 5 | Investigar los barrios peores | 33% de error en Mataderos merece diagnóstico | Bajo-medio |

**Lo que NO hay que hacer:** agregar agentes para mejorar el número. El cuello de
botella medido son los **datos de entrada**, no el procesamiento.

---

## 5. Reproducirlo

```bash
uv run python scripts/fetch_badata.py --years 2020
uv run python scripts/load_badata.py
uv run python scripts/run_backtest.py --sample 1500
```

Semilla fija (42), motor determinístico: da exactamente lo mismo siempre.
