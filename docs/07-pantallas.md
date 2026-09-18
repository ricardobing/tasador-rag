# 07 — Pantallas

**Stack:** Next.js 15 App Router + TypeScript + Tailwind + shadcn/ui + TanStack Table.
**Criterio:** se reusan las decisiones de UI que ya funcionaron en el panel de
la inmobiliaria (DataTable compartida, drawer en vez de navegación, skeletons en vez de
spinners, mobile-first porque los agentes usan el celular).

**Por qué la UI propia es prioritaria:** es la superficie del producto. Es lo que se
demuestra sin depender del panel de un cliente, y lo que ve la inmobiliaria #2.

---

## 1. Mapa de rutas

```
/login                        pública
/                             → redirect a /informes
/informes                     listado
/informes/nuevo               alta
/informes/[id]                ficha (o progreso si está corriendo)
/comparables                  explorador del corpus
/calidad                      backtest y métricas          [admin]
/admin/fuentes                salud de las fuentes de datos [admin]
/admin/organizacion           marca, cuotas, API keys       [admin]
/admin/usuarios               alta de usuarios              [admin]
```

---

## 2. `/login`

Email + contraseña. Logo del tenant si la URL trae `?org=slug`.
Sin registro público: los usuarios los crea un admin.

Errores genéricos ("credenciales inválidas"), rate limit a 5 intentos por minuto por
IP y por email.

---

## 3. `/informes` — listado

La pantalla donde vive el usuario.

**Tabla** (TanStack Table, columnas ocultables con persistencia en localStorage):

| Columna | Notas |
|---|---|
| Dirección | Link a la ficha |
| Barrio | |
| Tipo / Amb / m² | Compacto: `Depto · 3 amb · 78 m²` |
| Valor sugerido | `USD 182.000` + rango en gris debajo |
| USD/m² | |
| Confianza | Chip verde/ámbar/gris |
| Comparables | `9 de 47` |
| Estado | Chip. `INSUFFICIENT_DATA` en ámbar, no en rojo — no es un error |
| Fecha | Relativa ("hace 2 h") con tooltip absoluto |
| Agente | |
| — | Botones: PDF, regenerar, compartir |

**Filtros:** estado, barrio, rango de fechas, confianza, agente, y búsqueda por
dirección (trigram, tolerante a tildes y typos).

**Header:** contador del mes contra la cuota, y costo acumulado si el usuario es admin.

**Mobile:** la tabla colapsa a cards con dirección, valor, confianza y estado.

---

## 4. `/informes/nuevo` — alta

Formulario en dos bloques, con el segundo colapsado por defecto. Ese diseño refleja
el flujo real: **antes de la visita se sabe poco, después se sabe todo.**

**Bloque 1 — Lo mínimo (siempre visible)**

- Dirección (input con autocompletado contra Nominatim; muestra el barrio detectado en
  vivo, con un chip de confianza de geocodificación).
- Tipo: departamento / casa / PH.
- Ambientes, superficie total, superficie cubierta.

Con eso el botón "Generar" ya se habilita.

**Bloque 2 — "Tengo más datos" (colapsable)**

Dormitorios, baños, antigüedad, piso, ascensor, estado, orientación, cocheras,
expensas, amenities (chips multi-select), notas.

**Microcopy que importa:** debajo del botón, en gris:
*"Con más datos el rango es más ajustado. Podés generar ahora y regenerar después de
la visita."*

**Validación en vivo:** si `cubierta > total`, error inline. Si la superficie por
ambiente es implausible (< 12 m²/amb), una advertencia amarilla que no bloquea.

---

## 5. `/informes/[id]` — mientras corre

Stepper vertical alimentado por polling de `GET /v1/reports/{id}` cada 2 s.

```
✓ Normalizando la dirección            Belgrano · 1,8 s
✓ Buscando comparables                 47 candidatos · 0,4 s
✓ Analizando avisos                    47 procesados · 14,2 s
✓ Detectando duplicados                6 grupos · 5,1 s
◐ Seleccionando comparables            ...
○ Calculando el valor
○ Analizando el barrio
○ Redactando el informe
○ Verificando el informe
○ Generando PDF
```

**Por qué en castellano llano y no `extract_features`:** el usuario es un agente
inmobiliario. Y además comunica el método, que es parte del producto: se ve que el
sistema **verifica** lo que escribió.

Si tarda más de 3 minutos, aparece "Está tardando más de lo normal" con opción de
cancelar. El proceso sigue en background igual.

---

## 6. `/informes/[id]` — resultado

### 6.1 Encabezado

```
┌──────────────────────────────────────────────────────────┐
│  Av. Cabildo 2530 4°B — Belgrano                         │
│  Departamento · 3 amb · 78,5 m² totales / 72 m² cubiertos│
│                                                          │
│         USD 168.000 ── 182.000 ── 196.000                │
│                       ▲ sugerido                         │
│         USD 2.441 /m²         Confianza ALTA ●           │
│                                                          │
│  Rango esperado de cierre:  USD 154.700 – 172.900        │
│                                                          │
│  [ Descargar PDF ]  [ Compartir ]  [ Regenerar ]         │
└──────────────────────────────────────────────────────────┘
```

El rango de cierre va arriba, no escondido. Es el dato que evita la conversación
incómoda tres meses después.

### 6.2 Distribución de comparables

Un gráfico de puntos (no de barras) sobre el eje USD/m²: cada comparable es un punto,
los excluidos en gris translúcido, la mediana como línea vertical, la banda p25-p75
sombreada, y la propiedad sujeto marcada. Un solo gráfico que muestra dónde cae la
propiedad y qué tan disperso está el mercado.

### 6.3 Tabla de comparables

Ordenable, con toggle **"Ver también los 38 excluidos"**.

| Dir. | Fuente | Precio | m² pond. | Amb | USD/m² | Ajuste | USD/m² aj. | Dist. | Días | ✓/✗ |
|---|---|---|---|---|---|---|---|---|---|---|

Cada fila expande al hacer click y muestra el desglose de ajustes y el motivo de
exclusión si corresponde. La dirección linkea al aviso original.

**Esto es lo que genera confianza.** Un agente escéptico va a querer ver el de al lado
que él conoce, y poder decir "ah, lo descartó porque estaba en pesos".

### 6.4 Contexto del barrio

Cuatro tarjetas: USD/m² oficial del GCBA (con fecha y fuente), USD/m² de nuestro
corpus, stock activo competidor, tiempo mediano de publicación. Más el párrafo
redactado.

Que se muestre el dato oficial **al lado** del propio es deliberado: si divergen, el
usuario lo ve y pregunta. Ocultarlo sería la decisión cómoda.

### 6.5 Informe redactado

El markdown renderizado, con un botón "Copiar" para pegarlo en un mail o WhatsApp.

### 6.5 bis «Preguntale al informe» (18/09/2026)

Una caja de texto debajo de la narrativa. La respuesta llega con las citas que la
sostienen —cada una es un hecho del informe (`[C-07]`, `[V]`) o un párrafo de la
metodología (`[Met §4.2]`)— o llega rechazada: cuando no hay evidencia, la API no
llama al modelo y lo dice. Debajo de cada respuesta, el costo y el tiempo. No
aparece en el link compartido del propietario (doc 18 §5; ADR-013).

### 6.6 Limitaciones

Recuadro gris, siempre visible, nunca colapsado. Con el texto de
[05 §9](05-metodologia-de-valuacion.md) y la leyenda de no-tasación-legal.

### 6.7 Detalle técnico (colapsado, solo admin)

Tiempo y costo por nodo, modelos usados, versiones de motor/prompts/método,
`trace_id` con link a Langfuse, y rechazos del crítico.

---

## 7. `/informes/[id]` — sin datos suficientes

No es una pantalla de error. Es una pantalla que explica.

```
No pudimos generar el informe

Encontramos 3 comparables válidos y necesitamos al menos 5.

De 11 avisos encontrados en la zona:
  4 publicados en pesos          (no comparables)
  2 sin superficie declarada
  2 duplicados de otros avisos

Qué podés hacer:
  • Ampliar la búsqueda a barrios limítrofes   [ Reintentar así ]
  • Verificar la dirección: la ubicamos en "Villa Riachuelo"
  • Cargar comparables manualmente             [ Cargar ]
```

Con las mismas acciones ofrecidas como botones, no como sugerencias vacías.

---

## 8. `/comparables` — explorador del corpus

Para que el agente pueda buscar avisos sin generar un informe, y para que el equipo
pueda auditar la calidad de los datos.

Filtros por barrio, tipo, rango de m², rango de precio, fuente, antigüedad del aviso.
Tabla con las mismas columnas. Cada fila expandible muestra la descripción original y
las features extraídas **lado a lado** — que es la forma más rápida de detectar que
el extractor se está equivocando.

Botón "Reportar extracción incorrecta" → marca `needs_review`. Esos casos alimentan
el golden set de evals (ver [09](09-evaluacion-y-backtest.md)).

---

## 9. `/calidad` — backtest y métricas `[admin]`

La pantalla que hace que este proyecto sea demostrable.

**Arriba, la comparación que importa:**

```
        MdAPE        PPE20       Cobertura
Sistema  11,4%       71%          78%
Baseline 18,9%       52%         100%
         ▲ 40% mejor
```

Con el baseline (mediana de USD/m² del barrio) siempre al lado. Si el sistema no le
gana, se ve inmediatamente.

**Debajo:** evolución por versión de motor (línea temporal de MdAPE por release),
error por barrio (heatmap: dónde funciona bien y dónde no), distribución del error,
y la tabla de casos peores con link a cada uno para diagnosticar.

**Botón "Correr backtest"** con selector de dataset. Corre en background y notifica.

---

## 10. `/admin/fuentes` — salud de los datos

Una tarjeta por fuente:

```
┌─ PORTAL_A ──────────────────────────────── ● OK ─┐
│ Última corrida:  hoy 03:14        1.204 avisos     │
│ Descubiertos 340 · Bajados 128 · Cacheados 205    │
│ Bloqueados 7 (5,2%)   ← alerta si supera 20%      │
│ Costo del mes: USD 0,42 / presupuesto USD 3,00    │
│ [ Correr ahora ]  [ Ver log ]                     │
└────────────────────────────────────────────────────┘
```

Más el **chequeo de sesgo**: para cada barrio con datos, nuestro USD/m² contra el
oficial del GCBA, con la desviación. Si algún barrio se va de ±15%, chip rojo.

Esta pantalla es la que avisa que Portal A cambió su defensa **antes** de que el
corpus se pudra y los informes empiecen a salir mal en silencio.

---

## 11. `/admin/organizacion`

Marca del PDF (logo, color, pie de página), tono de la redacción, cuota mensual y
consumo, presupuesto de fetch, gestión de API keys (crear, ver prefijo y último uso,
revocar), y modo de sincronización de inventario (`push`/`pull`/`none`).

---

## 12. Criterios transversales de UI

| Criterio | Detalle |
|---|---|
| **Mobile first** | El agente genera el informe desde el auto, antes de subir |
| **Skeletons, no spinners** | Igual que en el panel |
| **Nada bloquea** | Todo lo que tarda va a background con feedback |
| **La confianza siempre visible** | Ningún número aparece sin su nivel de confianza al lado |
| **Los excluidos se pueden ver** | La transparencia es el producto |
| **Accesibilidad** | Contraste AA, foco visible, labels reales, navegable por teclado |
| **Sin dark patterns** | Ninguna limitación escondida, ningún número inflado por default |
