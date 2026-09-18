"""S4 â€” la API v1 contra el stack real. Solo GET y POST idempotentes/validacion."""

import json

import httpx

BASE = "http://127.0.0.1:8000"
H = {"X-Org-Slug": "inmo-demo"}
c = httpx.Client(base_url=BASE, timeout=30, headers=H)


def linea(m, url, **kw):
    r = c.request(m, url, **kw)
    ct = r.headers.get("content-type", "")
    cuerpo = ""
    if "json" in ct:
        try:
            cuerpo = json.dumps(r.json(), ensure_ascii=False)[:170]
        except Exception:
            cuerpo = r.text[:170]
    else:
        cuerpo = r.text[:110].replace("\n", " ")
    print(f"  {r.status_code}  {ct.split(';')[0]:<24} {m:<5}{url[:52]:<54}{cuerpo}")
    return r


print("=== 1. Salud y contrato base ===")
linea("GET", "/v1/health")
linea("GET", "/v1/ready")
linea("GET", "/v1/reports?limit=2")
linea("GET", "/v1/auth/me")

print("\n=== 2. Errores: Â¿son RFC 7807 (application/problem+json)? ===")
linea("GET", "/v1/reports/00000000-0000-0000-0000-000000000000")
linea("GET", "/v1/reports/no-es-un-uuid")
linea("GET", "/v1/no-existe")
linea("POST", "/v1/reports", json={})
linea("POST", "/v1/reports", json={"direccion": "x", "ambientes": "muchos"})
linea("GET", "/v1/reports?limit=99999")
linea("GET", "/v1/reports?limit=-1")
linea("GET", "/v1/reports?cursor=basura")
linea("GET", "/v1/reports?cursor=" + "A" * 5000)

print("\n=== 3. Autenticacion ===")
c2 = httpx.Client(base_url=BASE, timeout=30)
r = c2.get("/v1/reports")
print(f"  sin ningun header            -> {r.status_code}  {r.text[:90]}")
r = c2.get("/v1/reports", headers={"Authorization": "Bearer tsk_live_inventada12345"})
print(f"  Bearer invalido              -> {r.status_code}  {r.text[:90]}")
r = c2.get("/v1/reports", headers={"Authorization": "Bearer basura"})
print(f"  Bearer sin prefijo           -> {r.status_code}  {r.text[:90]}")
r = c2.get("/v1/reports", headers={"X-Org-Slug": "no-existe"})
print(f"  X-Org-Slug inexistente       -> {r.status_code}  {r.text[:90]}")
r = c2.get("/v1/reports", headers={"Cookie": "tasador_sesion=basura"})
print(f"  cookie de sesion invalida    -> {r.status_code}  {r.text[:90]}")

print("\n=== 4. Cabeceras de seguridad de la respuesta ===")
r = c.get("/v1/health")
for h in (
    "x-content-type-options",
    "x-frame-options",
    "strict-transport-security",
    "content-security-policy",
    "referrer-policy",
    "x-request-id",
    "server",
):
    print(f"  {h:<28} {r.headers.get(h, '(ausente)')}")

print("\n=== 5. CORS ===")
r = c.options(
    "/v1/reports",
    headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
)
print(
    f"  preflight desde evil.example -> {r.status_code}  "
    f"allow-origin={r.headers.get('access-control-allow-origin', '(ausente)')}  "
    f"allow-credentials={r.headers.get('access-control-allow-credentials', '(ausente)')}"
)

print("\n=== 6. Paginacion por keyset: Â¿el cursor es opaco y estable? ===")
r = c.get("/v1/reports?limit=3")
d = r.json()
cur = d.get("next_cursor")
print(f"  primera pagina: {len(d['items'])} items  next_cursor={str(cur)[:44]}")
if cur:
    import base64

    try:
        print(f"  cursor decodificado: {base64.urlsafe_b64decode(cur + '==').decode()[:60]}")
    except Exception as e:
        print(f"  no decodifica como base64url: {e}")
    r2 = c.get(f"/v1/reports?limit=3&cursor={cur}")
    d2 = r2.json()
    ids1 = {i["report_id"] for i in d["items"]}
    ids2 = {i["report_id"] for i in d2.get("items", [])}
    print(f"  segunda pagina: {len(d2.get('items', []))} items  solapamiento={len(ids1 & ids2)}")
    # Cursor manipulado
    roto = cur[:-4] + "AAAA"
    r3 = c.get(f"/v1/reports?limit=3&cursor={roto}")
    print(f"  cursor manipulado -> {r3.status_code}  {r3.text[:100]}")

print("\n=== 7. Endpoints declarados en doc 06 ===")
for ruta in (
    "/v1/comparables?limit=1",
    "/v1/calidad",
    "/v1/admin/fuentes",
    "/v1/admin/organizacion",
    "/v1/admin/usuarios",
    "/v1/usage",
    "/v1/inventory/snapshot",
):
    try:
        r = c.get(ruta)
        print(f"  GET {ruta:<34} -> {r.status_code}")
    except Exception as e:
        print(f"  GET {ruta:<34} -> ERROR {e}")

print("\n=== 8. Inputs hostiles en GET (no escriben nada) ===")
for q in (
    "/v1/reports?limit=abc",
    "/v1/reports?limit=1&cursor=" + "%00" * 20,
    "/v1/comparables?limit=1&barrio=' or 1=1--",
    "/v1/comparables?limit=1&solo_sin_extraer=maybe",
    "/v1/reports/../../etc/passwd",
    "/v1/reports/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
):
    try:
        r = c.get(q)
        print(f"  {q[:62]:<64} -> {r.status_code} {r.text[:60]}")
    except Exception as e:
        print(f"  {q[:62]:<64} -> ERROR {type(e).__name__}")
