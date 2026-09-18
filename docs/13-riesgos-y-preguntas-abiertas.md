# 13 — Riesgos y preguntas abiertas

---

## 1. Riesgos, ordenados por cuánto duelen

### 🔴 R1 — No conseguir comparables suficientes

**Probabilidad:** media-alta · **Impacto:** el proyecto no existe

`VERIFICADO`: Portal B devuelve 403 a todo desde una IP de datacenter, incluso al
sitemap. Portal A sirve `robots.txt` y sitemaps pero devuelve 403 en las páginas
HTML. Un VPS de Hetzner es una IP de datacenter.

**Mitigaciones:**

1. El fetcher es intercambiable (ADR-009): directo → Zyte → caché → error explícito.
2. Zyte PAYG a ~USD 0,13/1.000 requests entra en presupuesto.
3. **BA Data + inventario propio funcionan sin ningún portal.** El sistema degradado
   (menos preciso, con confianza BAJA) sigue siendo mejor que el status quo.
4. **La Etapa 1 existe para responder esto en semana 2,5**, con una puerta de decisión
   explícita.

**Plan B si todo falla:** el sistema se apoya en la serie oficial del GCBA + las 285
propiedades de la inmobiliaria + carga manual de comparables por el agente. Sigue siendo útil
—estructura el criterio, genera el PDF, documenta el razonamiento— pero deja de ser un
producto vendible. Se documentaría como tal.

---

### 🔴 R2 — El método no le gana al baseline

**Probabilidad:** media · **Impacto:** el pipeline no se justifica

Puede pasar que una mediana de USD/m² del barrio prediga casi tan bien como todo el
sistema. Sería un resultado incómodo y perfectamente posible.

**Mitigación:** la Etapa 2 lo mide **antes de construir un solo agente**, con el motor
determinístico solo. Si ahí ya no gana, el problema es de método o de comparables, y
se arregla en una función de 200 líneas.

**Si pasa igual con el sistema completo:** se documenta, se simplifica el sistema a lo
que sí aporta, y se dice públicamente. Un ingeniero que mide y reporta un resultado
negativo es más confiable que uno que solo muestra los buenos. Y sigue habiendo
producto: el informe, el PDF y la trazabilidad valen aunque el número lo diera una
query simple.

---

### 🟡 R3 — Sesgo del corpus

**Probabilidad:** alta · **Impacto:** informes sistemáticamente corridos

Si el corpus se arma solo con Portal A, hereda su composición: puede sobre-representar
cierto segmento, cierta zona o cierto tipo de publicador. Un sesgo del 8% en un barrio
es invisible en el uso diario y arruina las tasaciones.

**Mitigaciones:**

- **Comparación permanente contra la serie oficial del GCBA** (que además se construye
  sobre la base de Portal A, así que un desvío grande señala un problema nuestro, no
  del mercado). Alerta a ±15% por barrio, visible en `/admin/fuentes`.
- Métrica de **sesgo con signo** en cada backtest.
- Segmentación por barrio en `/calidad`: un sesgo local se ve.

---

### 🟡 R4 — Deduplicación fallida

**Probabilidad:** media · **Impacto:** precios corridos, en silencio

Si el mismo departamento entra tres veces (Portal A + Portal B + el CRM), pesa triple.
Es el bug más peligroso porque **no se nota**: el sistema sigue devolviendo números que
parecen razonables.

**Mitigaciones:** cascada de 3 capas con umbrales conservadores; golden set de 30 pares
con precisión objetivo > 0,95; y en `/calidad`, el ratio clusters/avisos por barrio
como métrica vigilada (si de golde cae, algo se rompió en el matching).

---

### 🟡 R5 — Un portal bloquea definitivamente

**Probabilidad:** media-alta (a 12 meses) · **Impacto:** medio

Es el escenario **esperado**, no el excepcional. La industria anti-bot se endurece,
no se relaja.

**Mitigación:** el diseño ya lo asume. Corpus persistido (lo que se bajó, se queda),
fuentes intercambiables, fuentes oficiales que no se bloquean, y alerta temprana en
`/admin/fuentes` cuando la tasa de bloqueo sube. El sistema degrada, no muere.

---

### 🟡 R6 — Que un informe malo llegue a un propietario

**Probabilidad:** baja-media · **Impacto:** reputacional, para la inmobiliaria y para mí

**Mitigaciones en capas:** `INSUFFICIENT_DATA` antes que un número malo; confianza
visible en cada informe; el crítico verificando cada cifra; limitaciones impresas en el
PDF; y el agente humano en el medio, que siempre revisa antes de entregar.

**Lo que hay que evitar activamente:** que el equipo empiece a confiar ciegamente. El
diseño de la UI lo trabaja a propósito — la confianza y los comparables excluidos están
siempre a la vista, no escondidos.

---

### 🟢 R7 — Costo descontrolado de LLM

**Probabilidad:** baja · **Impacto:** bajo

**Mitigación:** cuotas por tenant, presupuestos en LiteLLM, alerta a USD 2/día, costo
registrado por informe y por nodo. El peor caso realista es un backtest en loop, y eso
lo corta el presupuesto de la API key.

---

### 🟢 R8 — Sobre-ingeniería

**Probabilidad:** media · **Impacto:** el proyecto no se termina

El riesgo real de un proyecto cuyo objetivo secundario es demostrar habilidades es
agregar componentes para lucirlos. Un Kubernetes acá sería exactamente eso.

**Mitigación:** [01 §6](01-arquitectura.md) lista explícitamente lo que **no** está y
por qué. Cada etapa tiene un entregable demostrable; si una se estira más del 150%, se
recorta el alcance.

---

### 🟢 R9 — Que la inmobiliaria no lo use

**Probabilidad:** media · **Impacto:** bajo para el objetivo principal

Nadie lo pidió. Puede que no lo adopten.

**Mitigación:** el sistema es standalone. Funciona con su propia UI, sus propios datos
y su propio dominio. La integración con el panel es media jornada al final y detrás de
un flag. Si no lo usan, sigue siendo un sistema en producción con métricas reales —que
es el objetivo principal declarado en [00 §6](00-vision-y-alcance.md).

---

## 2. Preguntas abiertas

### 2.1 Bloqueantes de alguna etapa

| # | Pregunta | Cómo se responde | Etapa |
|---|---|---|---|
| Q1 | ¿Cuáles son los rate limits reales de la API de el CRM? | Mail a soporte | 0 |
| Q2 | ¿Una agencia puede tener más de una API key de el CRM? | Mail a soporte | 0 |
| Q3 | **¿el CRM habilita lectura de la Red por API?** | Mail a soporte. **Si es que sí, cambia la estrategia de datos entera** | 0 |
| Q4 | ¿El plan de Portal A/Portal B de la inmobiliaria incluye exportables o informes? | Preguntarle a Gastón | 0 |
| Q5 | ¿Portal A responde a un fetch directo desde el VPS con headers realistas? | Probarlo | 1 |
| Q6 | ¿Las fichas de Portal A tienen JSON-LD? | Inspeccionar el HTML | 1 |
| Q7 | ¿Qué dicen los T&C reales de Portal A sobre extracción automatizada? | Encontrar la URL correcta y leerlos | 1 |
| Q8 | ¿Cuántos avisos activos hay por barrio de interés? | Reporte de cobertura | 1 |
| Q9 | ¿El motor determinístico le gana al baseline? | Backtest v0 | 2 |

### 2.2 De diseño, con recomendación

| # | Pregunta | Recomendación | Estado |
|---|---|---|---|
| Q10 | ¿Nombre comercial del producto? | Definirlo antes del PDF con marca (Etapa 4). "Tasador" es el nombre técnico | Abierta |
| Q11 | ¿Precios en ARS se descartan o se convierten? | **Descartar** ([05 §3](05-metodologia-de-valuacion.md)). Convertir en un mercado con múltiples cotizaciones agrega más error del que resuelve | Decidida |
| Q12 | ¿Cochera como coeficiente o valor absoluto? | **Absoluto** (USD 12.000). Una cochera no vale proporcionalmente más en un depto caro | Decidida, a calibrar en Etapa 6 |
| Q13 | ¿Se muestra el informe al propietario directamente? | v1 **no**: el agente lo revisa y lo entrega. Un link compartible existe, pero lo comparte el agente | Decidida |
| Q14 | ¿El corpus se comparte entre tenants? | **Sí**, los avisos públicos no son de nadie y compartirlos hace el costo marginal casi cero ([11 §5](11-costos.md)). Lo que jamás se comparte es qué buscó cada tenant | Decidida |
| Q15 | ¿Alquileres en v2? | Sí, pero después de que ventas esté medido y andando | Diferida |
| Q16 | ¿Qué pasa si dos agentes tasan la misma propiedad? | Se generan dos informes; `/informes` los agrupa por dirección normalizada y muestra un aviso. No se bloquea | Decidida |
| Q17 | ¿Fotos de la propiedad para evaluar estado con visión? | Interesante y caro. **No en v1.** Se reevalúa cuando el sistema esté medido | Diferida |
| Q18 | ¿Modelo de precios si se vende? | Fuera de alcance por ahora. [11 §5](11-costos.md) tiene la aritmética por si aparece la oportunidad | Diferida |

---

## 3. Lo que hay que vigilar aunque no sea un riesgo hoy

| Señal | Por qué importa | Dónde se ve |
|---|---|---|
| Tasa de bloqueo por fuente sube | El portal cambió su defensa | `/admin/fuentes` |
| Ratio clusters/avisos cae de golpe | El dedup se rompió | `/calidad` |
| MdAPE de confianza ALTA ≈ MdAPE de confianza BAJA | El score de confianza miente y es peor que no tenerlo | `/calidad`, calibración |
| Sesgo con signo creciendo | Los coeficientes se desactualizaron respecto del mercado | Backtest semanal |
| `critic_rejections` promedio subiendo | Un cambio de prompt degradó la redacción | `reports` |
| Cobertura bajando mes a mes | El corpus se está quedando viejo | `/admin/fuentes` |
| Costo por informe subiendo sin más volumen | Algo entró en loop o un modelo cambió de precio | `/informes`, alerta diaria |

---

## 4. Lo que sé que no sé

Dicho explícitamente, para no confundir supuestos con hechos:

- **Los coeficientes de ajuste de v1 son criterio, no medición.** Están declarados como
  tales en [05 §4.3](05-metodologia-de-valuacion.md), y el plan para hacerlos empíricos
  está en la Etapa 6.
- **El descuento oferta→cierre (5-15%) es un dato de mercado citado, no medido por
  nosotros.** No tenemos forma de verificarlo sin acceso a precios de escrituración.
- **No sé si el HTML de Portal A tiene datos estructurados.** Eso cambia el costo y la
  fiabilidad del nodo 4 de forma significativa.
- **No sé si el CRM va a responder** al pedido de acceso a la Red, ni en cuánto tiempo.
- **La estimación de 10 semanas es de un proyecto sin sorpresas**, y los proyectos
  tienen sorpresas. Las etapas 1 y 2 son las que tienen menos incertidumbre de esfuerzo
  (y son las que más importan); la 3 es la que más puede estirarse.
