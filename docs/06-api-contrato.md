# 06 — API y contrato

**Base:** `https://api.tasador.<dominio>/v1`
**Versionado:** el path. `/v1` no rompe nunca; los cambios son aditivos. Un cambio
incompatible crea `/v2` y `/v1` sigue vivo al menos 6 meses.

---

## 1. Autenticación

Dos mecanismos, para dos consumidores distintos:

| Consumidor | Mecanismo | Detalle |
|---|---|---|
| **Sistemas** (panel de la inmobiliaria, otras integraciones) | `Authorization: Bearer tsk_live_...` | API key por tenant. Hash argon2id en `core.api_keys`. Nunca en el browser: el panel la usa server-side desde su route handler |
| **Usuarios** (UI del Tasador) | Cookie de sesión `HttpOnly; Secure; SameSite=Lax` | Login propio con email + contraseña |

Toda respuesta de error de auth es `401` con cuerpo genérico. No se distingue entre
"clave inexistente" y "clave revocada": es información gratis para un atacante.

**Rate limiting** (en Caddy + Redis): 60 req/min por API key, 600/hora. `429` con
`Retry-After`.

---

## 2. Endpoints

### `POST /v1/reports` — solicitar un informe

```http
POST /v1/reports
Authorization: Bearer tsk_live_...
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
Content-Type: application/json
```

```json
{
  "external_ref": "a3f1c2d4-...",
  "property": {
    "address_raw": "Av. Cabildo 2530 4°B",
    "city": "CABA",
    "property_type": "departamento",
    "rooms": 3,
    "bedrooms": 2,
    "bathrooms": 1,
    "surface_total": 78.5,
    "surface_covered": 72.0,
    "age_years": 25,
    "floor_number": 4,
    "has_elevator": true,
    "condition": "bueno",
    "orientation": "frente",
    "amenities": ["sum", "seguridad"],
    "parking_spaces": 1,
    "expenses_ars": 95000,
    "notes": "Cocina y baño originales"
  },
  "options": {
    "callback_url": "https://panel.inmo-demo.com.ar/api/tasador/callback",
    "locale": "es-AR"
  }
}
```

**Solo `address_raw` y `property_type` son obligatorios.** Todo lo demás mejora el
informe pero no lo bloquea — porque el flujo real es generar antes de la visita con
poco dato y regenerar después con todo.

**Respuesta `202 Accepted`:**

```json
{
  "report_id": "7c9e6679-...",
  "status": "QUEUED",
  "poll_url": "/v1/reports/7c9e6679-...",
  "estimated_seconds": 90
}
```

**Errores:** `400` validación (con detalle por campo) · `401` · `402` cuota mensual
agotada · `422` dirección no geocodificable · `429` rate limit.

**Idempotencia:** con el mismo `Idempotency-Key` dentro de 24 h se devuelve el
`report_id` original con `200` en vez de crear otro. Dos clicks del botón no cuestan
el doble.

---

### `GET /v1/reports/{id}` — estado y resultado

**Mientras corre — `200`:**

```json
{
  "report_id": "7c9e6679-...",
  "status": "RUNNING",
  "progress": {
    "current_node": "curate",
    "completed": 6,
    "total": 11,
    "steps": [
      {"node": "normalize_subject", "status": "OK", "duration_ms": 1840},
      {"node": "retrieve_candidates", "status": "OK", "duration_ms": 420,
       "detail": {"candidates": 47}},
      {"node": "extract_features", "status": "OK", "duration_ms": 14200}
    ]
  }
}
```

Esto es lo que alimenta el stepper en vivo de la UI. No es cosmético: un proceso de
90 segundos sin feedback se percibe como roto.

**Terminado — `200`:**

```json
{
  "report_id": "7c9e6679-...",
  "status": "SUCCEEDED",
  "external_ref": "a3f1c2d4-...",
  "generated_at": "2026-08-13T14:22:31Z",
  "valuation": {
    "currency": "USD",
    "suggested_listing_price": {"low": 168000, "mid": 182000, "high": 196000},
    "expected_closing_range": {"low": 154700, "high": 172900},
    "price_per_m2": 2441,
    "weighted_surface": 75.25
  },
  "confidence": {
    "level": "ALTA",
    "score": 0.81,
    "notes": ["9 comparables", "dispersión 11%", "datos de los últimos 45 días"]
  },
  "comparables": {
    "found": 47,
    "used": 9,
    "excluded": 38,
    "items": [
      {
        "source": "PORTAL_A",
        "url": "https://www.portal_a.com/...",
        "address": "Av. Cabildo 2480",
        "price": 175000, "currency": "USD",
        "surface_weighted": 71.0,
        "rooms": 3,
        "raw_price_per_m2": 2465,
        "adjusted_price_per_m2": 2398,
        "adjustments": {"estado": 0.94, "piso": 1.00, "total": 0.94},
        "distance_m": 320,
        "days_published": 38,
        "included": true
      }
    ]
  },
  "market_context": {
    "neighborhood": "Belgrano",
    "official_usd_m2": 2510,
    "official_source": "DGEyC-GCBA, jun-2026",
    "corpus_usd_m2": 2447,
    "active_stock": 214,
    "price_drops_90d_pct": 0.31,
    "median_days_on_market": 142
  },
  "narrative_md": "## Resumen ejecutivo\n\n...",
  "methodology_version": "2026.08.1",
  "pdf_url": "/v1/reports/7c9e6679-.../pdf",
  "limitations": [
    "Los valores corresponden a precios de publicación, no de escrituración.",
    "Este informe no constituye una tasación con validez legal."
  ]
}
```

**Sin datos suficientes — `200`** (no es un error):

```json
{
  "report_id": "...",
  "status": "INSUFFICIENT_DATA",
  "insufficient_reason": "Se encontraron 3 comparables válidos; el mínimo es 5.",
  "detail": {
    "candidates_found": 11,
    "excluded": [
      {"reason": "precio_en_ars", "count": 4},
      {"reason": "sin_superficie", "count": 2},
      {"reason": "duplicado", "count": 2}
    ],
    "suggestions": [
      "Ampliar el radio de búsqueda a barrios limítrofes",
      "Verificar la dirección: se geocodificó como 'Villa Riachuelo'"
    ]
  }
}
```

Que esto sea `200` y no `404`/`500` es deliberado: el sistema funcionó correctamente
y su respuesta correcta es "no hay datos".

---

**`property` (19/09/2026).** La ficha completa de la propiedad tasada, en todos los
estados: `address`, `neighborhood`, `property_type`, `rooms`, `bedrooms`, `bathrooms`,
`surface_total`, `surface_covered`, `age_years`, `floor_number`, `has_elevator`,
`condition`, `orientation`, `parking_spaces`, `expenses_ars`, `notes`. Lo no declarado es
`null` y el front lo muestra como «sin declarar»: el propietario ve qué se usó y qué no.
`GET /v1/shared/{token}` trae el mismo bloque.

### `PATCH /v1/auth/password` — cambiar la propia contraseña (19/09/2026)

Cuerpo: `{"actual": "…", "nueva": "…"}` (nueva ≥ 10 caracteres). Solo con sesión de
usuario (una API key no tiene contraseña: 403). La actual se verifica siempre, y el
endpoint comparte el rate limit del login (5 por minuto por IP y por email): verificar
una contraseña es probar una contraseña. 422 si la actual no coincide o la nueva es
igual.

### `GET /v1/reports/{id}/pdf`

`200 application/pdf` con `Content-Disposition: attachment`. Requiere el mismo tenant.
`404` si aún no terminó.

Para compartir con el propietario: `POST /v1/reports/{id}/share` devuelve una URL
firmada con expiración configurable (default 30 días), servida sin auth pero con
token de un solo recurso.

---

### `POST /v1/reports/{id}/regenerate`

Regenera con datos actualizados de la propiedad (el caso post-visita). Crea un
**nuevo** `report_id` y deja el anterior intacto. Fundamento: un informe entregado a
un cliente no se sobrescribe nunca.

```json
{ "property": { "condition": "a_refaccionar", "orientation": "contrafrente" },
  "reason": "Datos actualizados tras la visita del 14/08" }
```

---

### `POST /v1/reports/{id}/ask` — preguntar sobre un informe (doc 18 §5)

Solo sobre informes `SUCCEEDED`. Cuerpo: `{"pregunta": "…"}` (3 a 500 caracteres).

```json
{
  "report_id": "…",
  "pregunta": "¿Por qué no se usó el aviso de Thames 1800?",
  "respuesta": "Se descartó porque está en pozo: su precio incluye el plazo de obra [C-07].",
  "citas": [{"id": "C-07", "texto": "Comparable C-07: Thames 1800 (PORTAL_A), USD 240.000 … Descartado: …", "fuente": "informe"}],
  "rechazada": false,
  "motivo": null,
  "cost_usd": 0.0004,
  "duration_ms": 1820
}
```

`rechazada: true` con `motivo` cuando no hay evidencia (por debajo del umbral de
similitud no se llama al modelo), cuando el modelo declara que no la hay, o cuando
la respuesta no pasó la verificación (una cita inexistente, una cifra que no está
en lo citado). Cada pregunta queda como evento `ask` en la traza del informe, con
su costo. **No disponible en el informe compartido** (`/shared/{token}`).

### `POST /v1/inventory/snapshot` — el panel empuja su inventario

Este es el endpoint que **elimina el riesgo de cuota de el CRM**
(ver [02 §2.3](02-fuentes-de-datos.md)). El panel, que ya sincroniza el CRM, manda
una vez por día lo que descargó.

```json
{
  "source": "CRM",
  "full_refresh": true,
  "properties": [
    {
      "source_id": "1234567",
      "address_raw": "Ciudad de la Paz 2100",
      "property_type": "departamento",
      "operation": "SALE",
      "rooms": 3,
      "surface_total": 82.0,
      "surface_covered": 75.0,
      "price": 189000,
      "currency": "USD",
      "description": "...",
      "published": true
    }
  ]
}
```

Respuesta: `{"received": 285, "created": 12, "updated": 261, "unchanged": 12, "delisted": 4}`

Acepta hasta 1.000 propiedades por request, con paginación por `cursor` si hiciera
falta. Es idempotente por `(org_id, source, source_id)`.

---

### Endpoints de operación

| Endpoint | Uso |
|---|---|
| `GET /v1/health` | Liveness. Sin auth. Solo `{"status":"ok"}` |
| `GET /v1/ready` | Readiness: verifica Postgres, Redis y LiteLLM. Sin auth, sin detalles internos |
| `GET /v1/usage` | Consumo del tenant: informes del mes, cuota, costo, presupuesto de fetch restante |
| `GET /v1/reports?status=&from=&to=&cursor=` | Listado paginado por cursor |

### Endpoints agregados el 14/08 (implementados)

| Endpoint | Uso |
|---|---|
| `POST /v1/reports/{id}/regenerate` | Flujo post-visita: informe NUEVO sobre el mismo sujeto, actualizado con lo que venga en `property`. 409 si el anterior sigue corriendo |
| `POST /v1/reports/{id}/share` | Link firmado para el propietario (`aud=share`, 30 días). Solo informes `SUCCEEDED` |
| `GET /v1/shared/{token}` · `/pdf` | **Público**: el token es la credencial. Devuelve el resultado, no la traza ni los costos |
| `GET /v1/calidad` | [admin] Serie de `eval.backtest_runs` y `eval.component_runs` |
| `GET /v1/admin/fuentes` | [admin] Última corrida por fuente + cobertura y sesgo por barrio |
| `GET /v1/admin/organizacion` | [admin] Cuota, consumo y API keys (solo prefijos) |
| `POST /v1/admin/api-keys` · `DELETE /v1/admin/api-keys/{id}` | [admin] La clave en claro viaja UNA vez, al crear |
| `GET/POST /v1/admin/usuarios` · `PATCH /v1/admin/usuarios/{id}` | [admin **con sesión humana**: una API key no administra personas] |
| `POST /v1/inventory/snapshot` | ✅ implementado como está especificado arriba (§2). `full_refresh` da de baja con `published=false`, nunca borra |

`POST /v1/auth/login` tiene rate limit: 5 intentos/minuto por IP y por email
(429 + `Retry-After`).

---

## 3. Convenciones transversales

| Aspecto | Regla |
|---|---|
| **Formato de error** | RFC 7807 `application/problem+json`: `{type, title, status, detail, instance, errors[]}` |
| **Trazabilidad** | Todo response lleva `X-Request-Id`. Se loguea y se correlaciona con Langfuse |
| **Fechas** | ISO 8601 con zona, siempre UTC en el wire |
| **Dinero** | Siempre `{value, currency}`. Nunca un número suelto |
| **Paginación** | Cursor (`?cursor=`), no offset. Estable ante inserciones |
| **Nulos** | Un campo ausente y un campo `null` significan lo mismo: no hay dato |
| **Compatibilidad** | Los clientes deben ignorar campos desconocidos. Agregar campos no es breaking |

---

## 4. La integración del lado del panel

Todo lo que hay que escribir en el repo de la inmobiliaria. **Detrás de un feature flag**
(`TASADOR_ENABLED`), para poder apagarlo sin deployar.

```
src/lib/tasador/client.ts        ~60 líneas — fetch tipado
src/app/api/tasador/route.ts     ~40 líneas — proxy server-side (la API key no
                                              toca el browser)
src/app/api/tasador/[id]/route.ts ~25 líneas — proxy del polling
src/components/tasaciones/InformeButton.tsx  ~80 líneas — botón + stepper + link
```

~205 líneas, ningún cambio de schema, ninguna dependencia nueva. Si el Tasador no
responde, el botón muestra "informe no disponible" y nada más se rompe.

**El cron de inventario** es un endpoint más en el sync nocturno que ya existe:

```ts
// después del sync de el CRM que ya corre
if (process.env.TASADOR_ENABLED === "true") {
  await pushInventorySnapshot(propiedadesQueAcabamosDeSincronizar);
}
```

---

## 5. Compatibilidad y deprecación

- Agregar un campo a una respuesta: **no** es breaking.
- Agregar un campo opcional a un request: **no** es breaking.
- Quitar o renombrar un campo, o cambiar un tipo: **sí** es breaking → `/v2`.
- Un valor nuevo en un enum **sí puede romper** clientes estrictos: los enums de
  respuesta se documentan como abiertos y el cliente debe tener rama `default`.
- Toda deprecación se anuncia con el header `Deprecation` y `Sunset` (RFC 8594) con
  al menos 6 meses.
