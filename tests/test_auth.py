"""Autenticación — doc 06 §1.

Hasta el 14/08 el tenant salía del header `X-Org-Slug`, sin secreto: **quien
supiera el slug de otra inmobiliaria veía sus informes.** No era un agujero
sutil, era la ausencia de autenticación, y volvía teatro al gate de aislamiento
multi-tenant — que verifica que las consultas filtren por `org_id`, cosa que
hacían, sobre un `org_id` que elegía el cliente.

El test que más vale de este archivo es
`test_en_produccion_el_header_de_desarrollo_NO_alcanza`. El camino viejo sigue
existiendo para los tests y los scripts, y lo único que impide que sea un
agujero en producción es una condición sobre `env`. Si alguien la borra, esto
falla.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.security import (
    generar_api_key,
    hash_password,
    leer_sesion,
    prefijo_de,
    verificar_api_key,
    verificar_password,
)


@pytest.fixture
def cliente(db: AsyncSession):
    from fastapi.testclient import TestClient

    from tasador.db.base import get_session
    from tasador.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c


async def _org(db: AsyncSession, slug: str = "inmo-demo"):
    from tasador.db.models import Organization

    o = Organization(name=slug.title(), slug=slug)
    db.add(o)
    await db.flush()
    return o


async def _usuario(db: AsyncSession, org_id, email: str, clave: str | None = "buena-clave-1"):
    from tasador.db.models import User

    u = User(
        org_id=org_id,
        email=email,
        password_hash=hash_password(clave) if clave else None,
        role="agent",
    )
    db.add(u)
    await db.flush()
    return u


# ── Primitivas ───────────────────────────────────────────────────────────
def test_el_hash_de_password_es_argon2id():
    """doc 10 §3 lo fija. El prefijo del hash lo dice."""
    h = hash_password("una-clave")
    assert h.startswith("$argon2id$")
    assert verificar_password(h, "una-clave")
    assert not verificar_password(h, "otra-clave")


def test_dos_hasheos_de_la_misma_clave_dan_distinto():
    """Argon2 lleva sal. Es la razón por la que las API keys se buscan por
    PREFIJO y no por `key_hash`: buscar por el hash no puede funcionar."""
    assert hash_password("x") != hash_password("x")


def test_un_usuario_sin_password_no_entra_ni_con_la_cadena_vacia():
    """`password_hash` NULL = usuario creado por integración, sin login propio
    (doc 03). No es "entra con cualquier cosa"."""
    assert not verificar_password(None, "")
    assert not verificar_password(None, "loquesea")


def test_la_api_key_se_muestra_una_vez_y_se_guarda_hasheada():
    clave, prefijo, hash_ = generar_api_key()
    assert clave.startswith("tsk_live_")
    assert prefijo_de(clave) == prefijo
    assert clave not in hash_, "la clave en claro no puede estar dentro del hash"
    assert verificar_api_key(clave, hash_)
    assert not verificar_api_key(clave + "x", hash_)


def test_el_email_se_valida_de_verdad():
    """`EmailStr` rechaza los TLD reservados por RFC 2606 (`.test`, `.invalid`).

    Queda escrito porque cuesta media hora la primera vez: los tests de este
    archivo usaban `@inmo-demo.test` y el login devolvía 422, que se lee como un
    bug del endpoint y era el validador haciendo su trabajo.
    """
    import pydantic

    from tasador.v1.auth import LoginIn

    assert LoginIn(email="agente@inmo-demo.com.ar", password="x").email
    for malo in ("agente@inmo-demo.test", "sin-arroba", "a@b"):
        with pytest.raises(pydantic.ValidationError):
            LoginIn(email=malo, password="x")


def test_una_clave_con_otro_formato_no_tiene_prefijo():
    assert prefijo_de("cualquier-cosa") is None
    assert prefijo_de("tsk_live_") is None, "sin secreto no es una clave"


def test_una_sesion_manipulada_no_se_lee():
    from tasador.security import emitir_sesion

    token = emitir_sesion(
        "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "agent"
    )
    assert leer_sesion(token) is not None
    # Un byte cambiado en la firma la invalida.
    assert leer_sesion(token[:-2] + ("ab" if not token.endswith("ab") else "cd")) is None
    assert leer_sesion("no.es.un.jwt") is None


# ── Login ────────────────────────────────────────────────────────────────
async def test_login_correcto_deja_una_cookie_httponly(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "agente@inmo-demo.com.ar")
    await db.commit()

    r = cliente.post(
        "/v1/auth/login",
        json={"email": "agente@inmo-demo.com.ar", "password": "buena-clave-1"},
    )
    assert r.status_code == 200
    assert r.json()["org_slug"] == "inmo-demo"

    galleta = r.cookies.get("tasador_sesion")
    assert galleta
    cabecera = r.headers["set-cookie"].lower()
    # HttpOnly: que un XSS no pueda leer la sesión. SameSite=Lax: que un sitio
    # de terceros no pueda generar informes en nombre del usuario (doc 06 §1).
    assert "httponly" in cabecera
    assert "samesite=lax" in cabecera


async def test_la_contrasena_incorrecta_y_el_email_inexistente_dan_la_misma_respuesta(
    db: AsyncSession, cliente
):
    """No se distingue entre "no existe" y "está mal": la diferencia es
    información gratis para quien está probando (doc 06 §1)."""
    org = await _org(db)
    await _usuario(db, org.id, "agente@inmo-demo.com.ar")
    await db.commit()

    mala = cliente.post(
        "/v1/auth/login", json={"email": "agente@inmo-demo.com.ar", "password": "no-es"}
    )
    inexistente = cliente.post(
        "/v1/auth/login", json={"email": "nadie@inmo-demo.com.ar", "password": "no-es"}
    )
    assert mala.status_code == inexistente.status_code == 401
    assert mala.json() == inexistente.json()


async def test_un_usuario_desactivado_no_entra(db: AsyncSession, cliente):
    org = await _org(db)
    u = await _usuario(db, org.id, "ex@inmo-demo.com.ar")
    u.active = False
    await db.commit()

    r = cliente.post(
        "/v1/auth/login", json={"email": "ex@inmo-demo.com.ar", "password": "buena-clave-1"}
    )
    assert r.status_code == 401


async def test_desactivar_a_alguien_corta_su_sesion_ya(db: AsyncSession, cliente):
    """La sesión es un JWT y no se revoca, pero el usuario se relee de la base
    en cada request. Es lo que hace que "echar a alguien" tenga efecto sin
    esperar a que expire el token."""
    org = await _org(db)
    u = await _usuario(db, org.id, "agente@inmo-demo.com.ar")
    await db.commit()

    cliente.post(
        "/v1/auth/login",
        json={"email": "agente@inmo-demo.com.ar", "password": "buena-clave-1"},
    )
    assert cliente.get("/v1/auth/me").status_code == 200

    u.active = False
    await db.commit()
    assert cliente.get("/v1/auth/me").status_code == 401


async def test_logout_borra_la_cookie(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "agente@inmo-demo.com.ar")
    await db.commit()

    cliente.post(
        "/v1/auth/login",
        json={"email": "agente@inmo-demo.com.ar", "password": "buena-clave-1"},
    )
    cliente.post("/v1/auth/logout")
    assert cliente.cookies.get("tasador_sesion") in (None, "")


async def test_el_mismo_email_puede_existir_en_dos_tenants(db: AsyncSession, cliente):
    """`UniqueConstraint(org_id, email)`: un corredor que trabaja en dos
    inmobiliarias. `org_slug` desambigua."""
    a = await _org(db, "inmobiliaria-a")
    b = await _org(db, "inmobiliaria-b")
    await _usuario(db, a.id, "corredor@correo.com.ar")
    await _usuario(db, b.id, "corredor@correo.com.ar")
    await db.commit()

    r = cliente.post(
        "/v1/auth/login",
        json={
            "email": "corredor@correo.com.ar",
            "password": "buena-clave-1",
            "org_slug": "inmobiliaria-b",
        },
    )
    assert r.status_code == 200
    assert r.json()["org_slug"] == "inmobiliaria-b"


# ── API keys ─────────────────────────────────────────────────────────────
async def _api_key(db: AsyncSession, org_id) -> str:
    from tasador.db.models import ApiKey

    clave, prefijo, hash_ = generar_api_key()
    db.add(ApiKey(org_id=org_id, name="panel", prefix=prefijo, key_hash=hash_))
    await db.flush()
    return clave


async def test_una_api_key_valida_resuelve_su_tenant(db: AsyncSession, cliente):
    org = await _org(db)
    clave = await _api_key(db, org.id)
    await db.commit()

    r = cliente.get("/v1/auth/me", headers={"Authorization": f"Bearer {clave}"})
    assert r.status_code == 200
    assert r.json() == {
        "email": None,
        "full_name": None,
        "role": None,
        "org_slug": "inmo-demo",
        "org_name": "Inmo-Demo",
        "via": "api_key",
    }


async def test_una_api_key_revocada_no_entra(db: AsyncSession, cliente):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from tasador.db.models import ApiKey

    org = await _org(db)
    clave = await _api_key(db, org.id)
    await db.commit()

    k = (await db.execute(select(ApiKey))).scalar_one()
    k.revoked_at = datetime.now(UTC)
    await db.commit()

    assert (
        cliente.get("/v1/auth/me", headers={"Authorization": f"Bearer {clave}"}).status_code == 401
    )


async def test_una_api_key_de_un_tenant_no_ve_los_informes_de_otro(db: AsyncSession, cliente):
    from tasador.db.models import Report, SubjectProperty

    a = await _org(db, "inmobiliaria-a")
    b = await _org(db, "inmobiliaria-b")
    clave_b = await _api_key(db, b.id)

    sujeto = SubjectProperty(
        org_id=a.id, address_raw="Av. Cabildo 2530", property_type="departamento"
    )
    db.add(sujeto)
    await db.flush()
    informe = Report(
        org_id=a.id,
        subject_property_id=sujeto.id,
        engine_version="t",
        method_version="t",
        prompt_bundle_version="t",
    )
    db.add(informe)
    await db.commit()

    r = cliente.get(f"/v1/reports/{informe.id}", headers={"Authorization": f"Bearer {clave_b}"})
    assert r.status_code == 404


async def test_un_bearer_invalido_no_cae_al_header_de_desarrollo(db: AsyncSession, cliente):
    """Si un Bearer malo cayera al siguiente mecanismo, presentar una
    credencial inválida sería una forma de entrar por header. Una credencial
    PRESENTADA y mala corta acá."""
    await _org(db)
    await db.commit()

    r = cliente.get(
        "/v1/auth/me",
        headers={
            "Authorization": "Bearer tsk_live_inventadaaaaa",
            "X-Org-Slug": "inmo-demo",
        },
    )
    assert r.status_code == 401


# ── El header viejo ──────────────────────────────────────────────────────
async def test_fuera_de_produccion_el_header_sigue_andando(db: AsyncSession, cliente):
    """Se conserva a propósito: 279 tests y los scripts de desarrollo lo usan,
    y sacarlo de golpe habría sido cambiar dos cosas a la vez."""
    await _org(db)
    await db.commit()
    r = cliente.get("/v1/auth/me", headers={"X-Org-Slug": "inmo-demo"})
    assert r.status_code == 200
    assert r.json()["via"] == "header_dev"


async def test_en_produccion_el_header_de_desarrollo_no_alcanza(
    db: AsyncSession, cliente, monkeypatch: pytest.MonkeyPatch
):
    """EL test de este archivo.

    Lo único que separa "hay auth" de "no hay auth" es una condición sobre
    `env`. Si alguien la borra —o la cambia por una variable de entorno que un
    deploy puede traer mal— esto falla y se entera antes que un cliente.
    """
    from tasador.settings import get_settings

    await _org(db)
    await db.commit()
    assert cliente.get("/v1/auth/me", headers={"X-Org-Slug": "inmo-demo"}).status_code == 200

    monkeypatch.setattr(get_settings(), "env", "production")
    r = cliente.get("/v1/auth/me", headers={"X-Org-Slug": "inmo-demo"})
    assert r.status_code == 401, "en producción el header NO puede resolver el tenant"

    # Y los informes tampoco, que es lo que de verdad importa.
    assert cliente.get("/v1/reports", headers={"X-Org-Slug": "inmo-demo"}).status_code == 401


async def test_sin_ninguna_credencial_es_401(db: AsyncSession, cliente):
    """Sin organizaciones activas no hay a qué caer, ni siquiera en dev."""
    assert cliente.get("/v1/auth/me").status_code == 401
