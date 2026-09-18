# 00 — Visión y alcance

**Fecha:** 13/08/2026 · **Estado:** diseño · **Autor:** Ricardo Brossard

---

## 1. El problema

Cuando alguien pide una tasación a la inmobiliaria, el circuito hoy es:

1. Entra el lead de tasación (Meta Ads, formulario web, Cliengo, o teléfono).
   → **Esto ya está resuelto**: el módulo `appraisals` del panel lo captura,
   lo asigna, agenda la visita y registra el resultado.
2. El agente visita la propiedad.
3. **El agente pone un precio.** ← acá está el problema.
4. Le dice el número al dueño, casi siempre de palabra.

El paso 3 tiene cuatro problemas concretos:

| Problema | Consecuencia |
|---|---|
| **Es caro.** 30-60 minutos de un agente buscando avisos parecidos en Portal B y Portal A, anotando en un papel o un Excel. | A 40 tasaciones/mes son ~30 horas/mes de trabajo calificado. |
| **Es inconsistente.** Dos agentes tasan la misma propiedad con 15-20% de diferencia. | La inmobiliaria no tiene un criterio, tiene N criterios. |
| **No queda documentado.** El número existe, el razonamiento no. | Nadie puede auditar, corregir ni aprender de las tasaciones pasadas. |
| **No hay entregable.** El dueño escucha un número y no se lleva nada. | La competencia que llega con un informe impreso gana la captación. |

Y equivocarse cuesta plata de verdad, en las dos direcciones:

- **Precio alto** → la propiedad se estanca 6-10 meses, acumula "antigüedad de
  aviso" (que la castiga en los portales), y termina bajando igual. Costo de
  oportunidad enorme para la inmobiliaria y para el dueño.
- **Precio bajo** → el dueño se va a la inmobiliaria de al lado que le prometió más,
  o vende barato y queda disconforme.

## 2. La solución

Un servicio que, dada una propiedad a tasar, produce en ~90 segundos un
**Informe de Mercado Comparativo** con:

- Un **rango de precio de publicación sugerido** (mínimo / recomendado / máximo).
- Un **rango esperado de cierre** (aplicando el descuento típico oferta→escritura).
- La **tabla de comparables** usados, con link a cada aviso, y por qué cada uno
  entró o quedó afuera.
- Un **análisis del barrio**: precio medio de la zona, tendencia, tiempo típico de
  publicación, stock competidor.
- Una **narrativa** que explica el razonamiento en lenguaje que el dueño entienda.
- Un **PDF con la marca de la inmobiliaria**, listo para imprimir y entregar.

### 2.1 El principio de diseño que define todo el sistema

> **El LLM hace lenguaje y juicio. La matemática hace números.**

El modelo se usa para lo que un modelo hace bien:

- Convertir el texto libre de un aviso ("3 amb al frente, a refaccionar, expensas
  bajas, apto crédito") en JSON tipado.
- Decidir si dos avisos son la misma propiedad publicada en dos portales.
- Juzgar si un comparable es realmente comparable.
- Redactar el informe.

El precio **nunca** sale de un LLM. Sale de una mediana de USD/m² ajustada con
coeficientes explícitos y documentados. Esto no es purismo: es lo que hace el
sistema auditable, reproducible y defendible ante el dueño de la propiedad.

### 2.2 Las dos reglas duras

1. **Toda cifra del informe debe existir en la tabla de comparables.** Un agente
   crítico lo verifica antes de emitir. Si detecta un número que no puede
   trazar, el informe se rechaza y se regenera.
2. **Con menos de 5 comparables válidos, el sistema devuelve `insufficient_data`.**
   No estima, no extrapola, no adivina. Un sistema que sabe decir "no sé" es lo
   único que se puede poner delante de un cliente.

## 3. Para quién

| Usuario | Qué hace con esto |
|---|---|
| **Agente de la inmobiliaria** | Genera el informe antes de la visita y llega con datos. Después de la visita lo regenera con los datos reales (estado, orientación, piso) y se lo deja al dueño. |
| **Dueño/gerente de la inmobiliaria** | Ve todas las tasaciones con un criterio único. Audita. Detecta agentes que tasan sistemáticamente alto o bajo. |
| **Propietario** (indirecto) | Recibe un informe que justifica el precio en vez de un número a ojo. |

## 4. Alcance

### 4.1 Dentro (v1)

- **Solo VENTA.** Alquiler queda afuera (dinámica de precios distinta, otro
  conjunto de ajustes, y no es lo que pidió el negocio).
- **Departamentos, PH y casas.** Los tipos que concentran el volumen.
- **CABA y GBA Norte.** La zona donde opera la inmobiliaria y donde hay densidad de
  comparables. Sin densidad de datos el método no funciona y es honesto no
  prometerlo.
- Informe en PDF + ficha web.
- Multi-tenant desde el día 1.
- Backtest y métricas de calidad publicadas dentro del propio sistema.

### 4.2 Fuera (v1, explícitamente)

| Fuera | Por qué |
|---|---|
| Alquileres | Otro modelo de precios. Fase posterior. |
| Terrenos, galpones, locales, campos | Comparables escasos y muy heterogéneos; el método comparativo directo no aplica bien. |
| Interior del país | Sin densidad de avisos, el resultado sería ruido con formato lindo. |
| Tasación con validez legal | La firma un martillero matriculado. Esto es un **insumo** para esa tasación, no un reemplazo. Ver [10 — Seguridad y legal](10-seguridad-y-legal.md). |
| Predicción de precio de cierre real | No existe registro público de precios de escrituración en Argentina. Solo se puede estimar con un descuento documentado sobre el valor de oferta. |
| Valuación por fotos / visión por computadora | Interesante, caro, y no es el cuello de botella. Anotado como mejora futura. |
| Escribir en la base de la inmobiliaria | Regla de aislamiento. Ver [01 — Arquitectura](01-arquitectura.md). |

## 5. Criterios de éxito

El proyecto se considera exitoso si, al terminar la Etapa 5:

| # | Criterio | Cómo se mide |
|---|---|---|
| 1 | **Le gana al baseline** | MdAPE del sistema < MdAPE de "mediana de USD/m² del barrio" sobre el mismo dataset de backtest. Si no le gana al baseline, el pipeline de agentes no se justifica y hay que rediseñar. |
| 2 | **Precisión utilizable** | MdAPE ≤ 15% y PPE20 ≥ 65% (al menos 65% de los casos con error < 20%). |
| 3 | **Cobertura honesta** | ≥ 70% de las tasaciones de CABA/GBA Norte producen informe; el resto devuelve `insufficient_data` explícito, no un número malo. |
| 4 | **Costo** | ≤ USD 0,15 por informe y ≤ USD 15/mes de infraestructura total. |
| 5 | **Latencia** | p95 ≤ 3 minutos de punta a punta. |
| 6 | **Cero alucinación de cifras** | 0 informes emitidos con un número no trazable a la tabla de comparables, sobre el set de regresión. |
| 7 | **Uso real** | Al menos 20 informes generados por gente de la inmobiliaria que no sea yo. |

El criterio 1 es el que separa este proyecto de una demo. Si el sistema no le gana
a una consulta SQL de una línea, el sistema no sirve — y hay que estar dispuesto a
descubrirlo y decirlo.

## 6. Objetivo secundario (explícito, no oculto)

Este proyecto también existe para cubrir un hueco concreto de perfil profesional:
experiencia demostrable en **arquitecturas de agentes en producción**. Eso condiciona
algunas decisiones y conviene decirlo en voz alta:

- Se usan LangGraph, CrewAI, RAG con pgvector, un gateway de modelos, observabilidad
  con trazas y una suite de evals **porque el problema los justifica**, no al revés.
  Cada elección tiene su fundamento en [01 — Arquitectura](01-arquitectura.md), y
  donde una herramienta se usa por razones de perfil y no técnicas, está dicho.
- La disciplina de producción (tests, evals, backups, runbook, degradación limpia)
  no es adorno: es lo que hace la diferencia entre un repo de GitHub y un sistema.

Lo que **no** se hace: inflar la arquitectura con componentes que no aportan.
Si algo se puede resolver con una query, se resuelve con una query.

## 7. Glosario

| Término | Significado |
|---|---|
| **CMA** | *Comparative Market Analysis*. El informe que produce este sistema. |
| **Comparable** | Aviso de una propiedad similar (zona, tipo, superficie) usado como referencia de precio. |
| **Propiedad sujeto** (*subject property*) | La propiedad que se está tasando. |
| **USD/m²** | Unidad de comparación. Precio dividido superficie ponderada. |
| **Superficie ponderada** | Cubierta + 50% de la descubierta (criterio estándar del mercado, ver [05](05-metodologia-de-valuacion.md)). |
| **MdAPE** | Mediana del error porcentual absoluto. Métrica principal de precisión. |
| **PPE20** | % de predicciones con error < 20%. Métrica de consistencia. |
| **Tenant** | Una inmobiliaria cliente. la inmobiliaria es el tenant #1. |
| **Panel** | El sistema existente de la inmobiliaria (`leads-ventas`), ajeno a este proyecto. |
