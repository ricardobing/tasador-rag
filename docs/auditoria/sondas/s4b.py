"""Contrato real de GET /v1/reports/{id} contra doc 06 §2."""

import json

import httpx

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30, headers={"X-Org-Slug": "inmo-demo"})
rid = c.get("/v1/reports?limit=1").json()["items"][0]["report_id"]
d = c.get(f"/v1/reports/{rid}").json()

print(f"informe {rid[:8]} — claves de primer nivel:")
print("  ", sorted(d))
print()

ESPERADO_DOC06 = {
    "report_id",
    "status",
    "external_ref",
    "generated_at",
    "valuation",
    "confidence",
    "comparables",
    "market_context",
    "narrative_md",
    "methodology_version",
    "pdf_url",
    "limitations",
}
print("  faltan respecto de doc 06 §2:", sorted(ESPERADO_DOC06 - set(d)))
print("  de más (no documentadas) :", sorted(set(d) - ESPERADO_DOC06))
print()
for k in ("valuation", "confidence", "comparables", "market_context"):
    if isinstance(d.get(k), dict):
        print(f"  {k}: {sorted(d[k])}")
print()
items = (d.get("comparables") or {}).get("items") or []
if items:
    print("  comparables.items[0]:", sorted(items[0]))
    ESPERADO_ITEM = {
        "source",
        "url",
        "address",
        "price",
        "currency",
        "surface_weighted",
        "rooms",
        "raw_price_per_m2",
        "adjusted_price_per_m2",
        "adjustments",
        "distance_m",
        "days_published",
        "included",
    }
    print("    faltan:", sorted(ESPERADO_ITEM - set(items[0])))
print()
print("  ¿dinero como {value,currency} (doc 06 §3)?")
print("   ", json.dumps(d.get("valuation"), ensure_ascii=False)[:220])
print()
print("  ¿la traza y el costo se exponen?")
for k in ("progress", "events", "cost_usd", "trace"):
    print(f"    {k}: {'SÍ' if k in d else 'no'}")
