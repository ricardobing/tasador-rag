# S9 — Infraestructura y CI

**Alcance:** `docker-compose*.yml`, `docker/`, `.github/workflows/ci.yml`,
`caddy/`, `Makefile`, `make.ps1`, `ops/`.
**Método:** levantar la imagen de **producción** y compararla contra el repo;
correr los pasos del CI a mano, porque el CI nunca los corrió.

> Esta es la sección con los hallazgos más graves de la auditoría. El resumen en
> una línea: **nada de lo que está en el repo se ha desplegado nunca, y el
> mecanismo que debería avisarlo no existe.**

---

### H-46 · El CI nunca corrió: cero commits, sin remoto, y dispara en una rama que no es la actual

**Sección:** S9 · **Severidad:** alta

**Qué está mal:** el repositorio no tiene ni un commit, no tiene remoto, y el
workflow dispara en `main` mientras la rama activa es `master`.

**Cómo lo verifiqué:**

```
$ git log --oneline -5
fatal: your current branch 'master' does not have any commits yet
$ git rev-list --all --count
0
$ git branch -a
(vacío)
$ git remote -v
(vacío)
```

Y el disparador (`ci.yml:3-5`):

```yaml
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
```

**Por qué importa:** todo lo que ESTADO y los informes dicen sobre el CI —los
gates de aislamiento, el build de las imágenes, el escaneo de secretos con
gitleaks, Trivy, el `--cov-fail-under`— **nunca se ejecutó una sola vez**. No
están rotos ni funcionando: son un archivo YAML. Y el día que se haga el primer
commit, si es sobre `master`, tampoco va a correr.

Y hay un costo más inmediato: **no hay historia.** 26 entradas sin versionar,
~13.000 líneas de código y 20 documentos que existen solo en el árbol de
trabajo. Un `git clean -fd` mal tipeado, o el disco de la máquina, y no hay a
qué volver. Todos los informes de este proyecto hablan de "el arreglo del 14/08"
sin que exista un diff que lo muestre.

**Qué hay que hacer:** en este orden, y antes que cualquier otro ítem del
backlog.
1. `git add -A && git commit` con el estado actual. Verificar antes que
   `git status --ignored` no traiga `.env` (hoy está bien ignorado) ni
   `data/raw/`.
2. Crear el remoto y pushear.
3. Alinear el disparador: renombrar la rama a `main` (`git branch -m master main`)
   o cambiar `ci.yml` a `[main, master]`. Lo primero.
4. Correr el CI y **arreglar lo que salga en rojo** (ver H-47), no desactivarlo.

**Cómo se verifica que quedó bien:** un run del CI en verde en GitHub, con los
cinco jobs. No "el YAML está bien": un run.

**Riesgo de tocarlo:** el primer commit va a incluir `data/` y `test-results/`
si no se revisa `.gitignore` — hoy `test-results/` de la raíz **no** está
ignorado (solo `web/test-results/`). Y gitleaks va a correr por primera vez
sobre todo el árbol: puede encontrar algo en `data/raw/cdp-profile/`, que es un
perfil de Chrome completo dentro del repo.

---

### H-47 · Dos pasos del CI no pueden pasar, con el código de hoy

**Sección:** S9 · **Severidad:** media

**Qué está mal:** corrí a mano los pasos del job `quality`. Dos fallan de
manera determinística.

**Cómo lo verifiqué:**

```
$ uv run ruff check src tests scripts migrations      ✅ All checks passed
$ uv run ruff format --check src tests scripts        ✅ 116 files
$ uv run mypy src                                     ✅ 66 archivos

$ uv run pytest -m "not live" --cov=tasador --cov-fail-under=60
ERROR: Coverage failure: total of 56 is less than fail-under=60
FAIL Required test coverage of 60% not reached. Total coverage: 55.67%
366 passed, 2 skipped in 181.26s

$ uv run alembic upgrade head && uv run alembic check     # sobre una base nueva
FAILED: New upgrade operations detected:
  [('remove_index', Index('idx_listing_embeddings_hnsw', ...))]
```

**Por qué importa:** el comentario del YAML dice que 60% *«es lo que hay hoy más
un poco: es un piso para que no BAJE»*. Hoy hay 55,67%, así que el piso está por
encima del suelo. No es un problema de calidad —es un número mal puesto— pero
significa que el primer run del CI va a salir en rojo por dos motivos que no
tienen que ver con el cambio que lo disparó, y ese es el escenario en que la
gente desactiva gates.

**Qué hay que hacer:**
1. Arreglar la deriva de alembic (H-32 de S5): declarar el índice HNSW en el
   modelo. Es la corrección correcta; bajar el gate no.
2. Para la cobertura, elegir **una** y decirlo en el YAML: bajar el umbral a 55
   con el compromiso de subirlo, o subir la cobertura de los módulos que hoy
   están en 0% (`agents/runner.py`, `eval/backtest.py`, `eval/run.py`,
   `eval/componentes.py`, `sources/playwright_fetcher.py` — ver S11).
   Recomiendo lo segundo para `runner.py` y `eval/run.py`, que son código que
   corre en producción, y lo primero como transición.

**Cómo se verifica que quedó bien:** los cinco jobs del CI en verde en un run
real.

**Riesgo de tocarlo:** ninguno; son los gates.

---

### H-48 · La imagen que se desplegaría hoy no tiene cinco endpoints y trae doce archivos viejos

**Sección:** S9 · **Severidad:** alta

**Qué está mal:** las imágenes `ghcr.io/ricardobrossard/tasador-api:dev` y
`-worker:dev` —las que `docker-compose.yml` levanta en producción— se
construyeron hace 7 horas y **son anteriores a casi todo lo del 14/08 tarde y
noche**. En desarrollo no se nota porque el override monta `./src` por volumen.

**Cómo lo verifiqué:** comparando hash por hash el `src/` de la imagen contra el
del repo.

```
$ docker exec … python -c "hashlib.sha256 de cada .py de /app/src"  vs. el repo
archivos .py en el repo  : 66
archivos .py en la imagen: 63
AUSENTES en la imagen    : 3
   FALTA    tasador/v1/admin.py
   FALTA    tasador/v1/calidad.py
   FALTA    tasador/v1/inventory.py
DISTINTOS                : 12
   tasador/agents/nodes/dedup.py       tasador/ingest/csv_scan.py
   tasador/agents/nodes/retrieve.py    tasador/main.py
   tasador/ingest/core.py           tasador/security.py
   tasador/eval/backtest.py            tasador/sources/portal_a.py
   tasador/eval/run.py                 tasador/v1/auth.py
   tasador/ingest/portal_a_ingest.py  tasador/v1/reports.py
IGUALES                  : 51
```

Y el efecto, levantando la imagen de producción en el puerto 8099:

```
--- codigo del REPO (dev, con bind mount)     --- IMAGEN de produccion
    /v1/health              200                   /v1/health              200
    /v1/calidad             200                   /v1/calidad             404  <-- NO EXISTE
    /v1/admin/fuentes       200                   /v1/admin/fuentes       404  <-- NO EXISTE
    /v1/admin/organizacion  200                   /v1/admin/organizacion  404  <-- NO EXISTE
    /v1/admin/usuarios      200                   /v1/admin/usuarios      404  <-- NO EXISTE
    /v1/inventory/snapshot  405 (POST)            /v1/inventory/snapshot  404  <-- NO EXISTE
```

El `auth.py` de la imagen **no tiene el rate limit del login**:

```
$ docker exec … python -c "'_excedido' in auth.py"
imagen : c6603796f83a7696  9977 bytes   tiene rate limit: False
repo   : bbe335a57bc6ab87 11612 bytes
```

Y el worker está igual de atrás: `dedup.py`, `retrieve.py` y `ingest/core.py`
difieren, o sea que le faltan la clave por cuadra del dedup, el filtro de
cluster del nodo 2 y `CAMPOS_DEL_RAW` (el mapeo de cocheras y orientación).

**Por qué importa:** todo lo que ESTADO §5.2 declara ✅ —`/calidad`,
`/admin/*`, `/inventory/snapshot`, el rate limit del login— **no existe en lo
que se desplegaría**. Es la forma generalizada de la lección del 14/08 ("cinco
arreglos vivían solo en el override de dev"), y esta vez no son cinco variables
de entorno: son cinco endpoints y doce archivos.

La causa es simple y por eso da más miedo: `docker compose up -d` **sin
`--build`** reutiliza la imagen que ya está, y no hay despliegue, ni registro, ni
nada que compare el tag con el código.

**Qué hay que hacer:**
1. **Un gate que compare la imagen con el fuente.** Es la pieza que falta y la
   que impide que vuelva a pasar: un test en `tests/architecture/` —o mejor, un
   paso del CI después del build— que corra el hash de cada `.py` dentro de la
   imagen contra el del repo y falle ante cualquier diferencia. La sonda de
   arriba (`docs/auditoria/sondas/`) ya lo hace; hay que convertirla en un script de `ops/`.
2. **Estampar el commit en la imagen** (`ARG GIT_SHA` → `ENV TASADOR_GIT_SHA`) y
   exponerlo en `GET /v1/health`. Sin eso, "qué versión está corriendo" no tiene
   respuesta.
3. `make up` ya usa `--build`; el problema es la instrucción de ESTADO §8, que
   sí lo usa, y el `docker compose up -d` a secas que se escribe de memoria.
   Documentar que el deploy es `make up`, no `up`.

**Cómo se verifica que quedó bien:**

```
docker compose -f docker-compose.yml up -d --build
bash ops/verificar_imagen.sh          # el gate nuevo: 0 diferencias
curl -s localhost:8000/v1/health | jq .git_sha    # el commit que corre
curl -s -o /dev/null -w '%{http_code}' localhost:8000/v1/calidad   # 401, no 404
```

**Riesgo de tocarlo:** reconstruir e implantar la imagen nueva enciende de golpe
todo lo del 14/08 en producción, incluido el filtro de cluster del nodo 2 —que
hoy está mal (H-12)—. **El orden importa: H-12 antes que el redeploy**, o el
primer informe en producción va a usar los clusters encadenados.

---

### H-49 · `make backup` y `make seed` no pueden correr: la imagen no tiene ni los archivos ni `pg_dump`

**Sección:** S9 · **Severidad:** alta

**Qué está mal:** los dos objetivos corren dentro del contenedor `api`, y la
imagen de la API **no copia `scripts/` ni `ops/`, y no tiene `pg_dump`
instalado**.

**Cómo lo verifiqué:**

```
$ docker run --rm --entrypoint sh ghcr.io/ricardobrossard/tasador-api:dev -c "…"
  /app:      alembic.ini  config  migrations  prompts  src  templates
  scripts/:  ls: cannot access '/app/scripts': No such file or directory
  ops/:      ls: cannot access '/app/ops': No such file or directory
  pg_dump:   NO ESTA
  bash:      /usr/bin/bash
```

`docker/api.Dockerfile` copia `src config prompts templates migrations alembic.ini`
y nada más. `docker/worker.Dockerfile` sí copia `scripts/` y `ops/crontab`, pero
**tampoco tiene `pg_dump`**.

Los comandos afectados:

| Documentado en | Comando | Falla porque |
|---|---|---|
| `Makefile:50` | `make seed` → `compose run api python scripts/seed.py` | no hay `/app/scripts` |
| `Makefile:68` | `make backup` → `compose run api bash ops/backup.sh` | no hay `/app/ops` **ni** `pg_dump` |
| ESTADO §8 | `docker compose exec api python scripts/seed.py` | ídem |
| informe 14/08 §8 | `docker compose exec api python scripts/seed.py` | ídem |

**Por qué importa:** el backup. `ops/backup.sh` está bien escrito —tiene
`--verify` que restaura en una base descartable y compara conteos, y el chequeo
de que un dump de menos de 1 KB no es un backup— y **no puede ejecutarse por el
camino documentado**. Es literalmente lo que el propio archivo dice en su
cabecera: *«Un backup que se cree que corre es peor que uno que no existe — la
diferencia se descubre el día que hace falta restaurar»*. El archivo se escribió
para arreglar eso y el comando sigue roto, una capa más adentro.

Y hay un gate que debería haberlo visto:
`tests/architecture/test_entrypoints.py::test_los_shell_scripts_invocados_existen`
verifica que `ops/backup.sh` **exista en el repo**. Existe. Lo que no verifica es
que el contenedor que lo corre lo tenga. **El gate comprueba lo que no falla.**

**Qué hay que hacer:**
1. `docker/api.Dockerfile`: agregar `COPY --chown=app:app scripts/ ./scripts/`
   y `COPY --chown=app:app ops/ ./ops/`.
2. Instalar `postgresql-client` en la imagen que corra el backup — o, mejor,
   cambiar `make backup` para que use la imagen oficial de Postgres, que ya está
   descargada y trae `pg_dump` de la versión exacta:
   ```makefile
   backup:
       $(COMPOSE) exec -T postgres bash /ops/backup.sh
   ```
   con `./ops:/ops:ro` montado en el servicio `postgres`.
3. Extender `test_entrypoints.py`: para cada comando de la forma
   `compose run/exec <servicio> <algo>`, verificar que el archivo esté **entre
   los que el Dockerfile de ese servicio copia**. Es análisis estático del
   Dockerfile, igual que el que ya hace `test_contenedor.py`.

**Cómo se verifica que quedó bien:**

```
make backup            # produce un .dump y su .sha256
bash ops/backup.sh --verify
# ">> verificado: el dump restaura y los conteos coinciden"
```
Y `make seed` tiene que terminar en 0.

**Riesgo de tocarlo:** `--verify` crea y borra una base `verify_<sello>` en el
Postgres de producción. Es una escritura: **no la corrí en esta auditoría**. La
sesión 2 debería correrla una vez, a mano, mirando.

---

### H-50 · `.env.example` rompe el arranque en producción por un comentario en la misma línea

**Sección:** S9 · **Severidad:** media

**Qué está mal:** `docker --env-file` **no** interpreta comentarios al final de
una línea: se los lleva como parte del valor. `.env.example` tiene exactamente
una línea así, y es `ENV`.

**Cómo lo verifiqué:**

```
$ grep -nE "^[A-Z_]+=.*\S+\s+#" .env.example
ENV=development                  # development | production
```

```
$ docker run --rm --env-file .env … --entrypoint python …tasador-api:dev \
    -c "from tasador.settings import get_settings; print(get_settings().env)"
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
env
  Input should be 'development', 'test' or 'production'
  [type=literal_error, input_value='development                 ...evelopment | production']
```

**Por qué importa:** `docker-compose.yml` (producción) le pasa `env_file: [.env]`
a `api`, `worker`, `migrate` e `ingest`, **sin** un `environment: ENV:` que lo
pise. Los cuatro contenedores mueren al arrancar. En desarrollo no pasa porque
`docker-compose.dev.yml` declara `ENV: development` en el bloque `environment`,
que gana sobre el `env_file`.

O sea: **un despliegue limpio siguiendo la documentación no levanta**. Falla
fuerte y ruidoso —crash loop con un mensaje claro— que es el mejor modo de falla
posible, pero es un bloqueante.

**Qué hay que hacer:** mover el comentario a su propia línea en `.env.example`
(y en `.env`):

```
# development | production
ENV=development
```
Y un test que recorra `.env.example` y falle ante cualquier
`^[A-Z_]+=.*\S+\s+#`. Son tres líneas y cierra la clase entera de error.

**Cómo se verifica que quedó bien:**

```
docker run --rm --env-file .env.example --entrypoint python …tasador-api:dev \
  -c "from tasador.settings import get_settings; print(get_settings().env)"
# imprime 'development', no un ValidationError
```

**Riesgo de tocarlo:** ninguno.

---

### H-51 · `make lint` y `make test` son más flojos que el CI

**Sección:** S9 · **Severidad:** baja

**Qué está mal:** los objetivos del Makefile —lo que una persona corre antes de
dar algo por bueno— no coinciden con los del CI.

| | Makefile | CI |
|---|---|---|
| lint | `ruff check src tests scripts` | `… src tests scripts migrations` |
| test | `pytest -m "not live"` sin `DATABASE_URL` | con `DATABASE_URL` + paso que falla si hubo skips |

**Cómo lo verifiqué:**

```
$ uv run pytest -m "not live" --junitxml=…        # SIN DATABASE_URL
SIN DATABASE_URL -> tests=368  skipped=58  failures=0  errors=0
```

**58 de 368 tests se saltean** con `make test`, y salen en verde. Entre ellos,
todo el bloque de aislamiento multi-tenant en runtime y el contrato HTTP.

`migrations/` fuera del lint es exactamente el problema que el 14/08 se arregló
en el CI —donde había acumulado 11 errores que nadie veía— y que quedó sin
arreglar en el Makefile, que es de donde salía la costumbre.

**Qué hay que hacer:** que `make lint`, `make test` y `make typecheck` corran
**los mismos comandos** que `ci.yml`, incluyendo el chequeo de skips. Lo más
robusto es al revés: que el CI invoque `make lint` / `make test`, y que exista un
solo lugar donde esos comandos estén escritos.

**Cómo se verifica que quedó bien:** `make test` sin `DATABASE_URL` tiene que
FALLAR con un mensaje que diga qué falta, no salir verde con 58 skips.

**Riesgo de tocarlo:** obliga a tener Postgres arriba para correr los tests. Es
el costo correcto, y `conftest.py` ya deja el camino: sin `DATABASE_URL` se
saltea con un mensaje explícito — lo que falta es que `make test` la exija.

---

## 2. Lo que verifiqué y está bien

- **Las cinco variables de entorno del bug #23 están en las dos imágenes.**
  Verificado con `docker image inspect`: `XDG_CACHE_HOME`, `XDG_DATA_HOME`,
  `HOME`, `CREWAI_DISABLE_TELEMETRY`, `CREWAI_TRACING_ENABLED`, más
  `PYTHONPATH=/app/src` y `USER=app` en las dos.
- **Ninguna imagen corre como root** y las dos declaran `USER app` (uid 10001).
- **Los healthchecks funcionan**: los cinco contenedores están `healthy`, y el de
  LiteLLM usa Python en vez de curl porque la imagen no lo trae.
- **`docker-compose.yml` no publica puertos de Postgres ni de Redis**; solo
  Caddy toca la red externa.
- **`web` en producción corre `read_only: true`** con tmpfs acotado.
- **El tag propio de `web` en dev** (`tasador-web:dev-local`) evita la colisión
  de targets que produjo el `next: not found`.
- **`.env` está bien ignorado** (`git check-ignore` lo confirma) y los logs no
  filtran secretos (S7).
- **`ops/crontab` existe** y el `COPY` del worker lo incluye.

## 3. Qué NO pude verificar

- **El backup de verdad.** No pude correrlo por el camino documentado (H-49), y
  correrlo por otro escribe en la base. **La restauración sigue sin probarse
  nunca**, que es exactamente lo que ESTADO §2 declara como pendiente de la
  Etapa 6. Es el ítem que yo pondría primero de los que quedan sin verificar.
- **Caddy.** No corre en desarrollo (`profiles: donotstart`). Las cabeceras y el
  proxy los leí del `Caddyfile`; el comportamiento real de la cadena
  `cliente → Caddy → uvicorn` no lo probé (ver H-42).
- **El servicio `ingest`** (`supercronic /app/ops/crontab`): está detrás del
  perfil `ingest` y no se levanta. No lo probé.
- **Trivy y gitleaks**: son pasos del CI que nunca corrió. No sé qué van a
  encontrar. `data/raw/cdp-profile/` (un perfil de Chrome completo, con
  extensiones) está en el árbol e ignorado por `.gitignore`, pero gitleaks corre
  con `fetch-depth: 0` sobre la historia, que hoy no existe.

