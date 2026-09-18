# Guía — Anotar el golden set

**Para quién:** vos (Ricardo). Es la única tarea del proyecto que no puede
hacer una máquina, y es la que más impacto tiene por minuto invertido.

---

## 1. Qué es y por qué importa

El **golden set** es un conjunto de avisos reales donde un humano dejó escrito
qué dice cada aviso. Es la vara contra la que se mide el nodo 4 (extracción) y
el nodo 6 (curaduría): la exactitud "76%" que reporta `/calidad` significa "el
modelo coincide con la anotación humana en el 76% de los campos".

Hoy hay dos problemas, y los dos se resuelven anotando:

1. **El set actual lo anotó Claude** leyendo el mismo texto que lee el modelo.
   Sirve para detectar regresiones, pero no es ground truth independiente: si
   Claude y el extractor se equivocan igual, el error no se ve.
2. **El set tiene 24 avisos y la varianza medida es 17,6 puntos.** Cuatro
   corridas idénticas dieron 60,8% · 77,0% · 75,7% · 78,4%. No es el modelo:
   es ruido de muestreo de un set chico. Con ~74 avisos anotados la varianza
   teórica baja a la mitad; con 150, a un tercio.

**El objetivo: pasar de "creemos que extrae bien" a "extrae X% ± pocos puntos,
medido contra lo que un humano leyó".** Sin esto, ninguna mejora de prompt se
puede afirmar.

---

## 2. Qué necesitás

- El archivo **`tests/golden/palermo-para-anotar.yaml`** — ya está preparado
  con **90 avisos de Palermo** (texto completo incluido, no hace falta abrir
  ningún portal).
- Un editor de texto (VS Code va perfecto: colorea el YAML).
- **~2 minutos por aviso.** Los 90 son una tarde. No hace falta hacerlos todos
  de una: cada aviso que marques `revisado: true` entra al eval al instante.

No necesitás la base ni Docker: es leer texto y escribir valores.

---

## 3. Cómo se anota, campo por campo

Cada aviso del archivo tiene esta forma (los `_pistas` y `_texto` ya vienen
puestos):

```yaml
- id: "59762787"
  ref: "Maure al 2300 — USD 189.000"
  estrato: enriquecido
  fuente: PORTAL_B
  esperado:
    condition: null        # ← completar
    orientation: null      # ← completar
    floor_number: null     # ← completar
    has_elevator: null     # ← completar
    age_years: null        # ← completar
  no_evaluar: []
  descarte: null           # ← completar (o dejar null si el aviso SIRVE)
  revisado: false          # ← poner true cuando terminaste ESTE aviso
  _pistas:                 # fragmentos encontrados por regex — un buscador, no una opinión
  _texto: |                # el aviso completo
```

### La regla de oro: `null` = "el aviso NO lo dice"

Anotás **lo que el texto dice**, no lo que sospechás del inmueble. Si el aviso
no menciona ascensor, `has_elevator: null` — aunque sea una torre y "seguro
tiene". Que el modelo complete lo que el aviso no dice es un **error** que
mueve un precio real, y el eval tiene que poder castigarlo.

> El error clásico ya nos pasó: anotamos `parking_spaces: 0` donde el aviso no
> decía nada, y el eval le contó un error al modelo que tenía razón. **Un
> golden set mal anotado miente con la misma cara que un modelo.**

### Los valores posibles

| Campo | Valores | Cuenta como dicho si… |
|---|---|---|
| `condition` | `a_estrenar` · `excelente` · `muy_bueno` · `bueno` · `a_refaccionar` · `null` | habla del ESTADO del inmueble. "Excelente ubicación" no es condition; "a refaccionar" o "impecable, reciclado" sí |
| `orientation` | `frente` · `contrafrente` · `lateral` · `interno` · `null` | "al frente", "contrafrente", "interno". "Muy luminoso" NO es una orientación |
| `floor_number` | número o `null` | "5º piso", "PB" = 0. "Piso alto en torre" sin número = `null` |
| `has_elevator` | `true` · `false` · `null` | "con ascensor" / "sin ascensor". Que sea un edificio de 10 pisos no lo convierte en `true` |
| `age_years` | número o `null` | "30 años de antigüedad" = 30. "A estrenar" = 0. "Edificio antiguo" sin número = `null` |

### `no_evaluar`: la salida honesta para lo ambiguo

Si un campo es genuinamente dudoso — "todos los ambientes son externos" ¿es
`frente`? — agregalo a la lista y listo:

```yaml
no_evaluar: [orientation]
```

Ahí ni el acierto ni el error cuentan. **Declarar la duda es mejor que forzar
una respuesta y medir contra ella.**

### `descarte`: lo que mide la curaduría

`null` si el aviso sirve como comparable. Si NO sirve, uno de los motivos del
nodo 6:

| Motivo | Cuándo |
|---|---|
| `en_pozo_o_construccion` | en pozo, fideicomiso, "entrega 2027", cuotas de obra |
| `permuta_o_financiacion` | "acepta permuta", financiación especial del vendedor |
| `precio_promocional` | "oportunidad", precio de lanzamiento, "último precio" con rebaja explícita |
| `descripcion_inconsistente` | los datos no cierran entre sí (dice 3 amb y describe 1) |
| `tipologia_distinta` | no es un departamento/casa/PH de vivienda: local, oficina, cochera… |

El archivo trae `# candidatos: ...` como sugerencia de la regex — **puede
estar mal**; es un buscador, no una opinión. La curaduría es lo que más
necesita ojos: hoy su recall se mide sobre **6 casos**.

---

## 4. Dos ejemplos anotados de verdad

**Ejemplo 1 — el primero del archivo** (Av. Santa Fe 4437, monoambiente apto
profesional, USD 69.900). El texto dice: *"departamento apto profesional…
excelente luminosidad y vista abierta… Sistema de losa radiante… Aire
acondicionado instalado"*. Ni piso, ni ascensor, ni estado, ni antigüedad, ni
orientación (la "vista abierta con orientación privilegiada" no dice CUÁL).
¿Descarte? Es un monoambiente apto profesional que se vende como vivienda o
consultorio — sigue siendo un departamento: **no** es `tipologia_distinta`
(la sugerencia de la regex está de más). Queda:

```yaml
  esperado:
    condition: null
    orientation: null
    floor_number: null
    has_elevator: null
    age_years: null
  no_evaluar: []
  descarte: null
  revisado: true
```

**Ejemplo 2 — Maure al 2300** (3 ambientes, USD 189.000). El texto dice:
*"balcón al frente… refaccionado a nuevo… El edificio es de 7 pisos"*. Ojo:
"el edificio es de 7 pisos" **no** dice en qué piso está la unidad →
`floor_number: null`. "Refaccionado a nuevo" sí habla del estado. ¿"Apto
crédito… ¡Oportunidad única!"? Puro marketing, no es `precio_promocional`
(no hay rebaja ni precio de lanzamiento):

```yaml
  esperado:
    condition: excelente     # "refaccionado a nuevo con materiales de calidad"
    orientation: frente      # "balcón al frente"
    floor_number: null       # dice los pisos del EDIFICIO, no el de la unidad
    has_elevator: null
    age_years: null
  no_evaluar: []
  descarte: null
  revisado: true
```

> Si dudás entre dos valores de `condition` (¿"refaccionado a nuevo" es
> `excelente` o `muy_bueno`?), elegí uno y si te sigue haciendo ruido, mandalo
> a `no_evaluar: [condition]`. La consistencia importa más que el caso
> individual.

---

## 5. Cómo me los pasás

**No hay que "entregar" nada: el archivo ES la entrega.** Editás
`tests/golden/palermo-para-anotar.yaml` en el lugar, guardás, y los evals lo
levantan solos — `tasador.eval.golden.cargar()` lee todos los `*.yaml` de
`tests/golden/` y usa **solo** las entradas con `revisado: true`.

El circuito completo:

```powershell
# 1. Ver cuánto hay anotado (y cuánto falta)
uv run python scripts/golden_set.py --estado

# 2. Anotar en VS Code: tests/golden/palermo-para-anotar.yaml
#    (guardar de a tandas está perfecto)

# 3. Medir contra lo anotado — 3 corridas, se mira la mediana
uv run python scripts/eval_extraccion.py --guardar
uv run python scripts/eval_curaduria.py

# 4. El resultado queda en eval.component_runs y se ve en /calidad
```

También podés decirme en una sesión "anoté 30, corré los evals" y lo hago yo:
el punto es que **la anotación sea tuya** — es lo único que la convierte en
ground truth.

### Si se te acaban los 90

```powershell
uv run python scripts/golden_set.py --preparar --barrio Palermo --n 60 --salida tests/golden/palermo-tanda-2.yaml
```

Genera otra tanda con el mismo formato, excluyendo los ya anotados.

---

## 6. Cuánto hace falta (calculado, no estimado)

Para 95% de confianza en el número del eval:

| Precisión buscada | Extracción | Curaduría |
|---|---|---|
| ±10 puntos | 16 avisos | 175 avisos |
| ±5 puntos | 64 avisos | 695 avisos |

**Los 90 preparados alcanzan para dejar la extracción en ±5 pp** y para
multiplicar por 15 los casos de descarte de la curaduría (hoy: 6). La
curaduría fina va a pedir más tandas — por eso el estrato `enriquecido`
existe: junta descartes rápido.

La regla de lectura no cambia: **ninguna decisión de prompt con una sola
corrida**. Siempre 3, siempre la mediana — `/calidad` ya lo muestra así.
