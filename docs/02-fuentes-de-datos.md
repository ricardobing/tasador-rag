# 02 — Fuentes de datos

**Qué datos usa el sistema, de dónde salen y qué rol cumple cada capa.** La
investigación original sobre acceso a portales y proveedores no forma parte de este
repositorio; ver [10 §2](10-seguridad-y-legal.md) para la frontera del producto.

---

## 1. Las tres capas del corpus

| Capa | Fuente | Rol | Cómo entra | Se versiona |
|---|---|---|---|---|
| **Avisos vigentes** | Portales inmobiliarios, vía archivos de un recolector externo | Los comparables de cada informe | `scripts/ingest_csv.py --carpeta …` | No: es contenido de terceros |
| **Históricos** | BA Data (GCBA), CC-BY-2.5-AR | Backtest con precios conocidos; marcados `active=false`, nunca son comparables | `scripts/fetch_badata.py` + `load_badata.py` | No: se descarga |
| **Serie oficial** | Precio por barrio del GCBA | Chequeo de sesgo del corpus y contexto de mercado | `load_badata.py` | No |
| **Inventario del tenant** | El panel de la inmobiliaria (opcional, solo lectura) | Dataset de backtest actual; "N comparables son de tu cartera" | `POST /v1/inventory/snapshot` o rol de solo lectura (doc 01 §2.2) | No |
| **Carga manual** | El agente pega o tipea un aviso | Comparables que ningún portal tiene | doc 14 | No |
| **Demo** | Sintético, generado por `scripts/generar_corpus_demo.py` | Que el repo se pueda correr sin datos reales | `data/demo/` | **Sí** |

Lo que se mide en los informes de este repo se midió sobre el corpus real (miles de
avisos vigentes de dos barrios de Buenos Aires). El corpus demo reproduce el
**pipeline**, no las cifras.

## 2. El contrato de entrada

La ingesta por archivos acepta CSV, JSON y JSONL con el mismo esquema; reconoce los
archivos **por contenido** (claves `portal_property_id` y `url`/`canonical_url`), no
por nombre ni carpeta; es idempotente por SHA-256 del archivo y por hash de contenido
del aviso. Los campos que lee están en `ingest/csv_scan.py::fila_a_card`, y el único
camino de persistencia es `ingest/core.py`: descarte temprano, superficie ponderada,
upsert y snapshot de precio viven una sola vez.

El portal de origen se declara por entorno:

```
PORTALES=nombre-en-el-csv=PORTAL_A,otro=PORTAL_B
```

El código solo conoce los rótulos neutros. Qué portal es cada uno, y bajo qué
condiciones se obtienen sus avisos, es una decisión de quien opera la instancia.

## 3. Lo que un aviso tiene que traer para servir

Descartado antes de tocar la base (`motivo_descarte`), porque el costo de un aviso
inservible no es la fila sino la extracción por LLM que se le correría después:

| Motivo | Por qué |
|---|---|
| `sin_precio` | "Consultar precio" nunca puede ser comparable |
| `precio_no_usd` | El mercado de venta opera en dólares; convertir con un tipo de cambio arbitrario mete más error del que resuelve |
| `sin_superficie` · `superficie_implausible` | Sin USD/m² no hay método |
| `sin_direccion` | No se puede ubicar ni deduplicar |
| `usd_m2_fuera_de_rango` | Error de carga (umbrales en `config/adjustments.yaml`) |

Y lo que separa un aviso útil de uno excelente: la **ficha de detalle**. Un aviso
capturado solo desde el listado casi nunca declara estado de conservación (0% contra
93,8% medido); el estado es el coeficiente más grande del método. El flag
`ficha_completa` en `quality_flags` es lo que el nodo 2 usa para preferirlos.

## 4. Datos personales

No se guardan datos de contacto del publicador: solo el nombre comercial de la
inmobiliaria, que es dato público. Ver [10 §3](10-seguridad-y-legal.md).
