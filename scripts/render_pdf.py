"""Regenera el PDF de un informe ya guardado, sin volver a correr el grafo.

Un informe es un DOCUMENTO: sus números y su narrativa ya están en la base y no
cambian. Poder re-renderizar el PDF —porque cambió la plantilla, porque se
perdió el archivo, porque hay que reimprimirlo con otra marca— no tiene por qué
costar un centavo ni volver a llamar a ningún modelo.

    uv run python scripts/render_pdf.py                 # el último exitoso
    uv run python scripts/render_pdf.py --id <uuid>
    uv run python scripts/render_pdf.py --solo-html     # sin GTK, para mirar

⚠️ El paso a PDF necesita las librerías nativas de GTK: corre en el contenedor
Linux, no en Windows. `--solo-html` sí corre en cualquier lado.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from tasador.agents.nodes.render import html_a_pdf, informe_html
from tasador.agents.state import estado_inicial
from tasador.cli import run
from tasador.db.base import get_session_factory
from tasador.db.models import Listing, Neighborhood, Organization, Report, SubjectProperty


async def _armar(report_id: str | None) -> tuple[str, str, str]:
    """Reconstruye el estado mínimo que la plantilla necesita, desde la base."""
    async with get_session_factory()() as s:
        q = select(Report).where(Report.narrative_md.is_not(None))
        if report_id:
            q = select(Report).where(Report.id == report_id)
        informe = (
            await s.execute(q.order_by(Report.created_at.desc()).limit(1))
        ).scalar_one_or_none()
        if informe is None:
            raise SystemExit("No hay ningún informe con narrativa. Corré scripts/run_report.py")

        sujeto = (
            await s.execute(
                select(SubjectProperty).where(SubjectProperty.id == informe.subject_property_id)
            )
        ).scalar_one()
        org = (
            await s.execute(select(Organization.name).where(Organization.id == informe.org_id))
        ).scalar_one_or_none()

        barrio = None
        if sujeto.neighborhood_id is not None:
            barrio = (
                await s.execute(
                    select(Neighborhood.name).where(Neighborhood.id == sujeto.neighborhood_id)
                )
            ).scalar_one_or_none()
        # Las direcciones de los comparables: la plantilla las toma de los
        # candidatos del nodo 2, que acá no existen; se rearman desde el corpus.
        ids = [
            uuid.UUID(str(d["listing_id"]))
            for d in (informe.methodology or {}).get("detail", [])
            if d.get("listing_id")
        ]
        candidatos = []
        if ids:
            filas = (
                await s.execute(select(Listing.id, Listing.address_raw).where(Listing.id.in_(ids)))
            ).all()
            candidatos = [{"listing_id": str(i), "address": a} for i, a in filas]

    estado = estado_inicial(str(informe.id), str(informe.org_id), str(informe.subject_property_id))
    # La ficha completa de la propiedad, con las mismas claves que usa el grafo
    # (v1 de la plantilla solo mostraba la dirección; v2 imprime todo, y lo que
    # falta lo dice).
    estado["subject"] = {
        "address_raw": sujeto.address_raw,
        "neighborhood_name": barrio,
        "property_type": sujeto.property_type,
        "rooms": sujeto.rooms,
        "bedrooms": sujeto.bedrooms,
        "bathrooms": sujeto.bathrooms,
        "surface_total": str(sujeto.surface_total) if sujeto.surface_total is not None else None,
        "surface_covered": (
            str(sujeto.surface_covered) if sujeto.surface_covered is not None else None
        ),
        "age_years": sujeto.age_years,
        "floor_number": sujeto.floor_number,
        "has_elevator": sujeto.has_elevator,
        "condition": sujeto.condition,
        "orientation": sujeto.orientation,
        "parking_spaces": sujeto.parking_spaces,
        "expenses_ars": str(sujeto.expenses_ars) if sujeto.expenses_ars is not None else None,
    }
    estado["candidates"] = candidatos  # type: ignore[typeddict-item]
    # `methodology` es la traza completa del cálculo: alcanza para rearmar el
    # documento sin recalcular nada.
    estado["valuation"] = informe.methodology or {}
    estado["draft_md"] = informe.narrative_md or ""
    estado["prompt_bundle_version"] = informe.prompt_bundle_version  # type: ignore[typeddict-unknown-key]

    return informe_html(estado, org=org or "Tasador"), str(informe.id), informe.status


async def _main(args: argparse.Namespace) -> None:
    html, rid, estado = await _armar(args.id)
    salida = Path(args.salida or "data/artifacts")
    salida.mkdir(parents=True, exist_ok=True)

    ruta_html = salida / f"{rid}.html"
    ruta_html.write_text(html, encoding="utf-8")
    print(f"informe {rid} ({estado})")
    print(f"  HTML: {ruta_html}  ({len(html):,} caracteres)")

    if args.solo_html:
        return

    import hashlib

    pdf = html_a_pdf(html)
    ruta_pdf = salida / f"{rid}.pdf"
    ruta_pdf.write_bytes(pdf)
    print(f"  PDF : {ruta_pdf}  ({len(pdf):,} bytes)")
    print(f"  sha256: {hashlib.sha256(pdf).hexdigest()}")


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", default=None)
    ap.add_argument("--salida", default=None)
    ap.add_argument("--solo-html", action="store_true", help="sin GTK; para inspeccionar")
    run(_main(ap.parse_args()))


if __name__ == "__main__":
    main()
