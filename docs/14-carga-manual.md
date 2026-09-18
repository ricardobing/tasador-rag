# 14 — Carga manual de datos

> **Hueco detectado el 13/08/2026.** El diseño original asumía que todos los
> comparables vienen del corpus automático. Eso deja al sistema inútil en tres
> situaciones reales y frecuentes. Este documento cierra ese hueco.

---

## 1. Por qué hace falta

| Situación | Frecuencia | Qué pasa hoy sin esto |
|---|---|---|
| **Barrio sin cobertura en el corpus** | Alta al principio | `INSUFFICIENT_DATA` y el agente se queda sin informe |
| **El agente conoce un comparable que el corpus no tiene** — una venta que acaba de cerrar, una propiedad de un colega, algo que nunca se publicó | Muy alta | El mejor dato disponible se pierde |
| **La ingesta está caída o bloqueada** | Media | El producto entero deja de servir |
| **Propiedad atípica** (PH con terraza, piso único, casa en lote grande) donde los comparables automáticos no aplican | Media | El sistema tasa mal con comparables malos |
| **Demo / venta a un cliente nuevo** antes de tener corpus de su zona | — | No se puede mostrar nada |

Y una razón de fondo: **el agente inmobiliario sabe cosas que ningún portal publica.**
Un sistema que no le deja aportar ese conocimiento lo trata como un espectador, y
termina siendo un sistema que no usa.

---

## 2. Los cuatro modos de operación

```
   Datos de la propiedad          Comparables
   ─────────────────────          ───────────
A) manual (form)          +       automáticos (corpus)     ← el caso normal
B) manual (form)          +       automáticos + manuales   ← el más útil
C) manual (form)          +       100% manuales            ← fallback total
D) manual (form)          +       automáticos, editados    ← corrección del agente
```

**El modo A ya está diseñado** ([07 §4](07-pantallas.md)): `/informes/nuevo` es un
formulario, la propiedad a tasar siempre se carga a mano.

**Los modos B, C y D son lo que faltaba.**

---

## 3. Modo C — informe 100% manual

El más importante de los tres, porque **hace que el producto sirva desde el día 1**,
antes de que funcione una sola línea de ingesta.

### 3.1 Flujo

1. El agente carga la propiedad a tasar (igual que siempre).
2. Elige "Cargar comparables a mano".
3. Carga N comparables en una grilla tipo planilla, con lo mínimo:
   **precio, moneda, superficie, ambientes, dirección**. Opcionales: estado,
   antigüedad, orientación, piso, fuente, link, notas.
4. El sistema corre **el mismo pipeline** desde el nodo 6 (curaduría): reglas duras,
   ajustes, mediana robusta, rango, confianza, contexto, redacción, crítico, PDF.

**El motor de valuación es exactamente el mismo.** No hay un "modo degradado" con otra
matemática. Cambia de dónde vienen los comparables, nada más. Eso es lo que permite
que el modo C sirva de verdad y no sea un consuelo.

### 3.2 Formas de cargar

| Vía | Cuándo | Detalle |
|---|---|---|
| **Grilla en pantalla** | 3-10 comparables | Editable como planilla, tab entre celdas, validación en vivo |
| **Pegar desde Excel** | El agente ya tiene su planilla | Detecta columnas del portapapeles y las mapea, con confirmación |
| **Pegar una URL de aviso** | Uno por uno, rápido | Intenta bajarlo y parsearlo; si no puede, deja los campos vacíos para completar a mano |
| **Pegar texto libre del aviso** | Cuando la URL está bloqueada | El nodo 4 extrae los campos del texto pegado. **Acá el LLM sí aporta** |
| **CSV** | Carga masiva inicial | Plantilla descargable con las columnas esperadas |

La opción de **pegar el texto del aviso** merece énfasis: resuelve el caso de Portal B
sin scrapearlo. El agente copia el texto del aviso que está mirando en su navegador y
el sistema lo estructura. Es manual, es legítimo, y funciona.

---

## 4. Modo B — mezclar automáticos y manuales

El agente genera el informe normal y, en la ficha, agrega comparables que conoce.

- Cada comparable manual entra al set y **se recalcula todo** (mediana, rango,
  confianza) al instante.
- La tabla de comparables marca el origen con un chip: `Portal A` / `el CRM` /
  **`Manual`**.
- La confianza **sube** con más comparables, pero un set con mayoría de manuales
  agrega una nota en el informe (§7).

---

## 5. Modo D — corregir lo automático

Sobre la tabla de comparables de un informe generado, el agente puede:

| Acción | Requiere | Queda registrado |
|---|---|---|
| **Excluir** un comparable que el sistema incluyó | Motivo (lista cerrada + texto) | Sí, con quién y cuándo |
| **Incluir** uno que el sistema excluyó | Motivo | Sí |
| **Corregir un dato** (superficie mal extraída, precio desactualizado) | — | Sí, con valor anterior y nuevo |

Las correcciones de datos **vuelven al corpus** como `listing_features` verificadas por
un humano (`verified_by`), con prioridad sobre la extracción del LLM. Así el sistema
aprende de las correcciones en vez de repetir el error en cada informe.

Toda corrección alimenta además el golden set de evals
([09 §3.3](09-evaluacion-y-backtest.md)).

---

## 6. Modelo de datos — los cambios

### 6.1 `corpus.listings` — corpus público vs. privado

```sql
alter table corpus.listings
  add column org_id uuid references core.organizations(id) on delete cascade,
  add column created_by uuid references core.users(id),
  add column verified_by uuid references core.users(id),
  add column verified_at timestamptz;

-- 'MANUAL' y 'PASTED' se suman al CHECK de source
alter table corpus.listings drop constraint listings_source_check;
alter table corpus.listings add constraint listings_source_check
  check (source in ('PORTAL_A','PORTAL_B','MELI','CRM_NETWORK',
                    'BADATA','PROPERATI','MANUAL','PASTED'));

-- un comparable manual pertenece a su tenant; uno scrapeado es de todos
alter table corpus.listings add constraint listings_manual_tiene_org
  check (source not in ('MANUAL','PASTED') or org_id is not null);

create index idx_listings_org on corpus.listings (org_id) where org_id is not null;
```

**La decisión de fondo: `org_id` es NULLABLE en el corpus.**

- `org_id IS NULL` → aviso público (Portal A, BA Data). Compartido entre todos los
  tenants, que es lo que hace que el costo marginal por cliente nuevo sea casi cero
  ([11 §5](11-costos.md)).
- `org_id = <tenant>` → aviso cargado por ese tenant. **Solo ese tenant lo ve.**

Esto es innegociable: lo que un agente sabe de una venta cerrada es información
comercial suya. Filtrarla a otra inmobiliaria sería inaceptable — y probablemente
ilegal si incluye datos de una operación privada.

La query del nodo 2 pasa a filtrar `WHERE (org_id IS NULL OR org_id = :tenant)`. El
test de aislamiento por tenant cubre este caso explícitamente.

### 6.2 `core.reports` — transparencia sobre la intervención

```sql
alter table core.reports
  add column input_mode text not null default 'AUTO'
      check (input_mode in ('AUTO','MIXED','MANUAL')),
  add column manual_comparables smallint not null default 0,
  add column has_manual_override boolean not null default false;
```

### 6.3 `core.report_overrides` — auditoría de las correcciones

```sql
create table core.report_overrides (
  id             uuid primary key default gen_random_uuid(),
  report_id      uuid not null references core.reports(id) on delete cascade,
  listing_id     uuid references corpus.listings(id) on delete set null,
  action         text not null check (action in ('INCLUDE','EXCLUDE','EDIT_FIELD','ADD_MANUAL')),
  field          text,
  old_value      text,
  new_value      text,
  reason         text not null,
  created_by     uuid not null references core.users(id),
  created_at     timestamptz not null default now()
);
create index idx_overrides_report on core.report_overrides (report_id, created_at);
```

**Motivo obligatorio, sin excepción.** Un informe cuyo valor se movió porque alguien
excluyó tres comparables baratos tiene que poder explicarse seis meses después. Es el
mismo criterio de las correcciones administrativas de estado que ya usaste en el
módulo de tasaciones del panel.

---

## 7. Reglas de integridad — lo que impide que esto se convierta en un problema

El riesgo obvio de la carga manual es que alguien —sin mala intención— empuje el
número hacia donde quiere. Las defensas:

| Regla | Detalle |
|---|---|
| **Las reglas duras de curaduría se aplican igual** | Un comparable manual con USD/m² fuera del rango sensato se marca. El agente puede forzarlo, con motivo, y queda registrado |
| **Todo override deja rastro** | `report_overrides` con autor, motivo y valores. Visible en el informe |
| **El PDF declara el origen de los comparables** | *"9 comparables: 6 de portales, 3 cargados manualmente por Juan Pérez"* |
| **Mínimo de 5 sigue vigente** | Cargar 2 comparables a mano no genera un informe |
| **Confianza penalizada** | Un set con > 50% de manuales baja un escalón de confianza. No porque el dato manual sea peor, sino porque no es verificable por un tercero |
| **Los manuales no contaminan las métricas públicas** | `market_index` de `source='INTERNAL'` y el chequeo de sesgo se calculan **solo sobre el corpus público**. Si no, un tenant podría desviar su propio indicador de referencia |
| **Los informes con override se excluyen del backtest** | Medir la precisión del sistema sobre informes que un humano corrigió sería medir al humano |

Esa última regla es fácil de olvidar y arruinaría las métricas de
[09](09-evaluacion-y-backtest.md). Queda escrita acá.

---

## 8. API

```http
POST   /v1/reports/{id}/comparables          agregar uno manual
PATCH  /v1/reports/{id}/comparables/{cid}    incluir/excluir/corregir (requiere reason)
DELETE /v1/reports/{id}/comparables/{cid}    quitar uno manual
POST   /v1/reports/{id}/recalculate          recalcular desde el nodo 6
POST   /v1/comparables/parse-url             intentar parsear una URL de aviso
POST   /v1/comparables/parse-text            extraer campos de texto pegado
POST   /v1/comparables/import                CSV / pegado de planilla
GET    /v1/comparables/template.csv          plantilla de importación
```

`POST /v1/reports` acepta además comparables en el cuerpo, para el modo C directo:

```json
{
  "property": { "...": "..." },
  "comparables": [
    {"address":"Ugarteche 3000","price":140000,"currency":"USD",
     "surface_m2":60,"rooms":2,"condition":"bueno","source_url":"https://..."}
  ],
  "options": {"comparables_mode": "manual_only"}
}
```

`comparables_mode`: `auto` (default) · `augment` (corpus + los provistos) ·
`manual_only`.

**Recalcular es barato:** entra por el nodo 6, así que no repite extracción ni
búsqueda. Cuesta ~USD 0,02 y tarda ~25 s.

---

## 9. Pantallas

### 9.1 `/informes/nuevo` — selector de modo

Debajo del formulario, tres opciones:

```
Comparables:
  ● Automáticos          Buscamos en el mercado (recomendado)
  ○ Automáticos + míos   Buscamos y además agregás los que conocés
  ○ Solo los míos        Cargás vos todos los comparables
```

Si elige el 2° o 3°, aparece la grilla antes de generar.

### 9.2 Grilla de carga

Planilla editable, navegable con Tab, con validación por celda:

| Dirección* | Precio* | Mon.* | m²* | Amb | Dorm | Estado | Antig. | Piso | Fuente | URL | ▾ |
|---|---|---|---|---|---|---|---|---|---|---|---|

- Calcula y muestra el **USD/m² en vivo** por fila, y la mediana del set abajo. El
  agente ve al instante si cargó algo mal.
- Fila con USD/m² fuera de ±40% de la mediana: se resalta en ámbar (no bloquea).
- Botones: `+ Fila` · `Pegar de Excel` · `Pegar aviso` · `Importar CSV`.
- Contador: **"3 de 5 mínimos"**, en rojo hasta llegar a 5.

### 9.3 "Pegar aviso" — modal

Un textarea. El agente pega el texto de un aviso de cualquier portal. Al aceptar, el
sistema extrae los campos y **los muestra para confirmar antes de agregarlos**, con lo
inferido marcado.

Sin confirmación no entra nada. Una extracción silenciosa que se equivoca es peor que
pedir un click.

### 9.4 En la ficha del informe

La tabla de comparables suma:

- Chip de origen por fila: `Portal A` · `el CRM` · `Manual` · `Pegado`.
- Botón `+ Agregar comparable` arriba.
- Por fila, un menú: `Excluir` / `Incluir` / `Corregir dato`, cada uno pidiendo motivo.
- Banda superior cuando hay cambios sin aplicar:
  **`Modificaste el set de comparables — [Recalcular]`**.
- Si hubo overrides: un aviso permanente
  *"Este informe fue ajustado manualmente. Ver historial."* con link a
  `report_overrides`.

---

## 10. Impacto en el plan de desarrollo

Se suman a [12](12-plan-de-desarrollo.md):

| Etapa | Tarea nueva |
|---|---|
| **2** | 2.7 — El motor de valuación acepta comparables de cualquier origen (ya lo hace si se diseña bien; se testea explícito) |
| **3** | 3.14 — `parse-text`: extracción desde texto pegado (reusa el nodo 4) |
| **4** | 4.11 — Grilla de carga manual + pegar de Excel + importar CSV |
| **4** | 4.12 — Overrides en la ficha del informe + `report_overrides` + recalcular |
| **4** | 4.13 — Chips de origen y declaración en el PDF |
| **5** | 5.11 — Excluir del backtest los informes con override |

**Y un cambio de orden que importa:** el **modo C se implementa en la Etapa 4, no
después**. Razón: es lo que hace que el producto sea demostrable y utilizable aunque
la ingesta no funcione. Es el seguro contra el riesgo R1.

Costo estimado: **+3 días** sobre el plan original. Es la mejor relación
valor/esfuerzo de todo el proyecto.
