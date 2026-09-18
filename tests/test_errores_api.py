"""Todo error de la API es RFC 7807 — H-26, doc 06 §3.

El contrato promete `application/problem+json` con
`{type, title, status, detail, instance, errors[]}`. Hasta el 15/08 lo cumplía
UN handler, el de 500; todo lo demás salía con el `{"detail": ...}` por defecto
de FastAPI, donde `detail` es a veces un string y a veces una lista de objetos
de pydantic.

El consumidor de este contrato es el panel de la inmobiliaria: ~205 líneas de `fetch`
tipado escritas contra lo que dice doc 06. Un cliente que busca `title` y
`status` encontraba `detail`.

Y el 422 de validación **filtraba la estructura interna del modelo**: `loc` con
la ruta dentro del pydantic, el tipo de error, el `ctx`, y el `input` completo
que se había mandado.

Este test recorre TODAS las formas de fallar que la API tiene y afirma el
formato en todas. Que un endpoint nuevo salga con el default de FastAPI es
exactamente el agujero que esto cierra.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def cliente(db: AsyncSession):
    from fastapi.testclient import TestClient

    from tasador.db.base import get_session
    from tasador.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def org(db: AsyncSession):
    from tasador.db.models import Organization

    o = Organization(name="la inmobiliaria", slug="inmo-demo")
    db.add(o)
    await db.commit()
    return o


CABECERAS = {"X-Org-Slug": "inmo-demo"}

# Cada forma de fallar que la API tiene hoy, con el mecanismo que la produce.
# La columna de la derecha es la que importa: si dos filas comparten mecanismo,
# una sola las cubre; lo que no está acá es lo que se puede escapar.
CASOS = [
    # (método, ruta, cuerpo, status, mecanismo)
    ("GET", f"/v1/reports/{uuid.uuid4()}", None, 404, "HTTPException de un endpoint"),
    ("GET", "/v1/no-existe", None, 404, "404 de ruta, lo levanta Starlette"),
    ("GET", "/v1/reports/no-es-un-uuid", None, 422, "validación de path"),
    ("GET", "/v1/reports?limit=99999", None, 422, "validación de query"),
    ("GET", "/v1/reports?cursor=basura", None, 422, "HTTPException con detalle propio"),
    ("POST", "/v1/reports", {}, 422, "validación de body"),
    ("POST", "/v1/reports", {"property": {"address_raw": ""}}, 422, "validación anidada"),
    ("GET", f"/v1/reports/{uuid.uuid4()}/pdf", None, 404, "HTTPException en un subrecurso"),
]


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo", "status", "mecanismo"), CASOS)
def test_todo_error_sale_como_problem_json(
    cliente, org, metodo: str, ruta: str, cuerpo: dict | None, status: int, mecanismo: str
) -> None:
    r = cliente.request(metodo, ruta, json=cuerpo, headers=CABECERAS)

    assert r.status_code == status, mecanismo
    assert r.headers["content-type"].startswith("application/problem+json"), mecanismo

    b = r.json()
    assert set(b) >= {"type", "title", "status", "detail", "instance"}, mecanismo
    assert b["status"] == status
    assert b["instance"] == ruta.split("?")[0]
    assert isinstance(b["title"], str) and b["title"]


def test_el_401_tambien(cliente, db: AsyncSession) -> None:
    """La autenticación falla antes de llegar al endpoint: es el camino que más
    fácil se olvida al cambiar el formato de error."""
    r = cliente.get("/v1/reports", headers={"Authorization": "Bearer no-existe"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Unauthorized"


def test_el_422_no_filtra_las_tripas_de_pydantic(cliente, org) -> None:
    """Lo que el default de FastAPI mandaba al cliente:

        {"detail": [{"type": "missing", "loc": ["body","property"],
                     "msg": "Field required", "input": {...}, "url": "..."}]}

    `input` es el cuerpo que el cliente mandó, de vuelta. Con una dirección
    real adentro, eso es un dato del cliente rebotando en un mensaje de error.
    """
    r = cliente.post("/v1/reports", json={"secreto": "Av. Siempreviva 742"}, headers=CABECERAS)
    assert r.status_code == 422

    b = r.json()
    assert b["errors"], "un 422 sin `errors[]` no le dice al cliente qué arreglar"
    for e in b["errors"]:
        assert set(e) == {"field", "message"}, f"campo de más en el error: {e}"

    entero = r.text
    assert "Siempreviva" not in entero, "el 422 devolvió el cuerpo del cliente"
    for interno in ("ctx", '"input"', "pydantic", "value_error", "url"):
        assert interno not in entero, f"el 422 filtra `{interno}`"


def test_el_detail_sigue_estando_donde_estaba(cliente, org) -> None:
    """RFC 7807 conserva `detail`, así que el panel y los tests que ya lo leen
    no se rompen. Es la razón por la que este cambio no es breaking."""
    r = cliente.get(f"/v1/reports/{uuid.uuid4()}", headers=CABECERAS)
    assert r.json()["detail"] == "No existe ese informe."


def test_el_500_no_cuenta_nada() -> None:
    """El handler que ya cumplía el contrato tiene que seguir cumpliéndolo, y
    seguir sin filtrar internals.

    Se monta una ruta que explota de verdad en vez de parchear una interna: un
    monkeypatch que no alcanza el camino real deja el test pasando sin haber
    medido nada, que es peor que no tenerlo.
    """
    from fastapi.testclient import TestClient

    from tasador.main import create_app

    app = create_app()

    @app.get("/v1/_explota_a_proposito")
    async def _boom() -> None:
        raise RuntimeError("credenciales=hunter2")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/v1/_explota_a_proposito")

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "hunter2" not in r.text, "el 500 filtró el mensaje de la excepción"
    assert "Traceback" not in r.text
    assert r.json()["detail"] == "Ocurrió un error inesperado."
