"""Lo que las imágenes tienen que traer, y que solo se rompe en producción.

Tres bugs de la Etapa 3 (§11.3) y cuatro más (§12.2) existían **únicamente
dentro del contenedor**. Los arreglos quedaron en dos lugares distintos: en los
Dockerfile y en el bloque `environment` de `docker-compose.dev.yml`.

El 14/08 se descubrió que los de los Dockerfile no estaban aplicados: la línea

    ENV XDG_CACHE_HOME=... HOME=/tmp/home \\n    CREWAI_DISABLE_TELEMETRY=true

tenía un `\\n` LITERAL, Docker se comía la instrucción entera y ninguna de las
cinco variables llegaba a la imagen. Comprobado sobre la imagen construida:

    docker image inspect ... --format '{{json .Config.Env}}'
    -> ni XDG_CACHE_HOME ni HOME ni CREWAI_DISABLE_TELEMETRY

Y no se notaba porque el override de desarrollo las declara igual. O sea:
andaba en dev y en **producción** volvían los tres bugs — fontconfig sin caché,
CrewAI con `PermissionError` en `$HOME`, y la telemetría de CrewAI ENCENDIDA,
que doc 10 §3 no permite porque este sistema procesa datos de clientes.

Estos tests son estáticos: leen los Dockerfile, no construyen nada. No
reemplazan verificar en el contenedor —nada lo reemplaza— pero atajan la clase
de error que hace que "en dev anda" sea una respuesta.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
DOCKERFILES = sorted((RAIZ / "docker").glob("*.Dockerfile"))

# Las que los bugs 13, 14, 17, 18 y 19 dejaron como obligatorias. `HOME` está
# porque CrewAI toca $HOME aunque la telemetría esté apagada (bug 19).
ENV_OBLIGATORIAS = {
    "api.Dockerfile": {"XDG_CACHE_HOME", "XDG_DATA_HOME", "HOME", "CREWAI_DISABLE_TELEMETRY"},
    "worker.Dockerfile": {"XDG_CACHE_HOME", "XDG_DATA_HOME", "HOME", "CREWAI_DISABLE_TELEMETRY"},
}

_ENV = re.compile(r"^\s*ENV\s+(.*)$", re.MULTILINE)


def _env_declaradas(texto: str) -> set[str]:
    """Los nombres de variable que la imagen realmente declara.

    Se parsea igual que Docker: `ENV A=1 B=2` declara dos; una continuación de
    línea es un `\\` al FINAL de la línea, no un `\\n` en el medio.
    """
    nombres: set[str] = set()
    for cuerpo in _ENV.findall(texto):
        for token in cuerpo.split():
            if "=" in token:
                nombres.add(token.split("=", 1)[0])
    return nombres


@pytest.mark.parametrize("df", DOCKERFILES, ids=lambda p: p.name)
def test_ninguna_instruccion_tiene_un_salto_de_linea_literal(df: Path):
    r"""`\n` escrito como dos caracteres dentro de una instrucción.

    Docker no lo interpreta como salto: se come el resto de la instrucción sin
    avisar y la imagen sale sin lo que uno cree que le puso. Silencioso, y solo
    visible con `docker image inspect`.
    """
    texto = df.read_text(encoding="utf-8")
    culpables = [
        f"línea {i}: {linea.strip()[:90]}"
        for i, linea in enumerate(texto.splitlines(), 1)
        if "\\n" in linea and not linea.lstrip().startswith("#")
    ]
    assert not culpables, f"`\\n` literal en {df.name}: {culpables}"


@pytest.mark.parametrize("df", DOCKERFILES, ids=lambda p: p.name)
def test_las_variables_de_entorno_criticas_estan_declaradas(df: Path):
    """Que estén en `docker-compose.dev.yml` no alcanza: producción no usa ese
    override, y es justo donde corren CrewAI y WeasyPrint."""
    esperadas = ENV_OBLIGATORIAS.get(df.name)
    if esperadas is None:
        pytest.skip(f"{df.name} no tiene requisitos declarados")
    faltan = esperadas - _env_declaradas(df.read_text(encoding="utf-8"))
    assert not faltan, f"{df.name} no declara {sorted(faltan)} — en producción no van a existir"


@pytest.mark.parametrize("df", DOCKERFILES, ids=lambda p: p.name)
def test_la_imagen_no_corre_como_root(df: Path):
    texto = df.read_text(encoding="utf-8")
    usuarios = re.findall(r"^\s*USER\s+(\S+)", texto, re.MULTILINE)
    assert usuarios, f"{df.name} no declara USER: corre como root"
    assert usuarios[-1] != "root", f"{df.name} termina en USER root"


@pytest.mark.parametrize("df", DOCKERFILES, ids=lambda p: p.name)
def test_las_imagenes_traen_lo_que_los_nodos_leen_en_runtime(df: Path):
    """El bug 12: `templates/` no estaba en NINGUNA imagen y el nodo 11 fallaba
    en producción con `TemplateNotFound`. En Windows el path resolvía al repo y
    parecía andar.

    Los cuatro directorios son los que el grafo abre en caliente: config y
    prompts los lee `agents.yaml`, templates lo lee el nodo 11.
    """
    if df.name == "web.Dockerfile":
        pytest.skip("el front no lee ninguno de estos")
    texto = df.read_text(encoding="utf-8")
    for d in ("src/", "config/", "prompts/", "templates/"):
        assert re.search(rf"^\s*COPY\s+.*\s{re.escape(d)}", texto, re.MULTILINE), (
            f"{df.name} no copia {d}: el grafo lo lee en runtime y solo falla en el contenedor"
        )


# ── Lo que agregó la auditoría del 15/08 ─────────────────────────────────
_MKDIR_DATA = re.compile(r"mkdir\s+-p\s+([^\n&]*?/data/[^\n&]*)")


def _dirs_de_data(texto: str) -> set[str]:
    """Los `/data/...` que el Dockerfile crea."""
    out: set[str] = set()
    for cuerpo in _MKDIR_DATA.findall(texto):
        out.update(t for t in cuerpo.split() if t.startswith("/data/"))
    return out


# Los que `settings.data_path` y `settings.artifacts_path` AUTODETECTAN. No es
# la lista completa de `/data/...`: `/data/models` es del worker (caché de
# embeddings) y está bien que la API no lo tenga. Lo que no puede pasar es que
# una autodetección dé distinto según la imagen.
DATA_AUTODETECTADOS = ("/data/raw", "/data/artifacts")


@pytest.mark.parametrize("df", DOCKERFILES, ids=lambda p: p.name)
def test_las_rutas_que_settings_autodetecta_existen_en_las_dos_imagenes(df: Path):
    """`settings.data_path` autodetecta `/data/raw`; `artifacts_path`, `/data/artifacts`.

    `api.Dockerfile` creaba solo `/data/artifacts`, así que la API resolvía
    `data_path` a `/app/data` —que con `read_only: true` no es escribible— y el
    worker a `/data/raw`. Dos imágenes del mismo sistema autodetectando cosas
    distintas. Quedó en la traza como
    `PermissionError: [Errno 13] Permission denied: '/app/data'`.
    """
    if df.name == "web.Dockerfile":
        pytest.skip("el front no escribe en /data")
    creados = _dirs_de_data(df.read_text(encoding="utf-8"))
    faltan = [d for d in DATA_AUTODETECTADOS if d not in creados]
    assert not faltan, (
        f"{df.name} no crea {faltan}: `settings` los autodetecta y sin ellos "
        f"cae a una ruta distinta que la otra imagen"
    )


# `compose` en cualquier forma: `docker compose`, `$(COMPOSE)`, `$(COMPOSE_DEV)`,
# `$compose` de make.ps1. La primera versión de esta expresión pedía `compose`
# en minúscula y por lo tanto **no matcheaba ni una línea del Makefile**, que usa
# `$(COMPOSE)`. El gate pasaba en verde sobre cero comandos; lo agarró su propio
# self-test, que es exactamente para lo que está.
_COMANDO_EN_CONTENEDOR = re.compile(
    r"compose[^\n]*?(?:run|exec)\b[^\n]*?\b(api|worker|postgres)\b[^\n]*?"
    r"(?:python|bash|sh)\s+((?:scripts|ops)/[\w/.-]+)",
    re.IGNORECASE,
)


def test_todo_comando_que_corre_en_un_contenedor_tiene_su_archivo_en_esa_imagen():
    r"""El gate que faltaba, y es la contracara de `test_entrypoints.py`.

    Aquel verifica que `ops/backup.sh` exista **en el repo**. Existía. Lo que no
    verificaba —y es lo que fallaba— es que el CONTENEDOR que lo corre lo tenga:

        make backup  ->  compose run api bash ops/backup.sh
        api.Dockerfile no copiaba ops/  ->  "No such file or directory"

    Un gate que comprueba lo que no falla ocupa el lugar del que hacía falta.
    """
    fuentes = "\n".join(
        (RAIZ / n).read_text(encoding="utf-8")
        for n in ("Makefile", "make.ps1", "docs/ESTADO-2026-08.md")
        if (RAIZ / n).exists()
    )
    encontrados = set(_COMANDO_EN_CONTENEDOR.findall(fuentes))
    # Si no se encontró NINGUNO, este test no está midiendo nada: es más
    # probable que la expresión se haya quedado vieja que que el proyecto haya
    # dejado de correr scripts en contenedores.
    assert encontrados, (
        "no se encontró ni un comando `compose run/exec <servicio> python|bash <ruta>`: "
        "revisar `_COMANDO_EN_CONTENEDOR`, porque este gate estaría pasando sobre cero casos"
    )
    faltan: list[str] = []
    for servicio, ruta in encontrados:
        if servicio == "postgres":
            continue  # se le montan por volumen, no por COPY
        df = RAIZ / "docker" / f"{servicio}.Dockerfile"
        if not df.exists():
            continue
        carpeta = ruta.split("/", 1)[0] + "/"
        if not re.search(
            rf"^\s*COPY\s+.*\s{re.escape(carpeta)}", df.read_text(encoding="utf-8"), re.MULTILINE
        ):
            faltan.append(f"`{ruta}` corre en `{servicio}` y {df.name} no copia {carpeta}")
    assert not faltan, "comandos que el contenedor no puede ejecutar:\n  " + "\n  ".join(faltan)


_ENV_CON_COMENTARIO = re.compile(r"^([A-Z_][A-Z0-9_]*)=.*\S+[ \t]+#", re.MULTILINE)


@pytest.mark.parametrize("nombre", [".env.example"])
def test_el_env_no_tiene_comentarios_al_final_de_la_linea(nombre: str):
    """`docker --env-file` NO interpreta comentarios en línea.

    `.env.example` tenía `ENV=development   # development | production` y el
    valor que llegaba a pydantic era la línea entera. En producción, donde el
    compose pasa `env_file: [.env]` sin pisar `ENV`, los cuatro servicios morían
    al arrancar. En desarrollo no se veía porque el override declara
    `ENV: development` en su bloque `environment`.
    """
    p = RAIZ / nombre
    if not p.exists():
        pytest.skip(f"{nombre} no está en el repo")
    culpables = _ENV_CON_COMENTARIO.findall(p.read_text(encoding="utf-8"))
    assert not culpables, (
        f"{nombre}: estas claves llevan el comentario pegado al valor y "
        f"`docker --env-file` se lo lleva adentro: {culpables}"
    )


# ── Los tests de los tests ───────────────────────────────────────────────
# Un chequeo estático que no se prueba a sí mismo con un caso que DEBE fallar
# es la forma más cómoda de tener un gate en verde que no mira nada. El patrón
# viene de `test_aislamiento.py::test_la_heuristica_detecta_una_consulta_sin_filtro`.


def test_la_deteccion_de_comentarios_en_linea_funciona():
    bien = "# development | production\nENV=development\nDOMAIN=x  \n"
    mal = "ENV=development                  # development | production\n"
    assert _ENV_CON_COMENTARIO.findall(bien) == []
    assert _ENV_CON_COMENTARIO.findall(mal) == ["ENV"]
    # Un `#` que es parte del valor no cuenta: solo el separado por espacios.
    assert _ENV_CON_COMENTARIO.findall("SECRET_KEY=abc#def\n") == []


def test_la_deteccion_de_directorios_de_data_funciona():
    assert _dirs_de_data("RUN mkdir -p /data/artifacts && chown -R app:app /data") == {
        "/data/artifacts"
    }
    assert _dirs_de_data("RUN mkdir -p /data/artifacts /data/raw && chown -R app:app /data") == {
        "/data/artifacts",
        "/data/raw",
    }
    assert _dirs_de_data("RUN echo sin mkdir") == set()


def test_la_deteccion_de_comandos_en_contenedor_funciona():
    assert ("api", "scripts/seed.py") in set(
        _COMANDO_EN_CONTENEDOR.findall("$(COMPOSE) run --rm api python scripts/seed.py")
    )
    assert ("api", "ops/backup.sh") in set(
        _COMANDO_EN_CONTENEDOR.findall("$(COMPOSE) run --rm api bash ops/backup.sh")
    )
    assert ("worker", "scripts/eval_extraccion.py") in set(
        _COMANDO_EN_CONTENEDOR.findall(
            "docker compose exec worker python scripts/eval_extraccion.py"
        )
    )
    # Y no confunde un `python -m` con un script.
    assert not _COMANDO_EN_CONTENEDOR.findall(
        "$(COMPOSE) run --rm worker python -m tasador.eval.run"
    )
