# Manual de uso — Tasador

**Para Ricardo.** Qué hacer, en qué orden, y qué significa cada número que sale.

No es documentación de arquitectura: para eso están
[17](../17-arquitectura-viva.md) y [04](../04-pipeline-de-agentes.md). Esto es
la lista de cosas que vas a hacer.

---

## 1. Prender todo

```powershell
cd .

# El 3000 suele estar reservado por Hyper-V en Windows. Si el contenedor `web`
# no levanta con "forbidden by its access permissions" y NO hay nada
# escuchando en 3000, es esto:
#   netsh interface ipv4 show excludedportrange protocol=tcp
$env:WEB_PORT="3300"

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose ps
```

Los seis servicios tienen que quedar `Up`, y `api`, `web`, `postgres`, `redis`
y `litellm` en `(healthy)`.

| Dónde | Qué |
|---|---|
| http://127.0.0.1:3300 | el producto |
| http://127.0.0.1:8000/docs | la API, con su documentación interactiva |
| http://127.0.0.1:8000/v1/ready | ¿está todo conectado? |

**Si `web` queda reiniciando con `next: not found`**, es el volumen de
`node_modules` que quedó viejo:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml rm -sfv web
```

Para bajar todo: `docker compose down`. **`down -v` borra la base**, no lo uses
sin querer.

---

## 2. Entrar

No hay registro público: los usuarios los crea un admin, que sos vos.

```powershell
# La contraseña se pide por prompt, NO por argumento: un `--password` queda en
# el historial del shell y en `ps`.
uv run python scripts/crear_usuario.py --email vos@inmobiliaria.com.ar --rol owner

# O que la genere y te la muestre una vez:
uv run python scripts/crear_usuario.py --email otro@inmobiliaria.com.ar --generar-password

uv run python scripts/crear_usuario.py --listar
```

Roles: `owner`, `admin`, `agent`, `viewer`.

**Desde el 14/08 esto también se hace por la web**, sin SSH: un owner o admin
entra a **`/admin/usuarios`** y da de alta ahí. Si no escribe contraseña, el
sistema genera una y la muestra UNA vez. Desactivar a alguien corta su sesión
al instante — la API relee al usuario de la base en cada request.

El login tiene rate limit: **5 intentos por minuto** por IP y por email; el
sexto da 429 y hay que esperar un minuto.

### Para que otro sistema use la API (el panel de la inmobiliaria)

Por la web: **`/admin/organizacion` → Crear API key**. O por script:

```powershell
uv run python scripts/crear_usuario.py --api-key "panel de la inmobiliaria"
```

**La clave se muestra UNA vez y no se guarda en claro.** Si se pierde, se
revoca y se emite otra — es la única respuesta honesta. Se usa así:

```bash
curl -H "Authorization: Bearer tsk_live_..." http://127.0.0.1:8000/v1/reports
```

### Modo sin login, para desarrollo

`docker-compose.dev.yml` trae `TASADOR_ORG_SLUG=inmo-demo`, que deja entrar sin
usuario. Para probar el login de verdad, levantá con la variable vacía:

```powershell
$env:TASADOR_ORG_SLUG=""
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d web
```

En producción esa variable no existe y **la API ignora el header aunque
alguien la defina**. Hay un test que lo impone.

---

## 3. Generar un informe

Por la web: **Nuevo → dirección + tipo → Generar**. Con eso alcanza. El bloque
"Tengo más datos" es opcional a propósito: antes de la visita sabés poco,
después sabés todo, y podés regenerar.

Mientras corre vas a ver el stepper con los diez pasos en castellano. **Tarda
lo que tarda según el barrio:**

| Caso | Tiempo |
|---|---|
| Barrio ya usado antes | ~15 segundos |
| **Primer informe de un barrio nuevo** | **~5 minutos** |

La diferencia es el nodo 4: la primera vez tiene que leer con IA los ~60 avisos
del barrio. Después quedan cacheados para siempre y no se vuelven a pagar.

### Qué mirar del resultado

```
USD 135.888 ── 169.860 ── 203.832        ← el rango sugerido de PUBLICACIÓN
Rango esperado de cierre: 154.700 – 172.900
Confianza ALTA                            ← nunca la ignores
49 comparables usados de 60
```

**El rango de cierre está arriba y no escondido a propósito.** Es el dato que
evita la conversación incómoda tres meses después.

**La confianza no es decorativa**, y está calibrada contra el backtest:

| Nivel | Error mediano real |
|---|---|
| ALTA | 11,1% |
| MEDIA | 24,1% |
| BAJA | 75,6% |

Un informe BAJA no es un informe con un número peor: es un número que **no
deberías usar** sin más datos.

### Cuando dice "No pudimos generar el informe"

**No es un error.** Es el sistema diciendo que no hay con qué, que es la
respuesta correcta cuando no hay comparables. La pantalla te dice qué falta y
qué hacer — normalmente, capturar avisos de ese barrio y esa tipología.

Hoy el corpus solo cubre **Palermo y Belgrano**. Cualquier otro barrio va a dar
esto, y está bien que lo haga.

### Regenerar después de la visita

El flujo real es: informe rápido ANTES de la visita, informe completo DESPUÉS.
En la ficha del informe, **Regenerar** crea un informe NUEVO sobre la misma
propiedad y te lleva al stepper. El anterior no se toca — un informe es un
documento, no una vista. También sirve en un "sin datos" cuando entraron avisos
nuevos al corpus.

### Compartir con el propietario

**Compartir** en la ficha genera un link firmado que vence a los 30 días. El
propietario ve el rango, la narrativa, las limitaciones y el PDF — sin cuenta,
sin la traza ni los costos. Para invalidar TODOS los links emitidos se rota
`SECRET_KEY` (no hay revocación por informe: si algún día hace falta, se
agrega).

---

## 4. Cargar datos nuevos

El recolector externo deja los archivos en `C:\datos\avisos`, en una carpeta
por corrida (qué portal es cada archivo lo declara `PORTALES` en el `.env`;
ver doc 02 §2). **No hace falta que le digas cuáles son:**

```powershell
# Primero mirá qué entraría, sin escribir nada:
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos --dry-run

# Y después de verdad:
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos
```

Escanea subcarpetas, acepta csv/json/jsonl, reconoce los archivos por su
contenido y **es idempotente**: correrlo dos veces no duplica nada. Podés
correrlo cada vez que aparezcan datos.

> ⚠️ **Corré la ingesta después de CADA tanda del scraper, siempre.** El 14/08
> el scraper reescribió un archivo pasándolo de 4.062 a 8.586 avisos, y como
> nadie volvió a ingerir, **4.100 avisos estuvieron en el disco sin entrar**
> mientras dábamos el corpus por completo. La idempotencia es por contenido:
> un archivo reescrito es contenido nuevo y se procesa; uno igual se saltea
> gratis. No hay forma de "correrlo de más".

**Después de cargar, la secuencia completa** (cada paso usa lo del anterior):

```powershell
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos   # 1. cargar
uv run python scripts/dedup_corpus.py --aplicar                          # 2. agrupar duplicados
uv run python scripts/extraer_corpus.py --paralelo 16 --lote 8           # 3. features (~USD 0,25 / 2.500 avisos)
```

El orden importa: el dedup marca los canónicos y la extracción **solo procesa
canónicos**, así que extraer antes de deduplicar paga la misma descripción
tantas veces como la republicaron.

Lo que vas a ver:

```
  archivos nuevos       6
  ya estaban           13     ← mismo sha256, se saltean
  ignorados (ajenos) 1730     ← metadata.json del perfil de Chrome, etc.

  avisos NUEVOS       278
  descartados           9
      sin_precio        8     ← el descarte temprano hace su trabajo
```

⚠️ Si un CSV nuestro sale **VACÍO**, el dry-run te lo dice con ese nombre. No
es un archivo ajeno: es una corrida del scraper que volvió sin nada, casi
siempre por CAPTCHA. Eso hay que mirarlo.

### Ver duplicados

```powershell
uv run python scripts/dedup_corpus.py --barrio Palermo
uv run python scripts/dedup_corpus.py --juez        # resuelve las dudas, ~5 min
uv run python scripts/dedup_corpus.py --juez --aplicar   # y las escribe
```

Un departamento publicado por tres inmobiliarias **pesa triple en la mediana**
si no se detecta. Medido sobre el corpus actual: 584 avisos son 566
propiedades reales, o sea **3,1% de duplicados**.

Las dos primeras capas son gratis y determinísticas. `--juez` resuelve los
pares dudosos con IA y cuesta ~USD 0,05 sobre todo el corpus.

---

## 5. Ver el corpus por dentro — `/comparables`

Es la pantalla que te va a servir más seguido, y la que hace posible mejorar el
sistema.

Muestra **el texto del aviso a la izquierda y lo que el sistema entendió a la
derecha**. Si algo está mal, es evidente de un vistazo.

Los filtros:

| Filtro | Para qué |
|---|---|
| Palermo / Belgrano | por barrio |
| Portal A / Portal B | por portal |
| **sin extraer** | avisos que todavía no leyó la IA |
| **marcados para revisar** | los que ya reportaste |

Debajo de cada aviso dice cuántos de los 5 coeficientes quedaron sin dato. Eso
es **la explicación de por qué un rango sale ancho**: "sin dato, sin ajuste".

El botón **"Reportar extracción incorrecta"** marca el aviso. No lo corrige a
propósito: la corrección es tuya y va al golden set (§6). Si el botón
escribiera el dato "arreglado" en el corpus, las métricas de calidad medirían
el acuerdo del sistema con lo último que alguien tocó, y subirían solas sin que
nada mejore.

---

## 6. Lo único que te toca a vos: anotar

**Esto es lo que hoy bloquea todo lo demás**, y no lo puedo hacer yo. El motivo
es incómodo y hay que decirlo: el golden set actual lo anoté yo leyendo el
mismo texto que lee el modelo. Eso no es una respuesta independiente — sirve
para detectar errores groseros, no para afirmar que el sistema acierta.

```powershell
uv run python scripts/golden_set.py --estado
uv run python scripts/golden_set.py --preparar --barrio Palermo --n 90
```

Te deja `tests/golden/palermo-para-anotar.yaml`. Cada aviso trae su texto y las
pistas encontradas con búsquedas de texto — **no con IA**, para no anclarte a
una respuesta.

Anotás así:

```yaml
  - id: "19548301"
    ref: "Avenida Santa Fe 4400 — USD 69.900"
    esperado:
      condition: null          # el aviso NO lo dice
      orientation: contrafrente
      floor_number: 4
      has_elevator: true
      age_years: null
    no_evaluar: [orientation]  # genuinamente ambiguo: ni acierto ni error
    descarte: null             # o el motivo, si NO sirve como comparable
    revisado: true             # ← SIN ESTO NO CUENTA PARA NADA
```

Tres reglas, y la primera es la que más se equivoca:

1. **`null` significa "el aviso no lo dice".** Que el sistema lo complete es un
   ERROR, no una virtud: cada uno de esos campos mueve un precio real.
2. **Si es genuinamente ambiguo, ponelo en `no_evaluar`.** Declarar la duda es
   mejor que forzar una respuesta y medir contra ella.
3. **`revisado: true` o no cuenta.** Es la garantía de que preparar candidatos
   nunca puede inflar la métrica.

### Cuánto hace falta

Esto no es opinión, es la cuenta:

```
±10 pp de precisión    extracción   16 avisos     curaduría  175 avisos
±5 pp                  extracción   64 avisos     curaduría  695 avisos
```

Con los 24 de hoy, la métrica de extracción se mueve **17,6 puntos entre
corridas idénticas** — es ruido de muestreo, no que el modelo sea inestable.
Por eso ninguna decisión sobre el sistema se puede tomar con una sola corrida.

**Con 60-70 avisos anotados, el número empieza a significar algo.**

> 📖 **La guía completa de anotación, con ejemplos resueltos, está en
> [anotar-golden-set.md](anotar-golden-set.md).** Qué valor va en cada campo,
> qué hacer con los ambiguos, y cómo entra lo anotado a los evals.

La curaduría es más cara porque solo ~20% de los avisos son descartables. El
archivo preparado ya viene con 54 avisos "enriquecidos" (que parecen
descartables) para juntar casos más rápido.

---

## 7. Medir

```powershell
# Extracción: ¿el sistema entiende bien los avisos?
uv run python scripts/eval_extraccion.py --guardar

# Curaduría: ¿descarta lo que hay que descartar?
uv run python scripts/eval_curaduria.py

# Backtest: ¿le gana a una consulta SQL de una línea?
uv run python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300
uv run python -m tasador.eval.run --history --dataset BADATA_2015_2020

# El experimento del motor de ajustes (14/08): mismos casos del corpus VIGENTE,
# con y sin las features del nodo 4. La resta de los MdAPE es el aporte del
# motor. Antes hace falta que el corpus tenga features:
uv run python scripts/extraer_corpus.py --dry-run     # ¿cuántos faltan?
uv run python scripts/extraer_corpus.py --paralelo 16 --lote 8
uv run python -m tasador.eval.run --dataset VIGENTES_SIN_FEATURES --sample 300
uv run python -m tasador.eval.run --dataset VIGENTES --sample 300
```

**Corré los evals de componente 3 veces y mirá la mediana.** Una sola corrida
no dice nada con los golden sets del tamaño actual.

**Todo esto también se ve por la web, en `/calidad`** (solo admin): la
comparación contra el baseline arriba, la serie de backtests, los evals de
componente leídos por la mediana de las últimas 5 corridas, y el error por
barrio. Y **`/admin/fuentes`** muestra la salud de la ingesta —la métrica a
vigilar es `Bloqueados`— y la cobertura con el chequeo de sesgo por barrio.

Lo que buscás en el backtest:

```
              SISTEMA   BASELINE
MdAPE          14,5%      16,2%    ← tiene que GANARLE
Cobertura      99,7%     100,0%    ← se leen juntas
Hit rate       60,9%          —    ← objetivo 70-85%
```

Un MdAPE espectacular con cobertura baja es un sistema inútil. Se miran juntas
siempre.

---

### 7.1 La recuperación y el RAG (doc 18)

Tres comandos, en este orden la primera vez:

```powershell
# 1. Indexar el corpus (descarga el modelo la primera vez, 2,2 GB; después es
#    incremental: sin cambios escribe 0 filas). En CPU tarda cerca de una hora
#    por cada 8.000 avisos.
uv run python scripts/embed_corpus.py --chunker C
uv run python scripts/embed_corpus.py --chunker A       # la línea de base "truncar"

# 2. La tabla de ablación: A es lo de hoy; B-F son las variantes semánticas.
#    --juzgar hace que el propio pipeline juzgue el pool (nodos 4 a 7).
uv run python scripts/eval_retrieval.py --sistemas A,B,C,D,E,F --juzgar --avisos 60 --guardar

# 3. «Preguntale al informe»: rechazo, citas y cifras, sobre el último informe
uv run python scripts/eval_qa.py --corridas 3 --detalle
```

La recuperación semántica está **apagada** en `config/agents.yaml`
(`semantic.enabled: false`) hasta que la tabla cumpla el criterio de doc 18
§4.4. Encenderla sin haber indexado no rompe nada: los avisos sin chunks
quedan detrás de los puntuados, por recencia.

## 8. Cuando algo no anda

| Síntoma | Qué es |
|---|---|
| Informe en `QUEUED` para siempre | El worker no lo tomó: `docker compose logs worker --tail 50` |
| `INSUFFICIENT_DATA` en un barrio que sí tiene datos | Mirá el detalle del informe: probablemente la curaduría descartó de más |
| `web` reiniciando, `next: not found` | Volumen viejo: `docker compose ... rm -sfv web` |
| Todo da 401 | Sesión vencida (dura 12 h) o API key revocada |
| Un informe tarda 5 minutos | Normal la primera vez en un barrio nuevo |
| Scripts que no conectan desde Windows | El `DATABASE_URL` del `.env` apunta a la red de Docker. Desde Windows es `127.0.0.1:5433` |

```powershell
docker compose logs api --tail 50
docker compose logs worker --tail 50
curl http://127.0.0.1:8000/v1/ready
```

**Todo informe deja su traza completa en la base**, nodo por nodo, con tiempo y
costo:

```sql
select node, status, duration_ms, cost_usd, detail
  from core.report_events where report_id = '...' order by seq;
```

Esa tabla es la respuesta a "¿por qué salió esto?", y es la misma de la que
sale el costo que ves en pantalla.

---

## 9. Backups

```powershell
docker compose run --rm api bash ops/backup.sh
docker compose run --rm api bash ops/backup.sh --verify   # restaura y compara
```

**Usá `--verify` de vez en cuando.** Un backup no verificado no es un backup:
restaura el dump en una base descartable y compara los conteos. Es lento y es
la única forma de saber que el archivo sirve.

---

## 10. Lo que el sistema NO hace, y no es un olvido

- **No es una tasación con validez legal.** Está escrito en cada informe.
- **No inventa un número cuando no hay datos.** Prefiere decir que no puede.
- **El precio nunca sale de una IA.** Sale de una fórmula sobre comparables
  reales (ADR-002), y hay tests que lo imponen — incluido uno que verifica que
  el atajo por variable de entorno tampoco lo saltee.
- **Cada cifra del texto se verifica contra los datos** antes de salir. Si el
  redactor escribe un número que no está en la tabla, el informe no sale.
- **No borra los avisos descartados.** Los muestra con su motivo, para que
  puedas discutirlos.

---

## Referencia rápida

Para probar la app (a mano, por API, tests, Playwright, el CI en local): [probar-la-app.md](probar-la-app.md).


```powershell
# Prender / apagar
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose down

# Usuarios
uv run python scripts/crear_usuario.py --email x@y.com --rol owner
uv run python scripts/crear_usuario.py --api-key "nombre"
uv run python scripts/crear_usuario.py --listar

# Datos
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos --dry-run
uv run python scripts/ingest_csv.py --carpeta C:\datos\avisos
uv run python scripts/dedup_corpus.py --juez

# Golden set
uv run python scripts/golden_set.py --estado
uv run python scripts/golden_set.py --preparar --barrio Palermo --n 90

# Medir
uv run python scripts/eval_extraccion.py --guardar
uv run python scripts/eval_curaduria.py
uv run python -m tasador.eval.run --dataset BADATA_2015_2020 --sample 300

# Verificar que no rompiste nada
uv run pytest -m "not live"
cd web; npm run test:e2e
```
