"""Motor de valuación — nodo 7 del pipeline. DETERMINÍSTICO, sin LLM.

ADR-002: el precio nunca sale de un modelo de lenguaje. Sale de una mediana de
USD/m² ajustada con coeficientes explícitos. Eso es lo que hace el resultado
reproducible, auditable y defendible ante el dueño de una propiedad.

Dado el mismo conjunto de comparables, devuelve siempre exactamente lo mismo.
Sin esa propiedad, el backtest no significaría nada.
"""

from __future__ import annotations

import statistics
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tasador.valuation.adjustments import (
    compute_adjustments,
    load_config,
    parking_value,
    umbrales_de_plausibilidad,
)
from tasador.valuation.models import (
    AdjustedComparable,
    Comparable,
    Confidence,
    InsufficientReason,
    Property,
    Valuation,
)

D = Decimal


def _q(v: Decimal, places: str = "1") -> Decimal:
    return v.quantize(D(places), rounding=ROUND_HALF_UP)


def _pct(values: list[Decimal], p: float) -> Decimal:
    """Percentil por interpolación lineal. Se implementa en vez de usar numpy
    para no perder precisión de Decimal: con dinero, los float mienten."""
    if not values:
        raise ValueError("lista vacía")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = D(str(k - lo))
    return s[lo] + (s[hi] - s[lo]) * frac


def _median(values: list[Decimal]) -> Decimal:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _usd_m2_sin_cochera(cfg: dict[str, Any], comp: Comparable) -> Decimal | None:
    """USD/m² del comparable con la cochera DESCONTADA del precio.

    Doc 05 §4.1 trata la cochera como un valor absoluto —"+USD 12.000 al total,
    no al m²"— y el motor lo hacía **solo del lado del sujeto**: al comparable
    con cochera se le calculaba el USD/m² sobre un precio que la incluye, así
    que su metro cuadrado quedaba inflado y contaminaba la mediana. Después se
    le sumaba la cochera del sujeto encima: doble conteo.

    La distorsión por comparable no es chica: 175.000 / 70 m² con cochera son
    2.500 USD/m², y sin ella 2.329 — un 7,3%.

    Medido al arreglarlo, el efecto agregado HOY es cero: solo el 1,1% del
    corpus declara cochera y la mediana es robusta a una minoría. Se arregla
    igual porque el mecanismo crece con la cobertura del dato — Portal B ya la
    trae en el 30%.
    """
    sup = comp.prop.surface_weighted
    if not sup or sup <= 0 or comp.currency != "USD":
        return None
    precio = comp.price - parking_value(cfg, comp.prop.parking_spaces)
    # Un precio que se vuelve negativo o absurdo al descontar la cochera es un
    # dato malo, no un comparable barato: que lo agarre el filtro de rango.
    if precio <= 0:
        return None
    return precio / sup


def value(
    subject: Property,
    comparables: list[Comparable],
    *,
    config_path: str | None = None,
    min_comparables: int = 5,
    relaxation_steps: int = 0,
) -> Valuation:
    """Valúa una propiedad a partir de sus comparables.

    `relaxation_steps` es cuántos filtros de búsqueda hubo que relajar para
    conseguirlos: no cambia el número, pero baja la confianza (doc 04, nodo 2).
    """
    cfg = load_config(config_path)
    stats_cfg = cfg["statistics"]
    v = Valuation(
        comparables_found=len(comparables),
        method_version=f"{cfg['version']}+{cfg['_hash']}",
    )

    sup_sujeto = subject.surface_weighted
    if not sup_sujeto or sup_sujeto <= 0:
        v.insufficient_reason = InsufficientReason.SIN_SUPERFICIE_SUJETO
        return v
    # Un decimal: 75,25 m² se informa como 75,3, no como 75. Redondear la
    # superficie al entero mueve el valor final ~0,3%, que en USD 180.000 son
    # 600 dólares de ruido gratis.
    v.weighted_surface = _q(sup_sujeto, "0.1")

    # ── 1. Ajustar cada comparable ────────────────────────────────────────
    detalle: list[AdjustedComparable] = []
    limpios: list[Decimal] = []

    for comp in comparables:
        ac = AdjustedComparable(comparable=comp, included=False)
        crudo = _usd_m2_sin_cochera(cfg, comp)

        if crudo is None:
            ac.exclusion_reason = "sin_precio_o_superficie_en_usd"
            detalle.append(ac)
            continue

        ac.raw_price_per_m2 = _q(crudo)
        # El MISMO rango que usan la ingesta y el nodo 6, de un solo lugar.
        # Estaba acá como `statistics.outlier_usd_m2_*` y en las otras dos como
        # literales en el código: subir el máximo movía este y no los otros dos,
        # en silencio (H-16).
        u = umbrales_de_plausibilidad()
        if not (u.usd_m2_min <= crudo <= u.usd_m2_max):
            ac.exclusion_reason = "usd_m2_fuera_de_rango"
            detalle.append(ac)
            continue

        # `listing_age_coef` entra ADENTRO de `compute_adjustments`, no después.
        # Estaba afuera, así que no pasaba por el tope de ±25% ni entraba al
        # `total` que se guarda en `report_comparables.adjustments`: doc 05 §4.2
        # regla 1 se podía violar (0,7626 por 0,97 = 0,7397, bajo el piso de 0,75)
        # y la regla 3 —"todo ajuste queda registrado"— era falsa.
        coef, adj_detail, capped = compute_adjustments(subject, comp.prop, cfg, comparable=comp)
        ac.adjustments = adj_detail

        if capped:
            # Demasiado distinto para ajustarlo con honestidad.
            ac.exclusion_reason = "ajuste_excede_el_tope"
            detalle.append(ac)
            continue

        # ⚠️ Se guarda y se OPERA con el valor redondeado, no con el interno.
        #
        # `limpios` llevaba el valor sin redondear y el recorte del paso 2
        # comparaba contra `ac.adjusted_price_per_m2`, que sí está redondeado.
        # Con valores no enteros eso excluye comparables por el redondeo:
        # medido con 10 avisos idénticos a 2.328,57 USD/m², el recorte los sacó
        # a los diez y el informe salió sin valor.
        #
        # Trabajar con las cifras publicadas también es lo que hace que el
        # informe se pueda reconstruir desde `report_comparables`: la mediana es
        # la mediana de lo que la tabla muestra, no de un número que no está en
        # ningún lado.
        ajustado = _q(crudo / coef)
        ac.adjusted_price_per_m2 = ajustado
        ac.included = True
        detalle.append(ac)
        limpios.append(ajustado)

    v.detail = detalle

    # ── 2. Recorte por percentil ──────────────────────────────────────────
    # El parámetro vive en `adjustments.yaml` (`statistics.trim_pct`) y no acá:
    # es una decisión del MÉTODO, entra al `method_version` y por lo tanto un
    # cambio obliga a correr el backtest. Estaba escrito en el código, con lo
    # cual doc 05 §5 describía un método distinto del que corría.
    #
    # Solo con muestra suficiente: con 6 comparables, recortar por percentil
    # descarta casos legítimos.
    trim = float(stats_cfg.get("trim_pct", 0) or 0)
    if trim > 0 and len(limpios) >= 8:
        lo_p, hi_p = _pct(limpios, trim), _pct(limpios, 100 - trim)
        for ac in detalle:
            fuera = (
                ac.included
                and ac.adjusted_price_per_m2 is not None
                and not (lo_p <= ac.adjusted_price_per_m2 <= hi_p)
            )
            if fuera:
                ac.included = False
                # El nombre dice QUÉ regla lo sacó. `outlier_estadistico` se
                # confundía con `usd_m2_fuera_de_rango`, que es el filtro
                # absoluto de [300, 12.000] y es otra cosa.
                ac.exclusion_reason = f"recorte_p{trim:g}_p{100 - trim:g}"
        limpios = [
            ac.adjusted_price_per_m2
            for ac in detalle
            if ac.included and ac.adjusted_price_per_m2 is not None
        ]

    v.comparables_used = len(limpios)

    # ── 3. La regla dura ──────────────────────────────────────────────────
    # Un sistema que sabe decir "no sé" es lo único que se puede poner
    # delante de un cliente (doc 00 §2.2).
    if len(limpios) < min_comparables:
        v.insufficient_reason = InsufficientReason.POCOS_COMPARABLES
        return v

    # ── 4. Estadística robusta ────────────────────────────────────────────
    # Mediana y MAD, no media y desvío: un solo aviso absurdo mueve la media
    # varios puntos y a la mediana no la mueve. En un corpus de avisos
    # cargados a mano, los valores absurdos son el clima, no la excepción.
    mediana = _median(limpios)
    mad = _median([abs(x - mediana) for x in limpios])
    v.dispersion = (mad / mediana).quantize(D("0.0001")) if mediana else None

    # Winsorizar en vez de eliminar: con 8 comparables, sacar los 2 extremos
    # tira el 25% de la muestra. Recortarlos conserva la información de que
    # hay valores altos y bajos sin dejar que dominen.
    wl = _pct(limpios, stats_cfg["winsorize_lower_pct"])
    wu = _pct(limpios, stats_cfg["winsorize_upper_pct"])
    winsor = [min(max(x, wl), wu) for x in limpios]

    usd_m2 = _median(winsor)
    p_lo = _pct(winsor, stats_cfg["range_lower_pct"])
    p_hi = _pct(winsor, stats_cfg["range_upper_pct"])

    # ── 5. Rango ──────────────────────────────────────────────────────────
    # ⚠️ Se multiplica por las cifras que el informe PUBLICA, no por las
    # internas. `price_per_m2` sale redondeado al entero y `weighted_surface` a
    # un decimal, y el valor se calculaba con los dos sin redondear: el cliente
    # que multiplicaba lo que veía no llegaba a lo que veía. Medido: hasta
    # USD 30 de diferencia sobre 259.470 (0,01%), y el crítico no lo marcaba
    # porque su tolerancia era del 1%.
    #
    # Para un producto cuyo argumento es "salió de estos avisos con estos
    # ajustes", la primera cuenta que hace cualquiera —m² por USD/m²— tiene que
    # cerrar.
    usd_m2_pub = _q(usd_m2)
    sup_pub = _q(sup_sujeto, "0.1")
    cocheras = parking_value(cfg, subject.parking_spaces or 0)
    mid = usd_m2_pub * sup_pub + cocheras
    low = _q(p_lo) * sup_pub + cocheras
    high = _q(p_hi) * sup_pub + cocheras

    # El rango debe reflejar la INCERTIDUMBRE DE LA ESTIMACIÓN, no solo la
    # dispersión del mercado. El p25-p75 mide lo segundo; con MdAPE del 15%,
    # un rango de ±4% falla 2 de cada 3 veces (medido: hit rate 36,6%).
    # Se toma el mayor entre ambos criterios.
    if mid > 0:
        min_width = D(str(stats_cfg["min_range_width_pct"])) / 100
        unc = D(str(stats_cfg.get("uncertainty_half_width_pct", 0))) / 100
        half_actual = (high - low) / 2
        half_min = max(mid * min_width / 2, mid * unc)
        if half_actual < half_min:
            low, high = mid - half_min, mid + half_min

    v.price_per_m2 = usd_m2_pub
    v.value_mid, v.value_low, v.value_high = _q(mid), _q(low), _q(high)

    # ── 6. Confianza ──────────────────────────────────────────────────────
    v.confidence_score, v.confidence = _confidence(
        cfg, len(limpios), v.dispersion, comparables, relaxation_steps, subject
    )
    if v.confidence is Confidence.BAJA:
        # Se ensancha el rango: si sabemos menos, decimos menos.
        half = (v.value_high - v.value_low) / 2 * D("1.5")
        v.value_low, v.value_high = _q(v.value_mid - half), _q(v.value_mid + half)
        v.notes.append("Rango ampliado por baja confianza.")

    # ── 7. Rango de cierre ────────────────────────────────────────────────
    # Se ancla en el VALOR MEDIO y no en el rango, y esto es deliberado
    # (doc 05 §6.1): responde "si publicás al precio sugerido, ¿cuánto vas a
    # cobrar?". El rango de publicación responde otra cosa —cuánta
    # incertidumbre tiene la estimación— y son ejes distintos.
    #
    # ⚠️ De acá sale algo que PARECE un error y no lo es: cuando la banda de
    # incertidumbre es más ancha que el descuento de cierre (±20% contra
    # 5-15%), `value_low` queda POR DEBAJO de `closing_low`. El crítico
    # adversarial lo marcó como contradicción el 13/08 —"nadie publica por
    # debajo de lo que espera cobrar"— y tenía razón en que **se lee mal**,
    # pero el error estaba en el informe, no acá: presentaba los dos rangos
    # como si fueran comparables.
    #
    # Se probó anclar el cierre en el rango y se revirtió: rompía el invariante
    # que sí importa —el cierre esperado siempre por debajo del precio
    # sugerido— y lo detectó `test_caso_realista_belgrano`. La corrección vive
    # en `prompts/writer/v1.jinja`.
    cd = cfg["closing_discount"]
    v.closing_low = _q(v.value_mid * D(str(cd["low"])))
    v.closing_high = _q(v.value_mid * D(str(cd["high"])))

    if subject.only_total_surface:
        v.notes.append("El sujeto solo declara superficie total, no cubierta.")

    return v


def _f_count(n: int) -> Decimal:
    if n >= 12:
        return D("1.0")
    if n >= 8:
        return D("0.7")
    return D("0.3")


def _f_dispersion(disp: Decimal | None) -> Decimal:
    if disp is None:
        return D("0.5")
    if disp < D("0.10"):
        return D("1.0")
    if disp >= D("0.25"):
        return D("0.0")
    return (D("0.25") - disp) / D("0.15")


def _f_freshness(comps: list[Comparable]) -> Decimal:
    dias = [c.days_published for c in comps if c.days_published is not None]
    if not dias:
        return D("0.6")
    med = statistics.median(dias)
    if med < 30:
        return D("1.0")
    if med >= 120:
        return D("0.2")
    return D(str(round((120 - med) / 90, 3)))


def _confidence(
    cfg: dict[str, Any],
    n: int,
    dispersion: Decimal | None,
    comps: list[Comparable],
    relaxation: int,
    subject: Property,
) -> tuple[Decimal, Confidence]:
    """No es un adorno: determina si el informe sale con advertencias.

    Si esto miente, es peor que no tenerlo — por eso el backtest verifica la
    calibración (los ALTA deben tener MdAPE menor que los BAJA).
    """
    w = cfg["confidence"]["weights"]
    campos = [subject.condition, subject.orientation, subject.age_years, subject.floor_number]
    completitud = D(str(sum(1 for c in campos if c is not None))) / D(str(len(campos)))

    score = (
        D(str(w["count"])) * _f_count(n)
        + D(str(w["dispersion"])) * _f_dispersion(dispersion)
        + D(str(w["freshness"])) * _f_freshness(comps)
        + D(str(w["relaxation"])) * max(D("0.2"), D("1.0") - D(str(relaxation)) * D("0.2"))
        + D(str(w["completeness"])) * completitud
    )

    # Un set mayoritariamente manual baja un escalón: no porque el dato manual
    # sea peor, sino porque no es verificable por un tercero (doc 14 §7).
    mayoria_manual = comps and sum(1 for c in comps if c.is_manual) / len(comps) > 0.5
    if cfg["confidence"].get("manual_majority_penalty") and mayoria_manual:
        score *= D("0.85")

    score = score.quantize(D("0.001"))
    th = cfg["confidence"]["thresholds"]
    if score >= D(str(th["alta"])):
        return score, Confidence.ALTA
    if score >= D(str(th["media"])):
        return score, Confidence.MEDIA
    return score, Confidence.BAJA
