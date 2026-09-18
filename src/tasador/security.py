"""Autenticación — doc 06 §1 y doc 10 §3.

Dos mecanismos para dos consumidores, y **ninguno de los dos es el header
`X-Org-Slug`** con el que este sistema venía funcionando:

| Consumidor | Mecanismo |
|---|---|
| Sistemas (el panel de la inmobiliaria, integraciones) | `Authorization: Bearer tsk_live_...` |
| Usuarios (la UI del Tasador) | Cookie de sesión `HttpOnly; Secure; SameSite=Lax` |

## Por qué esto era el bloqueante y no los datos

Hasta el 14/08 el tenant salía de un header sin secreto: **cualquiera que
supiera el slug de otra inmobiliaria veía sus informes.** No es un agujero
sutil, es la ausencia total de autenticación, y convertía en teatro al gate de
aislamiento multi-tenant — que verifica que las consultas filtren por `org_id`,
cosa que hacen, sobre un `org_id` que elegía el cliente.

## Tres decisiones

**El hash de contraseña es argon2id** (doc 10 §3), con los parámetros por
defecto de `argon2-cffi`, que son los recomendados por el RFC 9106. No se
inventan costos a mano.

**Las API keys se buscan por PREFIJO y se verifican con argon2.** Un hash
argon2 lleva sal, así que dos hasheos de la misma clave dan strings distintos y
buscar por `key_hash` no puede funcionar. El prefijo (`tsk_live_a3f1b2c8`) es
determinístico y público a propósito: sirve para encontrar el candidato, y lo
que autentica es la verificación argon2 del secreto completo.

**Todo error de auth es un 401 con el mismo cuerpo.** No se distingue entre
"clave inexistente", "clave revocada" y "contraseña incorrecta": la diferencia
es información gratis para quien está probando.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from tasador.settings import get_settings

log = structlog.get_logger()

# Parámetros por defecto de argon2-cffi (RFC 9106). Se dejan explícitos como
# comentario y no como argumentos: la librería los actualiza cuando el consenso
# cambia, y fijarlos a mano acá los congelaría en 2026.
_ph = PasswordHasher()

PREFIJO_API_KEY = "tsk_live_"
LARGO_PREFIJO_VISIBLE = 8

# La sesión dura un día laboral. Más que eso es cómodo y menos seguro; menos es
# incómodo sin ganar nada, porque no hay refresh token.
DURACION_SESION = timedelta(hours=12)
COOKIE_SESION = "tasador_sesion"


# ── Contraseñas ──────────────────────────────────────────────────────────
def hash_password(clave: str) -> str:
    return _ph.hash(clave)


def verificar_password(hash_guardado: str | None, clave: str) -> bool:
    """`False` ante cualquier problema, y **siempre después de hashear**.

    El `if not hash_guardado` de arriba parece un atajo y es una fuga: un
    usuario sin contraseña respondería en microsegundos y uno con contraseña en
    ~50 ms, así que el tiempo de respuesta diría cuáles existen. Por eso se
    hashea igual contra un valor descartable antes de devolver.
    """
    if not hash_guardado:
        _ph.hash(clave)  # trabajo equivalente: que el tiempo no delate nada
        return False
    try:
        return _ph.verify(hash_guardado, clave)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hay_que_rehashear(hash_guardado: str) -> bool:
    """Los parámetros recomendados suben con el hardware. Cuando argon2-cffi
    los actualice, el próximo login rehashea sin que nadie migre nada."""
    return _ph.check_needs_rehash(hash_guardado)


# ── API keys ─────────────────────────────────────────────────────────────
def generar_api_key() -> tuple[str, str, str]:
    """Devuelve `(clave_en_claro, prefijo, hash)`.

    La clave en claro se muestra UNA vez, al crearla, y no se guarda nunca
    (doc 06 §1). Si el cliente la pierde, se revoca y se emite otra: es la única
    respuesta honesta y es mejor producto que poder recuperarla.
    """
    secreto = secrets.token_urlsafe(32)
    clave = f"{PREFIJO_API_KEY}{secreto}"
    prefijo = clave[: len(PREFIJO_API_KEY) + LARGO_PREFIJO_VISIBLE]
    return clave, prefijo, _ph.hash(clave)


def prefijo_de(clave: str) -> str | None:
    """El prefijo con el que se busca el candidato en la base."""
    if not clave.startswith(PREFIJO_API_KEY):
        return None
    if len(clave) <= len(PREFIJO_API_KEY) + LARGO_PREFIJO_VISIBLE:
        return None
    return clave[: len(PREFIJO_API_KEY) + LARGO_PREFIJO_VISIBLE]


def verificar_api_key(clave: str, hash_guardado: str) -> bool:
    try:
        return _ph.verify(hash_guardado, clave)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ── Sesiones ─────────────────────────────────────────────────────────────
def emitir_sesion(user_id: str, org_id: str, role: str) -> str:
    """JWT firmado con `SECRET_KEY`, para la cookie HttpOnly.

    JWT y no un id opaco contra Redis: la sesión no necesita revocación
    inmediata —dura 12 h y el caso de "echar a alguien ya" se resuelve
    desactivando el usuario, que se chequea en CADA request contra la base— y
    evita una dependencia más en el camino de todo request.

    El `org_id` va adentro del token, pero **no se confía en él**: se relee de
    la fila del usuario. Está en el token para poder loguear y diagnosticar,
    no para autorizar.
    """
    s = get_settings()
    ahora = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "org": org_id,
            "role": role,
            "iat": ahora,
            "exp": ahora + DURACION_SESION,
        },
        s.secret_key.get_secret_value(),
        algorithm="HS256",
    )


def leer_sesion(token: str) -> dict[str, Any] | None:
    """`None` ante cualquier problema: expirada, firma inválida, o basura."""
    s = get_settings()
    try:
        return dict(
            jwt.decode(
                token,
                s.secret_key.get_secret_value(),
                algorithms=["HS256"],
                options={"require": ["exp", "sub"]},
            )
        )
    except jwt.PyJWTError:
        return None


def opciones_de_cookie() -> dict[str, Any]:
    """`Secure` solo fuera de desarrollo: en `http://localhost` una cookie
    `Secure` no se guarda y el login "no funciona" sin decir por qué."""
    s = get_settings()
    return {
        "httponly": True,
        "secure": s.is_production,
        "samesite": "lax",
        "max_age": int(DURACION_SESION.total_seconds()),
        "path": "/",
    }


# ── Links compartidos ────────────────────────────────────────────────────
# El propietario recibe un link, no una cuenta. 30 días: más que la vida útil
# de una tasación de mercado, menos que "para siempre".
DURACION_SHARE = timedelta(days=30)


def emitir_share(report_id: str, org_id: str) -> str:
    """Token firmado que da acceso de LECTURA a UN informe.

    Sin tabla: revocar todos los links es rotar `SECRET_KEY` (documentado en la
    guía). Si mañana hace falta revocación por informe, se agrega una tabla —
    hoy sería una fila más que mantener para un caso que no ocurrió nunca.

    `aud` separa este token de las sesiones: un token de share NO abre una
    sesión aunque los firme la misma clave, porque `leer_sesion` exige `sub` y
    esto no lo trae — y viceversa, una sesión no pasa por `leer_share` porque
    exige `aud=share`.
    """
    s = get_settings()
    ahora = datetime.now(UTC)
    return jwt.encode(
        {
            "aud": "share",
            "report": report_id,
            "org": org_id,
            "iat": ahora,
            "exp": ahora + DURACION_SHARE,
        },
        s.secret_key.get_secret_value(),
        algorithm="HS256",
    )


def leer_share(token: str) -> tuple[str, str] | None:
    """`(report_id, org_id)` si el token es válido y no venció; `None` si no.

    Devuelve TAMBIÉN el org para que la consulta del endpoint filtre por tenant
    igual que todas las demás: el token es la credencial, pero el filtro de
    `org_id` no se negocia ni acá (tests/architecture/test_aislamiento.py).
    """
    s = get_settings()
    try:
        datos = jwt.decode(
            token,
            s.secret_key.get_secret_value(),
            algorithms=["HS256"],
            audience="share",
            options={"require": ["exp", "report", "org", "aud"]},
        )
        return str(datos["report"]), str(datos["org"])
    except jwt.PyJWTError:
        return None


def comparar_seguro(a: str, b: str) -> bool:
    """Comparación en tiempo constante, para tokens que no pasan por argon2."""
    return hmac.compare_digest(a.encode(), b.encode())
