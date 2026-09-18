"""¿Qué rutas tiene la imagen de PRODUCCION que se desplegaría hoy?"""

import httpx

RUTAS = (
    "/v1/health",
    "/v1/ready",
    "/v1/reports",
    "/v1/comparables",
    "/v1/calidad",
    "/v1/admin/fuentes",
    "/v1/admin/organizacion",
    "/v1/admin/usuarios",
    "/v1/inventory/snapshot",
    "/v1/auth/me",
)

for puerto, nombre in (
    (8000, "codigo del REPO (dev, con bind mount)"),
    (8099, "IMAGEN de produccion"),
):
    print(f"--- {nombre}  (:{puerto})")
    c = httpx.Client(base_url=f"http://127.0.0.1:{puerto}", timeout=20)
    for r in RUTAS:
        try:
            resp = c.get(r, headers={"X-Org-Slug": "inmo-demo"})
            marca = "  <-- NO EXISTE" if resp.status_code == 404 and r != "/v1/reports" else ""
            print(f"    {r:<30} {resp.status_code}{marca}")
        except Exception as e:
            print(f"    {r:<30} ERROR {type(e).__name__}")
    print()
