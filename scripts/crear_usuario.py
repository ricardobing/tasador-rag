"""Alta de usuarios y de API keys — doc 06 §1, doc 07 §2.

No hay registro público: los usuarios los crea un admin. Este script es ese
admin mientras `/admin/usuarios` no exista.

    uv run python scripts/crear_usuario.py --email ricardo@inmo-demo.com.ar --rol owner
    uv run python scripts/crear_usuario.py --email x@y.com --password-stdin
    uv run python scripts/crear_usuario.py --api-key "panel de la inmobiliaria"
    uv run python scripts/crear_usuario.py --listar

⚠️ La contraseña **no se toma por argumento**. Un `--password hunter2` queda en
el historial del shell, en `ps`, y en los logs de cualquier cosa que capture la
línea de comandos. Se pide por prompt (sin eco) o por stdin.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys

from tasador.cli import run

ROLES = ("owner", "admin", "agent", "viewer")


async def _crear_usuario(slug: str, email: str, rol: str, nombre: str | None, clave: str) -> int:
    from sqlalchemy import select

    from tasador.db.base import get_session_factory
    from tasador.db.models import Organization, User
    from tasador.security import hash_password

    async with get_session_factory()() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if org is None:
            print(f"No existe la organización '{slug}'. Correr scripts/seed.py primero.")
            return 2

        existente = (
            await session.execute(
                select(User).where(User.org_id == org.id, User.email == email.lower())
            )
        ).scalar_one_or_none()
        if existente is not None:
            # Idempotente pero explícito: cambiar la contraseña de alguien es
            # una acción distinta de crearlo y tiene que verse en la salida.
            existente.password_hash = hash_password(clave)
            existente.role = rol
            await session.commit()
            print(f"ya existía: {email} en {org.slug} — contraseña y rol ACTUALIZADOS")
            return 0

        session.add(
            User(
                org_id=org.id,
                email=email.lower(),
                full_name=nombre,
                password_hash=hash_password(clave),
                role=rol,
            )
        )
        await session.commit()
        print(f"creado: {email}  ({rol})  en {org.slug}")
        return 0


async def _crear_api_key(slug: str, nombre: str) -> int:
    from sqlalchemy import select

    from tasador.db.base import get_session_factory
    from tasador.db.models import ApiKey, Organization
    from tasador.security import generar_api_key

    async with get_session_factory()() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if org is None:
            print(f"No existe la organización '{slug}'.")
            return 2

        clave, prefijo, hash_ = generar_api_key()
        session.add(ApiKey(org_id=org.id, name=nombre, prefix=prefijo, key_hash=hash_))
        await session.commit()

        # LA ÚNICA VEZ que esta clave se ve. No se guarda en claro (doc 06 §1):
        # si se pierde, se revoca y se emite otra. Es la respuesta honesta y es
        # mejor producto que poder recuperarla.
        print()
        print("═" * 66)
        print(f"API KEY para {org.name} — «{nombre}»")
        print("═" * 66)
        print(f"\n  {clave}\n")
        print("Se muestra UNA sola vez: no se guarda en claro en ningún lado.")
        print(f"Para revocarla se busca por su prefijo: {prefijo}")
        print()
        print("Uso:  curl -H 'Authorization: Bearer <clave>' .../v1/reports")
        return 0


async def _listar(slug: str) -> int:
    from sqlalchemy import select

    from tasador.db.base import get_session_factory
    from tasador.db.models import ApiKey, Organization, User

    async with get_session_factory()() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if org is None:
            print(f"No existe la organización '{slug}'.")
            return 2

        usuarios = (
            (await session.execute(select(User).where(User.org_id == org.id).order_by(User.email)))
            .scalars()
            .all()
        )
        claves = (
            (await session.execute(select(ApiKey).where(ApiKey.org_id == org.id))).scalars().all()
        )

    print(f"\n{org.name} ({org.slug})\n")
    print(f"USUARIOS ({len(usuarios)})")
    for u in usuarios:
        estado = "activo" if u.active else "INACTIVO"
        login = u.last_login_at.date().isoformat() if u.last_login_at else "nunca"
        clave = "con clave" if u.password_hash else "SIN CLAVE (no puede loguear)"
        print(f"  {u.email:<34}{u.role:<8}{estado:<10}{clave:<28}último login: {login}")

    print(f"\nAPI KEYS ({len(claves)})")
    for k in claves:
        estado = "REVOCADA" if k.revoked_at else "activa"
        uso = k.last_used_at.date().isoformat() if k.last_used_at else "nunca usada"
        print(f"  {k.prefix:<22}{k.name:<26}{estado:<10}{uso}")
    return 0


def _pedir_clave(desde_stdin: bool) -> str | None:
    if desde_stdin:
        clave = sys.stdin.readline().strip()
        return clave or None
    clave = getpass.getpass("Contraseña: ")
    if not clave:
        return None
    if clave != getpass.getpass("Repetir: "):
        print("No coinciden.")
        return None
    if len(clave) < 12:
        # 12 y no 8: es el mínimo que hace que una contraseña sobreviva a un
        # ataque offline si algún día se filtra la tabla. argon2 ayuda, pero no
        # arregla una contraseña de seis letras.
        print("Muy corta: mínimo 12 caracteres.")
        return None
    return clave


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", default="inmo-demo", help="slug del tenant")
    ap.add_argument("--email")
    ap.add_argument("--nombre")
    ap.add_argument("--rol", choices=ROLES, default="agent")
    ap.add_argument(
        "--password-stdin", action="store_true", help="leer la contraseña de la entrada estándar"
    )
    ap.add_argument(
        "--generar-password",
        action="store_true",
        help="generar una contraseña al azar y mostrarla una vez",
    )
    ap.add_argument("--api-key", metavar="NOMBRE", help="emitir una API key para el tenant")
    ap.add_argument("--listar", action="store_true", help="usuarios y claves del tenant")
    args = ap.parse_args()

    if args.listar:
        return run(_listar(args.org))
    if args.api_key:
        return run(_crear_api_key(args.org, args.api_key))
    if not args.email:
        ap.error("hace falta --email, --api-key o --listar")

    if args.generar_password:
        clave: str | None = secrets.token_urlsafe(18)
        print(f"\nContraseña generada (se muestra una vez):\n\n  {clave}\n")
    else:
        clave = _pedir_clave(args.password_stdin)
    if not clave:
        return 2

    return run(_crear_usuario(args.org, args.email, args.rol, args.nombre, clave))


if __name__ == "__main__":
    raise SystemExit(main())


# El script vive en `scripts/` y no en `/admin/usuarios` porque esa pantalla es
# Etapa 4 y esto hace falta HOY para poder loguearse. Cuando exista la pantalla,
# lo que importa —hash argon2id, clave visible una sola vez— ya está en
# `tasador.security` y no se reescribe: este archivo es una interfaz, no una
# implementación.
