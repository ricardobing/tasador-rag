# Mapa del proyecto — cómo funciona, qué se construyó, cómo explicarlo

Siete diagramas interactivos (abrí [`index.html`](index.html) para verlos con la guía; HTML autocontenido, generados con
[Archify](https://github.com/tt-a1i/archify) a partir de los JSON de esta carpeta) y
esta guía para recorrerlos. Cada HTML abre solo en un navegador: tiene búsqueda, foco
por componente, trazado de relaciones, vistas guiadas, tema claro/oscuro y exportación a
PNG/SVG. En el de arquitectura, cada componente enlaza al archivo del repo que lo
implementa (evidencia verificada contra el commit `d3b4a1a`).

| # | Diagrama | Tipo | Responde |
|---|---|---|---|
| 01 | [Sistema](01-sistema.html) · [fuente](01-sistema.architecture.json) | arquitectura | ¿Qué corre y quién habla con quién? |
| 02 | [Del sujeto al precio](02-pipeline-a-precio.html) · [fuente](02-pipeline-a-precio.workflow.json) | workflow | ¿Cómo llega una dirección a un rango de precio? (nodos 1–7) |
| 03 | [Del precio al PDF](03-pipeline-a-pdf.html) · [fuente](03-pipeline-a-pdf.workflow.json) | workflow | ¿Cómo se escribe y se verifica la prosa? (nodos 8–11) |
| 04 | [Pedir un informe](04-informe.html) · [fuente](04-informe.sequence.json) | secuencia | ¿Qué pasa entre el navegador, la API, la cola, el worker y los modelos? |
| 05 | [Preguntale al informe](05-pregunta.html) · [fuente](05-pregunta.sequence.json) | secuencia | ¿Cómo se responde con citas y cuándo se rechaza sin modelo? |
| 06 | [Del aviso al veredicto](06-rag-y-evaluacion.html) · [fuente](06-rag-y-evaluacion.dataflow.json) | flujo de datos | ¿Cómo se indexó el corpus, qué sistemas se compararon y con qué vara? |
| 07 | [La vida de un informe](07-vida-de-un-informe.html) · [fuente](07-vida-de-un-informe.lifecycle.json) | ciclo de vida | ¿En qué estados puede quedar un informe y por qué? |

Cada diagrama está además exportado a PNG en [`img/`](img/), en tema claro y oscuro:
son los que usa el README de la raíz. Se regeneran con la misma fuente que el HTML.

Para regenerarlos (Node 22+; la skill se instala con `npx skills add tt-a1i/archify -g`):

```bash
cd ~/.agents/skills/archify
node bin/archify.mjs validate architecture <repo>/docs/mapa/01-sistema.architecture.json --quality showcase --repo-root <repo> --json
node bin/archify.mjs deliver  architecture <repo>/docs/mapa/01-sistema.architecture.json <repo>/docs/mapa/01-sistema.html --quality showcase --repo-root <repo> --json
# los otros seis: mismo par validate/deliver con su tipo (workflow, sequence, dataflow, lifecycle) y sin --repo-root
```

Los siete pasan la validación `showcase` de Archify (9 chequeos de artefacto, 0 errores
de composición) y el chequeo automático de navegador (`visual-check`: contención en
cuatro tamaños de escritorio, claro y oscuro). La revisión perceptual se hizo con
capturas del visor.

---

## 1. Cómo funciona, en un párrafo

Una inmobiliaria carga una dirección, los ambientes y los metros. La API crea el
informe y lo encola; un worker corre un grafo de once nodos: resuelve el barrio,
recupera 60 avisos comparables del corpus (filtro SQL con relajación progresiva,
ordenados por puntaje léxico), los lee con un modelo que devuelve cada dato **con la
cita textual del aviso** (verificada por comparación de strings, sin modelo), deduplica
el mismo inmueble publicado varias veces, descarta con un juez que solo puede elegir
entre cinco motivos y tiene que citar, y **calcula el precio sin ningún modelo**:
mediana robusta de USD/m² con coeficientes explícitos, en aritmética `Decimal`. Después
un redactor escribe seis secciones, un crítico en dos fases las verifica —fase A extrae
toda cifra de la prosa y la busca en los datos; fase B es un modelo adversarial— y sale
un PDF con la tabla de comparables usados y descartados, cada uno con su motivo. El
propietario puede preguntarle al informe; la respuesta cita hechos del informe y se
rechaza sin llamar al modelo cuando no hay evidencia.

**La frase que ordena todo:** el LLM hace lenguaje y juicio; la matemática hace los
números. Un test de arquitectura impide que el nodo del precio tenga un modelo asignado.

## 2. Qué se construyó (y qué se midió)

### 2.1 El producto (agosto)

- **Pipeline** de 11 nodos en LangGraph con checkpoint en Postgres, cola con arq/Redis,
  API FastAPI versionada, front Next.js, multi-tenant, PDF con WeasyPrint. Diagramas 02, 03, 04, 05 y 07.
- **Cuatro controles** sobre cada lugar donde un modelo interviene: cita verificada en
  la extracción, motivos cerrados con cita en la curaduría, crítico en dos fases,
  `INSUFFICIENT_DATA` con desglose cuando no hay 5 comparables válidos.
- **Medido contra sí mismo:** backtest de 1.500 casos (MdAPE 15,0% contra 16,2% del
  baseline), calibración de la confianza (ALTA 13% · MEDIA 21% · BAJA 66% de error),
  precios inventados que pasaban el crítico 50,5% → 4,3%, costo por informe USD
  0,03–0,08 trazado por nodo. Y la fila más importante: el aporte de la extracción al
  MdAPE **no se distingue del ruido** entre semillas. Se publicó como salió.

### 2.2 El RAG (18/09/2026, una sesión)

Diagrama 06. La propuesta ([doc 18](../18-rag-propuesta.md)) escribió primero el
criterio de aceptación y después construyó:

- **La vara:** nDCG@25, recall@30, MRR, bpref y `descartados@30` con bootstrap
  apareado, sobre 113 consultas (53 informes pasados + 59 avisos leídos como sujeto,
  leave-one-out). Los juicios de relevancia los produce el propio pipeline por
  *pooling* incremental: la unión del top-30 de cada sistema pasa por los nodos 4–7 y
  el veredicto de la curaduría es el grado.
- **El índice:** 83.063 chunks por oraciones con encabezado estructurado, embeddings
  locales (MiniLM-L12, 384 d, 13 pasajes/s en CPU) en pgvector y `tsvector` en español
  con GIN.
- **Los sistemas:** A recencia (lo que había) · B denso truncado · C denso por
  oraciones · D léxico · E híbrido C+D con RRF · F E + reranker (bge-reranker-base) ·
  H híbrido B+D.
- **El resultado:** D gana. nDCG@25 0,821 contra 0,747 de A; el híbrido 0,781 y el
  reranker 0,790, ambos por debajo de D con Δ apareada fuera del intervalo; D
  descarta menos en la curaduría (22% del top-30 contra 29%) y baja el MdAPE del
  backtest 2 pp (600 casos × 3 semillas). Los híbridos ganan solo bpref.
  **Se encendió el léxico** (`semantic.modo: lexico`); el denso y el reranker quedan
  construidos, medidos y apagados. [Informe completo](../informes/2026-09-18-rag-resultados.md).
- **«Preguntale al informe»:** RAG chico sobre los hechos del informe y la
  metodología, compuerta doble sin modelo (coseno ≥ 0,40 o léxico ≥ 0,34), citas
  obligatorias verificadas con la misma función que usa el crítico. Medido: rechazo
  0,96 · citas 0,89 · cifras 0,94.

### 2.3 Lo que la medición corrigió (la parte que vale contar)

1. `bge-m3` no lo sirve la librería; `e5-large` tarda 6–12 h en CPU → MiniLM para
   poder comparar en una tarde.
2. El tokenizador con el que se embebe trunca: contar tokens con él dice «ninguno se
   pasa». Con la copia sin truncar: 28,1% de los avisos supera 512 tokens.
3. Los juicios de informes de agosto eran relativos a un pool que ya no existe
   (`juzgados@25` = 0,27) → pooling por el pipeline.
4. El chunking por oraciones **no** le gana a truncar con un modelo de 128 tokens: lo
   contrario de lo que decía la propuesta.
5. El reranker con el tope de latencia de producción (15 s) degradaba a E en todas las
   consultas: la fila F medía la fila E. En el eval, 300 s y error si degrada.
6. El nodo 6 degrada sin juez (sobreviven todos): un gateway caído dejó 57 juicios
   con todo en grado 2. Se rehicieron, y ahora el eval corta sin guardar.
7. El crítico en un modelo abierto discutía el nivel de confianza que calculó el motor
   (prompt v2) y se cortaba en 4.096 tokens de salida (8.192 y brevedad).

### 2.4 Publicación y demo

Repo público saneado desde el privado (sin cliente, sin fuente ni datos del scraping;
rótulos `PORTAL_A/B`; corpus sintético con verdad por construcción), licencia PolyForm
Noncommercial, CI con lint, tipos, 492 tests, gitleaks, migraciones desde cero y tres
imágenes sin HIGH/CRITICAL en trivy. Demo verificada desde un clon limpio y una base
vacía: seed → ingesta → usuario → informe `SUCCEEDED` → pregunta con citas.

## 3. Cómo entenderlo y explicarlo

**En 30 segundos.** «Un motor de tasaciones para inmobiliarias. El precio lo calcula
matemática reproducible sobre comparables reales; los modelos leen avisos, juzgan y
escriben, y cada cosa que dicen viene con la cita que lo prueba. Construimos un RAG
encima, lo medimos con una vara escrita antes de medir, y encendimos la parte que ganó:
la búsqueda léxica. Lo que no ganó quedó medido y apagado.»

**En una entrevista técnica**, el orden que funciona:

1. **El principio** (diagrama 01, tarjeta «El principio»): dónde puede y dónde no
   puede intervenir un LLM, y cómo se hace cumplir con tests de arquitectura.
2. **Un informe de punta a punta** (02 → 03): seguir el camino feliz y después las
   dos salidas —`INSUFFICIENT_DATA` y «sin narrativa»—. La pregunta que siempre llega
   es «¿y si el modelo inventa un precio?»: fase A del crítico, 50,5% → 4,3%.
3. **La vara antes que el sistema** (06): por qué se escribió el criterio de
   aceptación antes de medir, qué es el pooling y por qué los juicios caducan con el
   recuperador.
4. **El resultado incómodo**: el sistema más simple ganó, y el más vistoso quedó
   apagado con la tabla que lo justifica. Es la parte que más credibilidad da.
5. **Lo operativo** (04, 05, 07): cola, checkpoint, eventos con costo, estados, y la demo
   desde una base vacía.

**Preguntas frecuentes, con la respuesta corta.**

- *¿Por qué no una base vectorial?* Postgres + pgvector con búsqueda exacta sobre el
  pool filtrado: recall 100%, milisegundos, y un sistema menos (ADR-010/012).
- *¿Por qué LangGraph y no una crew?* Hacen falta ciclos (crítico → redactor), salidas
  tempranas y checkpoint durable (ADR-001).
- *¿Por qué el léxico le ganó al denso?* Consultas cortas y estructuradas, corpus de
  dos barrios con vocabulario cerrado, y un modelo de embeddings de 128 tokens en CPU.
  Con e5-large o un corpus con más texto libre la tabla puede cambiar; está previsto.
- *¿Qué garantiza que la prosa no mienta?* Toda cifra se busca en los datos sin
  modelo; una sola no trazable rechaza; tras dos rechazos, sin prosa.
- *¿Cuánto cuesta?* USD 0,02–0,08 por informe, trazado por llamada; embeddings y
  reranker a costo cero en CPU.

## 4. Dónde está cada cosa

| Quiero ver… | Archivo |
|---|---|
| El grafo y sus nodos | `src/tasador/agents/graph.py`, `src/tasador/agents/nodes/*.py`, `config/agents.yaml` |
| El precio sin LLM | `src/tasador/valuation/engine.py`, `config/adjustments.yaml` |
| La cita verificada | `src/tasador/agents/nodes/extract.py` |
| El crítico en dos fases | `src/tasador/agents/nodes/critic.py`, `prompts/critic/v2.jinja` |
| El recuperador y sus modos | `src/tasador/agents/nodes/retrieve.py`, `src/tasador/rag/retriever.py` |
| Chunking, embeddings, reranker | `src/tasador/rag/{chunking,embedder,indexer,rerank}.py` |
| La vara | `src/tasador/eval/{retrieval,juicios,backtest}.py`, `scripts/eval_retrieval.py`, `scripts/run_backtest.py` |
| «Preguntale al informe» | `src/tasador/rag/qa.py`, `src/tasador/v1/ask.py`, `web/src/app/informes/[id]/preguntar.tsx` |
| Los números | `docs/informes/2026-09-18-rag-resultados.md`, `docs/informes/2026-08-13-etapa-2-backtest.md` |
| Las decisiones | `docs/01-arquitectura.md` §4 (ADR-001 a 013) |
| Cómo se levanta | `README.md`, `docs/guias/puesta-en-marcha.md` |
| Dónde quedó todo | `docs/ESTADO-2026-09-18.md` |
