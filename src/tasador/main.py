"""Punto de entrada de la API.

Etapa 0: solo /v1/health y /v1/ready. Lo mínimo para tener algo real
corriendo en producción con TLS, CI y backups antes de escribir lógica.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tasador.settings import get_settings
from tasador.v1 import admin, ask, auth, calidad, comparables, health, inventory, reports

log = structlog.get_logger()

PROBLEM_JSON = "application/problem+json"


def _titulo(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:  # un status fuera del registro
        return "Error"


def problema(
    status: int,
    detail: Any,
    *,
    path: str,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    """Un error, en el formato que el contrato promete: RFC 7807 (doc 06 §3).

    Hasta el 15/08 lo cumplía UN solo handler, el de 500. Todo lo demás salía
    como `application/json` con el `{"detail": ...}` por defecto de FastAPI —
    donde `detail` es a veces un string y a veces una lista de objetos de
    pydantic. Un cliente escrito contra el contrato buscaba `title` y `status`
    y no los encontraba.

    `detail` se conserva con el mismo significado que tenía, así que el panel y
    los tests que lo leen siguen funcionando: RFC 7807 lo incluye.
    """
    cuerpo: dict[str, Any] = {
        "type": "about:blank",
        "title": _titulo(status),
        "status": status,
        "detail": detail,
        "instance": path,
    }
    if errors is not None:
        cuerpo["errors"] = errors
    return JSONResponse(status_code=status, content=cuerpo, media_type=PROBLEM_JSON)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    log.info("arrancando", env=s.env, engine=s.engine_version, tag=s.tag)
    yield
    log.info("apagando")


def create_app() -> FastAPI:
    s = get_settings()

    app = FastAPI(
        title="Tasador API",
        version=s.engine_version,
        # La doc interactiva no se expone en producción.
        docs_url=None if s.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if s.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Correlación: todo log y toda respuesta llevan el mismo id."""
        rid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_problema(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Todo `HTTPException`, incluidos los 404 de ruta que levanta Starlette
        antes de llegar a un endpoint."""
        r = problema(exc.status_code, exc.detail, path=request.url.path)
        for k, v in (exc.headers or {}).items():
            r.headers[k] = v
        return r

    @app.exception_handler(RequestValidationError)
    async def validacion_problema(request: Request, exc: RequestValidationError) -> JSONResponse:
        """El 422 de pydantic, sin filtrar la estructura interna del modelo.

        El default incluye `loc` con la ruta completa dentro del modelo, el
        tipo de error de pydantic, el `ctx` y —lo peor— el `input` completo que
        se mandó, que puede traer de vuelta datos del cliente. Acá queda solo
        qué campo y qué le pasa.
        """
        errores = [
            {
                # `loc` viene como ("body", "property", "surface_covered"): se
                # descarta el primer tramo, que es dónde venía y no qué campo es.
                "field": ".".join(str(p) for p in e.get("loc", ())[1:]) or "body",
                "message": str(e.get("msg", "inválido")),
            }
            for e in exc.errors()
        ]
        return problema(
            422,
            "La solicitud no cumple el contrato.",
            path=request.url.path,
            errors=errores,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Nunca filtrar internals al cliente. El detalle va al log."""
        log.exception("error no manejado", path=request.url.path)
        return problema(500, "Ocurrió un error inesperado.", path=request.url.path)

    app.include_router(health.router, prefix="/v1", tags=["operación"])
    app.include_router(auth.router, prefix="/v1", tags=["autenticación"])
    app.include_router(reports.router, prefix="/v1", tags=["informes"])
    app.include_router(ask.router, prefix="/v1", tags=["informes"])
    app.include_router(comparables.router, prefix="/v1", tags=["corpus"])
    app.include_router(calidad.router, prefix="/v1", tags=["calidad"])
    app.include_router(admin.router, prefix="/v1", tags=["administración"])
    app.include_router(inventory.router, prefix="/v1", tags=["inventario"])
    return app


app = create_app()
