"""`/v1/admin/*` — operación del tenant (doc 07 §10-11).

Tres superficies:

  - **fuentes**: salud de la ingesta (`corpus.ingest_runs`) y el chequeo de
    sesgo por barrio, que reusa `corpus.coverage.coverage_report` — la misma
    implementación que la CLI, para que "qué dice la pantalla" y "qué dice el
    reporte" no puedan divergir.
  - **API keys**: crear, listar, revocar. La clave en claro se devuelve UNA vez.
  - **usuarios**: alta, listado, activar/desactivar.

## Quién puede qué

`require_admin` alcanza para fuentes, organización y API keys: una API key es
del tenant y puede todo lo del tenant. **Administrar usuarios pide además una
persona** (`user is not None`): que una integración pueda crear usuarios con
contraseña sería escalar un secreto de máquina a una identidad humana.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.corpus.coverage import coverage_report
from tasador.db.base import get_session
from tasador.db.models import USER_ROLES, ApiKey, IngestRun, User
from tasador.security import generar_api_key, hash_password
from tasador.v1.auth import Principal, resolve_principal
from tasador.v1.calidad import require_admin

router = APIRouter()
log = structlog.get_logger()


async def require_admin_humano(
    p: Annotated[Principal, Depends(resolve_principal)],
) -> Principal:
    """Administrar usuarios exige una PERSONA con rol, no una API key."""
    if p.via == "header_dev":  # el modo desarrollo no cambia de reglas
        return p
    if p.user is None or p.user.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Administrar usuarios requiere una sesión de owner o admin.",
        )
    return p


# ── Fuentes ──────────────────────────────────────────────────────────────
class CorridaOut(BaseModel):
    id: uuid.UUID
    mode: str
    status: str
    discovered: int
    fetched: int
    created: int
    updated: int
    skipped: int
    blocked: int
    errors: int
    cost_usd: float
    started_at: datetime
    finished_at: datetime | None


class FuenteOut(BaseModel):
    source: str
    corridas: int
    ultima: CorridaOut | None
    # Suma del mes corriente, para contrastar contra el presupuesto.
    costo_del_mes: float


class BarrioOut(BaseModel):
    name: str
    activos: int
    usables: int
    usd_m2_mediana: float | None
    usd_m2_oficial: float | None
    # None cuando falta alguna de las dos puntas. El oficial es BA Data, cuya
    # serie termina en 2019: el desvío se muestra como referencia histórica,
    # no como alarma — por eso el front no pinta rojo con esto.
    desvio: float | None
    estado: str


class FuentesOut(BaseModel):
    fuentes: list[FuenteOut]
    barrios: list[BarrioOut]


@router.get("/admin/fuentes", response_model=FuentesOut, summary="Salud de las fuentes")
async def fuentes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[Principal, Depends(require_admin)],
) -> FuentesOut:
    sources = (
        (await session.execute(select(IngestRun.source).distinct().order_by(IngestRun.source)))
        .scalars()
        .all()
    )

    inicio_de_mes = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    salida: list[FuenteOut] = []
    for src in sources:
        ultima = (
            await session.execute(
                select(IngestRun)
                .where(IngestRun.source == src)
                .order_by(IngestRun.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        corridas = (
            await session.execute(
                select(func.count()).select_from(IngestRun).where(IngestRun.source == src)
            )
        ).scalar_one()
        costo_mes = (
            await session.execute(
                select(func.coalesce(func.sum(IngestRun.cost_usd), 0)).where(
                    IngestRun.source == src, IngestRun.started_at >= inicio_de_mes
                )
            )
        ).scalar_one()
        salida.append(
            FuenteOut(
                source=src,
                corridas=corridas,
                costo_del_mes=float(costo_mes),
                ultima=CorridaOut(
                    id=ultima.id,
                    mode=ultima.mode,
                    status=ultima.status,
                    discovered=ultima.discovered,
                    fetched=ultima.fetched,
                    created=ultima.created,
                    updated=ultima.updated,
                    skipped=ultima.skipped,
                    blocked=ultima.blocked,
                    errors=ultima.errors,
                    cost_usd=float(ultima.cost_usd),
                    started_at=ultima.started_at,
                    finished_at=ultima.finished_at,
                )
                if ultima
                else None,
            )
        )

    cobertura = await coverage_report(session)
    barrios = [
        BarrioOut(
            name=b.name,
            activos=b.activos,
            usables=b.con_precio_y_superficie,
            usd_m2_mediana=float(b.usd_m2_mediana) if b.usd_m2_mediana is not None else None,
            usd_m2_oficial=float(b.usd_m2_oficial) if b.usd_m2_oficial is not None else None,
            desvio=float(b.desvio) if b.desvio is not None else None,
            estado=b.estado,
        )
        for b in cobertura
        if b.activos > 0
    ]

    return FuentesOut(fuentes=salida, barrios=barrios)


# ── Organización y API keys ──────────────────────────────────────────────
class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class OrganizacionOut(BaseModel):
    name: str
    slug: str
    monthly_report_quota: int
    fetch_budget_monthly: int
    informes_del_mes: int
    api_keys: list[ApiKeyOut]


@router.get(
    "/admin/organizacion", response_model=OrganizacionOut, summary="La organización y sus claves"
)
async def organizacion(
    session: Annotated[AsyncSession, Depends(get_session)],
    p: Annotated[Principal, Depends(require_admin)],
) -> OrganizacionOut:
    from tasador.db.models import Report

    inicio_de_mes = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    informes_del_mes = (
        await session.execute(
            select(func.count())
            .select_from(Report)
            .where(Report.org_id == p.org.id, Report.created_at >= inicio_de_mes)
        )
    ).scalar_one()

    claves = (
        (
            await session.execute(
                select(ApiKey).where(ApiKey.org_id == p.org.id).order_by(ApiKey.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return OrganizacionOut(
        name=p.org.name,
        slug=p.org.slug,
        monthly_report_quota=p.org.monthly_report_quota,
        fetch_budget_monthly=p.org.fetch_budget_monthly,
        informes_del_mes=informes_del_mes,
        api_keys=[
            ApiKeyOut(
                id=k.id,
                name=k.name,
                prefix=k.prefix,
                last_used_at=k.last_used_at,
                revoked_at=k.revoked_at,
                created_at=k.created_at,
            )
            for k in claves
        ],
    )


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyCreada(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    # La clave en claro, UNA vez. No se guarda y no se puede volver a pedir.
    api_key: str


@router.post("/admin/api-keys", response_model=ApiKeyCreada, summary="Crear una API key")
async def crear_api_key(
    body: ApiKeyIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    p: Annotated[Principal, Depends(require_admin)],
) -> ApiKeyCreada:
    clave, prefijo, hash_ = generar_api_key()
    fila = ApiKey(
        org_id=p.org.id,
        name=body.name,
        prefix=prefijo,
        key_hash=hash_,
        created_by=p.user.id if p.user else None,
    )
    session.add(fila)
    await session.commit()
    log.info("api key creada", org=p.org.slug, prefix=prefijo, name=body.name)
    return ApiKeyCreada(id=fila.id, name=fila.name, prefix=prefijo, api_key=clave)


@router.delete("/admin/api-keys/{key_id}", summary="Revocar una API key")
async def revocar_api_key(
    key_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    p: Annotated[Principal, Depends(require_admin)],
) -> dict[str, str]:
    fila = (
        await session.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == p.org.id))
    ).scalar_one_or_none()
    if fila is None:
        raise HTTPException(status_code=404, detail="No existe esa clave.")
    if fila.revoked_at is None:
        fila.revoked_at = datetime.now(UTC)
        await session.commit()
        log.info("api key revocada", org=p.org.slug, prefix=fila.prefix)
    # Revocar dos veces no es un error: el estado final es el pedido.
    return {"status": "revocada", "prefix": fila.prefix}


# ── Usuarios ─────────────────────────────────────────────────────────────
class UsuarioOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    active: bool
    last_login_at: datetime | None
    created_at: datetime


@router.get("/admin/usuarios", response_model=list[UsuarioOut], summary="Usuarios del tenant")
async def listar_usuarios(
    session: Annotated[AsyncSession, Depends(get_session)],
    p: Annotated[Principal, Depends(require_admin_humano)],
) -> list[UsuarioOut]:
    filas = (
        (
            await session.execute(
                select(User).where(User.org_id == p.org.id).order_by(User.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        UsuarioOut(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role,
            active=u.active,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u in filas
    ]


class UsuarioIn(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=200)
    role: str = "agent"
    # Opcional: sin contraseña se genera una y se devuelve UNA vez, igual que
    # una API key. Es mejor que dejar que el admin invente una débil.
    password: str | None = Field(default=None, min_length=10, max_length=200)


class UsuarioCreado(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    # Solo viene cuando se generó acá. El admin la pasa por un canal seguro y
    # el usuario debería cambiarla — cuando exista el flujo de cambio.
    password_generada: str | None


@router.post("/admin/usuarios", response_model=UsuarioCreado, summary="Crear un usuario")
async def crear_usuario(
    body: UsuarioIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    p: Annotated[Principal, Depends(require_admin_humano)],
) -> UsuarioCreado:
    if body.role not in USER_ROLES:
        raise HTTPException(status_code=422, detail=f"Rol inválido. Válidos: {USER_ROLES}")

    email = body.email.lower()
    ya = (
        await session.execute(select(User).where(User.org_id == p.org.id, User.email == email))
    ).scalar_one_or_none()
    if ya is not None:
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email.")

    generada = None
    clave = body.password
    if clave is None:
        generada = secrets.token_urlsafe(12)
        clave = generada

    fila = User(
        org_id=p.org.id,
        email=email,
        full_name=body.full_name,
        role=body.role,
        password_hash=hash_password(clave),
    )
    session.add(fila)
    await session.commit()
    log.info("usuario creado", org=p.org.slug, email=email, role=body.role)
    return UsuarioCreado(id=fila.id, email=email, role=body.role, password_generada=generada)


class UsuarioPatch(BaseModel):
    active: bool | None = None
    role: str | None = None


@router.patch("/admin/usuarios/{user_id}", summary="Activar, desactivar o cambiar rol")
async def modificar_usuario(
    user_id: uuid.UUID,
    body: UsuarioPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    p: Annotated[Principal, Depends(require_admin_humano)],
) -> UsuarioOut:
    fila = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == p.org.id))
    ).scalar_one_or_none()
    if fila is None:
        raise HTTPException(status_code=404, detail="No existe ese usuario.")

    # Nadie se desactiva ni se degrada a sí mismo: el tenant no puede quedar
    # sin ningún admin por un click. El último owner queda protegido por esto.
    if p.user is not None and fila.id == p.user.id:
        raise HTTPException(status_code=422, detail="No podés modificarte a vos mismo.")

    if body.role is not None:
        if body.role not in USER_ROLES:
            raise HTTPException(status_code=422, detail=f"Rol inválido. Válidos: {USER_ROLES}")
        fila.role = body.role
    if body.active is not None:
        fila.active = body.active
    await session.commit()
    log.info(
        "usuario modificado",
        org=p.org.slug,
        email=fila.email,
        active=fila.active,
        role=fila.role,
    )
    return UsuarioOut(
        id=fila.id,
        email=fila.email,
        full_name=fila.full_name,
        role=fila.role,
        active=fila.active,
        last_login_at=fila.last_login_at,
        created_at=fila.created_at,
    )
