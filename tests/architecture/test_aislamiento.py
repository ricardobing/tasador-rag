"""Cada tenant ve lo suyo y nada más.

Es el job que el CI llamaba "Aislamiento entre tenants" corriendo
`pytest tests/architecture -v` sobre un directorio VACÍO. El gate existía en el
YAML y no existía en el repo.

Importa más que la mayoría de los tests: `listings` es un corpus compartido a
propósito —`org_id` NULL es un aviso público, y por eso el costo marginal de un
cliente nuevo es casi cero (doc 17 §4.1)— pero `reports`, `subject_properties`
y `inventory_properties` son de un tenant y de uno solo. Una consulta a la que
se le cae el `org_id` no rompe nada visible: devuelve *más* filas, y quien la
mira ve datos donde esperaba datos.

Dos capas, porque una sola no alcanza:

  1. **Runtime** — se crean dos organizaciones con un informe cada una y se
     pide el de la otra. Prueba el comportamiento real de los endpoints.
  2. **Estática** — se revisa que ninguna consulta de `v1/` sobre una tabla con
     `org_id` se olvide el filtro. Cubre los endpoints que todavía no existen y
     los que se agreguen mañana, que es donde el bug va a entrar.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

RAIZ = Path(__file__).resolve().parents[2]
V1 = RAIZ / "src" / "tasador" / "v1"

# Tablas cuyo contenido pertenece a UN tenant. `listings`, `neighborhoods` y
# `market_index` no están: son corpus compartido y filtrarlas por org sería el
# bug opuesto.
MODELOS_POR_TENANT = {
    "Report",
    "SubjectProperty",
    "InventoryProperty",
    "ApiKey",
    "User",
    # Cuelgan de un informe y no tienen `org_id` propio: son de un tenant por
    # TRANSITIVIDAD. Se las dejaba afuera del conjunto, así que una consulta
    # `select(ReportEvent).where(ReportEvent.report_id == x)` sin dueño pasaba
    # sin que nadie mirara. Ver `_consultas_sin_org` para cómo se aceptan.
    "ReportEvent",
    "ReportComparable",
    "ReportArtifact",
}

TABLAS_HIJAS = {"ReportEvent", "ReportComparable", "ReportArtifact"}

# **La única excepción, y es una excepción de definición, no de conveniencia.**
#
# `v1/auth.py` es el módulo que RESUELVE el tenant: ahí el `org_id` es la
# salida de la consulta, no la entrada. Pedirle que filtre por `org_id` sería
# pedirle que sepa la respuesta antes de buscarla.
#
# Se acota por dos lados a la vez, porque exceptuar un archivo entero es
# exactamente cómo un gate se vuelve decorativo:
#
#   1. solo ESTE archivo, nombrado;
#   2. y solo si la consulta filtra por una CREDENCIAL — el prefijo de una API
#      key, el email, o el id que venía firmado en la sesión. Un
#      `select(User)` sin ninguna de las tres sigue fallando acá adentro.
RESOLUTOR_DE_TENANT = "auth.py"
COLUMNAS_DE_CREDENCIAL = ("ApiKey.prefix", "ApiKey.key_hash", "User.email", "User.id")


# ── 1. Runtime ───────────────────────────────────────────────────────────
async def _organizacion(session: AsyncSession, slug: str):
    from tasador.db.models import Organization

    org = Organization(name=slug.title(), slug=slug)
    session.add(org)
    await session.flush()
    return org


async def _informe(session: AsyncSession, org_id: uuid.UUID, direccion: str):
    from tasador.db.models import Report, SubjectProperty

    sujeto = SubjectProperty(org_id=org_id, address_raw=direccion, property_type="departamento")
    session.add(sujeto)
    await session.flush()
    informe = Report(
        org_id=org_id,
        subject_property_id=sujeto.id,
        status="QUEUED",
        engine_version="test",
        method_version="test",
        prompt_bundle_version="test",
    )
    session.add(informe)
    await session.flush()
    return informe


@pytest.fixture
def cliente(db: AsyncSession):
    """La app real, con la sesión apuntada a la base descartable del test."""
    from fastapi.testclient import TestClient

    from tasador.db.base import get_session
    from tasador.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c


async def test_un_tenant_no_puede_leer_el_informe_de_otro(db: AsyncSession, cliente):
    a = await _organizacion(db, "inmobiliaria-a")
    b = await _organizacion(db, "inmobiliaria-b")
    de_a = await _informe(db, a.id, "Av. Cabildo 2530")
    await db.commit()

    propio = cliente.get(f"/v1/reports/{de_a.id}", headers={"X-Org-Slug": "inmobiliaria-a"})
    assert propio.status_code == 200

    ajeno = cliente.get(f"/v1/reports/{de_a.id}", headers={"X-Org-Slug": "inmobiliaria-b"})
    # 404 y NO 403: responder "existe pero no es tuyo" le regala a quien prueba
    # ids la confirmación de que ese informe existe.
    assert ajeno.status_code == 404, (
        "el informe de otro tenant tiene que ser indistinguible de uno inexistente"
    )

    # Y "indistinguible" se mide comparando, no buscando el id en el cuerpo.
    #
    # La versión anterior afirmaba `str(de_a.id) not in ajeno.text`. Era un
    # proxy, y dejó de valer cuando los errores pasaron a ser RFC 7807 (H-26):
    # `instance` lleva la ruta pedida, y la ruta lleva el id — que es el id que
    # el cliente acaba de tipear, así que no le informa nada.
    #
    # Lo que hay que probar es que las dos respuestas sean la MISMA salvo por
    # ese eco. Si algún día una de las dos trajera un campo de más, un status
    # distinto o una cabecera distinta, quien prueba ids podría distinguir
    # "existe pero no es tuyo" de "no existe". Eso lo atrapa esto y no lo
    # atrapaba lo anterior.
    inexistente = uuid.uuid4()
    fantasma = cliente.get(f"/v1/reports/{inexistente}", headers={"X-Org-Slug": "inmobiliaria-b"})

    assert fantasma.status_code == ajeno.status_code
    assert fantasma.headers.get("content-type") == ajeno.headers.get("content-type")
    assert ajeno.text.replace(str(de_a.id), "ID") == fantasma.text.replace(str(inexistente), "ID")
    assert b is not None


async def test_el_listado_solo_trae_lo_del_tenant(db: AsyncSession, cliente):
    a = await _organizacion(db, "inmobiliaria-a")
    b = await _organizacion(db, "inmobiliaria-b")
    await _informe(db, a.id, "Av. Cabildo 2530")
    await _informe(db, a.id, "Juramento 1500")
    await _informe(db, b.id, "Thames 800")
    await db.commit()

    de_a = cliente.get("/v1/reports", headers={"X-Org-Slug": "inmobiliaria-a"}).json()
    de_b = cliente.get("/v1/reports", headers={"X-Org-Slug": "inmobiliaria-b"}).json()

    assert len(de_a["items"]) == 2
    assert len(de_b["items"]) == 1
    assert {i["address"] for i in de_a["items"]} == {"Av. Cabildo 2530", "Juramento 1500"}
    assert de_b["items"][0]["address"] == "Thames 800"


async def test_el_pdf_de_otro_tenant_tambien_da_404(db: AsyncSession, cliente):
    a = await _organizacion(db, "inmobiliaria-a")
    await _organizacion(db, "inmobiliaria-b")
    informe = await _informe(db, a.id, "Av. Cabildo 2530")
    await db.commit()

    r = cliente.get(f"/v1/reports/{informe.id}/pdf", headers={"X-Org-Slug": "inmobiliaria-b"})
    assert r.status_code == 404


# ── 2. Estática ──────────────────────────────────────────────────────────
def _archivos_v1() -> list[Path]:
    return sorted(p for p in V1.rglob("*.py") if p.name != "__init__.py")


# Las formas de pedirle filas a la base. `select` era la única que el gate
# reconocía; las otras tres no se usan hoy en `v1/` y por eso pasaba en verde
# **legítimamente** — un gate verde por ausencia del patrón, no por cobertura.
# El día que alguien escriba `sa.select(Report)` o `session.get(Report, id)`, el
# gate tiene que seguir mirando (H-31).
CONSTRUCTORES = ("select", "update", "delete", "get")


def _nombre_llamado(f: ast.expr) -> str | None:
    """El nombre de la función llamada, venga como `select(...)`,
    `sa.select(...)` o `session.get(...)`.

    El gate solo aceptaba `ast.Name`: `sa.select(Report)` es un `ast.Attribute`
    y se descartaba en la primera línea, sin mirarlo.
    """
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _modelos_por_tenant_en(args: list[ast.expr]) -> set[str]:
    """Los modelos por tenant nombrados como argumento, sea `Report` o
    `Report.id`."""
    out: set[str] = set()
    for a in args:
        if isinstance(a, ast.Name) and a.id in MODELOS_POR_TENANT:
            out.add(a.id)
        elif (
            isinstance(a, ast.Attribute)
            and isinstance(a.value, ast.Name)
            and a.value.id in MODELOS_POR_TENANT
        ):
            out.add(a.value.id)
    return out


def _consultas_sin_org(fuente: str, *, resuelve_tenant: bool = False) -> list[str]:
    """Consultas sobre un modelo por tenant a las que no se les ve el `org_id`.

    Heurística deliberadamente burda: busca la consulta y mira si en la misma
    sentencia aparece `org_id`. Un análisis de flujo real sería más exacto y
    muchísimo más frágil; esto atrapa el olvido, que es el error que de verdad
    ocurre.

    Lo que cubre, y por qué cada cosa:

      · `select(...)`, `sa.select(...)`   — la forma habitual y su alias
      · `update(...)`, `delete(...)`      — escriben, y un olvido acá es peor
      · `session.get(Modelo, id)`         — no pasa por `where`, así que el
                                            `org_id` no puede estar "más abajo"

    Las tablas HIJAS (`ReportEvent`, `ReportComparable`, `ReportArtifact`) son
    de un tenant por transitividad: no tienen `org_id` propio, lo heredan del
    informe. Se aceptan si la sentencia nombra `report_id` **y** la función
    resolvió antes el informe con `org_id`; si no, se marcan. Hoy el único uso
    es seguro solo porque doce líneas antes se verificó el dueño — y el gate no
    lo sabía.
    """
    arbol = ast.parse(fuente)
    culpables: list[str] = []

    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        nombre = _nombre_llamado(nodo.func)
        if nombre not in CONSTRUCTORES:
            continue

        # `session.get(Modelo, id)`: el modelo es el PRIMER argumento. En los
        # otros, cualquiera de los argumentos.
        args = nodo.args[:1] if nombre == "get" else nodo.args
        modelos = _modelos_por_tenant_en(args)
        hijas = {m for m in modelos if m in TABLAS_HIJAS}
        if not modelos:
            continue

        # La sentencia entera: `select(...).where(...).order_by(...)`.
        raiz = _raiz_de(arbol, nodo)
        sentencia = ast.unparse(raiz)
        if "org_id" in sentencia or "org.id" in sentencia:
            continue
        if resuelve_tenant and any(c in sentencia for c in COLUMNAS_DE_CREDENCIAL):
            continue
        # Una tabla hija cuelga del informe: alcanza con que la función haya
        # resuelto el informe con `org_id` y con que la consulta se ancle en él.
        if hijas == modelos and "report_id" in sentencia and _hay_informe_del_tenant(arbol, nodo):
            continue
        culpables.append(f"línea {nodo.lineno}: {nombre} sobre {sorted(modelos)} sin org_id")
    return culpables


def _hay_informe_del_tenant(arbol: ast.AST, objetivo: ast.AST) -> bool:
    """¿La función que contiene a `objetivo` resolvió antes el informe con
    `org_id`?

    Es la única concesión al flujo real, y acotada: se mira SOLO dentro de la
    misma función, y SOLO se acepta `Report.org_id`. Sin esto habría que
    duplicar el filtro en cada consulta a una tabla hija; con algo más laxo, el
    gate dejaría de medir.
    """
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if objetivo not in set(ast.walk(nodo)):
            continue
        cuerpo = ast.unparse(nodo)
        return "Report.org_id" in cuerpo
    return False


def _raiz_de(arbol: ast.AST, objetivo: ast.AST) -> ast.AST:
    """La sentencia que contiene a `objetivo`, para poder mirarla completa."""
    mejor: ast.AST = objetivo
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.stmt) and objetivo in set(ast.walk(nodo)):
            mejor = nodo
    return mejor


@pytest.mark.parametrize("archivo", _archivos_v1(), ids=lambda p: p.name)
def test_ninguna_consulta_de_la_api_pierde_el_filtro_de_tenant(archivo: Path):
    culpables = _consultas_sin_org(
        archivo.read_text(encoding="utf-8"),
        resuelve_tenant=archivo.name == RESOLUTOR_DE_TENANT,
    )
    assert not culpables, (
        f"{archivo.name} consulta datos de un tenant sin filtrar por org_id:\n  "
        + "\n  ".join(culpables)
    )


def test_la_excepcion_del_resolutor_de_tenant_no_es_un_permiso_general():
    """El test de la excepción.

    Exceptuar `auth.py` es correcto —ahí el `org_id` es la salida— pero si la
    excepción fuera "este archivo puede todo", mañana una consulta suelta sobre
    `User` pasaría sin que nadie lo note. Tiene que seguir haciendo falta una
    credencial.
    """
    con_credencial = "x = (await s.execute(select(User).where(User.email == e))).all()"
    sin_credencial = "x = (await s.execute(select(User).where(User.active.is_(True)))).all()"

    assert _consultas_sin_org(con_credencial, resuelve_tenant=True) == []
    assert len(_consultas_sin_org(sin_credencial, resuelve_tenant=True)) == 1
    # Y fuera del resolutor, ni siquiera con credencial.
    assert len(_consultas_sin_org(con_credencial, resuelve_tenant=False)) == 1


def test_la_heuristica_detecta_una_consulta_sin_filtro():
    """El test del test.

    Un chequeo estático que no se prueba a sí mismo es la forma más cómoda de
    tener un gate en verde que no mira nada: si mañana alguien renombra
    `select` o cambia el import, este archivo seguiría pasando sobre cero
    consultas encontradas y nadie se enteraría.
    """
    con_filtro = "x = (await s.execute(select(Report).where(Report.org_id == org.id))).all()"
    sin_filtro = "x = (await s.execute(select(Report).where(Report.status == 'QUEUED'))).all()"
    assert _consultas_sin_org(con_filtro) == []
    assert len(_consultas_sin_org(sin_filtro)) == 1


# ── Los tres huecos del gate (H-31) ──────────────────────────────────────
#
# El gate pasaba en verde, y lo hacía LEGÍTIMAMENTE: ninguno de estos tres
# patrones se usa hoy en `v1/`. Verde por ausencia del patrón, no por
# cobertura — que es la forma en que un gate deja de medir sin que nadie se
# entere. Cada caso de acá es una línea que alguien podría escribir mañana.


def test_el_gate_ve_el_select_con_prefijo_de_modulo():
    """`sa.select(Report)` es un `ast.Attribute`. El gate exigía `ast.Name` y
    lo descartaba en la primera línea, sin mirar el modelo."""
    sin_filtro = "x = (await s.execute(sa.select(Report).where(Report.id == i))).all()"
    con_filtro = "x = (await s.execute(sa.select(Report).where(Report.org_id == o))).all()"
    assert len(_consultas_sin_org(sin_filtro)) == 1
    assert _consultas_sin_org(con_filtro) == []


def test_el_gate_ve_las_escrituras():
    """Un `update` o un `delete` sin `org_id` es peor que un `select`: no
    devuelve filas de más, las cambia."""
    for fuente in (
        "await s.execute(update(Report).values(status='X'))",
        "await s.execute(delete(SubjectProperty).where(SubjectProperty.id == i))",
    ):
        assert len(_consultas_sin_org(fuente)) == 1, fuente

    ok = "await s.execute(update(Report).where(Report.org_id == o).values(status='X'))"
    assert _consultas_sin_org(ok) == []


def test_el_gate_ve_el_session_get():
    """`session.get(Report, id)` no pasa por un `where`, así que el `org_id` no
    puede estar "más abajo en la cadena": o está en la misma sentencia o no
    está."""
    assert len(_consultas_sin_org("r = await s.get(Report, report_id)")) == 1
    # El decorador de ruta también se llama `get` y no es una consulta.
    assert _consultas_sin_org('@router.get("/reports/{id}")\ndef f(): pass') == []


def test_el_gate_ve_las_tablas_hijas():
    """`ReportEvent`, `ReportComparable` y `ReportArtifact` no tienen `org_id`:
    son de un tenant por transitividad. Estaban fuera del conjunto, así que
    leerlas por `report_id` sin haber verificado el dueño pasaba en verde."""
    suelta = (
        "async def f():\n"
        "    e = (await s.execute(select(ReportEvent)"
        ".where(ReportEvent.report_id == rid))).all()\n"
    )
    assert len(_consultas_sin_org(suelta)) == 1, (
        "leer eventos por report_id sin haber resuelto el dueño tiene que marcarse"
    )

    # El uso real y seguro: la función resolvió antes el informe con `org_id`.
    con_dueno = (
        "async def f():\n"
        "    inf = (await s.execute(select(Report)"
        ".where(Report.id == rid, Report.org_id == org.id))).scalar_one_or_none()\n"
        "    e = (await s.execute(select(ReportEvent)"
        ".where(ReportEvent.report_id == rid))).all()\n"
    )
    assert _consultas_sin_org(con_dueno) == []


def test_la_excepcion_de_las_hijas_no_alcanza_para_las_tablas_con_org_id_propio():
    """La concesión al flujo real vale SOLO para las hijas. Un `select(Report)`
    en la misma función no se salva porque otra consulta haya nombrado
    `Report.org_id`: para eso está el filtro propio."""
    fuente = (
        "async def f():\n"
        "    a = (await s.execute(select(Report)"
        ".where(Report.id == x, Report.org_id == o))).all()\n"
        "    b = (await s.execute(select(SubjectProperty)"
        ".where(SubjectProperty.id == y))).all()\n"
    )
    culpables = _consultas_sin_org(fuente)
    assert len(culpables) == 1
    assert "SubjectProperty" in culpables[0]
