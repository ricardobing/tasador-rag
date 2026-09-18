# Tasador — tasaciones inmobiliarias con IA, sin que la IA ponga el precio

Motor de valuación comparativa para inmobiliarias argentinas. Recibe una propiedad,
recupera avisos comparables de un corpus real, los lee, los depura y produce un
**Informe de Mercado Comparativo** en PDF: un rango de precio justificado, la tabla de
comparables que lo sustenta —los usados y los descartados, cada uno con su motivo— y una
narrativa en la que **cada cifra fue verificada contra los datos antes de salir**.

```
propiedad ──▶ normalizar ──▶ recuperar comparables ──▶ extraer (LLM, con cita verificada)
          ──▶ deduplicar ──▶ curar (reglas + juez con cita) ──▶ ¿≥ 5? ──no──▶ INSUFFICIENT_DATA
          ──▶ VALUAR (determinístico, sin LLM) ──▶ contexto de mercado ──▶ redactar (LLM)
          ──▶ CRÍTICO (fase A sin LLM: toda cifra existe en los datos · fase B adversarial)
          ──▶ PDF + ficha web + link para el propietario
```

Once nodos en un grafo de LangGraph con checkpoint en Postgres, una cola de trabajos,
API versionada, front en Next.js, multi-tenant desde la primera línea. Desarrollado
para una inmobiliaria de Buenos Aires como caso de validación; **terminado y verificado
de punta a punta sobre datos reales de mercado, no desplegado.**

---

## El principio que ordena todo

> **El LLM hace lenguaje y juicio. La matemática hace los números.**

El precio sale de una mediana robusta de USD/m² con coeficientes de ajuste explícitos
(`config/adjustments.yaml`), en aritmética `Decimal`: el mismo conjunto de comparables da
siempre el mismo número. Un test de arquitectura impide que ese nodo tenga un modelo
asignado, y otro que el mínimo de 5 comparables se ablande por variable de entorno.
Alrededor, cuatro controles sobre cada lugar donde un modelo sí interviene:

| Control | Qué garantiza |
|---|---|
| **Cita textual verificada sin LLM** ([`extract.py`](src/tasador/agents/nodes/extract.py)) | Cada dato que mueve el precio —estado, orientación, piso, ascensor, antigüedad— viene con el fragmento del aviso que lo justifica, y una comparación de strings comprueba que ese fragmento exista. Sin cita verificada, el dato no ajusta el precio |
| **Descartes con motivo cerrado y cita** ([`curate.py`](src/tasador/agents/nodes/curate.py)) | El juez solo puede elegir entre cinco motivos y tiene que citar el texto que lo prueba |
| **Crítico en dos fases** ([`critic.py`](src/tasador/agents/nodes/critic.py)) | Fase A, sin LLM: extrae toda cifra de la narrativa y la busca en los datos; una sola no trazable rechaza el texto. Fase B, adversarial. Tras dos rechazos el informe sale con la tabla y sin prosa — nunca con prosa no verificada |
| **Saber decir "no sé"** ([`engine.py`](src/tasador/valuation/engine.py)) | Menos de 5 comparables válidos → `INSUFFICIENT_DATA` con el desglose de por qué se cayó cada candidato. Confianza calibrada: cuando dice BAJA, se equivoca cinco veces más |

El crítico se midió contra sí mismo inyectando precios inventados: dejaba pasar el
**50,5%**; tras tres cambios —uno de ellos bajar la tolerancia de 1% a 0,1% con la tabla
de sensibilidad escrita al lado del parámetro— pasó a **4,3%**. Está contado en
[el cierre de la auditoría](docs/informes/2026-08-15-cierre-auditoria.md).

## Los números, con su contexto

Medidos sobre un corpus privado de ~8.500 avisos vigentes de dos barrios de Buenos
Aires y 85.000 históricos de datos abiertos. El repo incluye un corpus **sintético**
para reproducir el pipeline, no las cifras.

| Qué | Resultado | Cómo |
|---|---|---|
| Backtest de punta a punta | **MdAPE 15,0%** contra **16,2%** del baseline (mediana de USD/m² del barrio) · PPE20 60,6% · sesgo 0,0% | 1.500 casos de datos abiertos, *leave-one-out*, criterio escrito antes de medir ([informe](docs/informes/2026-08-13-etapa-2-backtest.md)) |
| Calibración de la confianza | ALTA 13,4% · MEDIA 20,6% · BAJA 66,2% de error — monótona | ídem |
| Estabilidad del backtest | 14,5% y 15,0% con dos semillas: por debajo de 0,6 pp no hay mejora que reportar | ídem |
| Precios inventados que pasan la verificación | 50,5% → **4,3%** | sondas sobre informes reales |
| Costo por informe | **USD 0,03 – 0,08**, trazado por nodo y por llamada, con el modelo que atendió de verdad | `core.report_events` |
| Tiempo por informe | 12–31 s con el corpus caliente; ~5 min el primer informe de un barrio (extracción de 60 avisos) | ídem |
| Extracción (golden set, 24 avisos) | mediana 76% en 5 corridas, **17,6 pp de amplitud entre corridas idénticas** | por eso ninguna decisión de prompt se toma con una corrida |
| Aporte de la extracción al MdAPE | **no distinguible del ruido** (−0,33 pp contra 1,83 pp entre semillas) | 3 semillas × 300 casos; con una sola semilla parecía +0,6 pp |

La última fila es la más importante para leer las demás: este proyecto midió su propia
hipótesis central y la publicó como salió.

## Levantarlo con el corpus demo

Necesita Docker y una clave de un proveedor de modelos compatible con OpenAI (el gateway
LiteLLM la enruta; ver `config/litellm.yaml`).

```bash
cp .env.example .env            # completar OPENROUTER_API_KEY y las contraseñas
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
uv run python scripts/seed.py   # tenant demo + centroides de barrios
PORTALES=demo-a=PORTAL_A,demo-b=PORTAL_B uv run python scripts/ingest_csv.py --carpeta data/demo
uv run python scripts/crear_usuario.py --email vos@inmo-demo.com.ar --rol owner
```

Front en `http://127.0.0.1:3000`, API en `:8000`. Un informe desde la CLI, dentro del
contenedor (el PDF necesita las librerías nativas de WeasyPrint):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api \
  python scripts/run_report.py --direccion "Gorriti 5000" --amb 3 --m2 70
```

El corpus demo lo genera `scripts/generar_corpus_demo.py`: 600 avisos sintéticos con las
distribuciones del corpus real y descripciones redactadas por un modelo a partir de
hechos sorteados, así que el golden set (`tests/golden/demo-extraccion.yaml`) tiene la
verdad **por construcción**, no por anotación. Incluye a propósito pozos, permutas y el
mismo inmueble en dos portales con la altura aproximada.

Guía completa: [docs/guias/puesta-en-marcha.md](docs/guias/puesta-en-marcha.md) ·
[manual de uso](docs/guias/manual.md).

## Decisiones que vale la pena leer

- [**ADRs**](docs/01-arquitectura.md#4-decisiones-de-arquitectura-adr): LangGraph y no
  una crew (hacen falta ciclos, salidas tempranas y checkpoint durable); Postgres +
  pgvector y no una base vectorial; el precio nunca sale de un LLM; un gateway de modelos
  y cero SDKs de proveedor en el código; multi-tenant desde el día uno.
- [**La auditoría**](docs/auditoria/00-resumen.md): 58 hallazgos contra el propio
  código, cada uno con el comando que lo produjo. 35 cerrados con verificación
  ejecutada, y [las cuatro veces que la medición contradijo al hallazgo](docs/informes/2026-08-15-implementacion.md).
- [**Lo que aprendimos a los golpes**](docs/ESTADO-2026-08.md#9-lo-que-aprendimos-a-los-golpes):
  el prompt que medía mejor y extraía cero en el 96% del corpus; el cluster transitivo
  de 198 avisos con un test que afirmaba el bug; el worker "vivo y sordo" por un
  timeout de conexión de 1 segundo.
- [**Recuperación estructurada, y el RAG que se está midiendo**](docs/18-rag-propuesta.md):
  hoy la recuperación es SQL con relajación progresiva y el cupo se llena por
  recencia. Sobre eso hay construido un recuperador híbrido —embeddings locales con
  chunking por oraciones y encabezado estructurado, búsqueda léxica en español,
  fusión por RRF, reranking con cross-encoder— **apagado por configuración** hasta que
  la tabla de ablación cumpla el criterio escrito antes de medir (doc 18 §4.4). La
  vara se construyó primero: nDCG, recall, MRR y bpref con bootstrap apareado, sobre
  juicios que produce el propio pipeline por *pooling* (ADR-010 a 013).
- [**«Preguntale al informe»**](docs/06-api-contrato.md): un RAG chico sobre los hechos
  del informe y la metodología, con citas obligatorias verificadas sin LLM —la misma
  función que verifica las cifras del crítico— y rechazo sin llamar al modelo cuando
  no hay evidencia.

## Stack

Python 3.12+ · FastAPI · SQLAlchemy 2 async · Alembic · PostgreSQL 16 + pgvector +
pg_trgm · Redis + arq · LangGraph (checkpoint en Postgres) · CrewAI (un solo nodo) ·
instructor + Pydantic v2 · LiteLLM · fastembed · WeasyPrint · Next.js 15 · Docker Compose ·
pytest (429 tests, incluidos tests de arquitectura) · Playwright · mypy --strict · ruff.

Modelos: el crítico corre sobre Claude Sonnet 4.5; extracción, juicio y redacción sobre
modelos abiertos de bajo costo vía API compatible con OpenAI. Cambiar cualquiera es editar
`config/litellm.yaml`.

## Estado y límites

- **Terminado y verificado de punta a punta; no desplegado.** La última corrida
  completa documentada fue contra la imagen de producción reconstruida. No hubo
  usuarios.
- La cobertura útil del corpus real son dos barrios de Buenos Aires; fuera de ahí el
  sistema devuelve `INSUFFICIENT_DATA`, por diseño.
- Los golden sets reales son chicos y fueron anotados con asistencia de IA; el golden
  sintético tiene verdad por construcción pero prosa generada.
- **Este repositorio no incluye captura de portales.** El corpus se alimenta por
  archivos; de dónde salen es decisión de quien opera ([doc 10 §2](docs/10-seguridad-y-legal.md)).
- No es una tasación con validez legal: es un insumo para la que firma un martillero.

## Documentación

| | |
|---|---|
| [00 Visión y alcance](docs/00-vision-y-alcance.md) · [01 Arquitectura y ADRs](docs/01-arquitectura.md) · [02 Fuentes](docs/02-fuentes-de-datos.md) · [03 Modelo de datos](docs/03-modelo-de-datos.md) | Qué es y cómo está hecho |
| [04 Pipeline nodo por nodo](docs/04-pipeline-de-agentes.md) · [05 Metodología de valuación](docs/05-metodologia-de-valuacion.md) · [17 Arquitectura viva](docs/17-arquitectura-viva.md) | El grafo y la matemática |
| [06 API](docs/06-api-contrato.md) · [07 Pantallas](docs/07-pantallas.md) · [08 Infra](docs/08-infra-y-despliegue.md) · [10 Seguridad y legal](docs/10-seguridad-y-legal.md) · [11 Costos](docs/11-costos.md) | Producto y operación |
| [09 Evaluación y backtest](docs/09-evaluacion-y-backtest.md) · [auditoría](docs/auditoria/00-resumen.md) · [informes](docs/informes/) | Cómo se mide |
| [18 Propuesta RAG](docs/18-rag-propuesta.md) · [resultados](docs/informes/2026-09-18-rag-resultados.md) · [ESTADO 18/09](docs/ESTADO-2026-09-18.md) | Lo que sigue, lo medido, y dónde quedó |

## Licencia

[PolyForm Noncommercial 1.0.0](LICENSE.md): uso, modificación y redistribución libres
para fines no comerciales. Para uso comercial, contactar al autor.
