"""Healthcheck del contenedor.

Se ejecuta como `python -m tasador.healthcheck` desde el HEALTHCHECK de Docker.
Se usa esto en vez de curl para no instalar curl en la imagen final.

Exit 0 = sano. Exit 1 = enfermo (Docker reinicia).
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request


def main() -> int:
    # URL fija embebida, no viene de input del usuario -> S310 no aplica
    url = "http://127.0.0.1:8000/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return 0 if r.status == 200 else 1
    except (urllib.error.URLError, TimeoutError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
