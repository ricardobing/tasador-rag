"""Descarga los datasets abiertos del GCBA.

Sin cuentas, sin claves, sin tarjeta. Licencia CC-BY-2.5-AR.

    uv run python scripts/fetch_badata.py --years 2015-2020

⚠️ Las URLs NO son adivinables: los años viejos cuelgan de
`/datasets/departamentos-en-venta/` y 2020 de
`/datasets/secretaria-de-desarrollo-urbano/departamentos-venta/`.
Se resuelven por la API de CKAN, no se construyen a mano (aprendido con dos
404 el 13/08/2026).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx

CKAN = "https://data.buenosaires.gob.ar/api/3/action/package_show"

DATASETS = {
    "departamentos-venta": "Avisos individuales de departamentos en venta (backtest)",
    "mercado-inmobiliario": "Series oficiales: USD/m2 por barrio, alquileres, escrituras",
}


def resolve_resources(dataset: str) -> list[dict[str, str]]:
    r = httpx.get(CKAN, params={"id": dataset}, timeout=60)
    r.raise_for_status()
    body = r.json()
    if not body.get("success"):
        raise RuntimeError(f"CKAN devolvió success=false para {dataset}")
    result = body["result"]
    print(f"  licencia: {result.get('license_title')}")
    return [
        {"name": res["name"], "url": res["url"], "format": res.get("format", "")}
        for res in result["resources"]
        if res.get("format", "").upper() == "CSV" and res.get("url")
    ]


def wanted(name: str, years: range | None) -> bool:
    if years is None:
        return True
    return any(str(y) in name for y in years)


def download(url: str, dest: Path) -> tuple[int, str]:
    with httpx.stream("GET", url, timeout=180, follow_redirects=True) as r:
        r.raise_for_status()
        h = hashlib.sha256()
        size = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(65536):
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
    return size, h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Descarga datasets abiertos del GCBA")
    ap.add_argument("--years", default="2015-2020", help="rango, ej. 2015-2020, o 'all'")
    ap.add_argument("--out", default="data/raw/badata")
    args = ap.parse_args()

    years: range | None = None
    if args.years != "all":
        a, _, b = args.years.partition("-")
        years = range(int(a), int(b or a) + 1)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checksums_path = out / "checksums.json"
    checksums: dict[str, dict[str, object]] = (
        json.loads(checksums_path.read_text(encoding="utf-8")) if checksums_path.exists() else {}
    )

    total_bytes = 0
    for dataset, desc in DATASETS.items():
        print(f"\n{dataset}  —  {desc}")
        resources = resolve_resources(dataset)
        # La serie oficial no lleva año en el nombre: se baja siempre.
        targets = [
            r for r in resources if dataset == "mercado-inmobiliario" or wanted(r["name"], years)
        ]
        print(f"  {len(targets)} de {len(resources)} recursos CSV seleccionados")

        for res in targets:
            fname = res["url"].rsplit("/", 1)[-1]
            dest = out / fname
            if dest.exists() and fname in checksums:
                print(f"    ya está  {fname}")
                continue
            try:
                size, digest = download(res["url"], dest)
            except httpx.HTTPError as e:
                print(f"    ERROR    {fname}: {e}")
                dest.unlink(missing_ok=True)
                continue
            checksums[fname] = {"sha256": digest, "bytes": size, "url": res["url"]}
            total_bytes += size
            print(f"    ok       {fname:<52} {size / 1024 / 1024:6.1f} MB")

    # Los CSV no se versionan (pesan y se regeneran); los checksums sí, para
    # poder verificar que dos máquinas tienen exactamente los mismos datos.
    checksums_path.write_text(json.dumps(checksums, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\ndescargado ahora: {total_bytes / 1024 / 1024:.1f} MB")
    print(f"checksums en {checksums_path}  ({len(checksums)} archivos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
