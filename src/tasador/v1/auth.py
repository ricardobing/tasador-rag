"""`/v1/auth/*` y la resolución del tenant — doc 06 §1.

Reemplaza al header `X-Org-Slug`, que no era autenticación: era el cliente
eligiendo qué tenant quería ser.

## El orden en que se resuelve, y por qué

    1. Authorization: Bearer tsk_live_...   -> integraciones
    2. Cookie de sesión                     -> la UI
    3. X-Org-Slug                           -> SOLO fuera de producción

El paso 3 sobrevive porque los 279 tests y los scripts de desarrollo lo usan, y
sacarlo de golpe habría sido cambiar dos cosas a la vez. Pero **está apagado en
producción por código, no por configuración**, y hay un test que lo impone. La
diferencia importa: una variable de entorno mal puesta en un deploy vuelve a
abrir el agujero; una condición sobre `env == "production"` no.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.base import get_session
from tasador.db.models import ApiKey, Organization, User
from tasador.security import (
    COOKIE_SESION,
    emitir_sesion,
    hash_password,
    hay_que_rehashear,
    leer_sesion,
    opciones_de_cookie,
    prefijo_de,
    verificar_api_key,
    verificar_password,
)
from tasador.settings import get_settings

router = APIRouter()
log = structlog.get_logger()

# Un solo cuerpo para todo fallo de auth. No se distingue entre clave
# inexistente, revocada, usuario inactivo o contraseña incorrecta: la
# diferencia es información gratis para quien está probando (doc 06 §1).
_NO_AUTORIZADO = HTTPException(
    status_code=401,
    detail="No autorizado.",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(slots=True)
class Principal:
    """Quién está pidiendo. `user` es None cuando entra una integración."""

    org: Organization
    user: User | None
    via: str  # "api_key" | "sesion" | "header_dev"

    @property
    def es_admin(self) -> bool:
        # Una API key es del tenant, no de una persona: puede todo lo del
        # tenant. Lo que NO puede es administrar usuarios, y eso lo decide
        # cada endpoint pidiendo `user`.
        return self.via == "api_key" or (
            self.user is not None and self.user.role in ("owner", "admin")
        )


async def _por_api_key(session: AsyncSession, crudo: str) -> Principal | None:
    prefijo = prefijo_de(crudo)
    if prefijo is None:
        return None

    # Por PREFIJO: un hash argon2 lleva sal, así que buscar por `key_hash` no
    # puede funcionar. El prefijo encuentra al candidato; lo que autentica es
    # la verificación argon2 de abajo.
    candidatas = (
        (
            await session.execute(
                select(ApiKey).where(ApiKey.prefix == prefijo, ApiKey.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    for k in candidatas:
        if not verificar_api_key(crudo, k.key_hash):
            continue
        org = (
            await session.execute(
                select(Organization).where(
                    Organization.id == k.org_id, Organization.active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if org is None:
            return None
        # `last_used_at` es lo que permite revocar con criterio: una clave que
        # no se usa hace seis meses se revoca sin llamar a nadie.
        k.last_used_at = datetime.now(UTC)
        await session.commit()
        return Principal(org=org, user=None, via="api_key")
    return None


async def _por_sesion(session: AsyncSession, token: str) -> Principal | None:
    datos = leer_sesion(token)
    if datos is None:
        return None
    try:
        user_id = uuid.UUID(str(datos["sub"]))
    except (KeyError, ValueError):
        return None

    # Se relee el usuario de la base en CADA request: es lo que hace que
    # desactivar a alguien tenga efecto inmediato sin necesidad de revocar
    # tokens. El `org` del JWT no se usa para autorizar — se usa el de la fila.
    fila = (
        await session.execute(
            select(User, Organization)
            .join(Organization, Organization.id == User.org_id)
            .where(User.id == user_id, User.active.is_(True), Organization.active.is_(True))
        )
    ).first()
    if fila is None:
        return None
    usuario, org = fila
    return Principal(org=org, user=usuario, via="sesion")


async def _por_header_de_desarrollo(session: AsyncSession, slug: str | None) -> Principal | None:
    """El camino viejo. **Apagado en producción por código.**

    Se conserva para los tests y los scripts, que son muchos y no necesitan
    credenciales para probar lo que prueban. Si mañana alguien despliega con
    `ENV=production`, este camino no existe.
    """
    if get_settings().is_production:
        return None
    q = select(Organization).where(Organization.active.is_(True))
    if slug:
        q = q.where(Organization.slug == slug)
    orgs = (await session.execute(q)).scalars().all()
    if len(orgs) == 1:
        return Principal(org=orgs[0], user=None, via="header_dev")
    if not orgs:
        return None
    raise HTTPException(
        status_code=400,
        detail="Hay varias organizaciones activas: indicá cuál con el header X-Org-Slug.",
    )


async def resolve_principal(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_org_slug: Annotated[str | None, Header()] = None,
    tasador_sesion: Annotated[str | None, Cookie()] = None,
) -> Principal:
    if authorization and authorization.lower().startswith("bearer "):
        p = await _por_api_key(session, authorization[7:].strip())
        if p is not None:
            return p
        # Una credencial PRESENTADA y mala no cae al siguiente mecanismo: eso
        # convertiría un Bearer inválido en una entrada por header.
        raise _NO_AUTORIZADO

    if tasador_sesion:
        p = await _por_sesion(session, tasador_sesion)
        if p is not None:
            return p
        raise _NO_AUTORIZADO

    p = await _por_header_de_desarrollo(session, x_org_slug)
    if p is None:
        raise _NO_AUTORIZADO
    return p


async def resolve_org(p: Annotated[Principal, Depends(resolve_principal)]) -> Organization:
    """El tenant, para los endpoints que solo necesitan eso.

    Existe para que `reports.py` no cambie: pedía `Organization` y sigue
    pidiendo `Organization`. Lo que cambió es de dónde sale.
    """
    return p.org


# ── Endpoints ────────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    org_slug: str | None = None


class Yo(BaseModel):
    email: str | None
    full_name: str | None
    role: str | None
    org_slug: str
    org_name: str
    via: str


# ── Rate limit del login (doc 07 §2) ─────────────────────────────────────
# 5 intentos por minuto, por IP y por email. En memoria del proceso: hay UNA
# instancia de la API, y meter Redis en el camino del login agregaría una
# dependencia para un contador que cabe en un dict. Si mañana hay réplicas,
# esto se muda a Redis — y el test que lo impone no cambia.
_VENTANA_SEGUNDOS = 60.0
_MAX_INTENTOS = 5
_intentos: dict[str, deque[float]] = {}


def _excedido(clave: str) -> bool:
    """Ventana deslizante. Cuenta TODOS los intentos, no solo los fallidos:
    contar solo fallos convierte al limitador en un oráculo — quien prueba
    contraseñas sabría que acertó porque el contador dejó de subir."""
    ahora = time.monotonic()
    ventana = _intentos.setdefault(clave, deque())
    while ventana and ahora - ventana[0] > _VENTANA_SEGUNDOS:
        ventana.popleft()
    if len(ventana) >= _MAX_INTENTOS:
        return True
    ventana.append(ahora)
    return False


def _resetear_rate_limit() -> None:
    """Solo para los tests: cada test arranca con la ventana limpia."""
    _intentos.clear()


@router.post("/auth/login", summary="Iniciar sesión")
async def login(
    body: LoginIn,
    response: Response,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Yo:
    """Email + contraseña -> cookie de sesión HttpOnly.

    El `org_slug` es opcional y solo desambigua: el mismo email puede existir en
    dos tenants (`UniqueConstraint(org_id, email)`), que es lo correcto para un
    corredor que trabaja en dos inmobiliarias.
    """
    ip = request.client.host if request.client else "sin-ip"
    if _excedido(f"ip:{ip}") or _excedido(f"email:{body.email.lower()}"):
        log.warning("login limitado", email=body.email[:60], ip=ip)
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Esperá un minuto y volvé a probar.",
            headers={"Retry-After": "60"},
        )
    q = (
        select(User, Organization)
        .join(Organization, Organization.id == User.org_id)
        .where(User.email == body.email.lower(), Organization.active.is_(True))
    )
    if body.org_slug:
        q = q.where(Organization.slug == body.org_slug)
    filas = (await session.execute(q)).all()

    for usuario, org in filas:
        if not verificar_password(usuario.password_hash, body.password):
            continue
        if not usuario.active:
            # Se verifica DESPUÉS de la contraseña: si no, el tiempo de
            # respuesta diría qué emails existen aunque el cuerpo no lo diga.
            break
        if usuario.password_hash and hay_que_rehashear(usuario.password_hash):
            usuario.password_hash = hash_password(body.password)
        usuario.last_login_at = datetime.now(UTC)
        await session.commit()

        response.set_cookie(
            COOKIE_SESION,
            emitir_sesion(str(usuario.id), str(org.id), usuario.role),
            **opciones_de_cookie(),
        )
        log.info("login", user=str(usuario.id), org=org.slug)
        return Yo(
            email=usuario.email,
            full_name=usuario.full_name,
            role=usuario.role,
            org_slug=org.slug,
            org_name=org.name,
            via="sesion",
        )

    if not filas:
        # Aun sin candidatos se paga el costo de un hash, para que "el email no
        # existe" y "la contraseña está mal" tarden lo mismo.
        verificar_password(None, body.password)
    log.warning(
        "login fallido", email=body.email[:60], ip=request.client.host if request.client else None
    )
    raise _NO_AUTORIZADO


class PasswordIn(BaseModel):
    actual: str = Field(min_length=1, max_length=200)
    nueva: str = Field(min_length=10, max_length=200)


@router.patch("/auth/password", summary="Cambiar la propia contraseña")
async def cambiar_password(
    body: PasswordIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    p: Annotated[Principal, Depends(resolve_principal)],
) -> dict[str, str]:
    """La contraseña actual se verifica aunque haya sesión: una pestaña
    abierta no puede convertirse en un cambio de contraseña silencioso.

    Solo con sesión de usuario: una API key no tiene contraseña y el header de
    desarrollo no tiene usuario. Pasa por el mismo rate limit que el login,
    porque verificar la actual es probar una contraseña.
    """
    if p.via != "sesion" or p.user is None:
        raise HTTPException(status_code=403, detail="Solo con sesión de usuario.")
    ip = request.client.host if request.client else "sin-ip"
    if _excedido(f"ip:{ip}") or _excedido(f"email:{p.user.email.lower()}"):
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Esperá un minuto y volvé a probar.",
            headers={"Retry-After": "60"},
        )
    usuario = (
        await session.execute(select(User).where(User.id == p.user.id, User.org_id == p.org.id))
    ).scalar_one()
    if not verificar_password(usuario.password_hash, body.actual):
        raise HTTPException(status_code=422, detail="La contraseña actual no es correcta.")
    if body.actual == body.nueva:
        raise HTTPException(status_code=422, detail="La nueva tiene que ser distinta de la actual.")
    usuario.password_hash = hash_password(body.nueva)
    await session.commit()
    log.info("password cambiada", user=str(usuario.id))
    return {"ok": "contraseña cambiada"}


@router.post("/auth/logout", summary="Cerrar sesión")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(COOKIE_SESION, path="/")
    return {"status": "ok"}


@router.get("/auth/me", summary="Quién soy")
async def yo(p: Annotated[Principal, Depends(resolve_principal)]) -> Yo:
    return Yo(
        email=p.user.email if p.user else None,
        full_name=p.user.full_name if p.user else None,
        role=p.user.role if p.user else None,
        org_slug=p.org.slug,
        org_name=p.org.name,
        via=p.via,
    )
