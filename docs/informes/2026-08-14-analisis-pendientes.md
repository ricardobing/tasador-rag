# Análisis de pendientes — 14/08/2026 (sesión de cierre de Etapa 4)

**Contexto:** revisión completa del repo tras las etapas 0-3 terminadas y la 4 a
medias (5 de 12 pantallas). Este documento ordena lo que falta, separa lo que se
puede cerrar en esta sesión de lo que requiere decisión humana o datos, y fija
el plan de implementación.

---

## 1. Estado verificado al inicio

- Stack **corriendo** en Docker: api (8000), web (3300), postgres (5433),
  litellm (4000), redis (6380), worker. `GET /v1/health` → `{"status":"ok"}`.
- Pantallas hechas: `/login`, `/informes`, `/informes/nuevo`, `/informes/[id]`,
  `/comparables` — con 54 tests de Playwright.
- API: `POST/GET /v1/reports`, `GET /v1/reports/{id}(/pdf)`,
  `GET /v1/comparables`, `POST /v1/comparables/{id}/revisar`,
  `POST /v1/auth/login|logout`, `GET /v1/auth/me`, health/ready.
- Guías: existen `docs/guias/manual.md` y `puesta-en-marcha.md`; hay que
  actualizarlas con lo que se agregue.

## 2. Qué falta, en orden

### A. Cerrable en esta sesión (código, sin decisión humana)

| # | Qué | Por qué en este orden |
|---|---|---|
| 1 | **`GET /v1/calidad` + pantalla `/calidad`** | Los datos ya están en `eval.backtest_runs` y `eval.component_runs`; es lo que hace el proyecto demostrable (doc 07 §9). Sin botón "correr backtest" por ahora: corre por CLI y tarda minutos. |
| 2 | **`GET /v1/admin/fuentes` + pantalla** | `corpus.ingest_runs` ya guarda todo; el chequeo de sesgo sale de `market_index` (INTERNAL vs BADATA). Es la alarma temprana de que el corpus se pudre. |
| 3 | **API keys: listar/crear/revocar + `/admin/organizacion`** | El modelo `ApiKey` y el hashing ya existen (`scripts/crear_usuario.py --api-key`); falta exponerlo a un admin sin SSH. |
| 4 | **Usuarios: listar/crear/desactivar + `/admin/usuarios`** | Ídem: hoy solo por script. Solo owner/admin con sesión (una API key no administra personas — decisión ya tomada en `Principal.es_admin`). |
| 5 | **`POST /v1/reports/{id}/regenerate`** | El flujo real es generar antes de la visita y regenerar después. El sujeto ya existe; es crear un informe nuevo sobre el mismo sujeto con los campos actualizados. |
| 6 | **`POST /v1/reports/{id}/share` + vista pública** | URL firmada (HMAC + expiración) para que el propietario vea el informe sin cuenta. |
| 7 | **Rate limit en `/auth/login`** | 5/min por IP y por email (doc 07 §2), contra Redis. Es el único hueco de endurecimiento cerrable sin infra nueva. |
| 8 | **Navegación admin en el layout** | Los links a Calidad/Admin solo si `role in (owner, admin)`; la autorización real la hace la API. |

### B. Bloqueado por datos o por humano (NO se resuelve con código hoy)

| Qué | Bloqueado por |
|---|---|
| Anotar el golden set (90 avisos de Palermo preparados) | Requiere anotación humana; los evals ignoran `revisado: false` a propósito. |
| Backtest con nodo 4 activo | Decisión abierta §6.1 de ESTADO: qué dataset usar (BA Data no trae los atributos). |
| 282 pares de dedup en zona gris | `dedup_corpus.py --juez` gasta y conviene correrlo junto con la próxima tanda de datos. |
| Más barrios de datos | Depende del scraper externo (`C:\datos\avisos`). |
| kNN pgvector / embeddings | Deuda técnica declarada; no bloquea ninguna pantalla. |
| Nodo 3 (captura on-demand) | Necesita Chrome con IP residencial; fuera del alcance del repo. |
| Backups probados, Langfuse | Infra/operación, no código de esta sesión. |
| `POST /v1/inventory/snapshot` | Contrato con el panel de la inmobiliaria sin definir del lado de ellos; el modelo `InventoryProperty` ya existe. Se pospone hasta tener un consumidor real — implementarlo sin cliente sería un endpoint sin verificación posible. |

### C. Criterio de cierre de la sesión

Cada ítem de A se declara hecho solo con: (1) endpoint probado contra la API
real, (2) pantalla vista en el navegador contra el stack corriendo, (3)
`ruff` + `mypy --strict` + `pytest -m "not live"` en verde, (4) tests nuevos
para lo nuevo, (5) guías y ESTADO.md actualizados.

## 3. Decisiones de diseño tomadas en esta sesión

1. **`/calidad` sin "Correr backtest" por UI.** El backtest tarda minutos y ya
   tiene CLI con historia (`python -m tasador.eval.run`). Un botón que dispara
   un job de minutos merece su propia cola y notificación; hacerlo a medias
   sería un gate que puede pasar sin medir nada.
2. **La autorización admin vive en la API** (`Principal.es_admin` +
   `user is not None` para administrar usuarios). El front solo esconde links.
3. **El share token no toca la base:** HMAC firmado con la clave de sesión ya
   existente, con expiración. Revocar = cambiar la clave (documentado). Si
   mañana hace falta revocación por informe, se agrega tabla.
4. **`regenerate` crea un informe NUEVO** sobre el mismo `subject_property`
   (actualizado con lo que venga en el body). Un informe es un documento, no
   una vista: el anterior no se muta.

---

## 4. Resultado de la sesión (cierre)

Todo el punto A quedó **implementado, probado y verificado en el navegador
contra el stack real**:

| # | Qué | Verificación ejecutada |
|---|---|---|
| 1 | `GET /v1/calidad` + `/calidad` | Pantalla renderiza la serie real (2 backtests, 5 corridas de extracción). Playwright ✅ |
| 2 | `GET /v1/admin/fuentes` + pantalla | Tarjetas PORTAL_A/PORTAL_B con la corrida de las 17:53; cobertura Palermo 4.007 / Belgrano 107 |
| 3 | API keys por la web | Clave creada por UI, **autenticó** (`/v1/auth/me` 200), revocada, **dejó de autenticar** (401) |
| 4 | `/admin/usuarios` | Usuario creado por UI con contraseña generada; login 200; desactivado → sesión muere al instante (401) |
| 5 | `regenerate` | Botón → 202 → stepper en vivo → **SUCCEEDED, confianza ALTA (0,865)** por el pipeline real |
| 6 | `share` + `/compartido/[token]` | Link firmado abre informe y PDF **sin sesión**; token trucho → 404; un share NO abre sesión (test) |
| 7 | Rate limit login | 6º intento en un minuto → 429 con `Retry-After` (verificado por curl y por test) |

```
ruff · mypy --strict          ✅ verdes
pytest -m "not live"          ✅ 348 passed, 2 skipped  (17 tests nuevos en test_admin.py)
playwright                    ✅ 61 passed, 13 skipped  (13 tests nuevos de pantallas)
```

Además, **dedup del corpus nuevo**: `dedup_corpus.py --aplicar` marcó 1.826
avisos (33% de duplicados en Palermo; 2.672 propiedades reales). La zona gris
creció a **41.427 pares** — el juez ya no cuesta centavos y pasó a ser una
decisión (ESTADO §5.1).

Del punto B no se tocó nada, por diseño: son tareas de anotación humana,
decisión de dataset, o integraciones sin consumidor.

### El bug que trajo el dedup, y su arreglo verificado

Aplicar los clusters destapó un bug del nodo 2 que ningún test veía porque el
corpus nunca había tenido clusters: **el retrieve traía los 60 candidatos por
`last_seen` sin excluir los duplicados ya detectados**, así que las 20 unidades
de una torre en pozo ocupaban el límite. Medido en el mismo informe de Gorriti
5000:

```
                            comparables usados    confianza
antes del dedup                   22 de 60        ALTA (0,865)
tras dedup, nodo 2 viejo           7 de 60        BAJA (0,37)
tras dedup, nodo 2 arreglado      23 de 60        ALTA (0,865)   ✅
```

El arreglo: un cluster aporta UN candidato (el canónico) en el SQL del nodo 2;
un cluster con `canonical_id` NULL no silencia a sus miembros. Con dos tests
nuevos (`tests/test_retrieve_clusters.py`). Y la trampa que costó una corrida:
**el worker no tiene `--reload`** — el primer intento de verificación corrió el
código viejo.
