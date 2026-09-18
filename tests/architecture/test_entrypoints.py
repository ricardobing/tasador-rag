"""Todo comando que el proyecto le pide a alguien que corra, existe.

Este archivo nace de una auditoría del 14/08 que encontró **siete** comandos
documentados que fallaban al ejecutarlos:

    make seed        -> python -m tasador.scripts.seed     (módulo inexistente)
    make eval        -> python -m tasador.eval.run         (módulo inexistente)
    make backup      -> bash ops/backup.sh                 (archivo inexistente)
    CI eval-gate     -> python -m tasador.eval.run         (módulo inexistente)
    CI aislamiento   -> pytest tests/architecture          (directorio VACÍO)
    make dev / up    -> docker/web.Dockerfile              (archivo inexistente)
    ESTADO §8 dev    -> lo mismo: el stack no levantaba

Ninguno rompía un test, porque ningún test los ejecutaba. Un Makefile que
miente cuesta más que uno que no existe: el que lee la doc asume que el
problema es su máquina.

El test NO ejecuta los comandos (algunos levantan Docker o gastan en APIs).
Verifica lo único que se puede verificar barato y es lo que fallaba: que el
módulo se importe, que el archivo esté, y que el directorio de tests tenga
tests adentro.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]

# Los archivos donde el proyecto le dice a alguien —o a un cron— qué correr.
#
# ⚠️ `ops/crontab` entró el 15/08 y encontró TRES módulos inexistentes de cuatro
# líneas activas: `tasador.ingest.run`, `tasador.corpus.bias_check` y
# `tasador.ops.retention` (ni siquiera existe el paquete `tasador.ops`). Un cron
# roto dentro de un contenedor falla en silencio, que es peor que no tenerlo: el
# ausente se nota cuando alguien pregunta "¿y el backup?".
FUENTES = [
    RAIZ / "Makefile",
    RAIZ / "make.ps1",
    RAIZ / ".github" / "workflows" / "ci.yml",
    RAIZ / "docs" / "ESTADO-2026-08.md",
    RAIZ / "ops" / "crontab",
]

_MODULO = re.compile(r"python\s+-m\s+([\w.]+)")
_SCRIPT = re.compile(r"python\s+(scripts/[\w/]+\.py)")
_SHELL = re.compile(r"bash\s+(ops/[\w/]+\.sh)")
_PYTEST = re.compile(r"pytest\s+(tests/[\w/]+(?:\.py)?)")


def _texto(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _sin_comentarios(texto: str) -> str:
    r"""Las líneas ACTIVAS: se descartan las que arrancan con `#`.

    Una línea comentada en un crontab está apagada a propósito y —en este repo—
    con el motivo escrito al lado. Contarla como "comando documentado" pondría
    el gate en rojo por algo que ya se decidió no correr.

    El riesgo es obvio y hay que nombrarlo: alguien puede comentar una línea
    para callar el test en vez de arreglarla. Contra eso no hay regex; hay
    diff. Lo que sí evita este gate es lo que pasó de verdad —tres módulos
    inexistentes en líneas ACTIVAS del crontab, fallando en silencio dentro de
    un contenedor durante dos días.
    """
    return "\n".join(ln for ln in texto.splitlines() if not ln.lstrip().startswith("#"))


def _todo() -> str:
    return "\n".join(_sin_comentarios(_texto(p)) for p in FUENTES)


def _referencias(patron: re.Pattern[str]) -> set[str]:
    return set(patron.findall(_todo()))


def test_hay_fuentes_que_auditar():
    """Si alguien renombra el Makefile, este archivo no puede quedar pasando en
    verde sin auditar nada."""
    presentes = [p.name for p in FUENTES if p.exists()]
    assert len(presentes) >= 4, f"faltan archivos de comandos: {presentes}"


def test_se_estan_auditando_comandos_de_verdad():
    """El test del test.

    `_sin_comentarios` filtra líneas; si filtrara de más —o si una expresión se
    quedara vieja— este archivo pasaría en verde sobre cero comandos, que es
    exactamente el modo de falla que vino a cerrar.
    """
    assert len(_referencias(_MODULO)) >= 1, "no se detecta ni un `python -m`"
    assert len(_referencias(_SCRIPT)) >= 3, "no se detectan los scripts documentados"
    assert len(_referencias(_SHELL)) >= 1, "no se detecta `bash ops/*.sh`"


def test_las_lineas_comentadas_no_cuentan_pero_las_activas_si():
    """Un cron apagado a propósito no es un comando documentado; uno activo sí."""
    activo = "0 7 * * *  python -m tasador.eval.run --dataset X"
    apagado = "# 0 7 * * *  python -m tasador.no.existe --alert"
    assert _MODULO.findall(_sin_comentarios(activo)) == ["tasador.eval.run"]
    assert _MODULO.findall(_sin_comentarios(apagado)) == []
    # Y la indentación no lo salva.
    assert _MODULO.findall(_sin_comentarios("   #  python -m tasador.no.existe")) == []


@pytest.mark.parametrize("modulo", sorted(_referencias(_MODULO)))
def test_los_modulos_invocados_con_python_m_existen(modulo: str):
    """`python -m tasador.eval.run` en el CI y en `make eval`. No existía, y el
    gate de calidad de doc 09 §5 fallaba en todo PR que tocara un prompt."""
    assert importlib.util.find_spec(modulo) is not None, (
        f"algún comando invoca `python -m {modulo}` y el módulo no existe"
    )


@pytest.mark.parametrize("script", sorted(_referencias(_SCRIPT)))
def test_los_scripts_invocados_existen(script: str):
    assert (RAIZ / script).exists(), f"algún comando invoca `{script}` y el archivo no existe"


@pytest.mark.parametrize("sh", sorted(_referencias(_SHELL)))
def test_los_shell_scripts_invocados_existen(sh: str):
    """`make backup` invocaba `ops/backup.sh`. Un backup que no corre es peor
    que no tener backup: se cree que está."""
    assert (RAIZ / sh).exists(), f"algún comando invoca `bash {sh}` y el archivo no existe"


@pytest.mark.parametrize("ruta", sorted(_referencias(_PYTEST)))
def test_los_directorios_de_test_del_ci_tienen_tests(ruta: str):
    """`pytest tests/architecture` sobre un directorio vacío devuelve exit 5.

    Es el peor modo de falla posible para un gate: el job se pone en rojo
    diciendo "no tests ran", que se lee como un problema de configuración y no
    como lo que era — que el gate de aislamiento multi-tenant no existía.
    """
    destino = RAIZ / ruta
    assert destino.exists(), f"el CI corre `pytest {ruta}` y esa ruta no existe"
    if destino.is_dir():
        archivos = list(destino.rglob("test_*.py"))
        assert archivos, f"el CI corre `pytest {ruta}` y no hay ni un test adentro (exit 5)"


def test_los_dockerfiles_del_compose_existen():
    """`docker compose build` fallaba entero por un Dockerfile ausente, y con él
    `make dev`, `make up` y la instrucción de arranque de ESTADO §8."""
    compose = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
    faltan = []
    for nombre, svc in (compose.get("services") or {}).items():
        build = svc.get("build")
        if not isinstance(build, dict):
            continue
        contexto = (RAIZ / build.get("context", ".")).resolve()
        archivo = build.get("dockerfile", "Dockerfile")
        if not (contexto / archivo).resolve().exists():
            faltan.append(f"{nombre} -> {archivo} (contexto {build.get('context')})")
    assert not faltan, f"servicios del compose sin Dockerfile: {faltan}"
