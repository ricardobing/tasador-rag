"""Backtest — doc 09.

La pregunta que responde es la única que importa:
**¿el sistema le gana a una consulta SQL de una línea?**

Protocolo leave-one-out temporal sobre los avisos históricos de BA Data: se
toma un caso, se lo QUITA del conjunto de comparables, se lo trata como
propiedad sujeto y se compara la predicción contra su precio real.

El baseline se calcula EN LA MISMA CORRIDA y sobre LOS MISMOS CASOS. Comparar
contra un baseline medido en otro momento sobre otro conjunto es autoengaño
(doc 09 §2).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import Listing, ListingCluster, ListingFeatures, Neighborhood
from tasador.valuation.engine import value
from tasador.valuation.models import Comparable, Condition, Orientation, Property

log = structlog.get_logger()
D = Decimal


@dataclass(slots=True)
class Case:
    ref: str
    neighborhood_id: Any
    neighborhood: str
    actual_price: Decimal
    surface: Decimal
    rooms: int | None


@dataclass(slots=True)
class CaseResult:
    ref: str
    neighborhood: str
    actual: Decimal
    predicted: Decimal | None
    baseline: Decimal | None
    ape: Decimal | None = None
    baseline_ape: Decimal | None = None
    in_range: bool = False
    comparables_used: int = 0
    confidence: str | None = None
    status: str = "OK"


@dataclass(slots=True)
class BacktestResult:
    dataset: str
    method_version: str
    n_cases: int = 0
    n_evaluated: int = 0
    coverage: Decimal | None = None
    mdape: Decimal | None = None
    mape: Decimal | None = None
    ppe10: Decimal | None = None
    ppe20: Decimal | None = None
    hit_rate: Decimal | None = None
    bias: Decimal | None = None
    baseline_mdape: Decimal | None = None
    baseline_ppe20: Decimal | None = None
    por_barrio: dict[str, Decimal] = field(default_factory=dict)
    por_confianza: dict[str, Decimal] = field(default_factory=dict)
    items: list[CaseResult] = field(default_factory=list)

    @property
    def beats_baseline(self) -> bool:
        return (
            self.mdape is not None
            and self.baseline_mdape is not None
            and self.mdape < self.baseline_mdape
        )


def _median(xs: list[Decimal]) -> Decimal:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


async def load_cases(
    session: AsyncSession, *, sample: int, seed: int = 42, min_per_neighborhood: int = 40
) -> tuple[list[Case], dict[Any, list[Comparable]]]:
    """Carga los casos y el pool de comparables por barrio.

    Solo barrios con suficientes avisos: medir sobre un barrio con 6 avisos
    dice más del tamaño de la muestra que del método.
    """
    counts: dict[Any, int] = dict(
        (
            await session.execute(
                select(Listing.neighborhood_id, func.count())
                .where(Listing.source == "BADATA", Listing.neighborhood_id.is_not(None))
                .group_by(Listing.neighborhood_id)
            )
        ).all()  # type: ignore[arg-type]
    )
    validos = {nid for nid, c in counts.items() if c >= min_per_neighborhood}
    nombres: dict[Any, str] = dict(
        (await session.execute(select(Neighborhood.id, Neighborhood.name))).all()  # type: ignore[arg-type]
    )

    rows = (
        await session.execute(
            select(Listing, ListingFeatures)
            .join(ListingFeatures, ListingFeatures.listing_id == Listing.id)
            .where(
                Listing.source == "BADATA",
                Listing.neighborhood_id.in_(validos),
                Listing.price.is_not(None),
            )
        )
    ).all()

    pool: dict[Any, list[Comparable]] = {}
    todos: list[Case] = []

    for lst, feat in rows:
        sup = feat.surface_covered or feat.surface_total
        if not sup or sup <= 0 or not lst.price:
            continue
        nid = lst.neighborhood_id
        barrio = nombres.get(nid, "?")
        pool.setdefault(nid, []).append(
            Comparable(
                ref=str(lst.id),
                price=lst.price,
                currency="USD",
                source="BADATA",
                prop=Property(surface_covered=sup, rooms=feat.rooms),
            )
        )
        todos.append(
            Case(
                ref=str(lst.id),
                neighborhood_id=nid,
                neighborhood=barrio,
                actual_price=lst.price,
                surface=sup,
                rooms=feat.rooms,
            )
        )

    rnd = random.Random(seed)  # noqa: S311  reproducibilidad, no criptografía
    rnd.shuffle(todos)
    return todos[:sample], pool


def _prop_de_features(
    sup: Decimal, feat: ListingFeatures | None, *, con_features: bool
) -> Property:
    """La propiedad que ve el motor. Con `con_features=False` se degrada a
    superficie+ambientes: es EXACTAMENTE la diferencia que el par de datasets
    VIGENTES / VIGENTES_SIN_FEATURES existe para medir."""
    if feat is None or not con_features:
        return Property(surface_covered=sup, rooms=getattr(feat, "rooms", None))
    return Property(
        surface_covered=sup,
        rooms=feat.rooms,
        age_years=feat.age_years,
        floor_number=feat.floor_number,
        has_elevator=feat.has_elevator,
        condition=Condition(feat.condition) if feat.condition else None,
        orientation=Orientation(feat.orientation) if feat.orientation else None,
        parking_spaces=feat.parking_spaces or 0,
    )


async def load_cases_vigentes(
    session: AsyncSession,
    *,
    sample: int,
    seed: int = 42,
    con_features: bool = True,
    min_per_neighborhood: int = 40,
) -> tuple[list[Case], dict[Any, list[Comparable]]]:
    """El corpus VIGENTE como dataset de backtest (14/08, tarde).

    Es la medición que ESTADO §5.1 #5 pedía y BA Data no podía dar: BA Data no
    trae estado, antigüedad, orientación ni piso, así que el backtest histórico
    mide el motor de ajustes APAGADO. Acá los comparables llevan las features
    del nodo 4 (`scripts/extraer_corpus.py`), y correr el par
    VIGENTES / VIGENTES_SIN_FEATURES aísla cuánto aporta el motor.

    Dos decisiones contra la fuga de datos del leave-one-out:

      - **Solo canónicos de cluster.** Si el mismo inmueble publicado dos veces
        entra como caso Y como comparable, el sistema "predice" copiándose a sí
        mismo y el MdAPE da artificialmente bajo. Los pares de la zona gris que
        el juez no resolvió siguen siendo una fuga posible — está declarado, no
        escondido.
      - El precio real del caso es el PRECIO DE PUBLICACIÓN, no de cierre. Este
        dataset mide consistencia con el mercado publicado, que es más blando
        que el error contra cierres; se compara solo contra sí mismo.
    """
    from sqlalchemy import or_

    filas = (
        await session.execute(
            select(Listing, ListingFeatures)
            .outerjoin(ListingFeatures, ListingFeatures.listing_id == Listing.id)
            .outerjoin(ListingCluster, ListingCluster.id == Listing.cluster_id)
            .where(
                Listing.active.is_(True),
                Listing.operation == "SALE",
                Listing.currency == "USD",
                Listing.price.is_not(None),
                Listing.source.in_(("PORTAL_A", "PORTAL_B")),
                Listing.neighborhood_id.is_not(None),
                or_(
                    Listing.cluster_id.is_(None),
                    ListingCluster.canonical_id.is_(None),
                    ListingCluster.canonical_id == Listing.id,
                ),
            )
        )
    ).all()

    nombres: dict[Any, str] = dict(
        (await session.execute(select(Neighborhood.id, Neighborhood.name))).all()  # type: ignore[arg-type]
    )

    pool: dict[Any, list[Comparable]] = {}
    todos: list[Case] = []
    for lst, feat in filas:
        sup: Decimal | None = None
        if feat is not None:
            sup = feat.surface_covered or feat.surface_total
        if not sup:
            crudo = (lst.raw or {}).get("surface_weighted")
            sup = Decimal(str(crudo)) if crudo else None
        if not sup or sup <= 0 or not lst.price:
            continue

        nid = lst.neighborhood_id
        pool.setdefault(nid, []).append(
            Comparable(
                ref=str(lst.id),
                price=lst.price,
                currency="USD",
                source=lst.source,
                prop=_prop_de_features(sup, feat, con_features=con_features),
            )
        )
        todos.append(
            Case(
                ref=str(lst.id),
                neighborhood_id=nid,
                neighborhood=nombres.get(nid, "?"),
                actual_price=lst.price,
                surface=sup,
                rooms=feat.rooms if feat else None,
            )
        )

    # Solo barrios con masa: medir sobre 6 avisos habla del tamaño de la
    # muestra, no del método.
    validos = {nid for nid, comps in pool.items() if len(comps) >= min_per_neighborhood}
    todos = [c for c in todos if c.neighborhood_id in validos]
    pool = {nid: comps for nid, comps in pool.items() if nid in validos}

    rnd = random.Random(seed)  # noqa: S311  reproducibilidad, no criptografía
    rnd.shuffle(todos)
    return todos[:sample], pool


def _baseline(pool: list[Comparable], case: Case) -> Decimal | None:
    """`MEDIAN_COMPARABLES_RAW`: mediana cruda de USD/m² del barrio, sin curar,
    sin ajustar, sin filtrar por similitud. Una consulta SQL.

    Todo el pipeline existe para agregar valor sobre esto. Si no le gana, el
    pipeline no se justifica.
    """
    valores = [
        c.raw_price_per_m2 for c in pool if c.ref != case.ref and c.raw_price_per_m2 is not None
    ]
    if len(valores) < 5:
        return None
    return _median(valores) * case.surface


def _select_comparables(pool: list[Comparable], case: Case, k: int = 25) -> list[Comparable]:
    """Selección por similitud de superficie: el filtro estructurado que hace
    el nodo 2 en producción, reducido a lo que este dataset permite (no trae
    estado, antigüedad ni orientación)."""
    cands = [
        c
        for c in pool
        if c.ref != case.ref
        and c.prop.surface_weighted
        and case.surface * D("0.7") <= c.prop.surface_weighted <= case.surface * D("1.3")
        and (
            case.rooms is None or c.prop.rooms is None or abs((c.prop.rooms or 0) - case.rooms) <= 1
        )
    ]
    cands.sort(key=lambda c: abs((c.prop.surface_weighted or D(0)) - case.surface))
    return cands[:k]


async def run_backtest(
    session: AsyncSession, *, dataset: str = "BADATA_2020", sample: int = 500, seed: int = 42
) -> BacktestResult:
    if dataset.startswith("VIGENTES"):
        cases, pool = await load_cases_vigentes(
            session,
            sample=sample,
            seed=seed,
            con_features=dataset != "VIGENTES_SIN_FEATURES",
        )
    else:
        cases, pool = await load_cases(session, sample=sample, seed=seed)
    res = BacktestResult(dataset=dataset, method_version="", n_cases=len(cases))
    log.info("backtest: casos cargados", n=len(cases), barrios=len(pool))

    for case in cases:
        barrio_pool = pool.get(case.neighborhood_id, [])
        base = _baseline(barrio_pool, case)
        comps = _select_comparables(barrio_pool, case)

        subject = Property(surface_covered=case.surface, rooms=case.rooms)
        v = value(subject, comps)
        if not res.method_version:
            res.method_version = v.method_version

        item = CaseResult(
            ref=case.ref,
            neighborhood=case.neighborhood,
            actual=case.actual_price,
            predicted=v.value_mid,
            baseline=base,
            comparables_used=v.comparables_used,
            confidence=v.confidence.value if v.confidence else None,
        )

        if not v.ok or v.value_mid is None:
            item.status = "INSUFFICIENT_DATA"
        else:
            item.ape = abs(v.value_mid - case.actual_price) / case.actual_price
            item.in_range = bool(
                v.value_low is not None
                and v.value_high is not None
                and v.value_low <= case.actual_price <= v.value_high
            )
            res.n_evaluated += 1
        if base is not None:
            item.baseline_ape = abs(base - case.actual_price) / case.actual_price

        res.items.append(item)

    _compute_metrics(res)
    return res


def _compute_metrics(res: BacktestResult) -> None:
    ok = [i for i in res.items if i.status == "OK" and i.ape is not None]
    if not res.items:
        return

    res.coverage = (D(res.n_evaluated) / D(len(res.items))).quantize(D("0.0001"))
    if not ok:
        return

    apes = [i.ape for i in ok if i.ape is not None]
    res.mdape = _median(apes).quantize(D("0.0001"))
    res.mape = (sum(apes, D(0)) / len(apes)).quantize(D("0.0001"))
    res.ppe10 = (D(sum(1 for a in apes if a < D("0.10"))) / len(apes)).quantize(D("0.0001"))
    res.ppe20 = (D(sum(1 for a in apes if a < D("0.20"))) / len(apes)).quantize(D("0.0001"))
    res.hit_rate = (D(sum(1 for i in ok if i.in_range)) / len(ok)).quantize(D("0.0001"))

    # Sesgo CON SIGNO: detecta si el sistema tasa sistemáticamente alto o bajo,
    # cosa que el error absoluto esconde.
    señalado = [(i.predicted - i.actual) / i.actual for i in ok if i.predicted is not None]
    res.bias = _median(señalado).quantize(D("0.0001"))

    # Baseline sobre LOS MISMOS casos evaluados: comparar peras con peras.
    base_apes = [i.baseline_ape for i in ok if i.baseline_ape is not None]
    if base_apes:
        res.baseline_mdape = _median(base_apes).quantize(D("0.0001"))
        res.baseline_ppe20 = (
            D(sum(1 for a in base_apes if a < D("0.20"))) / len(base_apes)
        ).quantize(D("0.0001"))

    # Segmentaciones: el número global miente (doc 09 §4.2).
    por_barrio: dict[str, list[Decimal]] = {}
    por_conf: dict[str, list[Decimal]] = {}
    for i in ok:
        if i.ape is None:
            continue
        por_barrio.setdefault(i.neighborhood, []).append(i.ape)
        if i.confidence:
            por_conf.setdefault(i.confidence, []).append(i.ape)

    res.por_barrio = {
        k: _median(v).quantize(D("0.0001")) for k, v in por_barrio.items() if len(v) >= 5
    }
    res.por_confianza = {k: _median(v).quantize(D("0.0001")) for k, v in por_conf.items()}


def render(res: BacktestResult) -> str:
    def pct(v: Decimal | None) -> str:
        return f"{v * 100:.1f}%" if v is not None else "—"

    lines = [
        "",
        "═" * 66,
        f"BACKTEST — {res.dataset}",
        f"método {res.method_version}   ·   {res.n_cases} casos, {res.n_evaluated} evaluados",
        "═" * 66,
        "",
        f"{'':<14}{'SISTEMA':>12}{'BASELINE':>12}",
        "─" * 42,
        f"{'MdAPE':<14}{pct(res.mdape):>12}{pct(res.baseline_mdape):>12}",
        f"{'PPE20':<14}{pct(res.ppe20):>12}{pct(res.baseline_ppe20):>12}",
        f"{'PPE10':<14}{pct(res.ppe10):>12}{'—':>12}",
        f"{'MAPE':<14}{pct(res.mape):>12}{'—':>12}",
        f"{'Cobertura':<14}{pct(res.coverage):>12}{'100.0%':>12}",
        f"{'Hit rate':<14}{pct(res.hit_rate):>12}{'—':>12}",
        f"{'Sesgo':<14}{pct(res.bias):>12}{'—':>12}",
        "─" * 42,
    ]

    if res.mdape is not None and res.baseline_mdape is not None:
        mejora = (res.baseline_mdape - res.mdape) / res.baseline_mdape * 100
        if res.beats_baseline:
            lines.append(f"✅ Le gana al baseline por {mejora:.1f}%")
        else:
            lines.append(f"🔴 NO le gana al baseline ({mejora:+.1f}%)")
            lines.append("   Ver doc 09 §7: es un hallazgo válido, se documenta y se")
            lines.append("   simplifica el sistema a lo que sí aporta.")

    if res.por_confianza:
        lines += ["", "CALIBRACIÓN DE LA CONFIANZA (MdAPE por nivel declarado)"]
        for nivel in ("ALTA", "MEDIA", "BAJA"):
            if nivel in res.por_confianza:
                lines.append(f"  {nivel:<8}{pct(res.por_confianza[nivel]):>10}")
        niveles = [res.por_confianza.get(n) for n in ("ALTA", "MEDIA", "BAJA")]
        presentes = [n for n in niveles if n is not None]
        if len(presentes) >= 2 and presentes != sorted(presentes):
            lines.append("  ⚠️  La confianza NO está calibrada: los ALTA deberían")
            lines.append("      tener menos error que los BAJA. Si miente, es peor")
            lines.append("      que no tenerla (doc 09 §4.2).")

    if res.por_barrio:
        peores = sorted(res.por_barrio.items(), key=lambda kv: -kv[1])[:5]
        mejores = sorted(res.por_barrio.items(), key=lambda kv: kv[1])[:5]
        lines += ["", "POR BARRIO — mejores / peores"]
        lines += [f"  ✔ {k:<22}{pct(v):>8}" for k, v in mejores]
        lines += [f"  ✘ {k:<22}{pct(v):>8}" for k, v in peores]

    lines += ["", "CRITERIO DE ÉXITO (doc 00 §5)"]
    checks = [
        ("Le gana al baseline", res.beats_baseline),
        ("MdAPE ≤ 15%", res.mdape is not None and res.mdape <= D("0.15")),
        ("PPE20 ≥ 65%", res.ppe20 is not None and res.ppe20 >= D("0.65")),
        ("Cobertura ≥ 70%", res.coverage is not None and res.coverage >= D("0.70")),
        ("|Sesgo| < 3%", res.bias is not None and abs(res.bias) < D("0.03")),
    ]
    for nombre, ok_ in checks:
        lines.append(f"  {'✅' if ok_ else '❌'} {nombre}")

    return "\n".join(lines)
