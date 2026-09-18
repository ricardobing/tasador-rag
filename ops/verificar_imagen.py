"""¿La imagen construida es el código del repo?

    uv run python ops/verificar_imagen.py
    uv run python ops/verificar_imagen.py --imagen ghcr.io/x/tasador-api:dev

Sale 1 si hay una sola diferencia. Pensado como paso del CI después del build,
y como chequeo antes de desplegar.

## Por qué existe

El 15/08 la imagen `tasador-api:dev` que `docker-compose.yml` levanta en
producción estaba **12 archivos atrás** y le faltaban **tres módulos enteros**:

    AUSENTES : tasador/v1/admin.py, tasador/v1/calidad.py, tasador/v1/inventory.py
    DISTINTOS: auth.py, reports.py, security.py, ingest/core.py,
               agents/nodes/dedup.py, agents/nodes/retrieve.py, main.py, …

Efecto medido levantando las dos: cinco endpoints daban 404 —`/calidad`,
`/admin/fuentes`, `/admin/organizacion`, `/admin/usuarios`,
`/inventory/snapshot`— y el rate limit del login no existía. Los cinco están
declarados ✅ en ESTADO §5.2.

En desarrollo no se veía porque el override monta `./src` por volumen. La causa
es simple: `docker compose up -d` **sin `--build`** reutiliza la imagen que hay,
y no había nada que comparara el tag con el código.

Es la forma generalizada de la lección del 14/08 —"cinco arreglos vivían solo en
el override de dev"— y esta vez no eran cinco variables de entorno.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

# Lo que la imagen tiene que traer idéntico. `templates/`, `prompts/` y
# `config/` también se copian y el grafo los lee en caliente, así que un
# desfase ahí cambia el informe sin cambiar una línea de Python.
DIRECTORIOS = {
    "src": ("/app/src", "*.py"),
    "config": ("/app/config", "*.yaml"),
    "prompts": ("/app/prompts", "*.jinja"),
    "templates": ("/app/templates", "*.jinja"),
}

_DENTRO = (
    "import hashlib,pathlib,json,sys;"
    "raiz=pathlib.Path(sys.argv[1]);"
    "print(json.dumps({str(p.relative_to(raiz)).replace(chr(92),'/'):"
    "hashlib.sha256(p.read_bytes()).hexdigest()[:16]"
    " for p in sorted(raiz.rglob(sys.argv[2]))}))"
)


def _en_la_imagen(imagen: str, ruta: str, patron: str) -> dict[str, str]:
    salida = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", imagen, "-c", _DENTRO, ruta, patron],
        capture_output=True,
        text=True,
        check=True,
    )
    datos: dict[str, str] = json.loads(salida.stdout)
    return datos


def _en_el_repo(carpeta: str, patron: str) -> dict[str, str]:
    raiz = pathlib.Path(carpeta)
    return {
        str(p.relative_to(raiz)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(raiz.rglob(patron))
    }


def verificar(imagen: str) -> int:
    problemas = 0
    for carpeta, (ruta, patron) in DIRECTORIOS.items():
        try:
            img = _en_la_imagen(imagen, ruta, patron)
        except subprocess.CalledProcessError as e:
            print(f"  {carpeta:<12} NO SE PUDO LEER de {imagen}: {e.stderr.strip()[:120]}")
            problemas += 1
            continue
        repo = _en_el_repo(carpeta, patron)
        faltan = sorted(set(repo) - set(img))
        sobran = sorted(set(img) - set(repo))
        distintos = sorted(k for k in repo if k in img and img[k] != repo[k])
        estado = "OK" if not (faltan or sobran or distintos) else "DERIVA"
        print(f"  {carpeta:<12} repo {len(repo):>3}  imagen {len(img):>3}  {estado}")
        for k in faltan:
            print(f"      FALTA en la imagen : {carpeta}/{k}")
        for k in sobran:
            print(f"      SOBRA en la imagen : {carpeta}/{k}")
        for k in distintos:
            print(f"      DISTINTO           : {carpeta}/{k}")
        problemas += len(faltan) + len(sobran) + len(distintos)
    return problemas


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--imagen",
        action="append",
        default=None,
        help="repetible. Por defecto, las de api y worker del compose",
    )
    args = ap.parse_args()
    imagenes = args.imagen or [
        "ghcr.io/ricardobrossard/tasador-api:dev",
        "ghcr.io/ricardobrossard/tasador-worker:dev",
    ]

    total = 0
    for img in imagenes:
        print(f"\n{img}")
        total += verificar(img)

    print()
    if total:
        print(f"🔴 {total} diferencia(s) entre la imagen y el repo.")
        print("   `docker compose build` y volver a correr. Desplegar así es")
        print("   desplegar código que nadie revisó.")
        return 1
    print("✅ Las imágenes son el repo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
