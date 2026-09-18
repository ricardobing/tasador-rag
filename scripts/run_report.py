"""Corre un informe de punta a punta, sin API ni worker de por medio.

Es el gate de verificación del grafo: crea la propiedad sujeto, crea el
informe, corre los 9 nodos habilitados y muestra la traza real de
`core.report_events` — qué nodo corrió, cuánto tardó, cuánto costó y cuál
todavía es un stub.

    uv run python scripts/run_report.py --direccion "Av. Cabildo 2530" --amb 3 --m2 78

Con `--api` hace lo mismo pero atravesando `POST /v1/reports` y
`GET /v1/reports/{id}` en proceso (sin levantar uvicorn), para verificar el
contrato HTTP y no solo el grafo.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from tasador.cli import run
from tasador.db.base import get_session_factory
from tasador.db.models import Organization, Report, ReportEvent, SubjectProperty
from tasador.settings import get_settings


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")


async def _crear(args: argparse.Namespace) -> tuple[str, str]:
    from tasador.agents.config import load_agents_config

    s = get_settings()
    async with get_session_factory()() as session:
        org = (
            await session.execute(
                select(Organization).where(Organization.active.is_(True)).limit(1)
            )
        ).scalar_one_or_none()
        if org is None:
            raise SystemExit(
                "No hay ninguna organización. Corré: uv run python scripts/seed_org.py"
            )

        sujeto = SubjectProperty(
            org_id=org.id,
            address_raw=args.direccion,
            city=args.ciudad,
            property_type=args.tipo,
            rooms=args.amb,
            surface_total=Decimal(str(args.m2)) if args.m2 else None,
            surface_covered=Decimal(str(args.m2_cubiertos)) if args.m2_cubiertos else None,
            condition=args.estado,
            orientation=args.orientacion,
            floor_number=args.piso,
        )
        session.add(sujeto)
        await session.flush()

        informe = Report(
            org_id=org.id,
            subject_property_id=sujeto.id,
            status="QUEUED",
            requested_via="UI",
            engine_version=s.engine_version,
            method_version=s.method_version,
            prompt_bundle_version=load_agents_config().bundle_hash,
        )
        session.add(informe)
        await session.commit()
        return str(informe.id), org.slug


async def _traza(report_id: str) -> None:
    async with get_session_factory()() as session:
        informe = (await session.execute(select(Report).where(Report.id == report_id))).scalar_one()
        eventos = (
            (
                await session.execute(
                    select(ReportEvent)
                    .where(ReportEvent.report_id == report_id)
                    .order_by(ReportEvent.seq)
                )
            )
            .scalars()
            .all()
        )

    print(f"\nTRAZA de core.report_events  ({len(eventos)} filas)")
    print(f"{'seq':>3}  {'NODO':<20} {'ESTADO':<8} {'ms':>6} {'USD':>10}  DETALLE")
    print("-" * 92)
    for e in eventos:
        det = e.detail or {}
        marca = "STUB — " + str(det.get("pendiente", "")) if det.get("stub") else ""
        if not marca:
            marca = ", ".join(
                f"{k}={v}"
                for k, v in det.items()
                if k
                in ("comparables_found", "comparables_used", "confidence", "insufficient_reason")
                and v is not None
            )
        costo = f"{float(e.cost_usd):.6f}" if e.cost_usd is not None else "—"
        print(
            f"{e.seq:>3}  {e.node:<20} {e.status:<8} {e.duration_ms or 0:>6} {costo:>10}  {marca}"
        )

    print(f"\nRESULTADO   status={informe.status}")
    if informe.insufficient_reason:
        print(f"            insufficient_reason={informe.insufficient_reason}")
    if informe.error_code:
        print(f"            error={informe.error_code}: {informe.error_detail}")
    if informe.value_mid is not None:
        print(
            f"            USD {informe.value_low:,.0f} — {informe.value_mid:,.0f} — "
            f"{informe.value_high:,.0f}   ({informe.confidence}, "
            f"{informe.comparables_used}/{informe.comparables_found} comparables)"
        )
    print(f"            costo total USD {float(informe.cost_usd):.6f} · {informe.duration_ms} ms")
    print(
        f"            versiones: motor={informe.engine_version} "
        f"metodo={informe.method_version} prompts={informe.prompt_bundle_version}"
    )


async def _por_api(args: argparse.Namespace) -> str:
    """Mismo recorrido pero atravesando los endpoints HTTP, en proceso."""
    import httpx

    from tasador.main import app

    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://test") as c:
        r = await c.post(
            "/v1/reports",
            json={
                "external_ref": "gate-fase-1",
                "property": {
                    "address_raw": args.direccion,
                    "property_type": args.tipo,
                    "rooms": args.amb,
                    "surface_total": float(args.m2) if args.m2 else None,
                },
            },
            headers={"Idempotency-Key": "gate-fase-1-001"},
        )
        print(f"POST /v1/reports -> {r.status_code}")
        print(f"  {r.json()}")
        r.raise_for_status()
        report_id = r.json()["report_id"]

        # Segunda vez con la MISMA clave: no debe crear otro informe.
        r2 = await c.post(
            "/v1/reports",
            json={"property": {"address_raw": args.direccion, "property_type": args.tipo}},
            headers={"Idempotency-Key": "gate-fase-1-001"},
        )
        igual = r2.json()["report_id"] == report_id
        print(f"POST idempotente -> {r2.status_code}  mismo report_id: {igual}")
        if not igual:
            raise SystemExit("❌ La idempotencia no funciona: creó un informe nuevo.")

    return str(report_id)


async def _consultar_api(report_id: str) -> dict[str, Any]:
    import httpx

    from tasador.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get(f"/v1/reports/{report_id}")
        r.raise_for_status()
        return dict(r.json())


async def _main(args: argparse.Namespace) -> None:
    from tasador.agents.runner import run_report

    if args.api:
        report_id = await _por_api(args)
    else:
        report_id, slug = await _crear(args)
        print(f"informe {report_id} creado para '{slug}'")

    print("\ncorriendo el grafo…")
    await run_report(report_id)
    await _traza(report_id)

    if args.api:
        cuerpo = await _consultar_api(report_id)
        p = cuerpo["progress"]
        print(
            f"\nGET /v1/reports/{{id}} -> status={cuerpo['status']} "
            f"progreso={p['completed']}/{p['total']} pasos={len(p['steps'])}"
        )
        if cuerpo.get("insufficient_reason"):
            print(f"  insufficient_reason: {cuerpo['insufficient_reason']}")
            print(f"  detail: {cuerpo.get('detail')}")


def main() -> None:
    _utf8()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--direccion", default="Av. Cabildo 2530")
    ap.add_argument("--ciudad", default="CABA")
    ap.add_argument("--tipo", default="departamento")
    ap.add_argument("--amb", type=int, default=3)
    ap.add_argument("--m2", type=float, default=78.0)
    ap.add_argument("--m2-cubiertos", type=float, default=None)
    ap.add_argument("--estado", default=None)
    ap.add_argument("--orientacion", default=None)
    ap.add_argument("--piso", type=int, default=None)
    ap.add_argument("--api", action="store_true", help="atravesar los endpoints HTTP")
    run(_main(ap.parse_args()))


if __name__ == "__main__":
    main()
