"""Reporte de cobertura por barrio — la puerta de decisión de la Etapa 1.

Responde la pregunta que define si el proyecto sigue: **¿hay suficientes
comparables activos en los barrios donde opera el tenant?**

Y el chequeo de sesgo: nuestro USD/m² contra la serie oficial del GCBA. Como
esa serie se construye sobre la base de Portal A, un desvío grande señala un
problema NUESTRO (parseo, dedup, filtros), no del mercado.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Listing, MarketIndex, Neighborhood
from tasador.ingest.cards import usd_per_m2

MIN_COMPARABLES = 5  # regla dura de doc 05 §8
OBJETIVO_POR_BARRIO = 20  # para tener margen tras curar
DESVIO_ALERTA = Decimal("0.15")


@dataclass(slots=True)
class BarrioCoverage:
    name: str
    city: str
    activos: int
    con_precio_y_superficie: int
    usd_m2_mediana: Decimal | None
    usd_m2_oficial: Decimal | None
    desvio: Decimal | None
    fuentes: dict[str, int]

    @property
    def estado(self) -> str:
        if self.con_precio_y_superficie >= OBJETIVO_POR_BARRIO:
            return "OK"
        if self.con_precio_y_superficie >= MIN_COMPARABLES:
            return "JUSTO"
        return "INSUFICIENTE"

    @property
    def sesgo_alerta(self) -> bool:
        return self.desvio is not None and abs(self.desvio) > DESVIO_ALERTA


async def _official_usd_m2(session: AsyncSession, neighborhood_id: uuid.UUID) -> Decimal | None:
    """Último dato oficial disponible del barrio. La serie tiene huecos, así
    que se toma el más reciente que exista, no el del período actual."""
    row = (
        await session.execute(
            select(MarketIndex.usd_per_m2)
            .where(
                MarketIndex.neighborhood_id == neighborhood_id,
                MarketIndex.source == "BADATA",
                MarketIndex.property_type == "departamento",
            )
            .order_by(MarketIndex.period.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def coverage_report(
    session: AsyncSession, barrios: list[str] | None = None
) -> list[BarrioCoverage]:
    q = select(Neighborhood).where(Neighborhood.active)
    if barrios:
        q = q.where(Neighborhood.name.in_(barrios))
    neighborhoods = (await session.execute(q.order_by(Neighborhood.name))).scalars().all()

    out: list[BarrioCoverage] = []
    for n in neighborhoods:
        # `active` excluye los históricos de BA Data a propósito: no son
        # mercado vigente y contarlos daría una cobertura falsa.
        listings = (
            (
                await session.execute(
                    select(Listing).where(
                        Listing.neighborhood_id == n.id,
                        Listing.active,
                        Listing.operation == "SALE",
                    )
                )
            )
            .scalars()
            .all()
        )

        valores = [v for lst in listings if (v := usd_per_m2(lst)) is not None]
        fuentes: dict[str, int] = {}
        for lst in listings:
            fuentes[lst.source] = fuentes.get(lst.source, 0) + 1

        mediana = Decimal(statistics.median(valores)).quantize(Decimal("1")) if valores else None
        oficial = await _official_usd_m2(session, n.id)
        desvio = (
            ((mediana - oficial) / oficial).quantize(Decimal("0.001"))
            if (mediana and oficial and oficial > 0)
            else None
        )

        out.append(
            BarrioCoverage(
                name=n.name,
                city=n.city,
                activos=len(listings),
                con_precio_y_superficie=len(valores),
                usd_m2_mediana=mediana,
                usd_m2_oficial=oficial,
                desvio=desvio,
                fuentes=fuentes,
            )
        )
    return out


def render(rows: list[BarrioCoverage]) -> str:
    """Salida para consola. Es el artefacto de la puerta de decisión: se pega
    tal cual en el informe de cierre de etapa."""
    lines = [
        "",
        f"{'BARRIO':<22}{'ACT':>5}{'USABLES':>9}{'USD/m2':>9}{'OFICIAL':>9}{'DESVIO':>9}  ESTADO",
        "─" * 78,
    ]
    for r in sorted(rows, key=lambda x: -x.con_precio_y_superficie):
        desvio = f"{r.desvio * 100:+.1f}%" if r.desvio is not None else "—"
        flag = " ⚠" if r.sesgo_alerta else ""
        lines.append(
            f"{r.name:<22}{r.activos:>5}{r.con_precio_y_superficie:>9}"
            f"{r.usd_m2_mediana or '—'!s:>9}{r.usd_m2_oficial or '—'!s:>9}"
            f"{desvio:>9}  {r.estado}{flag}"
        )

    ok = sum(1 for r in rows if r.estado == "OK")
    justo = sum(1 for r in rows if r.estado == "JUSTO")
    insuf = sum(1 for r in rows if r.estado == "INSUFICIENTE")
    total = len(rows) or 1

    lines += [
        "─" * 78,
        f"OK {ok}   JUSTO {justo}   INSUFICIENTE {insuf}   "
        f"(cobertura utilizable: {100 * (ok + justo) // total}%)",
        "",
        "PUERTA DE DECISIÓN (doc 12, Etapa 1):",
    ]
    if (ok + justo) / total >= 0.7:
        lines.append("  ✅ Cobertura suficiente. Seguir a la Etapa 2.")
    elif ok + justo > 0:
        lines.append("  ⚠️  Cobertura parcial. Ampliar ingesta antes de seguir,")
        lines.append("      o acotar el alcance a los barrios en OK.")
    else:
        lines.append("  🔴 Sin cobertura. Replantear la estrategia de datos (doc 13 R1).")

    alertas = [r for r in rows if r.sesgo_alerta]
    if alertas:
        lines += ["", f"⚠️  SESGO en {len(alertas)} barrio(s) — desvío > 15% vs. serie oficial:"]
        lines += [
            f"     {r.name}: nuestro {r.usd_m2_mediana} vs oficial {r.usd_m2_oficial}"
            for r in alertas
        ]
        lines.append("     Revisar parseo, deduplicación y filtros antes de confiar en el corpus.")

    return "\n".join(lines)
