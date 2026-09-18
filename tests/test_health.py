"""Tests de la API de Etapa 0.

No tocan internet ni gastan en APIs: `/health` no consulta dependencias
y `/ready` se prueba con las verificaciones mockeadas.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tasador.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_health_no_depende_de_nada(client: TestClient) -> None:
    """Liveness tiene que responder aunque Postgres esté caído: si no,
    Docker reiniciaría el contenedor por un problema ajeno."""
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_devuelve_version(client: TestClient) -> None:
    assert "version" in client.get("/v1/health").json()


def test_request_id_en_toda_respuesta(client: TestClient) -> None:
    assert client.get("/v1/health").headers.get("X-Request-Id")


def test_request_id_se_respeta_si_viene(client: TestClient) -> None:
    r = client.get("/v1/health", headers={"X-Request-Id": "abc-123"})
    assert r.headers["X-Request-Id"] == "abc-123"


def test_ready_503_si_postgres_caido(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from tasador.v1 import health as h

    async def down() -> str:
        return "down"

    async def ok() -> str:
        return "ok"

    monkeypatch.setattr(h, "_check_postgres", down)
    monkeypatch.setattr(h, "_check_redis", ok)
    monkeypatch.setattr(h, "_check_litellm", ok)

    r = client.get("/v1/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "down"


def test_ready_degradado_si_solo_falla_litellm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El gateway de modelos caído no debe sacar la API de rotación: los
    informes ya generados se siguen sirviendo y el trabajo se encola."""
    from tasador.v1 import health as h

    async def ok() -> str:
        return "ok"

    async def degraded() -> str:
        return "degraded"

    monkeypatch.setattr(h, "_check_postgres", ok)
    monkeypatch.setattr(h, "_check_redis", ok)
    monkeypatch.setattr(h, "_check_litellm", degraded)

    r = client.get("/v1/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
