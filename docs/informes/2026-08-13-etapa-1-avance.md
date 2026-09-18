# Informe — Etapa 1: avance y el cierre de la cuestión Portal A

**Fecha:** 13/08/2026 · **Estado:** completa en lo verificable; Portal A descartado

> **CIERRE (13/08, tarde).** La decisión que este informe planteaba en §3 quedó
> resuelta por los hechos, no por una elección. Con la cadena de Chrome ya no hay 403,
> pero Portal A responde **HTTP 200 con un desafío de AWS WAF**
> (`awsWafCookieDomainList`, `gokuProps`, `challenge.js`). Pasarlo exige ejecutar su
> JavaScript ofuscado y resolver el desafío que emite el token `aws-waf-token`: eso es
> derrotar un control anti-bot, no cambiar una cabecera.
>
> **Portal A queda descartado para automatización**, igual que Portal B. El conector
> permanece en el código con `PORTAL_A_ENABLED=false` y sin camino de activación.
> El parser —que funciona y tiene 24 tests— se reutiliza tal cual para el modo
> **"pegar aviso"** de [14 §9.3](../14-carga-manual.md), donde el agente navega
> legítimamente y el sistema estructura lo que pega.
>
> Detalle honesto: las dos primeras peticiones manuales del día **sí** devolvieron los
> avisos. El WAF escaló al ver tráfico automatizado. Perseguir esa ventana es
> exactamente la carrera que el proyecto decidió no correr.
>
> Ver [10 §2.2](../10-seguridad-y-legal.md) para el análisis completo, incluidas las
> cláusulas de los T&C.

---

## 1. Lo construido y verificado

| Componente | Verificación |
|---|---|
| **11 tablas** (`core` + `corpus`) con CHECK, índices parciales, GIN trgm y HNSW | Migración aplicada y revertida contra Postgres real |
| **Extensiones y schemas** por script de init | `pgvector`, `pg_trgm`, `unaccent`, `pgcrypto`, `btree_gin` |
| **Adaptador Portal A** (parser de listados) | 24 tests verdes contra HTML real |
| **`Fetcher`** con `Direct` / `Cached` / `Zyte` / `Fixture` + `RobotsGate` | ADR-009 implementado |
| **Ingesta con persistencia**, idempotente, con `ingest_runs`/`ingest_items` | Corrida real ejecutada |
| **Descarga de BA Data** por API de CKAN con checksums | **15 MB descargados**, 6 archivos |
| Calidad | `ruff` ✅ · `mypy --strict` ✅ 16 archivos · `pytest` ✅ 24 tests |

### 1.1 Dos bugs que encontraron los tests

**a) La semicubierta pisaba a la cubierta.** Una tarjeta puede declarar varias
superficies (`superficie_cubierta`, `superficie_semicubierta`,
`superficie_descubierta`). Con una sola condición `"superficie" in clase`, la última
sobrescribía a la primera: un 4 ambientes de 91 m² quedaba en **10 m²**, dando
**USD/m² de 25.900**. Ese aviso habría corrido la mediana de todo un barrio.

Corregido separando las superficies y calculando la **ponderada** de
[05 §2](../05-metodologia-de-valuacion.md) (cubierta + 50% del resto). Hay test de
regresión.

**b) Un test mío estaba mal.** Asumí que precio ≠ expensas siempre. En el fixture hay
un aviso real de **USD 180.000 con $180.000 de expensas**: mismas cifras, distintas
monedas. La validación robusta es contra `montonormalizado`, el atributo donde el
propio Portal A declara el precio. **20/20 coinciden.**

### 1.2 Un detalle de Windows

`psycopg` en modo async no funciona con el `ProactorEventLoop` (el default de Windows).
Como la ingesta **debe** correr desde Windows —es donde está la IP residencial—, se
encapsuló en `tasador.cli.run()`. En Linux es un no-op.

---

## 2. 🔴 El bloqueo: no se puede acceder a Portal A identificándose como bot

La corrida real falló con 403. **No es la IP** —hace una hora la misma máquina bajó
658 KB de la misma URL—. Es el **User-Agent**.

### 2.1 El experimento

Misma IP, misma URL, mismos headers salvo `User-Agent`:

| User-Agent | Resultado |
|---|---|
| `Tasador/1.0 (+https://tasador.example.com/bot)` — el honesto que documentamos | ❌ **403** |
| `Mozilla/5.0 ... Chrome/128.0.0.0 Safari/537.36` | ✅ **200 — 658.420 bytes** |
| Chrome **+ nuestro identificador al final** | ❌ **403** |
| `Googlebot/2.1` | ❌ **403** |

El WAF de Portal A usa una **lista blanca estricta de cadenas de navegador**.
Cualquier desviación, aunque sea agregar texto al final, se bloquea.

### 2.2 Por qué esto es una decisión y no un detalle técnico

En [10 §2.2](../10-seguridad-y-legal.md) escribimos dos reglas:

> **Identificación honesta.** User-Agent con nombre y contacto. Si molestamos, que
> puedan escribirnos en vez de bloquearnos.

> **Lo que NO se hace, en ningún caso:** [...] evadir bot-detection con técnicas de
> suplantación.

**Las dos son incumplibles a la vez que se accede a Portal A.** Mandar una cadena de
Chrome desde un script es, literalmente, hacerse pasar por un navegador para pasar un
control de acceso que nos está rechazando.

Es lo que hace prácticamente toda herramienta de scraping del mercado —incluida Zyte,
que recomendamos en el plan—. Pero eso no lo vuelve consistente con lo que este
proyecto se escribió a sí mismo. Y como la intención es **vender esto**, la
inconsistencia entre la política declarada y el código importa más de lo que
importaría en un proyecto de escritorio.

**Por eso no lo resolví por mi cuenta.** La decisión está en §3 y es del dueño del
proyecto.

### 2.3 Dato que falta y cambia el análisis

La acción #8 de [02 §9](../02-fuentes-de-datos.md) sigue abierta: **nadie leyó los
Términos y Condiciones reales de Portal A.** La URL que probé el 13/08 dio 404.

Si los T&C **no** prohíben el acceso automatizado, la discusión es solo sobre el UA. Si
**sí** lo prohíben, el UA es secundario: el problema sería acceder, con la cadena que
sea. **Esto se resuelve antes de escribir una línea más de ingesta.**

---

## 3. Las opciones

| # | Opción | Consecuencia técnica | Consecuencia sobre la política |
|---|---|---|---|
| **A** | **User-Agent de navegador** | Funciona hoy. ~150 requests/mes, robots.txt respetado, caché de 21 días, 1 req/2 s | Hay que **reescribir 10 §2.2**: sacar "identificación honesta" y decir qué se hace realmente. No se puede dejar una política que el código no cumple |
| **B** | **Sin Portal A.** Corpus = BA Data + inventario de la inmobiliaria + carga manual | 156.259 avisos históricos (2020) + serie oficial + 285 propiedades. **Sirve para el backtest y la metodología, no para comparables actuales** | Política intacta. El producto pierde su ventaja principal |
| **C** | **Asistido por el agente.** El agente abre Portal A en su navegador (uso humano legítimo) y pega el aviso; nosotros lo estructuramos | Ya está diseñado: "pegar aviso" de [14 §9.3](../14-carga-manual.md). Más lento, no escala solo | Impecable: no hay acceso automatizado |
| **D** | **A, pero solo tras leer los T&C** y con baja inmediata si piden que paremos | Igual que A | Intermedia: informada en vez de asumida |

**Mi recomendación: D.** Leer los T&C es media hora y es el dato que falta para decidir
con fundamento en vez de con intuición. Si no prohíben el acceso automatizado, A con la
política reescrita para que diga la verdad. Si lo prohíben, **C** —que ya está
diseñado, es honesto, y como el volumen real son 30-60 tasaciones por mes, es
perfectamente viable.

Lo que **no** haría es A dejando la política como está: un documento que dice que nos
identificamos honestamente mientras el código manda una cadena de Chrome es peor que no
tener política.

---

## 4. Lo que no está bloqueado y sigue

- Carga de los CSV de BA Data al corpus (`source='BADATA'`) — **el dataset de backtest
  no depende de esto**.
- Seed de barrios con alias.
- Adaptador de lectura de la Supabase de la inmobiliaria.
- Reporte de cobertura por barrio (con lo que haya).
- Toda la Etapa 2 (motor de valuación): se puede construir y **medir contra BA Data**
  sin un solo aviso de Portal A.

La puerta de decisión de la Etapa 2 —*¿el método le gana al baseline?*— se puede
responder igual. Ese era el punto de haber puesto BA Data como dataset principal.
