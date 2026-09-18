# Guía para probar la app

Cuatro maneras de probarla, de la más rápida a la más completa, y qué esperar de cada
una. Todo corre en tu máquina: **el CI de GitHub no es obligatorio para saber si algo
anda** —es la misma lista de pasos, y acá está en un solo comando—.

| Quiero… | Comando | Tarda |
|---|---|---|
| Saber si rompí algo (lint, tipos, tests) | `.\make.ps1 ci` · `make ci` | 4–6 min |
| Lo mismo más las imágenes Docker y trivy | `.\make.ps1 ci-imagenes` | +10 min |
| Probar las pantallas de verdad, contra la API real | `.\make.ps1 e2e` | 5 min |
| Probarla a mano, como la usaría la inmobiliaria | §2 | 15 min la primera vez |

En Windows los objetivos `ci` y `e2e` usan Git Bash (viene con Git for Windows). Si
preferís los comandos sueltos, están en cada sección.

---

## 1. Qué prueba cada capa (y qué no)

| Capa | Qué cubre | Qué NO cubre |
|---|---|---|
| **Tests de Python** (492, `pytest -m "not live"`) | El motor de valuación con casos a mano, la verificación de citas y cifras, el contrato HTTP, el aislamiento entre tenants, los tests de arquitectura (el nodo del precio no puede tener modelo), las métricas del eval | Que un modelo real responda bien; que las pantallas se vean |
| **Playwright** (57 tests contra el stack levantado) | Listado, alta, ficha, «Preguntale al informe», PDF, comparables, calidad, login real con `E2E_CON_AUTH=1` | La calidad de la prosa; el costo |
| **CI local / GitHub** | Todo lo anterior más: formato, migraciones sobre base vacía, secretos filtrados, auditoría de publicación, imágenes sin vulnerabilidades | El backtest (necesita el corpus) |
| **Evals** (doc 09 y doc 18) | Backtest del precio, extracción contra el golden set, recuperación, QA | Son mediciones, no gates: se leen |
| **A mano** | Lo que un agente ve, en el orden en que lo ve | La repetibilidad |

---

## 2. Probarla a mano, de punta a punta

### 2.1 Levantar

```powershell
.\make.ps1 dev          # o: docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose ps       # todos Up; los que tienen healthcheck, (healthy)
```

Si es la primera vez o la base está vacía, cargá el corpus demo (todo dentro del
contenedor, porque el `.env` apunta a `postgres:5432`):

```powershell
$tas = 'docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api python'
Invoke-Expression "$tas scripts/seed.py"                                   # tenant + 59 barrios + centroides
Invoke-Expression "$tas scripts/ingest_csv.py --carpeta /data/raw/demo"    # 581 avisos sintéticos
Invoke-Expression "$tas scripts/crear_usuario.py --email vos@inmo-demo.com.ar --rol owner --api-key demo"
```

Guardá la API key que imprime: se muestra una sola vez. Con `PORTALES=demo-a=PORTAL_A,demo-b=PORTAL_B`
en el `.env` (ya viene así en la plantilla comentada).

### 2.2 Entrar

- Front: `http://127.0.0.1:3000` (o el `WEB_PORT` que hayas puesto; en Windows el
  3000 suele estar reservado, ver `docker-compose.dev.yml`).
- En desarrollo no hace falta login: `TASADOR_ORG_SLUG=inmo-demo` te deja entrar.
  Para probar el login real: `$env:TASADOR_ORG_SLUG=""` y volver a levantar `web`;
  entrás con el usuario que creaste. Cinco intentos fallidos por minuto dan 429.

### 2.3 El recorrido que hay que hacer

1. **`/informes`** — el listado. Tiene que mostrar dirección y barrio, no UUIDs; cada
   valor con su nivel de confianza al lado; `INSUFFICIENT_DATA` en ámbar, no como error.
2. **Nuevo informe** — `Gorriti 5000`, departamento, 3 ambientes, 70 m². Con dirección y
   tipo alcanza. Mirá el stepper: los diez pasos en castellano. Con el corpus demo el
   primer informe de Palermo tarda **~5 minutos** (lee 60 avisos con IA); los siguientes,
   segundos. Lo que tiene que salir: `SUCCEEDED`, `ALTA`, 45 de 60 comparables, USD
   0,02–0,07.
3. **La ficha** — el rango de cierre arriba, no escondido; el recuadro de limitaciones
   visible; la tabla de comparables con los descartados y su motivo; el PDF se descarga
   (abrilo: cada cifra de la prosa existe en la tabla).
4. **«Preguntale al informe»** — dos preguntas, siempre las mismas:
   - «¿Cuántos comparables se usaron y cuál es el valor por metro cuadrado?» → responde
     con citas `[N]` y `[V]`; la primera pregunta a un informe tarda ~1–2 min (embebe el
     informe), las siguientes 1–3 s.
   - «¿Qué color tiene la puerta del edificio?» → «Eso no está en este informe», en
     milisegundos y sin llamar al modelo.
5. **Un caso sin comparables** — `Av. Libertador 9000, Núñez` (barrio sin corpus en la
   demo) → la pantalla que **explica** por qué no hay precio, no un error.
6. **`/comparables`** — el texto del aviso al lado de lo extraído, filtros que son
   links, el botón de reportar una extracción incorrecta.
7. **`/calidad`** — la comparación contra el baseline arriba; los evals de componente
   leídos por la mediana.
8. **`/admin/usuarios` y `/admin/organizacion`** — alta de usuario, API key.

### 2.3 bis Qué direcciones probar (con los datos que hay hoy)

El corpus real cubre **dos barrios y un tipo de propiedad**: departamentos en Palermo,
con densidad, y en Belgrano, apenas. Todo lo demás termina en `INSUFFICIENT_DATA` por
diseño. Medido sobre la base local el 19/09/2026 (avisos vigentes en USD con ficha
extraída):

| Barrio | Tipo · ambientes | Avisos | m² típicos | USD/m² mediano | Qué esperar |
|---|---|---|---|---|---|
| Palermo | departamento · 2 | 1.268 | 45 | 3.720 | `ALTA`, 45–55 de 60 comparables, sin relajar |
| Palermo | departamento · 1 | 967 | 32 | 3.556 | `ALTA` |
| Palermo | departamento · 3 | 905 | 70 | 3.360 | `ALTA` |
| Palermo | departamento · 4 | 619 | 119 | 3.551 | `ALTA` |
| Palermo | departamento · 5 | 101 | 165 | 3.600 | `ALTA` o `MEDIA` |
| Palermo | departamento · 6–7 | 44 | 210–240 | 3.400 | `MEDIA`, con relajación |
| Palermo | PH · 3 | 10 | 74 | 2.343 | `BAJA` o `INSUFFICIENT_DATA` |
| Belgrano | departamento · 1–4 | 14–27 por ambientes | 42–138 | 2.540–2.800 | `MEDIA`/`BAJA`, la escalera se relaja a linderos |
| cualquier otro barrio | — | 0 | — | — | `INSUFFICIENT_DATA` con el desglose |

Direcciones que sirven para probar (calles reales; el número es solo para geocodificar
la cuadra, no hace falta que exista el edificio):

```
Palermo, el caso fácil     Gorriti 5000 · 3 amb · 70 m²        → ALTA, ~50 comparables
Palermo, chico             Thames 1800 · 1 amb · 32 m²          → ALTA
Palermo, grande            Av. Santa Fe 4200 · 4 amb · 120 m²   → ALTA
Palermo, borde del corpus  Charcas 3400 · 6 amb · 200 m²        → MEDIA, escalera relajada
Belgrano                   Av. Cabildo 2500 · 3 amb · 78 m²     → MEDIA/BAJA, pocos comparables
Belgrano, chico            Vuelta de Obligado 2200 · 1 amb · 42 m² → BAJA o sin datos
Sin corpus                 Av. del Libertador 9000, Núñez · 3 amb → INSUFFICIENT_DATA
Sin corpus                 Av. Rivadavia 6000, Caballito · 2 amb → INSUFFICIENT_DATA
```

Con el **corpus demo** (581 avisos sintéticos: ~350 de Palermo y ~240 de Belgrano, 1 a 5
ambientes) las dos primeras zonas funcionan parecido entre sí y con menos comparables
(45 de 60 en Palermo); no hay PH ni barrios fuera de esos dos.

Para ver el efecto de los datos del sujeto, generá el mismo caso dos veces: primero
solo con dirección, tipo, ambientes y metros; después desplegando «Tengo más datos» con
estado *a refaccionar*, contrafrente y piso bajo. El valor tiene que bajar y la confianza
puede cambiar; los comparables descartados por ajuste excesivo aparecen con su motivo.

### 2.4 Lo mismo por API (para el panel de una inmobiliaria)

```powershell
$k = "tsk_live_..."      # la que imprimió crear_usuario
$h = @{ Authorization = "Bearer $k"; "Content-Type" = "application/json" }
# crear
$r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/reports -Headers $h -Body (@{ address_raw = "Gorriti 5000"; property_type = "departamento"; rooms = 3; surface_total = 70 } | ConvertTo-Json)
$r.id
# estado (repetir hasta SUCCEEDED)
Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/reports/$($r.id)" -Headers $h | Select-Object status, confidence, comparables_used
# preguntar
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/reports/$($r.id)/ask" -Headers $h -Body (@{ pregunta = "Cuantos comparables se usaron?" } | ConvertTo-Json)
```

El contrato completo está en [doc 06](../06-api-contrato.md). Desde Git Bash, `curl`
manda el JSON en cp1252 y la API devuelve 400 con tildes en el cuerpo: usá PowerShell
o Python.

### 2.5 Lo mismo por línea de comandos (sin front ni API)

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm api python scripts/run_report.py --direccion "Gorriti 5000" --amb 3 --m2 70
```

Imprime la traza de `core.report_events` nodo por nodo, con milisegundos y costo, y el
resultado. Es la forma más rápida de ver **dónde** se cae algo.

---

## 3. Los tests automáticos

### 3.1 Todo el CI, en local

```powershell
.\make.ps1 ci              # bash ops/ci-local.sh
.\make.ps1 ci-imagenes     # + build de las tres imágenes, "la imagen es el repo" y trivy
bash ops/ci-local.sh --rapido   # solo lint · formato · tipos · tests
```

Corre exactamente los pasos de `.github/workflows/ci.yml`, en el mismo orden, contra
una base **aparte** (`tasador_test`, creada vacía en el Postgres del stack): lint,
formato, `mypy --strict`, tests con cobertura mínima, la comprobación de que ningún test
de base se salteó, migraciones sobre base vacía + `alembic check`, aislamiento entre
tenants, gitleaks (la misma imagen que el CI) y la auditoría de publicación. Si esto
está en verde, GitHub va a estar en verde: el push es confirmación.

Los tests solos, cuando iterás sobre algo puntual:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://tasador:<POSTGRES_PASSWORD>@127.0.0.1:5433/tasador_test"
uv run pytest tests/test_critic.py -q
uv run pytest -m "not live" -q          # todos, ~3 min
```

Sin `DATABASE_URL` se saltean 58 tests y el comando sale verde igual: por eso
`make test` se niega a correr sin ella.

### 3.2 Las pantallas, con Playwright

```powershell
.\make.ps1 dev                     # el stack tiene que estar levantado, con datos
.\make.ps1 e2e                     # 57 tests, ~5 min, contra http://127.0.0.1:$WEB_PORT
$env:E2E_CON_AUTH = "1"; .\make.ps1 e2e   # además el login real (stack con TASADOR_ORG_SLUG vacío)
```

No se mockea la API, a propósito: un test de UI contra una API falsa prueba que el
componente renderiza lo que el mock devuelve. Por eso necesitan el stack levantado,
corren en serie (comparten la base) y sin reintentos. Los tests que necesitan un
informe `SUCCEEDED` se saltean si no hay ninguno: generá uno antes (§2.3 paso 2).

Si después de actualizar dependencias la suite da "browser not found":
`cd web; npx playwright install chromium`.

En GitHub corre como job aparte (`Pantallas`) contra un stack levantado con Docker
Compose y el corpus demo; sin clave de modelo, así que los informes que necesitan IA no
se generan ahí y esos tests se saltean.

### 3.3 Los evals (mediciones, no gates)

```powershell
$env:DATABASE_URL = "postgresql+psycopg://tasador:<PASS>@127.0.0.1:5433/tasador"; $env:LITELLM_BASE_URL = "http://127.0.0.1:4000"; $env:PYTHONUTF8 = "1"
uv run python scripts/run_backtest.py --dataset VIGENTES --sample 300 --seeds 42,43,44          # el precio
uv run python scripts/eval_retrieval.py --sistemas A,D,E --avisos 60                             # la recuperación (con juicios guardados)
uv run python scripts/eval_qa.py --corridas 3                                                    # «Preguntale al informe»
uv run python scripts/eval_extraccion.py                                                         # extracción contra el golden set
```

Cómo leerlos está en [doc 09](../09-evaluacion-y-backtest.md) y en el
[informe del 18/09](../informes/2026-09-18-rag-resultados.md). Regla de la casa: ninguna
diferencia menor que la amplitud entre semillas (1,5–2 pp de MdAPE, 17 pp entre corridas
de extracción) se reporta como mejora.

---

## 4. Cuando algo falla, dónde mirar

| Síntoma | Dónde mirar | Qué suele ser |
|---|---|---|
| El informe queda en `FAILED` | `run_report.py` (§2.5) o `select node, status, detail from core.report_events where report_id=...` | `BARRIO_NO_RESUELTO` (dirección sin barrio), proveedor de modelos caído, clave inválida |
| `SUCCEEDED` pero sin narrativa | eventos `critic`: `veredicto`, `hallazgos` | El crítico rechazó tres veces, o se cortó (`max_tokens`, timeout) |
| «Preguntale» devuelve 400 | el cuerpo del request | Encoding del `curl` de Git Bash; usar PowerShell o Python |
| Tests que se saltean | `DATABASE_URL` | No estaba definida, o apunta a una base sin migrar |
| El worker no toma trabajos | `docker compose logs worker` | `conn_timeout` de Redis, contraseña, o el worker viejo sigue vivo |
| Puertos que "no responden" desde el host con contenedores sanos | `docker compose restart <servicio>` | El proxy de puertos de Docker Desktop tras un reinicio de WSL |
| `make ci` falla en gitleaks | la salida dice archivo y línea | Un valor real donde iba un placeholder; `.gitleaks.toml` lista las formas permitidas |

Más en [manual §8](manual.md#8-cuando-algo-no-anda) y en
[ESTADO §2.2](../ESTADO-2026-09-18.md).
