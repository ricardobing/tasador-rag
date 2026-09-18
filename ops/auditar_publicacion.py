"""Gate de publicación: ¿este repo se puede hacer público tal como está?

    uv run python ops/auditar_publicacion.py              # árbol versionado
    uv run python ops/auditar_publicacion.py --historial  # + todos los commits

Sale con 1 si encuentra algo BLOQUEANTE. Las advertencias no cortan: son
decisiones del humano (nombrar al cliente, publicar contenido de terceros) que
el script no puede tomar, solo mostrar.

Tres cosas que hace a propósito:

1. **Compara contra los VALORES reales del `.env` local**, no solo contra
   patrones. Un regex encuentra `sk-...`; no encuentra una contraseña de
   Postgres escrita en una guía. El 17/09 había tres así, y ningún escáner de
   patrones las vio porque no se parecen a nada.
2. **Nunca imprime un secreto.** Muestra el NOMBRE de la variable, el archivo y
   la línea. La salida de este script se puede pegar en un issue.
3. **Falla si no pudo medir.** Sin `.env` no puede hacer la comparación por
   valor, y lo dice en vez de pasar en verde (la regla de los gates de este
   proyecto: uno que puede pasar sin medir es peor que no tenerlo).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Valores del .env que NO son secretos aunque sean largos: configuración que
# ya vive, legítimamente, en `.env.example`.
NO_SECRETAS = {
    "ENV",
    "LOG_LEVEL",
    "DOMAIN",
    "GH_OWNER",
    "EMBEDDING_MODEL",
    "LANGFUSE_HOST",
    "LITELLM_BASE_URL",
    "NOMINATIM_URL",
    "NOMINATIM_USER_AGENT",
    "FETCH_USER_AGENT",
    "R2_BUCKET",
}
LARGO_MINIMO = 8

PATRONES_DE_SECRETO = {
    "clave de API (sk-...)": re.compile(r"sk-(?:or-v1-)?[A-Za-z0-9]{24,}"),
    "API key del producto (tsk_live_)": re.compile(r"tsk_live_[A-Za-z0-9_-]{24,}"),
    "token de GitHub": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "clave de AWS": re.compile(r"AKIA[0-9A-Z]{16}"),
    "clave privada": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\."),
}

# Decisiones del humano: se listan, no bloquean.
ADVERTENCIAS = {
    "email personal": re.compile(r"[A-Za-z0-9._%+-]+@gmail\.com"),
    "ruta local de la máquina": re.compile(r"C:\\\\?(?:tmp|Users)\\\\?", re.IGNORECASE),
}
# Palabras que no deberían aparecer (nombre del cliente, de las fuentes…), una
# por línea, en un archivo LOCAL que no se versiona: la lista es, ella misma,
# la información que se quiere mantener fuera del repo.
PALABRAS_PRIVADAS = RAIZ / "ops" / "palabras-privadas.txt"
if PALABRAS_PRIVADAS.exists():
    for _palabra in PALABRAS_PRIVADAS.read_text(encoding="utf-8").split():
        ADVERTENCIAS[f"nombra «{_palabra}»"] = re.compile(re.escape(_palabra), re.IGNORECASE)
# Contenido de terceros: un HTML de portal o un YAML con texto de avisos reales
# es redistribución, no código.
TERCEROS = ("tests/fixtures/", "tests/golden/privado/")
PESO_SOSPECHOSO = 100_000


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", errors="ignore"
    ).stdout


def _secretos_del_env() -> dict[str, str]:
    ruta = RAIZ / ".env"
    if not ruta.exists():
        return {}
    out: dict[str, str] = {}
    for linea in ruta.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in linea or linea.lstrip().startswith("#"):
            continue
        clave, valor = linea.split("=", 1)
        clave, valor = clave.strip(), valor.strip().strip("\"'")
        if clave in NO_SECRETAS or len(valor) < LARGO_MINIMO:
            continue
        out[clave] = valor
    # Las URLs con credencial adentro se cubren por su contraseña; la URL entera
    # solo agrega ruido.
    for compuesta in ("DATABASE_URL", "REDIS_URL"):
        out.pop(compuesta, None)
    suelta = RAIZ / "openrouter.txt"
    if suelta.exists():
        out["(openrouter.txt)"] = suelta.read_text(encoding="utf-8", errors="ignore").strip()
    # Un valor que también está en `.env.example` es un placeholder, no un secreto.
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8", errors="ignore")
    return {k: v for k, v in out.items() if v and v not in ejemplo}


def _revisar_texto(
    origen: str, texto: str, secretos: dict[str, str], bloqueantes: list[str]
) -> None:
    for n, linea in enumerate(texto.splitlines(), 1):
        for nombre, valor in secretos.items():
            if valor in linea:
                bloqueantes.append(f"{origen}:{n}  valor real de {nombre}")
        for nombre, rx in PATRONES_DE_SECRETO.items():
            if rx.search(linea):
                bloqueantes.append(f"{origen}:{n}  {nombre}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--historial", action="store_true", help="revisar también todos los commits")
    args = ap.parse_args()

    secretos = _secretos_del_env()
    if not secretos:
        print("✗ No hay .env (o no tiene valores): no se puede comparar por valor.")
        print("  Este gate no pasa sin medir. Correlo en la máquina que tiene el .env.")
        return 1

    archivos = [a for a in _git("ls-files").splitlines() if a]
    bloqueantes: list[str] = []
    avisos: dict[str, list[str]] = {k: [] for k in ADVERTENCIAS}
    terceros: list[str] = []

    for rel in archivos:
        ruta = RAIZ / rel
        if rel == "ops/auditar_publicacion.py" or not ruta.is_file():
            continue
        if Path(rel).name == ".env" or rel.endswith((".pem", ".key")):
            bloqueantes.append(f"{rel}  archivo de secretos versionado")
        if rel.startswith(TERCEROS) and ruta.stat().st_size > PESO_SOSPECHOSO:
            terceros.append(f"{rel}  ({ruta.stat().st_size // 1024} KB)")
        try:
            texto = ruta.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        _revisar_texto(rel, texto, secretos, bloqueantes)
        for nombre, rx in ADVERTENCIAS.items():
            if rx.search(texto):
                avisos[nombre].append(rel)

    if args.historial:
        parche = _git("log", "--all", "-p", "--format=COMMIT %h")
        commit = "?"
        vistos: set[tuple[str, str]] = set()
        for linea in parche.splitlines():
            if linea.startswith("COMMIT "):
                commit = linea.split()[1]
                continue
            if not linea.startswith("+"):
                continue
            for nombre, valor in secretos.items():
                if valor in linea and (commit, nombre) not in vistos:
                    vistos.add((commit, nombre))
                    bloqueantes.append(f"historial {commit}  valor real de {nombre}")
            for nombre, rx in PATRONES_DE_SECRETO.items():
                if rx.search(linea) and (commit, nombre) not in vistos:
                    vistos.add((commit, nombre))
                    bloqueantes.append(f"historial {commit}  {nombre}")

    print(f"Revisados {len(archivos)} archivos contra {len(secretos)} valores del .env local")
    print(
        f"y {len(PATRONES_DE_SECRETO)} patrones"
        + (" + historial completo." if args.historial else ".")
    )
    print()
    if bloqueantes:
        print(f"✗ BLOQUEANTES ({len(bloqueantes)}) — no publicar así:")
        for b in bloqueantes:
            print("   ", b)
    else:
        print("✓ Sin secretos en lo revisado.")
    print()
    if terceros:
        print(f"⚠ Contenido de terceros versionado ({len(terceros)}):")
        for t in terceros:
            print("   ", t)
    for nombre, lista in avisos.items():
        if lista:
            print(f"⚠ {nombre}: {len(lista)} archivos  (ej.: {', '.join(lista[:3])})")
    return 1 if bloqueantes else 0


if __name__ == "__main__":
    sys.exit(main())
