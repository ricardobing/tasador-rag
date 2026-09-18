# S4 — API v1

**Alcance:** `src/tasador/v1/` contra `docs/06-api-contrato.md`.
**Método:** requests reales contra el stack que está corriendo, y contra la
**imagen de producción** levantada aparte con `ENV=production`.

> ⚠️ **Antes de leer esta sección**: todo lo que verifiqué en `:8000` corre el
> código del repo montado por volumen. La imagen que se desplegaría hoy es otra
> y le faltan cinco endpoints — ver **H-32** en [09-infra-y-ci.md](09-infra-y-ci.md).
> Esta sección audita el CÓDIGO; el que corre en producción es peor.

---

## 1. Lo que está bien

**El aislamiento entre tenants funciona y el header de desarrollo está cerrado
en producción.** Verificado contra la imagen de producción real:

```
$ docker run -d -p 8099:8000 -e ENV=production ... ghcr.io/ricardobrossard/tasador-api:dev
$ curl -s -o /dev/null -w '%{http_code}' -H 'X-Org-Slug: inmo-demo' localhost:8099/v1/reports
401
```

**El manejo de credenciales no filtra información:**

```
sin ningun header            -> 200   (solo en dev: hay UNA org activa)
Bearer invalido              -> 401  {"detail":"No autorizado."}
Bearer sin prefijo           -> 401  {"detail":"No autorizado."}
X-Org-Slug inexistente       -> 401  {"detail":"No autorizado."}
cookie de sesion invalida    -> 401  {"detail":"No autorizado."}
```

Un Bearer inválido **no cae** al header de desarrollo (`auth.py:177`), que era
el agujero obvio.

**La paginación por keyset funciona y el cursor manipulado se rechaza:**

```
primera pagina: 3 items  next_cursor=MjAyNi0wOC0xNFQxOToyMDoyNi45NzE2NjUrMDA6MDB8…
segunda pagina: 3 items  solapamiento=0
cursor manipulado -> 422  {"detail":"Cursor inválido."}
cursor de 5.000 caracteres -> 422
```

**Los inputs hostiles no rompen nada:**

```
/v1/comparables?limit=1&barrio=' or 1=1--     -> 200  {"items":[],...}   (parametrizado)
/v1/reports/../../etc/passwd                  -> 404
/v1/reports/%2e%2e%2f%2e%2e%2fetc%2fpasswd    -> 404
/v1/reports?limit=99999                       -> 422  (le=100)
/v1/reports?limit=-1                          -> 422  (ge=1)
/v1/reports/no-es-un-uuid                     -> 422
```

**No hay CORS abierto:** preflight desde `https://evil.example` devuelve 405 sin
`Access-Control-Allow-Origin`. Es correcto — el front habla con la API
server-side (`web/src/lib/proxy.ts`) y comparten origen detrás de Caddy.

---

### H-26 · Los errores no son RFC 7807, que es lo que el contrato promete

**Sección:** S4 · **Severidad:** media

**Qué está mal:** doc 06 §3 fija *«Formato de error: RFC 7807
`application/problem+json`: `{type, title, status, detail, instance, errors[]}`»*.
Todos los errores salen como `application/json` con el `{"detail": ...}` por
defecto de FastAPI. El único que respeta el contrato es el handler de 500.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s4_api.py
  404  application/json  GET /v1/reports/0000...  {"detail": "No existe ese informe."}
  422  application/json  GET /v1/reports/no-es-un-uuid  {"detail": [{"type": "uuid_parsing", ...}]}
  404  application/json  GET /v1/no-existe        {"detail": "Not Found"}
  422  application/json  POST /v1/reports         {"detail": [{"type": "missing", "loc": ["body","property"], ...}]}
  422  application/json  GET /v1/reports?cursor=basura  {"detail": "Cursor inválido."}
```

`main.py:58-71` sí devuelve `application/problem+json` — pero solo para
excepciones no manejadas.

**Por qué importa:** el consumidor de este contrato es el panel de la inmobiliaria, que
según doc 06 §4 son ~205 líneas de `fetch` tipado. Un cliente escrito contra el
contrato documentado va a buscar `title` y `status` y va a encontrar `detail`,
que a veces es un string y a veces una lista de objetos de pydantic. Y el 422 de
validación **filtra la estructura interna del modelo** (`loc: ["body","property"]`,
tipos de pydantic, el `input` completo que se mandó).

**Qué hay que hacer:** un handler único en `main.py`:

```python
@app.exception_handler(HTTPException)
async def problem(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"type": "about:blank", "title": HTTPStatus(exc.status_code).phrase,
                 "status": exc.status_code, "detail": exc.detail,
                 "instance": str(request.url.path)},
        media_type="application/problem+json", headers=exc.headers)

@app.exception_handler(RequestValidationError)
async def problem_validacion(request, exc):
    # `errors[]` con campo y mensaje, SIN el `input` ni el `ctx` de pydantic.
    ...
```

**Cómo se verifica que quedó bien:**

```
curl -si localhost:8000/v1/reports/00000000-0000-0000-0000-000000000000 -H 'X-Org-Slug: inmo-demo' | head -20
# Content-Type: application/problem+json  y el cuerpo con type/title/status/detail/instance
uv run pytest tests/test_reports_api.py -q
```
Y un test nuevo que recorra una lista de requests que fallan y afirme el
content-type en todas.

**Riesgo de tocarlo:** el front y los tests leen `detail`. Como RFC 7807 conserva
`detail`, no debería romper nada; hay que revisar `web/src/lib/api.ts` y los
tests que hacen `assert r.json()["detail"] == ...`.

---

### H-27 · `GET /v1/reports/{id}` devuelve menos de lo que el contrato documenta

**Sección:** S4 · **Severidad:** media

**Qué está mal:** faltan tres cosas que doc 06 §2 documenta como parte de la
respuesta de un informe terminado.

**Cómo lo verifiqué:**

```
$ uv run python docs/auditoria/sondas/s4b.py
informe 4f3a88f7 — claves de primer nivel:
   ['comparables','confidence','cost_usd','external_ref','generated_at','limitations',
    'methodology_version','narrative_md','progress','prompt_bundle_version','report_id',
    'status','valuation']

  faltan respecto de doc 06 §2: ['market_context', 'pdf_url']
  de más (no documentadas)    : ['cost_usd', 'progress', 'prompt_bundle_version']

  comparables: ['found', 'used']        <- doc 06 documenta `items[]` con 13 campos
  confidence : ['level', 'score']       <- doc 06 documenta `notes[]`
```

**Por qué importa:** `comparables.items[]` es la tabla que justifica el número
—lo que hace al informe auditable— y el contrato dice que viaja en la respuesta.
Hoy la única forma de conseguirla por API es… no hay: está en
`reports.methodology` en la base y no se expone. `pdf_url` es cómo el cliente
sabe que hay PDF, y hoy tiene que adivinar la URL y comerse un 404 (que además,
en producción, siempre es 404 — ver H-10).

`external_ref` se devuelve **siempre en `None`** (`reports.py:494`), aunque doc 06
lo documenta como el identificador con el que el panel correlaciona.

**Qué hay que hacer:** completar la respuesta de `obtener_informe`
(`reports.py:523-550`) con `comparables.items` (desde `report_comparables`, que
ya tiene el snapshot), `pdf_url` cuando exista el artefacto, `confidence.notes`
desde `methodology.notes`, `market_context` (ver H-28) y `external_ref` desde
`subject_properties.external_ref`.

**Cómo se verifica que quedó bien:** `uv run python docs/auditoria/sondas/s4b.py` tiene que
imprimir `faltan respecto de doc 06 §2: []`.

**Riesgo de tocarlo:** agregar campos no es breaking (doc 06 §5). El peso de la
respuesta sube: con 60 comparables son ~40 KB. Vale acotar `items` a los
incluidos y dejar los excluidos detrás de `?incluir=descartados`.

---

### H-28 · El contexto de mercado se calcula, se paga y se tira

**Sección:** S4 · **Severidad:** media

**Qué está mal:** el nodo 8 corre cuatro consultas SQL y una crew de CrewAI, y
deja el resultado en `state["market_context"]`. **Nadie lo persiste.**
`runner._persist_result` guarda `methodology` (solo la valuación) y
`narrative_md`. El contexto vive en memoria hasta que el redactor lo usa y
después desaparece.

**Cómo lo verifiqué:**

```
$ rg -n "market_context" src/tasador
src/tasador/agents/state.py:108      market_context: Annotated[dict, _ultimo]
src/tasador/agents/nodes/market.py   (el nodo)
src/tasador/agents/nodes/write.py:109  "contexto_de_mercado": state.get("market_context")
src/tasador/agents/nodes/__init__.py
```
Ni una aparición en `runner.py`, ni en `v1/`, ni en `db/models.py`.

Y en la base:

```sql
select count(*) filter (where methodology ? 'market_context') from core.reports;
 0
```

**Por qué importa:** tres cosas a la vez.

1. **Doc 06 §2 lo documenta** como una clave de la respuesta, con seis campos
   (`official_usd_m2`, `corpus_usd_m2`, `active_stock`, `price_drops_90d_pct`,
   `median_days_on_market`). No se puede entregar lo que no se guardó.
2. **Se paga.** El nodo 8 son USD 0,0107 acumulados en 36 corridas — poco, pero
   es dinero por un dato que se descarta. Y el chequeo de sesgo contra el GCBA
   (`desvio_vs_oficial_pct`) es, según el propio módulo, *"la comparación que
   hace creíble a todo lo demás"*: hoy solo queda en `report_events.detail`.
3. **Un informe entregado no se puede reconstruir.** El PDF y la narrativa citan
   cifras de mercado que en seis meses ya no van a poder verificarse contra la
   base, porque el corpus cambió y el snapshot no se guardó. Es exactamente lo
   que `report_comparables` sí hace bien para los comparables.

**Qué hay que hacer:** en `runner._persist_result`, guardar el contexto junto a
la metodología:

```python
report.methodology = {**v, "market_context": final.get("market_context") or {},
                      "degraded_nodes": final.get("degraded_nodes") or []}
```
(el mismo cambio que H-15) y exponerlo en `obtener_informe`.

**Cómo se verifica que quedó bien:**

```sql
select methodology->'market_context'->'stock' from core.reports
order by created_at desc limit 1;
-- {"stock_activo": ..., "usd_m2_mediano": ...}
```
y `GET /v1/reports/{id}` tiene que traer la clave.

**Riesgo de tocarlo:** ninguno. `methodology` es JSONB y agregar claves no rompe
a nadie. Los informes viejos quedan sin el dato, y eso es un hecho histórico.

---

### H-29 · El rate limit por API key que documenta doc 06 §1 no existe

**Sección:** S4 · **Severidad:** media

**Qué está mal:** doc 06 §1 dice *«Rate limiting (en Caddy + Redis): 60 req/min
por API key, 600/hora. `429` con `Retry-After`»*. No está en Caddy, no está en
la API, y no está en Redis. Lo único que existe es el limitador del login, en
memoria del proceso.

**Cómo lo verifiqué:** el `caddy/Caddyfile` completo tiene 56 líneas y ningún
directivo de rate limit:

```
$ rg -n "rate|limit" caddy/Caddyfile
(sin resultados)
```

Y contra la API, 8 requests seguidos a `/v1/reports` con la misma credencial: 8
× 200. El único limitador que sí funciona es el del login, verificado contra el
código del repo:

```
$ uv run python -c "... 8 POST /v1/auth/login ..."
[401, 401, 401, 401, 401, 429, 429, 429]
```

**Por qué importa:** un tenant (o alguien con una API key filtrada) puede
encolar informes sin tope. Cada informe cuesta USD 0,03-0,05 y ocupa un worker
por minutos. El único freno es `max_budget: 5` de LiteLLM, que **corta el
sistema entero** cuando se agota, no al que abusó. O sea: el mecanismo de
protección que hay convierte el abuso de un tenant en una caída para todos.

Hay además un `quota` por organización en el modelo de datos (`/admin/organizacion`
muestra "cuota y consumo"): habría que verificar que se aplique en
`POST /v1/reports` — no encontré el chequeo.

**Qué hay que hacer:** dos capas, la segunda es la que importa:
1. En Caddy, `rate_limit` por `{http.request.header.Authorization}` (requiere el
   módulo `caddy-ratelimit`, que no está en la imagen `caddy:2.8-alpine`: hay que
   construir una imagen con `xcaddy` o usar otro mecanismo).
2. **En la API, un chequeo de cuota mensual en `POST /v1/reports`**, que es
   barato (una consulta) y da el `402` que doc 06 §2 ya documenta como error
   posible. Esto no depende de Caddy y protege el gasto, que es lo que duele.

**Cómo se verifica que quedó bien:** con la cuota del tenant en 1, el segundo
`POST /v1/reports` del mes tiene que dar 402. Un test en `tests/test_reports_api.py`.

**Riesgo de tocarlo:** poner una cuota mal calculada bloquea a un cliente real.
Arrancar con el chequeo en modo "avisar" (log + header) antes de hacerlo duro.

---

### H-30 · `GET /v1/usage` está documentado y no existe

**Sección:** S4 · **Severidad:** baja

**Qué está mal:** doc 06 §2 lo lista en "Endpoints de operación" sin marca de
pendiente. Devuelve 404.

**Cómo lo verifiqué:**

```
GET /v1/usage                          -> 404
GET /v1/inventory/snapshot             -> 405   (POST only: correcto)
GET /v1/comparables?limit=1            -> 200
GET /v1/calidad                        -> 200
GET /v1/admin/fuentes                  -> 200
```

**Por qué importa:** poco por sí solo — `/v1/admin/organizacion` cubre casi lo
mismo. Importa como higiene del contrato: doc 17 §3 sí lo marca ⬜ y doc 06 no,
así que los dos documentos que describen la misma API se contradicen.

**Qué hay que hacer:** o implementarlo (son 20 líneas sobre las mismas consultas
de `/admin/organizacion`), o marcarlo ⬜ en doc 06 §2. Recomiendo lo segundo y
apuntar a `/v1/admin/organizacion`.

**Cómo se verifica que quedó bien:** el test
`tests/architecture/test_entrypoints.py` no cubre endpoints HTTP documentados —
agregarle un chequeo que extraiga las rutas `/v1/...` de doc 06 y verifique que
existan en el `openapi.json` de la app.

**Riesgo de tocarlo:** ninguno.

---

### H-31 · El gate estático de aislamiento tiene tres huecos por los que un bug entraría

**Sección:** S4 · **Severidad:** baja (hoy) · el gate es el que importa

**Qué está mal:** `tests/architecture/test_aislamiento.py::_consultas_sin_org`
solo detecta llamadas cuya función sea **exactamente el nombre `select`** y cuyo
argumento sea un modelo de `MODELOS_POR_TENANT`. Quedan afuera:

1. `sa.select(Report)` — el `func` sería un `ast.Attribute`, no un `ast.Name`:
   `if not (isinstance(f, ast.Name) and f.id == "select")` lo descarta.
2. `session.get(Report, id)`, `update(Report)`, `delete(Report)` — no son
   `select`.
3. **Las tablas hijas**: `ReportEvent`, `ReportComparable`, `ReportArtifact` no
   están en `MODELOS_POR_TENANT`, y pertenecen a un tenant por transitividad.

**Cómo lo verifiqué:** el gate pasa en verde hoy, y lo hace legítimamente:

```
$ uv run pytest tests/architecture -q
20 passed
```

```
$ rg -n "sa\.select|session\.get\(|\bupdate\(|\bdelete\(" src/tasador/v1
src/tasador/v1/admin.py:276:@router.delete("/admin/api-keys/{key_id}", ...)   # el decorador, no la query
```

Ninguno de los tres patrones se usa hoy. Pero el tercero **ya está a un paso**:

```python
# reports.py:464-470 — sin filtro de org
eventos = (await session.execute(
    select(ReportEvent).where(ReportEvent.report_id == report_id)...
```
Es seguro **solo porque** doce líneas antes se verificó que el informe es del
tenant (`reports.py:457`). El gate no lo sabe y no lo verifica: si mañana
alguien escribe un endpoint que lee `report_events` por id sin ese chequeo
previo, el gate sigue en verde.

**Por qué importa:** un gate que no cubre las formas en que el bug va a entrar es
la definición de R3. Y el propio archivo lo dice: la parte estática existe *"para
cubrir los endpoints que todavía no existen y los que se agreguen mañana, que es
donde el bug va a entrar"*.

**Qué hay que hacer:**
1. Aceptar también `ast.Attribute` con `attr == "select"` (cubre `sa.select`).
2. Detectar `update(...)`, `delete(...)` y `session.get(Modelo, ...)`.
3. Agregar `ReportEvent`, `ReportComparable`, `ReportArtifact` a
   `MODELOS_POR_TENANT` con una excepción explícita: se acepta si la sentencia
   hace `join(Report)` **y** menciona `org_id`, o si en la misma función hay una
   consulta previa a `Report` con `org_id` (esto último es más frágil; la
   alternativa robusta es exigir siempre el join).
4. Extender `test_la_heuristica_detecta_una_consulta_sin_filtro` con un caso por
   cada forma nueva, para que la heurística siga probándose a sí misma.

**Cómo se verifica que quedó bien:** agregar a mano `select(ReportEvent).where(...)`
sin filtro en un archivo de `v1/` tiene que poner el gate en rojo, y `sa.select(Report)`
sin `org_id` también.

**Riesgo de tocarlo:** el punto 3 puede generar falsos positivos sobre
`reports.py:467`, que hoy es correcto. Hay que resolverlo agregándole el
`join(Report).where(Report.org_id == org.id)` a esa consulta —que además es más
robusto que depender del orden de las líneas— y no aflojando el gate.

---

## 2. Qué NO pude verificar

- **`POST /v1/reports` con inputs hostiles.** Un payload válido **escribe en la
  base y encola un job que gasta**, y esta sesión no hace eso sin autorización.
  Verifiqué solo los que fallan en validación. Lo que falta probar: dirección de
  20.000 caracteres, `property.notes` con una inyección de prompt, y
  `Idempotency-Key` repetida con cuerpo distinto. **Es lo primero que debería
  hacer la sesión 2**, contra una base descartable con la fixture `db`.
- **`POST /v1/inventory/snapshot`** con 1.000 propiedades (el límite de doc 06):
  escribe. Hay 6 tests que lo cubren.
- **`POST /v1/reports/{id}/share` y `/regenerate`**: escriben.
- **La cuota por organización.** No encontré dónde se aplica; puede que esté y
  no la haya visto, o puede que no exista (H-29).

