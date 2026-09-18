"""Configuración del sistema.

Todo se lee del entorno. Nada de valores mágicos repartidos por el código.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Entorno ──────────────────────────────────────────────────────────
    env: Literal["development", "test", "production"] = "development"
    domain: str = "tasador.localhost"
    log_level: str = "INFO"
    tag: str = "dev"

    # Versiones que se estampan en cada informe. Sin esto el backtest no
    # significa nada (doc 03 §3.5).
    engine_version: str = "2026.08.1"
    method_version: str = "2026.08.1"

    # ── Datos ────────────────────────────────────────────────────────────
    database_url: str
    redis_url: str

    # ── Seguridad ────────────────────────────────────────────────────────
    secret_key: SecretStr
    encryption_key: SecretStr

    # ── LLM ──────────────────────────────────────────────────────────────
    # La app SOLO conoce esta URL. Nunca un SDK de proveedor (ADR-003).
    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: SecretStr

    # ── Observabilidad ───────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"
    sentry_dsn: str = ""

    # ── Embeddings ───────────────────────────────────────────────────────
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024
    # Dónde se cachea el modelo (2,2 GB): un volumen, no la imagen. Vacío =
    # `/data/models` si existe (el contenedor) o `data/models` del repo.
    models_dir: str = ""

    @property
    def models_path(self) -> Path:
        if self.models_dir:
            return Path(self.models_dir)
        montado = Path("/data/models")
        if montado.exists():
            return montado
        return Path(__file__).resolve().parents[2] / "data" / "models"

    # ── Ingesta ──────────────────────────────────────────────────────────
    # Qué portales alimentan ESTA instancia y con qué rótulo entran al corpus:
    # `"nombre-en-el-csv=PORTAL_A,otro=PORTAL_B"`. Vacío = solo datasets
    # públicos. Es una decisión de quien opera, no un default del producto
    # (doc 10 §2).
    portales: str = ""

    # Fuente externa OPCIONAL (doc 01 §2.2): el inventario del panel de la
    # inmobiliaria, por un rol de solo lectura. Con esto en false el sistema
    # arranca y funciona igual; hay un test que lo verifica.
    panel_source_enabled: bool = False
    panel_readonly_dsn: SecretStr = SecretStr("")

    # ── Geocodificación ──────────────────────────────────────────────────
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "Tasador/1.0"
    google_geocoding_api_key: SecretStr = SecretStr("")

    # ── Valuación ────────────────────────────────────────────────────────
    # Regla dura de doc 05 §8: por debajo de esto NO se emite un número.
    #
    # ⚠️ `ge=5`, no `ge=3`. Con `ge=3`, un `MIN_COMPARABLES=3` en el `.env`
    # bajaba la regla sin que nada avisara — y un informe con 3 comparables es
    # exactamente lo que doc 05 §8 dice que este sistema no emite. ADR-002 tiene
    # un test que cierra esa misma puerta trasera para el nodo 7
    # (`test_el_override_de_entorno_no_puede_saltear_el_adr_002`); acá la puerta
    # estaba abierta y el motivo por el que importa es el mismo (H-09).
    #
    # SUBIRLO desde el entorno sigue permitido: endurecer la regla no la viola.
    min_comparables: int = Field(default=5, ge=5)
    max_candidates: int = Field(default=60, ge=10)
    critic_max_retries: int = Field(default=2, ge=0, le=5)

    # ── Dónde se escribe ─────────────────────────────────────────────────
    # Vacío = autodetectar. En el contenedor `/app` es SOLO LECTURA y el
    # volumen escribible está montado en `/data/raw`; en Windows se trabaja
    # sobre el repo. Escribir en la ruta equivocada no falla al arrancar:
    # falla en el primer informe, con un PermissionError que no dice nada.
    data_dir: str = ""

    # ⚠️ EXPLÍCITO, y no derivado de `data_path`.
    #
    # El nodo 11 guardaba el PDF en `data_path / "artifacts"`. `data_path`
    # autodetecta `/data/raw`, y el volumen que la API y el worker COMPARTEN
    # está montado en `/data/artifacts`. En desarrollo no se notaba porque el
    # override monta `./data:/data/raw` en los dos servicios; en producción el
    # worker escribía en su propia capa efímera y
    # `GET /v1/reports/{id}/pdf` devolvía 404 SIEMPRE — verificado levantando
    # las dos imágenes.
    #
    # Vacío = `/data/artifacts` si existe (el contenedor), y `data_path /
    # "artifacts"` si no (Windows).
    artifacts_dir: str = ""

    @property
    def data_path(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir)
        montado = Path("/data/raw")
        if montado.is_dir():
            return montado
        return Path(__file__).resolve().parents[2] / "data"

    @property
    def artifacts_path(self) -> Path:
        if self.artifacts_dir:
            return Path(self.artifacts_dir)
        compartido = Path("/data/artifacts")
        if compartido.is_dir():
            return compartido
        return self.data_path / "artifacts"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def testing(self) -> bool:
        return self.env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
