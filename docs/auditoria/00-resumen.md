# Auditoría del código — resumen

**15/08/2026** · 12 secciones · **58 hallazgos** (8 altas, 26 medias, 24 bajas)
· Método: ejecutar y medir. Cada hallazgo tiene su comando y su salida en el
documento de su sección.

> Los scripts de sonda que produjeron esas mediciones no se publican: corren
> contra el corpus real y la base de la inmobiliaria, así que no se pueden
> reproducir con el corpus demo de este repositorio. Los comandos y sus
> salidas quedan transcritos en cada sección tal como se ejecutaron.

---

## El estado, en un párrafo

El código es mejor de lo que esta auditoría va a hacer parecer. La matemática de
valuación **reproduce exactamente** contra los informes ya emitidos, ADR-002 no
tiene una sola grieta, las migraciones corren desde cero sin deriva, los 19
scripts funcionan, ningún test pasa sin afirmar nada, y los comentarios del
código explican qué se midió y por qué. El problema no está en lo que se
escribió: **está en la distancia entre lo escrito y lo que corre.** El
repositorio no tiene un solo commit, el CI nunca se ejecutó, y la imagen que se
desplegaría hoy es anterior a casi todo el trabajo del 14/08. La mitad de los
hallazgos graves son de esa distancia; la otra mitad son controles que están
puestos y no miden lo que dicen medir.

---

## Los 10 que más importan

| # | Hallazgo | Qué pasa hoy |
|---|---|---|
| **1** | **[H-48](09-infra-y-ci.md) · La imagen de producción está 12 archivos atrás y le faltan 5 endpoints** | Desplegar hoy deja el sistema **sin** `/calidad`, `/admin/*` ni `/inventory/snapshot`, **sin** rate limit de login, y con el mapeo de cocheras roto. Verificado levantando la imagen: 5 rutas dan 404 |
| **2** | **[H-11](02-grafo-y-nodos.md) · La defensa anti-alucinación deja pasar la mitad de los precios inventados** | Sobre un informe real, **50,5%** de los precios al azar entre USD 100k y 400k pasan la fase A del crítico. El ejemplo del propio docstring —"USD 312.000"— pasa en los tres informes que probé |
| **3** | **[H-12](02-grafo-y-nodos.md) · El dedup encadena y borra comparables legítimos** | Un cluster de **198 avisos** de 23 a 73 m² y de USD 111.000 a 436.900, donde solo el **7,6%** de los pares cumple el criterio. 3.470 avisos vigentes invisibles para todo informe; en Palermo, el 35% del universo |
| **4** | **[H-10](02-grafo-y-nodos.md) · En producción el PDF no se puede descargar** | El worker escribe en `/data/raw/artifacts` y el volumen compartido es `/data/artifacts`. La API devuelve 404 siempre. Verificado en los contenedores de producción |
| **5** | **[H-42](07-seguridad.md) · El rate limit del login lo evade cualquiera** | uvicorn corre con `--forwarded-allow-ips *` y toma el **primer** valor de `X-Forwarded-For`, que el cliente elige. Fuerza bruta sin tope, y el dict de intentos crece sin límite |
| **6** | **[H-46](09-infra-y-ci.md) · El CI nunca corrió** | Cero commits, sin remoto, y dispara en `main` estando en `master`. Todos los gates que ESTADO da por activos son un archivo YAML. Y ~13.000 líneas existen solo en el árbol de trabajo |
| **7** | **[H-49](09-infra-y-ci.md) · `make backup` no puede correr** | La imagen `api` no tiene `/app/ops`, no tiene `/app/scripts` y no tiene `pg_dump`. El script está bien escrito y es inalcanzable. **La restauración nunca se probó** |
| **8** | **[H-41](07-seguridad.md) · El nodo 5 manda el texto de los avisos sin delimitar** | Los nodos 4 y 6 usan `wrap_external`; el 5 no. Un aviso publicado en un portal puede intentar dirigir al juez de dedup — y `dedup_corpus.py` persiste el resultado en el corpus |
| **9** | **[H-01](01-valuacion.md) · El motor elimina comparables por una regla que doc 05 dice que no usa** | Un recorte p5–p95 no documentado saca 2 de 8 comparables (el 25% que doc 05 §5 argumenta explícitamente que no se tira). 88 exclusiones reales |
| **10** | **[H-50](09-infra-y-ci.md) · `.env.example` impide el arranque en producción** | `ENV=development  # comentario` — `docker --env-file` no saca comentarios en línea y pydantic rechaza el valor. Los cuatro servicios mueren al arrancar |

---

## El veredicto: qué es confiable hoy

### Confiable

- **El motor de valuación como pieza de software.** Recalculé cinco informes
  SUCCEEDED desde las filas persistidas y los seis valores de cada uno coinciden.
  Es determinístico, auditable y el número sale de donde dice que sale.
- **ADR-002.** El nodo 7 no puede tener modelo, el atajo por variable de entorno
  falla igual, ningún nodo LLM escribe las columnas de precio, y la base tiene
  un CHECK. Sin grietas.
- **El esquema y las migraciones.** Desde cero producen exactamente la base real,
  tabla por tabla y columna por columna. Los CHECK son sensatos y ninguno se
  viola.
- **La traza por nodo.** `report_events` registra el motivo de cada degradación,
  el modelo que **realmente** atendió, el costo y la duración. Es lo que hizo
  posible auditar el resto.
- **El aislamiento entre tenants** en los caminos que existen hoy, y el header
  `X-Org-Slug` apagado en producción **por código** (verificado contra la imagen).
- **La ingesta desde CSV.** Idempotente por sha256 y por `content_hash`,
  verificado contando filas. Un solo camino de descarte, compartido.
- **`tests/architecture/`.** Es el mejor material del repo: verifica invariantes,
  no comportamiento. Sus huecos son de alcance, no de concepto.

### No confiable

- **Cualquier cosa sobre "producción".** No hay despliegue, no hay commit, no hay
  CI, la imagen está vieja y el `.env` de ejemplo no arranca. Todo lo verde que
  este proyecto declara está verificado sobre el código montado por volumen en
  desarrollo. **La palabra "producción" en la documentación de hoy no describe
  nada que exista.**
- **El conjunto de comparables que entra al motor.** El motor calcula bien sobre
  lo que le llega, y lo que le llega pasó por un dedup que encadena (H-12) y por
  un recorte no documentado (H-01). El número es correcto dado el input; el input
  no está bien justificado.
- **El crítico como garantía de que no sale una cifra inventada** (H-11). La fase
  B (adversarial, con LLM) sigue funcionando; la fase A —la que no depende de un
  modelo, la que sostiene el argumento— es una moneda al aire.
- **"5.027 propiedades únicas".** Es el resultado del clustering de H-12.
- **El backup.** No corrió nunca y no puede correr por el camino documentado.
- **La confianza como indicador de cinco factores.** Funciona —la calibración del
  backtest es monótona— pero la deciden dos: los otros tres aportan constantes
  (H-06, H-07).

### Bloqueado por datos, y correctamente

`published_at` vacío en los 8.497 avisos vigentes apaga tres mecanismos a la vez
(el coeficiente de antigüedad del aviso, la frescura de la confianza y la regla
`aviso_vencido`). La causa **sí** es de código —el dato está en `raw.attrs` y no
llega a la columna (H-21)— pero el volumen depende del scraper. Lo mismo con los
ambientes de Portal A y con el golden set sin anotar: están bien identificados
en ESTADO §5.1 y no los toqué.

---

## Cómo leer el backlog

[99-backlog.md](99-backlog.md) tiene los 58 en orden de ejecución, no de
severidad. El orden importa en un punto y conviene decirlo acá:

> **H-12 (el dedup) va ANTES que H-48 (reconstruir la imagen).** Reconstruir
> enciende en producción el filtro de cluster del nodo 2, que hoy opera sobre
> clusters encadenados. Desplegar primero es empeorar el número.

Y una advertencia sobre el orden inverso: **H-46 (commitear) va primero de todo**.
Cincuenta y ocho arreglos sobre un árbol sin control de versiones es la peor
forma posible de hacer este trabajo.

---

## Lo que no pude verificar

Está detallado al pie de cada sección. Lo que más pesa:

1. **La restauración de un backup.** No se puede correr como está documentado
   (H-49) y hacerlo por otro camino escribe en la base. Sigue sin probarse nunca.
2. **`POST /v1/reports` con inputs hostiles.** Escribe y gasta. Solo probé los
   que fallan en validación.
3. **El efecto de H-01 y H-12 sobre el MdAPE.** Requiere correr el backtest, que
   escribe en `eval.backtest_runs`. Es la primera medición que debería hacer la
   sesión 2 — y para eso el dataset `VIGENTES` ya está implementado (H-54), lo
   que ESTADO §5.1 da por pendiente de diseño.
4. **Caddy.** No corre en desarrollo, así que la cadena
   `cliente → Caddy → uvicorn` de H-42 la deduje del Caddyfile; el
   comportamiento de uvicorn sí lo medí.
5. **H-19** (una re-extracción puede borrar features buenas): leído en el código,
   no ejecutado.
