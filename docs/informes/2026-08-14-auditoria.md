# Informe — Auditoría del 14/08/2026

**Alcance:** el repo entero contra los documentos · **Método:** ejecutar, no leer

> Los resultados se publican como salen. Un backtest que solo se muestra cuando
> da bien no es un backtest (doc 09 §7). Una auditoría que solo reporta lo que
> ya se sabía, tampoco.

---

## 1. El punto de partida era verde, y el verde era más chico de lo que parecía

```
ruff check src tests scripts   ✅ All checks passed
ruff format --check            ✅ 82 files
mypy src                       ✅ 56 archivos
pytest -m "not live"           ✅ 218 passed in 9.32s
```

Los cuatro comandos son ciertos. Lo que no decían:

| Gate | Alcance real | Qué quedaba afuera |
|---|---|---|
| `ruff check` | `src tests scripts` | **`migrations/`** — 11 errores acumulados |
| `mypy` | `src` | `tests/` y `scripts/` |
| `pytest --cov` | sin `--cov-fail-under` | la cobertura se imprimía y no era gate |
| CI "aislamiento" | `pytest tests/architecture` | **el directorio estaba VACÍO** |

El caso del directorio vacío es el que más enseña. `pytest` sobre un directorio
sin tests devuelve **exit 5** con el mensaje *"no tests ran"*, que se lee como
un problema de configuración del runner. El job salía en rojo, y en rojo por un
motivo que invitaba a mirar para otro lado. **El gate de aislamiento
multi-tenant no existía, y lo que había en su lugar era peor que nada: una
casilla que alguien podía dar por tildada.**

La cobertura contaba la misma historia desde otro ángulo:

```
src/tasador/valuation/engine.py      94%     ← lo difícil de razonar
src/tasador/v1/reports.py            37%     ← la superficie del producto
src/tasador/agents/runner.py          0%
src/tasador/eval/backtest.py          0%     ← el que produce el MdAPE
src/tasador/capture/filters.py        0%
src/tasador/worker.py                 0%
```

218 tests, y el contrato HTTP que consume el producto casi sin probar. Los
tests siguieron a donde uno teme equivocarse, no a donde uno se equivoca.

---

## 2. Doce defectos, con lo que costaba cada uno

Ninguno se encontró leyendo el código. Los cuatro últimos aparecieron recién al
crecer el corpus.

| # | Defecto | Qué costaba |
|---|---|---|
| 21 | **`prompts/market_context/v1.jinja` no existía.** `agents.yaml` lo declaraba y los prompts del nodo 8 vivían en `market.py`, en DOS copias distintas | El `prompt_bundle_version` **no cubría el nodo 8**: se podía cambiar el texto que ve el modelo sin mover el hash estampado en el informe |
| 22 | `docker/web.Dockerfile` no existía | `docker compose build` fallaba ENTERO. Con él `make up`, `make dev` y la instrucción de arranque de ESTADO §8 |
| 23 | `ENV` con un `\n` **literal** en las dos imágenes | Docker se comía la instrucción: **ninguna** de las 5 variables llegaba a la imagen. En dev andaba (el override las declara); en producción volvían los bugs 13, 14, 17, 18 y 19 — incluida **la telemetría de CrewAI encendida**, que doc 10 §3 no permite |
| 24 | `tasador.eval.run` no existía | Todo PR que tocara `prompts/`, `adjustments.yaml` o `valuation/` disparaba el eval-gate y el gate reventaba con `ModuleNotFoundError`. Falla igual si el cambio era bueno que si era malo: no informa nada |
| 25 | `eval.backtest_runs` no existía (el schema `eval` sí estaba en `MANAGED_SCHEMAS`) | Ningún backtest quedaba registrado. El MdAPE 15,0% vive en un documento, no en la base: no se puede comparar contra el próximo con una consulta |
| 26 | `make seed` → `tasador.scripts.seed`, `make backup` → `ops/backup.sh` | Módulos y archivos inexistentes. Un backup que se cree que corre es peor que no tener backup |
| 27 | `core.subject_properties.neighborhood_id` **nunca se escribía** | 38 propiedades, 0 con barrio. El nodo 1 lo resolvía y no lo bajaba a la fila. La columna "Barrio" del listado salía vacía siempre |
| 28 | El compose de prod y el de dev construían **targets distintos con el MISMO tag** | El último build ganaba en silencio. Síntoma: `sh: next: not found` y el contenedor en loop, apuntando a las dependencias cuando el problema era de tags |
| 29 | El nodo 4 hacía `commit` **después de todos los lotes** | Un timeout tiraba a la basura toda la extracción ya pagada. El caché por aviso —"un aviso extraído no se procesa nunca más"— solo vale si alguien lo escribió |
| 30 | `job_timeout = 600` de arq | El primer informe de Palermo (60 candidatos, corpus frío) se pasó y murió con `TimeoutError` |
| 31 | **`_prop()` no convertía los enteros.** `listings.raw` guarda todo como texto | El nodo 7 —**el que calcula el precio, el único que no puede degradar**— murió con `TypeError: '<=' not supported between instances of 'str' and 'int'` |
| 32 | El crítico marcaba la altura de la dirección como cifra no trazable | Tres rechazos y el propietario recibe el informe **sin narrativa** |

### 2.1 El 21 es el más grave, y no rompía nada

Es el mismo patrón que el bug #4 de la Etapa 3 (el extractor atendido por el
modelo del juez): el sistema funcionaba, daba respuestas correctas, y la
trazabilidad mentía.

`_bundle_hash` hashea los prompts que `agents.yaml` declara y trata a los
ausentes como `<ausente>` — a propósito, para que el día que se escriban el
bundle cambie. El mecanismo estaba bien. Lo que faltaba era **que alguien
avisara que había uno ausente**.

La prueba de que ahora está cubierto es que escribir el archivo movió el hash:

```
bundle_hash  b8f1845e5505  ->  94c53f5750b5
```

Efecto colateral que vale registrar: la comparación **crewai vs. secuencial**
del informe de la Etapa 3 §12 comparó dos motores con **dos prompts distintos**
—eran paráfrasis, no el mismo texto—. Parte del delta que se atribuyó al
andamiaje de la crew era texto diferente. Ahora los dos motores leen del mismo
archivo y ese número se puede volver a medir de verdad.

### 2.2 El 23 solo existía en producción

```
$ docker image inspect ...tasador-api:dev --format '{{json .Config.Env}}'
[... "PYTHONPATH=/app/src"]        ← ni XDG_CACHE_HOME, ni HOME, ni CREWAI_*
```

La línea era:

```dockerfile
ENV XDG_CACHE_HOME=/tmp/cache XDG_DATA_HOME=/tmp/data HOME=/tmp/home \n    CREWAI_DISABLE_TELEMETRY=true ...
```

Ese `\n` son dos caracteres, no un salto de línea. Docker se come la
instrucción entera **sin decir nada**. Pasaba desapercibido porque
`docker-compose.dev.yml` declara las mismas cinco en su bloque `environment`:
en desarrollo andaba. Después del arreglo, verificado por inspección:

```
"XDG_CACHE_HOME=/tmp/cache"  "XDG_DATA_HOME=/tmp/data"  "HOME=/tmp/home"
"CREWAI_DISABLE_TELEMETRY=true"  "CREWAI_TRACING_ENABLED=false"
```

**La lección se repite y conviene decirla fuerte: "lo verifiqué en el
contenedor" tiene que ser el contenedor de PRODUCCIÓN.** Los arreglos de los
bugs 13/14/17/18/19 de la Etapa 3 quedaron en el override de dev, y el override
de dev es justamente el que no se despliega.

### 2.3 Los cuatro últimos aparecieron por los datos, no por el código

29, 30, 31 y 32 estaban latentes desde siempre y salieron el mismo día, al
entrar 288 avisos de Palermo. Ninguno es un error de razonamiento: son
suposiciones que 24 avisos de un solo barrio nunca contradijeron.

El 31 es el más ilustrativo. `corpus.listings.raw` guarda **todo como texto**,
a propósito. Los decimales pasaban por `dec()`; los enteros no pasaban por
nada. Con Portal B nadie lo vio porque esas tarjetas no traían antigüedad. El
CSV de Portal A trae `age` en 231 de 300, y el nodo 7 se cayó.

**Un sistema probado sobre un solo barrio está probado sobre un solo barrio.**

---

## 3. El corpus: de 24 avisos a 312

Se escribió `tasador.ingest.csv_scan` para incorporar los CSV del scraper
externo. La decisión de diseño es que **no tiene lógica nueva**: traduce una
fila a un `Card` y llama a `motivo_descarte()` y `_upsert()`, los
mismos que usa la captura por HTML. Hay un test que lo impone:

```python
from tasador.ingest.core import motivo_descarte as original
assert motivo_descarte is original
```

Dos caminos de ingesta con dos reglas de descarte distintas serían el mismo
error que el eval del nodo 4 de la Etapa 3, con la diferencia de que
ensuciarían el corpus en vez de una métrica.

```
portal_a_venta_departamento_palermo_2026-08-14.csv
  300 filas   288 usables
    sin_superficie              8
    superficie_implausible      2
    usd_m2_fuera_de_rango       1
    precio_no_usd               1
```

```
 source   |  barrio  | avisos | precio_medio | m2_min | m2_max
----------+----------+--------+--------------+--------+--------
PORTAL_A | Palermo  |    288 |      227.865 |     18 |    300
PORTAL_B  | Belgrano |     24 |      391.505 |     86 |    152
```

Idempotencia en dos niveles, verificada corriéndolo dos veces: por SHA-256 del
archivo (no por nombre: el scraper puede reescribir el mismo nombre con
contenido nuevo) y por `content_hash` del aviso.

### 3.1 El primer informe de Palermo

```
NODO                  EST       ms        USD
normalize_subject     OK        32   0.000000
retrieve_candidates   OK       169   0.000000
extract_features      OK    93.825   0.000818
dedup_cluster         OK    36.089   0.001076
curate                OK   136.159   0.007549
adjust_and_value      OK        30   0.000000
market_context        OK    24.343   0.000607
write_report          OK    14.737   0.009042
critic                OK     ~5.900  0.018       aprobado al primer intento
render_pdf            OK     2.963   0.000000
                                     USD 0,048

rango sugerido : USD 184.451 — 230.564 — 276.676
cierre esperado: USD 195.979 – 219.036
USD/m²         : 2.829   ·   superficie ponderada 81,5 m²
confianza      : ALTA (0,754)      ← la primera ALTA del proyecto
comparables    : 53 usados de 60 encontrados
```

**Que la confianza sea ALTA no es un logro del código: es lo que pasa cuando
hay datos.** Belgrano daba MEDIA con 22 comparables y 3,8× de dispersión;
Palermo da ALTA con 53. Es exactamente la conclusión de la Etapa 3 —"faltan
datos de entrada, no procesamiento"— confirmada por el camino contrario.

Y la latencia subió: **~5 minutos** contra los 12,2 s del corpus caliente de
Belgrano. Es corpus frío (60 avisos a extraer). El p95 ≤ 180 s de doc 09 §4
**no se cumple para el primer informe de un barrio nuevo**, y eso hay que
decirlo en vez de promediarlo con los calientes.

---

## 4. El backtest ahora deja rastro

`eval.backtest_runs` existe y `python -m tasador.eval.run` la escribe. Dos
corridas reales, misma muestra, distinta semilla:

```
BADATA_2015_2020 — últimas 2 corridas
fecha         casos    MdAPE     base    PPE20  motor / bundle
2026-08-14      300    15.0%    16.2%    60.0%  2026.08.1 / 94c53f5750b5
2026-08-14      299    14.5%    16.2%    60.5%  2026.08.1 / 94c53f5750b5
```

```
CONTRA LA CORRIDA ANTERIOR
  MdAPE 14.5%  ->  15.0%   (+0.6 pp)
  ⚠️  Empeoró 0.6 pp, dentro de la tolerancia.
```

**Ese 0,6 es el dato más útil de esta sección y no se buscaba.** Es la varianza
entre dos semillas sobre 300 casos, sin que cambie una línea de código. O sea:
con este tamaño de muestra, **una "mejora" de medio punto es ruido**. La
tolerancia de 2 pp de doc 09 §5 tiene sentido; celebrar un −0,5 pp, no.

La calibración de la confianza sigue siendo monótona y bien separada, que es lo
que hace que el nivel que ve el usuario signifique algo:

```
ALTA 11,1%   ·   MEDIA 24,1%   ·   BAJA 75,6%
```

---

## 5. Lo que se agregó

**Trazabilidad**
- `prompts/market_context/v1.jinja` con las 10 secciones del nodo 8; los dos
  motores leen de ahí. `prompts.secciones()` parte un archivo en piezas para
  que sea **un archivo, un hash, un texto**.
- Tests: que todo prompt declarado exista, que ninguno vuelva al código, y que
  `bundle.lock.json` no quede viejo. `scripts/bundle_lock.py` para regenerarlo.

**Calidad medible**
- `eval.backtest_runs` + migración `a1c7f2e40d18` (crea el schema con
  `IF NOT EXISTS`: `alembic upgrade head` sobre una base vacía tiene que bastar).
- `tasador.eval.run` con `--fail-on-regression`, `--fail-if-mdape-worse-than`
  y `--history`. Compara contra la **última corrida del mismo dataset**, no
  contra un umbral escrito a mano que envejece.

**Gates que ahora existen**
- `tests/architecture/` con tres archivos: aislamiento multi-tenant (runtime +
  análisis estático de las consultas de `v1/`), invariantes de las imágenes, y
  **que todo comando documentado exista**. Este último atrapó, de una,
  `tasador.eval.run`, `tasador.scripts.seed`, `ops/backup.sh` y
  `docker/web.Dockerfile`.
- `tests/conftest.py` con la fixture `db`: **se saltea solo si falta
  `DATABASE_URL`**. Si está definida y la base no responde, FALLA. Un skip ante
  cualquier error convierte el gate en decorativo.
- CI: `migrations/` entra al lint, `--cov-fail-under=60`, build de la imagen
  `web`, migraciones desde cero con `alembic check`, y un paso que falla si los
  tests de base se saltearon teniendo `DATABASE_URL`.

**Producto**
- `GET /v1/reports` con paginación por keyset. El cursor es base64url porque en
  texto plano el `+` del ISO-8601 se decodifica como espacio y volvía roto — lo
  encontró un test.
- `web/`: Next.js 15 con `/informes`, `/informes/nuevo`, `/informes/[id]` (los
  tres estados: stepper en vivo, resultado, y la pantalla que EXPLICA el
  `INSUFFICIENT_DATA`), proxy del PDF y `/api/health`. Compila y corre en el
  contenedor.
- `tasador.ingest.csv_scan` + `scripts/ingest_csv.py` con `--dry-run`.
- `ops/backup.sh` con `--verify`, que restaura el dump en una base descartable
  y compara conteos. Un backup no verificado no es un backup.

**Tests:** 218 → **279** (260 sin base, 21 salteados).

---

## 6. Lo que NO se hizo, y por qué

- **Autenticación.** Sigue sin existir: el tenant se resuelve por
  `X-Org-Slug`, sin secreto. Es la decisión #5 de ESTADO §6 y es del humano,
  no del agente. El front está escrito para que cuando llegue la sesión se
  cambie **un solo lugar** (`web/src/lib/api.ts`).
- **El eval-gate del CI queda desactivado**, con el motivo escrito en el YAML:
  el backtest lee el corpus de la base y el runner no lo tiene. Correrlo contra
  una base vacía daría "0 casos" y un gate verde, que es peor que no tener
  gate. Hay un `::warning::` que avisa cuando debería haber corrido.
- **`--fail-on-regression` sobre `GOLDEN_SET` devuelve 1 a propósito**, porque
  todavía no está cableado a un umbral persistido. Un gate que pasa sin medir
  es peor que uno que no existe.
- **Nodo 4 al 68%** contra el objetivo de 92%. No se tocó: es trabajo de prompt
  con el eval al lado, no de auditoría.
- **`web/` es un scaffold, no las 12 pantallas de doc 07.** Están las cuatro
  que la API ya soporta entera. Faltan `/comparables`, `/calidad`,
  `/admin/*`, y todo lo que necesita auth.

---

## 7. Lo que aprendimos, esta vez

1. **Un gate roto es peor que un gate ausente.** El ausente se nota; el roto
   ocupa el lugar del que hacía falta. `pytest` sobre un directorio vacío sale
   en rojo con un mensaje que invita a mirar para otro lado.
2. **"Verificado en el contenedor" tiene que ser el de producción.** Cinco
   arreglos vivían solo en el override de dev, que es el único que no se
   despliega.
3. **Un `\n` literal en un `ENV` se come la instrucción entera, en silencio.**
   Lo único que lo dice es `docker image inspect`.
4. **Dos compose que construyen targets distintos con el mismo tag** producen
   un error que apunta a otro lado por completo (`next: not found`).
5. **El trabajo que solo vive en memoria no existe.** Un caché que se escribe
   al final es una promesa hasta que alguien commitea.
6. **Los datos nuevos son un test de integración que nadie escribió.** Cuatro
   bugs latentes salieron el mismo día en que el corpus pasó de 24 a 312.
7. **Rechazar de más cuesta el producto.** El crítico marcó la altura de la
   dirección y el propietario se quedaba sin narrativa. Es el mismo aprendizaje
   de la Etapa 3 §10.3, con otra cara: **la regla estaba bien y la
   implementación era más angosta que la regla** — `1800` SÍ estaba en los
   datos, adentro de un string.
8. **La varianza entre semillas es la vara.** 0,6 pp entre dos corridas
   idénticas salvo la semilla: cualquier "mejora" menor que eso es ruido.
9. **Windows reserva rangos de puertos.** El 3000 cae adentro seguido y el
   mensaje (`forbidden by its access permissions`, con nada escuchando) no lo
   dice. Se ve con `netsh interface ipv4 show excludedportrange protocol=tcp`.

---

## 8. Reproducirlo

```powershell
$env:WEB_PORT="3300"          # el 3000 puede estar reservado por Hyper-V
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

docker compose exec api python scripts/seed.py
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos\output --dry-run
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos\output
docker compose run --rm worker python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300
docker compose run --rm worker python -m tasador.eval.run --history --dataset BADATA_2015_2020
```

⚠️ Si el contenedor `web` queda reiniciando con `next: not found`, es el volumen
anónimo de `node_modules` que quedó viejo:
`docker compose ... rm -sfv web` y volver a levantarlo.
