# Sondas de la auditoría

Los scripts que produjeron las mediciones de los documentos de esta carpeta.
**No son código de producción**: quedan acá para que cada hallazgo se pueda
volver a verificar, y para que la sesión 2 pueda comprobar que un arreglo
funcionó midiendo lo mismo que lo detectó.

Están fuera de `src/`, `tests/` y `scripts/`, así que no entran a `ruff` ni a
`mypy`. Si alguno sobrevive al backlog, el lugar donde tiene que terminar está
dicho en el hallazgo correspondiente (varios son tests, y `s9_imagen.py` debería
ser un paso del CI).

## Cómo correrlas

```powershell
$env:DATABASE_URL="postgresql+psycopg://tasador:<PASS>@127.0.0.1:5433/tasador"
$env:LITELLM_BASE_URL="http://127.0.0.1:4000"
uv run python docs/auditoria/sondas/<sonda>.py
```

Ninguna escribe en la base ni gasta en LLM. `q.py` es un ayudante: toma un
archivo con bloques `título \n SQL` separados por `===` y los imprime.

| Sonda | Hallazgo | Qué mide | Valor al 15/08 |
|---|---|---|---|
| `s1_probe.py` | H-01..H-06 | Cochera del comparable, tope vs `listing_age`, coeficientes muertos, recorte p5–p95, ancho de rango, completitud | ver [01](../01-valuacion.md) |
| `s1_reproduce.py` | S1 §1 | Recalcula informes reales desde `report_comparables` y compara con lo guardado | 6/6 OK en 5 informes |
| `s1_parking.py` | H-02 | Contrafactual: descontar la cochera de los comparables | +0,00% hoy |
| `s2_critico.py` | **H-11** | Qué fracción de precios inventados deja pasar la fase A | **50,5% / 27,2% / 27,1%** |
| `s2_critico2.py` | H-11 | Qué regla abre el agujero, y la sensibilidad a la tolerancia | `v*100`: +20 pp |
| `s2_cluster.py` | **H-12** | % de pares que realmente matchean dentro de cada cluster | **7,6% / 12,2% / 47,8% / 20,3%** |
| `s3_contrato.py` | H-23 | Atributos de `CAMPOS_DEL_RAW`/`_HASH` que una tarjeta no tiene | `Card`: faltan 5 y 2 |
| `s4_api.py` | H-26..H-31 | Contrato, errores, auth, cabeceras, CORS, keyset, inputs hostiles | ver [04](../04-api.md) |
| `s4b.py` | H-27 | Respuesta de `GET /v1/reports/{id}` vs doc 06 §2 | faltan `market_context`, `pdf_url` |
| `s7_injection.py` | **H-41** | Si cada nodo envuelve el texto de terceros | nodo 5: **no** |
| `s8_gate.py` | H-44 | Si el gate del backtest sale verde con 0 casos | **verde** con los flags del CI |
| `s9_imagen.py` | **H-48** | Rutas de la imagen de producción vs el código del repo | 5 rutas ausentes |
| `s5_migraciones.ps1` | H-32 | Migraciones desde cero en una base descartable + `alembic check` | esquema idéntico, `check` en rojo |

## Las que necesitan preparación

- **`s9_imagen.py`** compara `:8000` (repo) contra `:8099` (imagen de
  producción). Antes hay que levantar la imagen:
  ```bash
  docker run -d --rm --name tasador-prod-test -p 8099:8000 --network tasador_internal \
    -e ENV=production -e DATABASE_URL=... -e REDIS_URL=... \
    -e SECRET_KEY=x -e ENCRYPTION_KEY=x -e LITELLM_MASTER_KEY=x \
    ghcr.io/ricardobrossard/tasador-api:dev
  ```
- **`s5_migraciones.ps1`** crea y borra la base `tasador_auditoria_s5`. Es la
  única sonda que escribe, y solo sobre una base descartable propia.

## Agregadas en la sesión de implementación (15/08)

| Sonda | Hallazgo | Qué mide | Valor medido |
|---|---|---|---|
| `h33_superficie.py` | H-33 | Los tres variantes de la consulta del nodo 2, con los parámetros reales de la escalera | A 62,4 ms / 37.821 buffers · B 62,7 / 36.237 · C 56,7 / 33.385, y C pierde 5 candidatos de 1.637 |
| `s8_gate.py` (ampliada) | H-44 | El gate del backtest con 0 y con 30 casos | los dos en ROJO; antes los dos en VERDE con los flags del CI |

> `s8_gate.py` tenía cinco casos y el hallazgo pegó cuatro líneas: el quinto —el
> caso VERDE, el que prueba que el gate no está roto al revés— crasheaba por un
> tipo mal pasado y nunca había corrido. Corregido.

Lo que la sesión de implementación movió a `tests/` para que corra en cada
commit en vez de cuando alguien se acuerda:

| De la sonda | Al suite |
|---|---|
| `s8_gate.py` | `tests/test_eval_gate.py` — 7 tests, incluido uno que lee `ci.yml` y verifica que los flags que ejercita sean los que el CI usa |
| `s4_api.py` (la parte de errores) | `tests/test_errores_api.py` — 12 tests, uno por cada forma de fallar que la API tiene |
| `s4b.py` | `tests/test_reports_api.py` — el contrato de doc 06 §2, sobre comparables reales |
