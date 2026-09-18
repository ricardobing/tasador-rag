# Sesión 2 — implementación: trece hallazgos, y cuatro que dijeron otra cosa

**15/08/2026** · continuación de [la auditoría](../auditoria/00-resumen.md) y de
[su cierre](2026-08-15-cierre-auditoria.md).

De 22 hallazgos cerrados se pasó a **35 de 58**. Lo que sigue no es la lista de
cambios —esa está en los mensajes de commit, uno por bloque— sino lo que se
midió, y en particular las cuatro veces en que la medición contradijo al hallazgo.

---

## 1. Lo que se cerró

| | Hallazgo | Antes | Después |
|---|---|---|---|
| H-22 | Dos `upsert` y uno no escribía el barrio | 6 de 17 avisos sin `neighborhood_id` | 17 de 17 |
| H-16 | Umbrales de descarte en 3 lugares | 3 copias, nada las sincroniza | 1, en `config/adjustments.yaml` |
| H-26 | Los errores no eran RFC 7807 | 1 de 9 respuestas de error | 9 de 9 |
| H-27 | Faltaban 5 campos del contrato | `faltan: ['market_context','pdf_url']` | `faltan: []` |
| H-31 | Tres huecos en el gate de aislamiento | 3 patrones sin cubrir | 0, con 5 autotests |
| H-44 | El gate del backtest pasaba con 0 casos | VERDE con los flags del CI | ROJO |
| H-37 | Sin `error.tsx` / `not-found.tsx` | `/informes/no-es-uuid` → **500** | → **404** con la pantalla propia |
| H-33 | El filtro de superficie no usa índice | *parcial* — ver §3 | |
| H-05 | El gate del ancho de rango no podía fallar | `ancho >= 0.079` sobre un 40% real | igualdad exacta; falla si se toca el YAML |
| H-09 | `MIN_COMPARABLES` lo ablandaba el entorno | `MIN_COMPARABLES=3` pasaba | rechazado |
| H-38 | El botón principal a 2,51:1 | 2,51:1 (AA pide 4,5) | 6,59 en claro · 7,21 en oscuro |
| H-39 | Placeholders de label, 51 `<th>` sin scope | 4 campos, 51 `<th>` | 0 y 0, con gate en las 7 pantallas |
| H-25 | 11 avisos con la clave vieja | script listo, sin aplicar | espera autorización |

**Tests: 415 → 463**, más 32 de Playwright solo en accesibilidad. Los gates en
verde en cada commit: `ruff`, `ruff format`, `mypy --strict` (66 archivos),
`pytest`, `alembic check` y `ops/verificar_imagen.py`.

---

## 2. Lo que encontraron los tests nuevos, que no estaba en la auditoría

Tres cosas, y las tres aparecieron por correr el sistema en vez de leerlo.

### El rótulo de barrio de Portal A no se podía parsear

H-22 decía "borrá `upsert_card` y usá `_upsert`, que sí escribe
`neighborhood_id`". Se hizo, y el test que corre la ingesta entera contra el
HTML guardado falló: **6 de 17 avisos seguían sin barrio**.

`_match_neighborhood` probaba el rótulo entero y cada parte separada por coma.
El rótulo de una tarjeta de Portal A es
`"Departamento en Venta en Palermo, Capital Federal"`: ninguna de sus dos partes
es un barrio —una es la operación y la otra la ciudad—. Estaba tapado porque el
upsert que se borró ni siquiera intentaba el match.

Ahora prueba también el final de cada parte, del sufijo más largo al más corto
para que "Palermo Chico" gane sobre "Chico".

### Un helper mío leía una tabla de otro tenant

Veinte minutos después de escribir `_items_de_comparables`, el gate de
aislamiento —el que acababa de ampliar por H-31— lo marcó: lee
`report_comparables` por `report_id` y la verificación del dueño está en quien
lo llama. Hoy es seguro; mañana, con otro caller, devuelve los comparables de
otra inmobiliaria y la función se ve bien.

Ahora hace join contra `Report` con el `org_id`: es segura por construcción y no
por disciplina del caller.

### El quinto caso de mi propia sonda nunca había corrido

`s8_gate.py` tenía cinco casos y el hallazgo pegó cuatro líneas de salida. El
quinto —el caso VERDE, el que prueba que el gate no está roto al revés— crasheaba
por un tipo mal pasado. Un gate que solo se ejercita con casos que fallan no
prueba que deje pasar lo bueno.

---

## 3. Las cuatro veces que la medición contradijo al hallazgo

### H-33 · el índice no sirve y el pre-filtro pierde candidatos

El hallazgo proponía tres cosas: la columna `surface_weighted`, un índice
compuesto y un pre-filtro con un cerco más ancho. Se implementaron las tres y se
midieron con los parámetros **reales** de la escalera (`surface_pct: 30`).

Palermo, 80 m², 3 ambientes, escalón 1:

```
A · original (raw JSONB)     62,4 ms   37.821 buffers   1.637 candidatos
B · columna, sin cerco       62,7 ms   36.237 buffers   1.637 candidatos
C · columna + cerco          56,7 ms   33.385 buffers   1.632 candidatos
```

- **El índice**: los buffers son idénticos con y sin él, *al buffer*. Sin un
  predicado sobre la columna sola el planner no lo puede usar. Tirado.
- **El cerco**: 9% de tiempo a cambio de 5 candidatos de 1.637. Los comparables
  son el recurso escaso de este sistema: un informe se cae a
  `INSUFFICIENT_DATA` por no llegar al mínimo, no por tardar 6 ms más. Y perdía
  justo donde el nodo 4 lee una superficie muy distinta de la del portal, que es
  el caso en que la interpretación aporta. Sacado.
- **La columna**: se queda. Mismo conjunto exacto de candidatos, 1.584 buffers
  menos, y un cast de texto menos en el camino caliente.

**H-33 queda abierto con un diagnóstico nuevo:** lo dominante es el bitmap heap
scan trayendo 8.383 filas *anchas* de `listings` —con el JSONB `raw` adentro—
para devolver 60.

La primera medición dio 117 ms → 10,9 ms, un 10×. Era falsa: `sup_pct` es un
porcentaje (30) y se había pasado `0.25`, o sea ±0,25% en vez de ±30%. Con un
rango de superficie absurdamente angosto cualquier índice parece un milagro.

### H-37 · el `loading.tsx` que pedía rompe el criterio que pedía

El hallazgo pide `error.tsx`, `not-found.tsx` **y** `loading.tsx`, y verifica con
`curl ... → 404`. Las dos cosas no pueden ser ciertas a la vez.

Un `loading.tsx` crea un límite de Suspense y Next empieza a mandar la respuesta
**antes** de terminar de renderizar. El status va en la primera línea: cuando la
página resuelve, el 200 ya se mandó.

```
con loading.tsx en la raíz    /informes/no-es-uuid          -> 200
con loading.tsx en /calidad   /calidad con la API abajo     -> 200 y el cuerpo
                                                               se queda en "Cargando…"
```

La pantalla correcta con el código equivocado. Para un monitor o un crawler,
"todo bien". El beneficio era una línea que dice "Cargando…". Sacado.

### H-44 · el gate del golden set tenía el mismo agujero

El hallazgo apunta al backtest. Al arreglarlo apareció el gemelo:
`_golden_set` imprimía "sin mediciones registradas" y devolvía 1 **solo** con
`--fail-on-regression`. "No hay regresión" no es una conclusión válida cuando no
hay mediciones que comparar. Ahora corta siempre.

### H-38 · el gate del hallazgo no habría atrapado al hallazgo

El hallazgo sugiere dejar la sonda de contraste como test de Playwright.
Playwright corre en `prefers-color-scheme: light` por default, y **el 2,51:1 es
de modo oscuro**: en claro el mismo botón da 6,59:1.

Verificado revirtiendo el CSS y volviendo a correr:

```
contraste AA (light)  -> passed
contraste AA (dark)   -> FAILED   "ratio": 2.51
```

2,51 exacto, el número de la auditoría. Un test de contraste sin
`emulateMedia` habría pasado en verde sobre el único esquema que no falla — el
mismo error que el test de labels, que miraba la única pantalla de siete que ya
cumplía.

---

## 4. Un error propio, y el candado

`uv run ruff format docs` reformatea el Python que vive **dentro** de los bloques
` ```python ` de la documentación. Colapsó código alineado a propósito en doc 05
§5 y tocó dos informes fechados. Peor: los documentos de auditoría pegan salidas
de comando como evidencia, y reformatearlas las convierte en evidencia que ya no
coincide con lo que el comando imprimió.

Los `.md` volvieron a su contenido previo y hay un candado en `pyproject.toml`
—`extend-exclude` + `force-exclude`— verificado en las tres formas de invocarlo,
incluida `ruff format .`, que es la que uno escribe sin pensar.

---

## 5. Que conecte

Imagen reconstruida, `ops/verificar_imagen.py` en verde (`Las imágenes son el
repo`), y un informe real de punta a punta contra esa imagen:

```
informe 98f02217 · SUCCEEDED · 10/10 nodos · 428 s · USD 0,045
narrativa 3.937 chars · PDF 30.564 bytes servido por la API
value_mid = usd/m² × superficie ponderada, exacto
19/19 verificaciones en verde
```

Las 19 cubren cada campo agregado en H-26 y H-27 sobre la respuesta real, no
sobre un fixture.

> El nodo 6 tuvo un timeout del juez en el primer intento
> (`el juez no respondió`) y el grafo siguió, que es el comportamiento correcto:
> degradó y no se cayó. El informe tardó 428 s en vez de los ~30 s habituales
> por ese reintento.

---

## 6. Lo que queda

23 hallazgos. Los que más pesan, en orden:

1. **El remoto y el primer run del CI.** Sigue sin correr nunca. Es lo único de
   esta lista que no se puede hacer sin el humano.
2. **H-59 · las 26 divergencias de documentación**, que crecieron con esta
   sesión: doc 05 §5 y doc 06 §2 ahora describen un sistema anterior.
3. **H-19** · una re-extracción pisa con `None` un campo que ya tenía valor.
4. **H-40** · los tests de Playwright —ahora 32 solo de accesibilidad— no corren
   en el CI. Un test que el CI no corre es un test que se pudre.
5. **H-25** · una línea, y espera autorización porque escribe en el corpus:
   `uv run python scripts/reparar_claves_del_raw.py --aplicar`
