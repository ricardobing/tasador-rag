# S12 — Documentación

**Alcance:** 17 docs numerados + 3 guías + 9 informes + ESTADO.md.
**Criterio:** un documento desactualizado es peor que ausente, porque se lee
como verdad. Solo listo divergencias **concretas y verificadas**, con dónde.

---

## 1. La divergencia sistemática: doc 17 quedó en la Etapa 3

`docs/17-arquitectura-viva.md` es el documento que se define a sí mismo como
*"cómo va quedando el proyecto de verdad, no cómo se diseñó. Se actualiza cuando
la realidad difiere del plan"*. Es el que más atrás está.

| doc 17 dice | La realidad, medida |
|---|---|
| §3 `⬜ GET /v1/reports/{id}/pdf` | existe (`reports.py:324`) |
| §3 `⬜ POST /v1/reports/{id}/regenerate` | existe |
| §3 `⬜ POST /v1/inventory/snapshot` | existe |
| §3 no menciona `/v1/comparables`, `/v1/calidad`, `/v1/admin/*`, `/v1/shared/{token}` | los cinco existen |
| §6 `web ⬜ Next.js — Etapa 4` · `worker ⬜ arq + LangGraph — Etapa 3` | los dos corren |
| §4 `corpus.listings 85.022 filas · 24 PORTAL_B ◀ vivos` | **93.495 filas · 8.497 vigentes** |
| §4 `listing_features ⬜` | 90.153 filas |
| §2 "43 s / USD 0,042 el informe completo" | ESTADO §3 dice 12,2 s / USD 0,032 para el mismo caso |
| §8 "Ahora: nodos 5 (dedup) y 6 (curate)" | los 11 nodos están construidos |

**Por qué importa más que las otras:** es el documento al que el prompt de la
próxima sesión manda leer en cuarto lugar *"para saber cómo quedó de verdad"*, y
describe un sistema de hace dos etapas. Alguien que lo lea va a creer que faltan
cinco endpoints que existen y que sobran dos servicios que corren.

**Qué hay que hacer:** reescribir §3, §4 y §6 con los números medidos, y
resolver el conflicto de §2 contra ESTADO §3 (dos costos distintos para el mismo
informe, en dos documentos del mismo repo).

---

## 2. Divergencias por documento

### doc 05 — Metodología de valuación

Es el documento que se le muestra a un martillero, así que sus divergencias
pesan distinto.

| §  | Dice | Verificado |
|---|---|---|
| §5 | *"Por qué winsorizar en vez de eliminar: con 8 comparables, eliminar los 2 extremos tira el 25% de la muestra"* | el motor **elimina** por p5–p95 antes de winsorizar, y con n=8 saca exactamente 2 (H-01). 88 exclusiones reales en la base |
| §6 | *"Ancho mínimo del rango: 8%… se fuerza a ±4% del medio"* | el piso efectivo es **±20%**; el ±4% no gana nunca (H-05) |
| §4.1 | Expensas > 2× la mediana → 0,96 | el coeficiente nunca se aplica (H-04) |
| §4.1 | PB con patio → 1,02 | nunca se aplica (H-04) |
| §4.1 | Antigüedad del aviso > 90 días → 0,97 | nunca dispara: `published_at` está vacío en los 8.497 vigentes (H-21) |
| §4.1 | Cochera: +USD 12.000 al total | solo del lado del sujeto; en el comparable no se descuenta (H-02) |
| §4.2 r.1 | tope ±25% o se descarta | `listing_age_coef` se aplica **después** del tope (H-03) |
| §4.2 r.3 | *"Todo ajuste queda registrado"* | el `total` guardado no incluye la antigüedad del aviso (H-03) |
| §7 | `f_frescura(dias_medianos)` con peso 0,15 | constante 0,6: no hay fechas (H-06) |
| §10 | 9 tests obligatorios | están 8; falta `test_caso_real_conocido` |

### doc 06 — API y contrato

| §  | Dice | Verificado |
|---|---|---|
| §3 | Errores RFC 7807 `application/problem+json` | todos son `application/json` con `{"detail": …}` (H-26) |
| §1 | Rate limiting en Caddy + Redis, 60 req/min por API key | no existe en ningún lado (H-29) |
| §2 | `GET /v1/usage` | 404 (H-30) |
| §2 | la respuesta trae `market_context`, `pdf_url`, `comparables.items[]`, `confidence.notes` | ninguno de los cuatro (H-27) |
| §2 | `external_ref` en la respuesta | siempre `null` |
| §2 | `POST /v1/reports` puede devolver `402` cuota agotada | no encontré el chequeo de cuota |

### ESTADO.md

| §  | Dice | Verificado |
|---|---|---|
| §2 | `pytest -m "not live"` → 350 passed, 2 skipped | **366 passed, 2 skipped** (creció) |
| §2 | `mypy --strict` → 65 archivos | 66 |
| §2 | playwright → 61 passed, 13 skipped | 63 passed, 11 skipped |
| §2 | 8.497 avisos → **5.027 propiedades únicas** | el clustering que produce ese número encadena: un cluster de 198 avisos de 23 a 73 m² y de USD 111.000 a 436.900 (H-12) |
| §5.2 | Rate limit login ✅ | está en el repo y **no en la imagen** que se desplegaría (H-48) |
| §5.2 | `/calidad`, `/admin/*`, `/inventory/snapshot` ✅ | ídem: no existen en la imagen (H-48) |
| §5.1 #5 | *"Backtest con el nodo 4 activo… hay que pensar el dataset"* | el dataset `VIGENTES` ya está implementado (H-54) |
| §4.4 | `run_backtest.py` mide el MdAPE end-to-end | no persiste; el que persiste es `python -m tasador.eval.run` (H-53) |
| §8 | `docker compose exec api python scripts/seed.py` | la imagen `api` no tiene `/app/scripts` (H-49) |

### docs/informes/2026-08-13-etapa-3

§13 indica `scripts/seed_org.py` + `geocode_neighborhoods.py`; hoy es
`scripts/seed.py`, que hace las dos cosas (H-55). **Es un informe fechado y su
valor es histórico**: no hay que reescribirlo. Lo que sí conviene es una nota al
pie que diga que los comandos de §13 quedaron reemplazados.

---

### H-59 · Nada obliga a que la documentación siga al código

**Sección:** S12 · **Severidad:** media

**Qué está mal:** hay **26 divergencias verificadas** entre lo que los documentos
afirman y lo que el sistema hace, y ningún mecanismo las detecta. El proyecto
tiene gates para los comandos documentados (`test_entrypoints.py`) y para los
prompts declarados (`test_todo_prompt_declarado_existe_como_archivo`), pero
ninguno para las afirmaciones verificables de los docs.

**Cómo lo verifiqué:** las tablas de arriba. Cada fila tiene su comando en la
sección correspondiente.

**Por qué importa:** el propio prompt de esta auditoría lo enuncia —*"un doc
desactualizado es peor que ausente: se lee como verdad"*— y el proyecto ya pagó
por eso: doc 04 tenía el SQL del nodo 2 con un `INNER JOIN` que devolvía cero, y
hubo que corregirlo con un bloque `CORREGIDO`. La diferencia es que aquel se
descubrió corriendo; estos 26 están esperando a que alguien los lea y les crea.

Y hay una asimetría que conviene nombrar: **las divergencias de doc 05 son las
peores**, no porque sean más, sino porque doc 05 es el documento que se muestra
fuera del equipo. Un martillero que lea §5 y después audite un informe va a
encontrar comparables eliminados por una regla que el documento dice
explícitamente que no se usa.

**Qué hay que hacer:** tres cosas, de más barata a más cara.

1. **Corregir las 26.** Están todas listadas arriba con su sección.
2. **Un gate para lo verificable.** No para la prosa: para los números y los
   nombres. Concretamente, un test que:
   - extraiga las rutas `/v1/...` de doc 06 §2 y las cruce contra el
     `openapi.json` de la app (atrapa H-30 y el inverso);
   - recorra las claves de `config/adjustments.yaml` y falle si alguna no está
     referenciada en `src/tasador/valuation/` (atrapa H-04);
   - cruce las tres versiones de la tabla de coeficientes de doc 05 §4.1 contra
     el YAML.
3. **Mover los números que envejecen de la prosa a una consulta.** ESTADO §2 y
   §4.3 tienen conteos del corpus escritos a mano que quedan viejos en una
   ingesta. `/admin/fuentes` ya los muestra; el documento debería apuntar ahí en
   vez de repetirlos.

**Cómo se verifica que quedó bien:**

```
uv run pytest tests/architecture -q     # con los tres chequeos nuevos, en verde
```
Y volver a recorrer las tablas de esta sección: cada fila tiene su comando.

**Riesgo de tocarlo:** el punto 2 puede volverse frágil si se intenta parsear
prosa. Acotarlo a rutas, nombres de claves y números que ya viven en un YAML.

---

## 3. Lo que la documentación hace bien, y es mucho

Vale decirlo porque es lo que hizo posible esta auditoría en un día:

- **Los informes fechados son de una calidad inusual.** `2026-08-14-que-datos-tenemos.md`
  es un modelo: hipótesis, medición, hipótesis refutada, medición, conclusión con
  números. La sección "el prompt que medía bien y extraía cero" es el mejor
  ejemplo de método que vi en el repo.
- **Los comentarios del código explican el POR QUÉ, con el número medido al
  lado.** `dedup.py`, `critic.py`, `conftest.py` y `litellm.yaml` no documentan
  qué hace el código —eso se lee— sino qué se probó, qué falló y por qué la
  decisión es esa. Varios hallazgos de esta auditoría salieron de leer un
  comentario y verificar si seguía siendo cierto.
- **Lo que NO se hizo está escrito.** ESTADO §5, §6 y §5.3 declaran los huecos
  con más honestidad que la mayoría de los proyectos declara sus logros.
- **Las decisiones abiertas están numeradas** (ESTADO §6) y marcadas como del
  humano, no del agente.

Ninguna de las 26 divergencias es un intento de que algo parezca mejor de lo que
es. Todas son de lo mismo: **el código se movió más rápido que los documentos, y
los documentos se movieron más rápido que las imágenes** (S9).
