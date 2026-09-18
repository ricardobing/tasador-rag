# Cinco departamentos de Palermo, cargados como los cargaría un agente

**15/08/2026** · qué pasa cuando la app recibe casos reales, y qué parte de una
descripción llega al número.

Los casos están en [`scripts/ejemplos_palermo.py`](../../scripts/ejemplos_palermo.py):

```bash
uv run python scripts/ejemplos_palermo.py --ver
```

---

## 1. Los resultados

| Caso | USD/m² | Sugerido | Confianza | Comparables |
|---|---:|---:|---|---:|
| Soho · 3 amb · 62 m² · a refaccionar | 2.305 | **134.843** | ALTA | 19 |
| Hollywood · monoambiente · 42 m² · excelente | 3.151 | **126.040** | ALTA | 36 |
| Palermo Chico · 4 amb · 95 m² · con cochera | 2.790 | **267.285** | ALTA | 43 |
| Palermo Nuevo · PH dúplex · 50 m² | 2.513 | **113.085** | ALTA | 34 |
| Palermo Viejo · 3 amb · 78 m² · 50 años | — | **INSUFFICIENT_DATA** | — | 0 |

Cuatro de cinco dieron número, con 19 a 43 comparables reales del corpus y
confianza ALTA. Los USD/m² se ordenan como uno esperaría: el impecable de
Hollywood arriba, el a-refaccionar de Soho abajo.

## 2. El texto libre no llega al número

Es lo primero que hay que saber, y es **deliberado**. El campo "Notas" se guarda
en `subject_properties.notes` y no entra al estado del grafo
([`runner.py:62`](../../src/tasador/agents/runner.py)):

> `notes` NO va al estado: puede tener datos del propietario y no tiene por qué
> llegar a un proveedor de LLM (doc 10 §3).

O sea: "balcón corrido con baranda floja", "membrana deteriorada", "cañerías a
revisar" se guardan con el informe y **no ajustan nada**. Lo que mueve el precio
son los campos estructurados. Del texto de un agente, la app hoy aprovecha lo
que quien carga traduzca a `condition`, `orientation`, `floor_number` y las
superficies.

**No es un bug, es una decisión — pero hay que saberla antes de esperar otra
cosa.** Si se quiere que el texto aporte, hay dos caminos: un nodo que extraiga
campos del texto (y entonces el texto sí viaja al LLM, que es lo que doc 10 §3
evita), o un formulario con más campos.

## 3. Cuatro coeficientes que el formulario no pide

Existen en `config/adjustments.yaml`, el motor los aplica, y la pantalla no
tiene dónde cargarlos:

| | Efecto | Ejemplo que lo necesita |
|---|---|---|
| `has_elevator` | **−12%** si el piso ≥ 3 y no hay | Palermo Viejo, 3º de un edificio de 50 años |
| `parking_spaces` | **USD 12.000** absolutos por cochera | Palermo Chico, "espacio guardacoches" |
| `amenities` | **+5%** con amenities completos | Hollywood, "terraza, parrilla y laundry" |
| `expenses_ars` | **−4%** si son altas | Palermo Chico, "expensas elevadas" |

Medido, no estimado: el caso 3 se generó **dos veces**, con y sin esos campos.

```
con cochera/ascensor/expensas : USD 267.285
como lo carga el web hoy      : USD 255.285
diferencia                    : USD 12.000  (+4,7%)
```

Los USD 12.000 son exactamente la cochera. Es el número que un agente pierde por
no tener el campo.

## 4. El caso que no pudo, y por qué

Palermo Viejo devolvió `INSUFFICIENT_DATA`, que es el comportamiento correcto:
el sistema no emite un número que no puede sostener. El detalle dice dónde se
cayó:

```
60 candidatos encontrados
  45  ajuste_excede_el_tope
   9  en_pozo_o_construccion
   4  aviso_vencido
   1  duplicado_de_cluster
   1  permuta_o_financiacion
```

**45 de 60 por el tope de ±25%.** El sujeto acumula tres factores adversos
—a refaccionar, 50 años, contrafrente— y llevar un comparable normal hasta ahí
pide un ajuste que supera el tope, así que se descarta (doc 05 §4.2: ajustar un
40% no es ajustar, es inventar).

El contraste está en el mismo lote: Soho también es `a_refaccionar` y
contrafrente, pero sin antigüedad declarada, y perdió 26 en vez de 45 — le
alcanzó.

> Esto es una limitación estructural, no un error: **una propiedad muy castigada
> no se puede tasar con un corpus donde casi todo está en mejor estado.** La
> salida honesta sería más corpus de esa franja, no aflojar el tope.

## 5. Lo que se arregló mientras se probaba

- **El informe mostraba el markdown crudo**: `## Resumen ejecutivo` y
  `**USD 134.667**` con los símbolos a la vista, en la pantalla que es el
  entregable. El PDF sí lo renderizaba. Arreglado reutilizando
  `markdown_a_html`, el renderer seguro que ya usaba el PDF.

## 6. Lo que quedó anotado y no se tocó

- **El nodo 6 anula veredictos correctos del juez.** Observado en vivo: el juez
  detecta un aviso en pozo y devuelve una cita abreviada con `...`; la
  validación exige que la cita aparezca literal y la comparación falla, así que
  el aviso **entra igual como comparable**. Dos casos en este lote.

- **La tabla de comparables no se muestra en la web.** La API ya la devuelve
  desde H-27 (`comparables.items[]`, con los 13 campos); el front todavía solo
  dice "18 comparables usados de 60".

- **El índice oficial del GCBA es de abril de 2019.** La narrativa lo declara
  ("para abril de 2019"), pero después compara la valuación contra él —"12,1%
  por debajo del índice oficial"— y esa comparación cruza siete años de mercado
  argentino.

- **Ningún aviso del corpus tiene coordenadas** (0 de 8.502), así que
  `distance_m` siempre es nulo. No mueve el precio —no hay coeficiente por
  distancia— pero la columna "a X metros" no se puede mostrar.

- **El corpus cubre Palermo y poco más**: 8.388 avisos vigentes en Palermo, 107
  en Belgrano. Fuera de ahí no hay con qué tasar.
