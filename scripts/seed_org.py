"""Crea la organización de desarrollo. Idempotente.

`core.reports.org_id` es NOT NULL desde el día 1 y no hay atajo: sin tenant no
hay informe. Esto existe para no tener que inventar uno a mano en psql cada
vez que se recrea el volumen.

    uv run python scripts/seed_org.py --slug inmo-demo --name "la inmobiliaria"
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from tasador.cli import run
from tasador.db.base import get_session_factory
from tasador.db.models import Organization


async def _seed(slug: str, name: str) -> None:
    async with get_session_factory()() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if org is not None:
            print(f"ya existía: {org.name} ({org.slug})  id={org.id}")
            return
        org = Organization(name=name, slug=slug)
        session.add(org)
        await session.commit()
        print(f"creada: {org.name} ({org.slug})  id={org.id}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="inmo-demo")
    ap.add_argument("--name", default="la inmobiliaria")
    args = ap.parse_args()
    run(_seed(args.slug, args.name))


if __name__ == "__main__":
    main()
