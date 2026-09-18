"""Las superficies de administración y el cierre de la Etapa 4 (14/08, tarde).

Cubre lo que se agregó al final de la etapa: `/v1/calidad`, `/v1/admin/*`,
regenerate, el link compartido y el rate limit del login. El criterio es el
de siempre: cada regla de autorización tiene un test que la impone, porque un
gate que puede pasar sin medir nada es peor que no tener gate.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.security import (
    emitir_sesion,
    emitir_share,
    generar_api_key,
    hash_password,
    leer_sesion,
    leer_share,
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


async def _usuario(db: AsyncSession, org_id, email: str, rol: str = "agent"):
    from tasador.db.models import User

    u = User(org_id=org_id, email=email, password_hash=hash_password("buena-clave-1"), role=rol)
    db.add(u)
    await db.flush()
    return u


async def _api_key(db: AsyncSession, org_id) -> str:
    from tasador.db.models import ApiKey

    clave, prefijo, hash_ = generar_api_key()
    db.add(ApiKey(org_id=org_id, name="panel", prefix=prefijo, key_hash=hash_))
    await db.flush()
    return clave


async def _informe(db: AsyncSession, org_id, status: str = "QUEUED"):
    from tasador.db.models import Report, SubjectProperty

    sujeto = SubjectProperty(org_id=org_id, address_raw="Perú 1355", property_type="departamento")
    db.add(sujeto)
    await db.flush()
    extra = {}
    if status == "SUCCEEDED":
        extra = {"value_low": 100, "value_mid": 110, "value_high": 120, "currency": "USD"}
    r = Report(
        org_id=org_id,
        subject_property_id=sujeto.id,
        status=status,
        engine_version="t",
        method_version="t",
        prompt_bundle_version="t",
        **extra,
    )
    db.add(r)
    await db.flush()
    return r


def _login(cliente, email: str = "admin@inmo-demo.com.ar"):
    r = cliente.post("/v1/auth/login", json={"email": email, "password": "buena-clave-1"})
    assert r.status_code == 200


# ── Autorización de admin ────────────────────────────────────────────────
async def test_un_agente_no_ve_calidad_ni_admin(db: AsyncSession, cliente):
    """403 y no 401: la identidad vale, el rol no alcanza."""
    org = await _org(db)
    await _usuario(db, org.id, "agente@inmo-demo.com.ar", rol="agent")
    await db.commit()
    _login(cliente, "agente@inmo-demo.com.ar")

    for ruta in ("/v1/calidad", "/v1/admin/fuentes", "/v1/admin/organizacion"):
        assert cliente.get(ruta).status_code == 403, ruta


async def test_un_admin_si_ve_calidad_y_admin(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "admin@inmo-demo.com.ar", rol="admin")
    await db.commit()
    _login(cliente)

    for ruta in ("/v1/calidad", "/v1/admin/fuentes", "/v1/admin/organizacion"):
        assert cliente.get(ruta).status_code == 200, ruta


async def test_una_api_key_administra_claves_pero_no_usuarios(db: AsyncSession, cliente):
    """La API key es del tenant y puede lo del tenant. Administrar PERSONAS
    pide una persona: que una integración creara usuarios con contraseña sería
    escalar un secreto de máquina a una identidad humana."""
    org = await _org(db)
    clave = await _api_key(db, org.id)
    await db.commit()
    h = {"Authorization": f"Bearer {clave}"}

    assert cliente.get("/v1/admin/organizacion", headers=h).status_code == 200
    assert cliente.get("/v1/admin/usuarios", headers=h).status_code == 403
    r = cliente.post(
        "/v1/admin/usuarios",
        headers=h,
        json={"email": "x@inmo-demo.com.ar", "role": "agent"},
    )
    assert r.status_code == 403


# ── API keys ─────────────────────────────────────────────────────────────
async def test_crear_una_api_key_la_devuelve_una_vez_y_funciona(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "admin@inmo-demo.com.ar", rol="owner")
    await db.commit()
    _login(cliente)

    r = cliente.post("/v1/admin/api-keys", json={"name": "integración nueva"})
    assert r.status_code == 200
    creada = r.json()
    assert creada["api_key"].startswith("tsk_live_")

    # La clave recién creada autentica de verdad (sin la cookie).
    quien = cliente.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {creada['api_key']}"},
        cookies={},
    )
    assert quien.status_code == 200

    # Y el listado NO la muestra en claro: solo el prefijo.
    listado = cliente.get("/v1/admin/organizacion").json()
    assert creada["api_key"] not in str(listado)
    assert any(k["prefix"] == creada["prefix"] for k in listado["api_keys"])


async def test_revocar_una_api_key_la_corta(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "admin@inmo-demo.com.ar", rol="owner")
    await db.commit()
    _login(cliente)

    creada = cliente.post("/v1/admin/api-keys", json={"name": "efímera"}).json()
    assert cliente.delete(f"/v1/admin/api-keys/{creada['id']}").status_code == 200

    r = cliente.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {creada['api_key']}"}, cookies={}
    )
    assert r.status_code == 401


async def test_no_se_puede_revocar_la_clave_de_otro_tenant(db: AsyncSession, cliente):
    from sqlalchemy import select

    from tasador.db.models import ApiKey

    a = await _org(db, "inmobiliaria-a")
    b = await _org(db, "inmobiliaria-b")
    await _usuario(db, a.id, "admin@inmo-demo.com.ar", rol="owner")
    await _api_key(db, b.id)
    await db.commit()
    _login(cliente)

    ajena = (await db.execute(select(ApiKey).where(ApiKey.org_id == b.id))).scalars().one()
    assert cliente.delete(f"/v1/admin/api-keys/{ajena.id}").status_code == 404


# ── Usuarios ─────────────────────────────────────────────────────────────
async def test_alta_de_usuario_con_password_generada(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "admin@inmo-demo.com.ar", rol="owner")
    await db.commit()
    _login(cliente)

    r = cliente.post(
        "/v1/admin/usuarios", json={"email": "Nuevo@inmo-demo.com.ar", "role": "agent"}
    )
    assert r.status_code == 200
    creado = r.json()
    assert creado["email"] == "nuevo@inmo-demo.com.ar", "el email se normaliza a minúsculas"
    assert creado["password_generada"], "sin contraseña dada, se genera y se muestra UNA vez"

    # Y esa contraseña entra.
    login = cliente.post(
        "/v1/auth/login",
        json={"email": "nuevo@inmo-demo.com.ar", "password": creado["password_generada"]},
    )
    assert login.status_code == 200


async def test_el_email_duplicado_da_409(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "admin@inmo-demo.com.ar", rol="owner")
    await db.commit()
    _login(cliente)

    assert (
        cliente.post("/v1/admin/usuarios", json={"email": "admin@inmo-demo.com.ar"}).status_code
        == 409
    )


async def test_nadie_se_desactiva_a_si_mismo(db: AsyncSession, cliente):
    """El tenant no puede quedar sin ningún admin por un click."""
    org = await _org(db)
    yo = await _usuario(db, org.id, "admin@inmo-demo.com.ar", rol="owner")
    await db.commit()
    _login(cliente)

    r = cliente.patch(f"/v1/admin/usuarios/{yo.id}", json={"active": False})
    assert r.status_code == 422


async def test_desactivar_a_otro_funciona_y_su_login_muere(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "admin@inmo-demo.com.ar", rol="owner")
    otro = await _usuario(db, org.id, "otro@inmo-demo.com.ar")
    await db.commit()
    _login(cliente)

    assert cliente.patch(f"/v1/admin/usuarios/{otro.id}", json={"active": False}).status_code == 200
    login = cliente.post(
        "/v1/auth/login",
        json={"email": "otro@inmo-demo.com.ar", "password": "buena-clave-1"},
        cookies={},
    )
    assert login.status_code == 401


# ── Regenerate ───────────────────────────────────────────────────────────
async def test_regenerar_crea_un_informe_nuevo_y_no_muta_el_anterior(db: AsyncSession, cliente):
    from sqlalchemy import select

    from tasador.db.models import Report

    org = await _org(db)
    previo = await _informe(db, org.id, status="SUCCEEDED")
    await db.commit()

    r = cliente.post(
        f"/v1/reports/{previo.id}/regenerate",
        json={"property": {"condition": "muy_bueno", "surface_total": 80}},
        headers={"X-Org-Slug": "inmo-demo"},
    )
    assert r.status_code == 202
    nuevo_id = r.json()["report_id"]
    assert nuevo_id != str(previo.id)

    filas = (await db.execute(select(Report))).scalars().all()
    assert len(filas) == 2
    viejo = next(f for f in filas if str(f.id) == str(previo.id))
    assert viejo.status == "SUCCEEDED", "el informe anterior es un documento: no se muta"


async def test_regenerar_uno_corriendo_da_409(db: AsyncSession, cliente):
    org = await _org(db)
    corriendo = await _informe(db, org.id, status="RUNNING")
    await db.commit()

    r = cliente.post(
        f"/v1/reports/{corriendo.id}/regenerate",
        json={},
        headers={"X-Org-Slug": "inmo-demo"},
    )
    assert r.status_code == 409


async def test_regenerar_el_informe_de_otro_tenant_da_404(db: AsyncSession, cliente):
    a = await _org(db, "inmobiliaria-a")
    await _org(db, "inmobiliaria-b")
    ajeno = await _informe(db, a.id, status="SUCCEEDED")
    await db.commit()

    r = cliente.post(
        f"/v1/reports/{ajeno.id}/regenerate",
        json={},
        headers={"X-Org-Slug": "inmobiliaria-b"},
    )
    assert r.status_code == 404


# ── El link compartido ───────────────────────────────────────────────────
def test_un_share_no_abre_una_sesion_ni_al_reves():
    """Los firman la misma clave; los separa el `aud`. Si esto falla, un link
    de propietario sería una cookie de sesión gratis."""
    share = emitir_share(
        "11111111-1111-1111-1111-111111111111", "2" * 8 + "-2222" * 3 + "-" + "2" * 12
    )
    assert leer_share(share) is not None
    assert leer_sesion(share) is None, "un token de share NO es una sesión"

    sesion = emitir_sesion("1" * 8, "2" * 8, "agent")
    assert leer_share(sesion) is None, "una sesión NO es un token de share"


async def test_compartir_solo_funciona_con_un_informe_terminado(db: AsyncSession, cliente):
    org = await _org(db)
    corriendo = await _informe(db, org.id, status="RUNNING")
    terminado = await _informe(db, org.id, status="SUCCEEDED")
    await db.commit()
    h = {"X-Org-Slug": "inmo-demo"}

    assert cliente.post(f"/v1/reports/{corriendo.id}/share", headers=h).status_code == 409
    r = cliente.post(f"/v1/reports/{terminado.id}/share", headers=h)
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("/compartido/")

    # El link abre el informe SIN credenciales.
    token = url.removeprefix("/compartido/")
    publico = cliente.get(f"/v1/shared/{token}", cookies={})
    assert publico.status_code == 200
    cuerpo = publico.json()
    assert cuerpo["address"] == "Perú 1355"
    # Y devuelve el RESULTADO, no la cocina: ni traza ni costos.
    assert "progress" not in cuerpo
    assert "cost_usd" not in cuerpo


async def test_un_token_trucho_o_ajeno_da_404(db: AsyncSession, cliente):
    org = await _org(db)
    informe = await _informe(db, org.id, status="SUCCEEDED")
    await db.commit()

    assert cliente.get("/v1/shared/no-es-un-token").status_code == 404
    # Un token firmado para OTRO org no abre este informe: el filtro de tenant
    # también corre acá.
    import uuid as _uuid

    otro_org = str(_uuid.uuid4())
    cruzado = emitir_share(str(informe.id), otro_org)
    assert cliente.get(f"/v1/shared/{cruzado}").status_code == 404


# ── Rate limit del login (doc 07 §2) ─────────────────────────────────────
async def test_el_sexto_intento_en_un_minuto_da_429(db: AsyncSession, cliente):
    org = await _org(db)
    await _usuario(db, org.id, "agente@inmo-demo.com.ar")
    await db.commit()

    for _ in range(5):
        r = cliente.post(
            "/v1/auth/login", json={"email": "agente@inmo-demo.com.ar", "password": "mal"}
        )
        assert r.status_code == 401
    r = cliente.post("/v1/auth/login", json={"email": "agente@inmo-demo.com.ar", "password": "mal"})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"

    # Y con la contraseña BUENA tampoco entra: el limitador no es un oráculo.
    r = cliente.post(
        "/v1/auth/login",
        json={"email": "agente@inmo-demo.com.ar", "password": "buena-clave-1"},
    )
    assert r.status_code == 429


# ── Cambiar la propia contraseña ─────────────────────────────────────────
async def test_cambiar_password_exige_la_actual_y_despues_entra_con_la_nueva(
    db: AsyncSession, cliente
):
    org = await _org(db)
    await _usuario(db, org.id, "agente@inmo-demo.com.ar", rol="agent")
    await db.commit()
    _login(cliente, "agente@inmo-demo.com.ar")

    mal = cliente.patch(
        "/v1/auth/password", json={"actual": "otra-cosa-larga", "nueva": "nueva-clave-segura-1"}
    )
    assert mal.status_code == 422

    corta = cliente.patch("/v1/auth/password", json={"actual": "buena-clave-1", "nueva": "corta"})
    assert corta.status_code == 422

    ok = cliente.patch(
        "/v1/auth/password", json={"actual": "buena-clave-1", "nueva": "nueva-clave-segura-1"}
    )
    assert ok.status_code == 200

    cliente.post("/v1/auth/logout")
    vieja = cliente.post(
        "/v1/auth/login", json={"email": "agente@inmo-demo.com.ar", "password": "buena-clave-1"}
    )
    assert vieja.status_code == 401
    nueva = cliente.post(
        "/v1/auth/login",
        json={"email": "agente@inmo-demo.com.ar", "password": "nueva-clave-segura-1"},
    )
    assert nueva.status_code == 200


async def test_una_api_key_no_puede_cambiar_contrasenas(db: AsyncSession, cliente):
    """Una API key no tiene contraseña: 403, no 422."""
    org = await _org(db)
    clave = await _api_key(db, org.id)
    await db.commit()
    r = cliente.patch(
        "/v1/auth/password",
        json={"actual": "x", "nueva": "nueva-clave-segura-1"},
        headers={"Authorization": f"Bearer {clave}"},
        cookies={},
    )
    assert r.status_code == 403
