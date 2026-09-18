# Prompts de la auditoría del código

Este archivo tiene **dos** prompts. El agente que lo abre por primera vez tiene
que ejecutar solo el primero.

**Mensaje para abrir la sesión** (esto es lo que se pega en el chat nuevo):

```
Vas a auditar el código de "Tasador", el proyecto que está en ..

Tus instrucciones completas están en el repo:
docs/auditoria/prompt-sesion-1-analisis.md

Leé ese archivo entero y seguí la parte "Sesión 1: auditoría completa del
código (solo análisis)". Ignorá por ahora la sección "Sesión 2": esa es la
próxima, y solo arranca cuando yo apruebe lo que produzcas ahora.

Ahí adentro está todo: qué leer y en qué orden, el método de auditoría, las
12 secciones a revisar, qué NO tenés que tocar, el formato exacto de cada
hallazgo y los documentos que tenés que entregar.

Empezá leyendo. Cuando termines de leer, decime tu plan de auditoría antes de
arrancar.
```

---

# Prompt — Sesión 1: auditoría completa del código (solo análisis)

> **Esta sesión NO escribe código de producción**: devuelve documentos. La
> implementación es la sesión 2.

---

```
Sos el auditor de "Tasador": un motor de valuación comparativa de inmuebles con
IA para inmobiliarias argentinas. El repo está en .. El stack corre
en Docker y está arriba (api :8000, web :3300, postgres :5433, redis :6380,
litellm :4000, worker).

TU TAREA EN ESTA SESIÓN: auditar el código entero, sección por sección, y
producir DOCUMENTOS DE ANÁLISIS. No implementás nada todavía. El resultado que
se evalúa es la calidad de los documentos: si están bien, la próxima sesión
ejecuta lo que digan sin volver a pensar.

═══════════════════════════════════════════════════════════════════════
1. LEÉ ESTO PRIMERO, EN ESTE ORDEN
═══════════════════════════════════════════════════════════════════════

  1. ESTADO.md — qué hay, qué falta, cómo levantarlo. Empezá acá.
  2. docs/informes/2026-08-14-que-datos-tenemos.md — la auditoría de datos de
     anoche. Es el mejor ejemplo del ESTÁNDAR que se espera de vos: hipótesis,
     medición, hipótesis refutada, medición, conclusión con números.
  3. docs/informes/2026-08-14-auditoria.md — los 12 defectos que había en lo
     declarado terminado.
  4. docs/informes/2026-08-13-etapa-3-grafo-y-extraccion.md — los 20 bugs
     anteriores y lo medido. Es el doc que más contexto real da.
  5. docs/17-arquitectura-viva.md — cómo quedó de verdad vs. el diseño.
  6. docs/01-arquitectura.md — los 9 ADRs. ADR-002 y ADR-003 no se negocian.

Después: config/agents.yaml y config/litellm.yaml (la topología y los modelos
salen de ahí, no del código).

═══════════════════════════════════════════════════════════════════════
2. CÓMO SE AUDITA ACÁ (esto es lo que más importa)
═══════════════════════════════════════════════════════════════════════

Este proyecto lleva ~35 bugs encontrados. **Ninguno apareció leyendo código.**
Todos aparecieron corriendo algo y mirando el resultado. Las reglas que
salieron de eso, y que son tu método:

  R1. NO INFERIR: PROBAR. Cada afirmación de tu informe tiene que tener al lado
      el comando que la produjo y su salida pegada. "Esto parece estar mal" no
      es un hallazgo; "corrí X, devolvió Y, esperaba Z" sí lo es.

  R2. VERIFICÁ QUÉ IMPLEMENTACIÓN CORRE DE VERDAD. Anoche se arregló un mapeo
      de datos con 5 tests nuevos en verde y el corpus no cambió un solo campo:
      había TRES copias de la misma función y se tocó la que usa un camino
      apagado. Antes de dar por bueno un test, seguí la cadena de llamadas
      hasta el punto de entrada real (script, endpoint, nodo del grafo).
      **Buscá activamente lógica duplicada**: es el patrón que más daño hizo.

  R3. UN GATE QUE PUEDE PASAR SIN MEDIR ES PEOR QUE NO TENER GATE. Un gate
      ausente se nota; uno roto ocupa el lugar del que hacía falta. Revisá cada
      test y cada job de CI preguntando: ¿este control puede pasar en verde sin
      haber verificado nada? (Ejemplos ya vividos: `pytest tests/architecture`
      sobre un directorio vacío; el job de backtest contra una base sin corpus.)

  R4. LOS NOMBRES ENTRE CAPAS SON UN CONTRATO. Un renombre de una palabra apagó
      un coeficiente entero: la ingesta guardaba `raw.parking` y el nodo 2 leía
      `raw.parking_spaces`. Nadie falló, nada se rompió, el dato simplemente no
      llegaba. Buscá desajustes de nombre entre lo que se escribe y lo que se
      lee (base ↔ estado del grafo ↔ API ↔ front).

  R5. LO QUE CORRE EN PRODUCCIÓN SE VERIFICA EN EL CONTENEDOR DE PRODUCCIÓN.
      Cinco arreglos vivieron solo en docker-compose.dev.yml, que es el único
      que no se despliega.

  R6. LA VARIANZA ES LA VARA. Los evals de componente tienen 17,6 pp de
      amplitud entre corridas idénticas. Ninguna conclusión sobre calidad de
      un prompt con UNA corrida: siempre 3, siempre la mediana.

  R7. CONTÁ FILAS DESPUÉS DE ACTUAR. La forma más rápida de descubrir que algo
      no hizo nada es medir el efecto en la base, no leer el log.

  R8. DECÍ LO QUE NO PUDISTE VERIFICAR. Un "no lo probé" explícito vale más que
      una afirmación sin respaldo. Si algo requiere datos o tiempo que no
      tenés, escribilo como tal.

═══════════════════════════════════════════════════════════════════════
3. LAS SECCIONES (una por una, en este orden)
═══════════════════════════════════════════════════════════════════════

El orden va de lo que más rompe hacia lo que menos. Para cada sección: leer,
ejecutar, medir, y escribir el documento antes de pasar a la siguiente.

  S1. NÚCLEO DE VALUACIÓN — src/tasador/valuation/ (engine 301 líneas, models)
      + config/adjustments.yaml. Es donde vive el número que le damos al
      cliente. ADR-002: el precio NUNCA sale de un LLM. Verificá que los tests
      que lo imponen no se puedan saltear. Revisá la matemática contra
      docs/05-metodologia-de-valuacion.md: superficie ponderada, ajustes,
      estadística robusta, bandas de confianza, el tope de ajuste acumulado.

  S2. EL GRAFO Y LOS 11 NODOS — src/tasador/agents/ (~3.000 líneas).
      Nodo por nodo: contrato de entrada/salida, qué pasa cuando degrada, qué
      queda en report_events. Los más grandes y menos mirados: market.py (555),
      dedup.py (536), extract.py (505), critic.py (325), curate.py (317).
      Preguntas: ¿un nodo que falla deja el informe en un estado coherente?
      ¿la traza permite reconstruir qué pasó? ¿hay costo que no se contabiliza?

  S3. INGESTA Y FUENTES — src/tasador/ingest/, src/tasador/capture/,
      src/tasador/sources/. **Acá ya se encontró duplicación triple**: se
      consolidó `_raw`/`_content_hash` en ingest/core.py, pero quedan dos
      `upsert` (ingest/core.py::_upsert y portal_a_ingest.py::upsert_card).
      Buscá el resto. Verificá idempotencia con datos reales.

  S4. API v1 — src/tasador/v1/ (reports 798, admin 432, auth 327, comparables
      262, calidad 161, inventory, health). Contrato vs docs/06-api-contrato.md.
      Aislamiento entre tenants (hay un gate estático en tests/architecture).
      Errores RFC 7807. Paginación por keyset. Qué pasa con inputs hostiles.

  S5. MODELO DE DATOS Y MIGRACIONES — src/tasador/db/models.py (1.077 líneas,
      20 tablas, 28 FKs) + migrations/. ¿El esquema del código y el de la base
      coinciden? ¿Las migraciones corren desde cero? ¿Hay CHECK que la
      aplicación viola o que impiden un estado legítimo? ¿Índices para las
      consultas que de verdad se hacen (mirá las lentas con EXPLAIN)?

  S6. FRONTEND — web/src/ (~2.900 líneas, 10 rutas). **No tiene NINGÚN control
      automático en CI**: no corre tsc, ni eslint, ni Playwright. Verificá
      manejo de errores de API, estados de carga, accesibilidad (doc 07 §12
      pide contraste AA, foco visible, labels reales), y que ninguna pantalla
      rompa el layout en celular. Los 61 tests de Playwright existen pero
      corren a mano.

  S7. SEGURIDAD — src/tasador/security.py, v1/auth.py, doc 10. Sesiones,
      API keys, rate limit, prompt injection (el texto de terceros va envuelto:
      verificá que TODOS los caminos lo hagan), PII, secretos en logs, el
      token de share. Qué pasa si SECRET_KEY cambia. CORS. Headers.

  S8. EVALUACIÓN — src/tasador/eval/ + scripts/eval_*.py + tests/golden/.
      ¿Los evals miden el MISMO camino que producción? (ya pasó que no).
      ¿El gate puede pasar sin medir? ¿La serie histórica es comparable entre
      filas? Mirá docs/09-evaluacion-y-backtest.md.

  S9. INFRA Y CI — docker-compose*.yml, docker/, .github/workflows/ci.yml,
      caddy/, Makefile. Qué se despliega y qué no. El job de backtest está
      declarado y DESACTIVADO a propósito (falta corpus en el runner): decidí
      si eso es aceptable o hay que resolverlo. Backups: nunca se probó una
      restauración. Healthchecks. Secretos.

  S10. SCRIPTS OPERATIVOS — scripts/ (19 scripts, 3.015 líneas). Son la
      interfaz real de operación. ¿Cuáles están rotos o desactualizados?
      ¿Cuáles duplican lógica de src/? ¿Cuál falta?

  S11. TESTS — tests/ (5.176 líneas, 366 tests). No mires el número: mirá QUÉ
      cubren. ¿Qué parte del código no tiene ninguna prueba que la ejercite?
      ¿Cuántos tests pasan sin afirmar nada relevante? ¿Cuántos prueban una
      implementación que no corre (R2)? Considerá cobertura real con
      `pytest --cov` si podés instalarlo sin romper nada.

  S12. DOCUMENTACIÓN — docs/ (17 docs + guías + informes). Qué documento
      MIENTE respecto del código de hoy. Un doc desactualizado es peor que
      ausente: se lee como verdad. Listá cada divergencia concreta.

═══════════════════════════════════════════════════════════════════════
4. LO QUE **NO** TENÉS QUE HACER
═══════════════════════════════════════════════════════════════════════

  · NO cambies código de producción. Si encontrás algo urgente, escribilo con
    el diff propuesto en el documento; no lo apliques. (Excepción única: si
    encontrás una vulnerabilidad que expone datos, decilo INMEDIATAMENTE en la
    respuesta, no solo en un doc.)
  · NO toques los datos ni el corpus. Nada de scrapers, nada de ingesta, nada
    de re-extracción. **El enriquecimiento de los datos de Palermo lo está
    haciendo el humano por su lado, en paralelo. La calidad y completitud del
    corpus NO es tu problema en esta auditoría**: si un hallazgo depende de que
    falten datos, anotalo como "bloqueado por datos" y seguí.
  · NO gastes en LLM más allá de lo necesario para verificar algo puntual.
    deepseek-v4-flash es gratis para desarrollo; el resto se usa con criterio.
    Si una verificación cuesta más de USD 1, pedí autorización primero.
  · NO reescribas los documentos existentes. Los tuyos son nuevos.

═══════════════════════════════════════════════════════════════════════
5. QUÉ TENÉS QUE ENTREGAR
═══════════════════════════════════════════════════════════════════════

En docs/auditoria/:

  00-resumen.md          El documento que se lee primero. Máximo 2 páginas:
                         estado general, los 10 hallazgos que más importan
                         ordenados por impacto, y el veredicto — ¿qué partes
                         del sistema son confiables hoy y cuáles no?

  01-valuacion.md ... 12-documentacion.md   Uno por sección (S1..S12).

  99-backlog.md          El plan de la sesión 2. Es el entregable operativo.

**Formato de cada hallazgo** (respetalo, la sesión 2 lo va a ejecutar):

    ### H-07 · Título corto y concreto
    **Sección:** S3 · **Severidad:** alta | media | baja
    **Qué está mal:** una o dos frases.
    **Cómo lo verifiqué:**
    ```
    $ comando exacto
    salida pegada
    ```
    **Por qué importa:** el daño concreto, con números si los hay.
    **Qué hay que hacer:** los pasos, con los archivos y funciones nombrados.
    **Cómo se verifica que quedó bien:** el comando y el resultado esperado.
    **Riesgo de tocarlo:** qué se puede romper y qué test lo protege.

**Severidad**, con este criterio y no otro:
  alta   = produce un número equivocado en un informe entregado al cliente,
           expone datos de un tenant a otro, o hace perder trabajo pagado.
  media  = degrada calidad, cuesta plata, o esconde un problema.
  baja   = deuda, prolijidad, o riesgo futuro.

El **99-backlog.md** ordena TODOS los hallazgos en el orden en que hay que
ejecutarlos —dependencias primero— y para cada uno estima esfuerzo y dice si
puede romper algo. Es lo que la sesión 2 va a seguir de arriba a abajo.

═══════════════════════════════════════════════════════════════════════
6. CONTEXTO OPERATIVO QUE VAS A NECESITAR
═══════════════════════════════════════════════════════════════════════

  · Desde Windows, la base es 127.0.0.1:5433 (el DATABASE_URL del .env apunta
    a la red de Docker y no sirve afuera). En PowerShell:
      $env:DATABASE_URL="postgresql+psycopg://tasador:<POSTGRES_PASSWORD>@127.0.0.1:5433/tasador"
      $env:LITELLM_BASE_URL="http://127.0.0.1:4000"
    (esa clave es la de desarrollo local; en producción sale del .env)
  · Verde hoy: ruff, ruff format, mypy --strict (66 archivos),
    pytest -m "not live" (366 passed, 2 skipped), playwright (61 passed).
    Si algo de eso se pone en rojo por algo que hiciste, es tuyo.
  · La API tiene --reload y ve los cambios; **el worker NO**: después de tocar
    un nodo hay que `docker restart tasador-worker-1`. En Windows, Next a veces
    no ve directorios nuevos: `docker restart tasador-web-1`.
  · El PDF (WeasyPrint) solo corre dentro del contenedor: necesita GTK.
  · El corpus hoy: ~8.500 avisos vigentes → ~5.000 propiedades únicas, Palermo
    y Belgrano, dos portales. Está creciendo por fuera de esta sesión.
  · Hay 3 informes SUCCEEDED en la base para probar contra datos reales.

Empezá por leer, después decime tu plan de auditoría —qué vas a ejecutar en
cada sección— y recién ahí arrancá. No pidas permiso para leer ni para medir;
sí para cualquier cosa que escriba en la base o gaste plata.
```

---

# Prompt — Sesión 2: implementación autónoma

> **Solo después de aprobar los documentos de la sesión 1.** Ajustá la primera
> línea con lo que quieras excluir del backlog.

```
Vengo de una auditoría completa del código de "Tasador" (.). Los
documentos están en docs/auditoria/. Leélos todos, empezando por 00-resumen.md
y terminando por 99-backlog.md.

TU TAREA: ejecutar el backlog completo, de arriba a abajo, en una sesión
autónoma. No me devuelvas el control hasta que esté todo hecho y verificado, o
hasta que te topes con algo que solo yo puedo decidir.

CÓMO TRABAJAR:

  · Seguí el orden del backlog: está ordenado por dependencias.
  · Cada hallazgo se cierra con su "cómo se verifica que quedó bien" EJECUTADO
    y la salida pegada. Un ítem sin verificación ejecutada no está cerrado.
  · Antes de dar por bueno un arreglo, confirmá que tocaste la implementación
    que CORRE (regla R2 de la auditoría). Si el arreglo no cambia nada
    observable, no está hecho.
  · Tests nuevos para todo lo que arregles. Un arreglo sin test es una
    hipótesis.
  · Después de cada bloque: ruff + ruff format + mypy --strict + pytest.
    Todo verde antes de seguir. Si algo se pone en rojo, se arregla antes de
    avanzar, no después.
  · Las pantallas se verifican EN EL NAVEGADOR contra el stack real, no solo
    con tests.
  · Actualizá ESTADO.md y las guías a medida que avanzás, no al final.
  · Si un ítem resulta más grande de lo estimado o se contradice con lo que ves
    en el código, anotalo y seguí con el siguiente; no te bloquees.

RESTRICCIONES QUE NO SE NEGOCIAN:

  · ADR-002: el precio NUNCA sale de un LLM. Hay tests que lo imponen.
  · ADR-003: todo pasa por LiteLLM; el código pide TAREAS, nunca proveedores.
    Si es una decisión, va a config/agents.yaml — nada nuevo hardcodeado.
  · Los tests no tocan internet ni gastan.
  · Nada se declara terminado sin verificación real ejecutada.
  · No toques el corpus ni corras scrapers: los datos los maneja el humano.
  · Un gate que puede pasar sin medir es peor que no tener gate.

PEDIME PERMISO SOLO PARA: cambios destructivos sobre la base, gastos de LLM
mayores a USD 5, o decisiones de producto que no estén ya resueltas en los
documentos.

Cuando termines, escribí docs/informes/<fecha>-cierre-auditoria.md con: qué se
hizo, qué se midió antes y después, qué quedó afuera y por qué.
```
