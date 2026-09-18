"""Tests del motor de valuación — los 9 de doc 05 §10, más algunos.

Como el motor es determinístico y puro, se testea como cualquier función.
Sin base, sin red, sin LLM. Estos son los tests más importantes del proyecto:
lo que verifican es que el número que se le entrega a un cliente sea correcto.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tasador.valuation.engine import value
from tasador.valuation.models import (
    Comparable,
    Condition,
    Confidence,
    InsufficientReason,
    Orientation,
    Property,
)

D = Decimal


def comp(
    ref: str,
    price: str,
    m2: str,
    *,
    condition: Condition | None = None,
    orientation: Orientation | None = None,
    age: int | None = None,
    floor: int | None = None,
    elevator: bool | None = None,
    days: int | None = 30,
    manual: bool = False,
) -> Comparable:
    return Comparable(
        ref=ref,
        price=D(price),
        currency="USD",
        days_published=days,
        is_manual=manual,
        prop=Property(
            surface_covered=D(m2),
            condition=condition,
            orientation=orientation,
            age_years=age,
            floor_number=floor,
            has_elevator=elevator,
        ),
    )


def base_comps(n: int = 10, usd_m2: int = 2500, m2: int = 70) -> list[Comparable]:
    """Set homogéneo: mismo USD/m² para todos, así cualquier desvío del
    resultado viene de la lógica y no del ruido."""
    return [comp(f"c{i}", str(usd_m2 * m2), str(m2)) for i in range(n)]


SUJETO = Property(surface_covered=D("70"))


# ── 1. Superficie ponderada ──────────────────────────────────────────────


def test_superficie_ponderada() -> None:
    """cubierta + 50% de lo no cubierto (doc 05 §2)."""
    assert Property(surface_covered=D("70")).surface_weighted == D("70")
    # 70 cubiertos + 10 semi -> 75
    assert Property(surface_covered=D("70"), surface_semi=D("10")).surface_weighted == D("75")
    # 70 cubiertos, 90 totales -> 70 + 20/2 = 80
    assert Property(surface_covered=D("70"), surface_total=D("90")).surface_weighted == D("80")
    # solo total: se usa tal cual y se marca
    p = Property(surface_total=D("85"))
    assert p.surface_weighted == D("85")
    assert p.only_total_surface


# ── 2. Los ajustes se topan ──────────────────────────────────────────────


def test_ajustes_topados_descartan_el_comparable() -> None:
    """Un comparable con varios factores adversos supera el tope de ±25% y se
    descarta: ajustar un 40% no es ajustar, es inventar (doc 05 §4.2)."""
    sujeto = Property(surface_covered=D("70"), condition=Condition.MUY_BUENO)
    comps = base_comps(9)
    comps.append(
        comp(
            "extremo",
            "175000",
            "70",
            condition=Condition.A_ESTRENAR,
            orientation=Orientation.FRENTE,
            age=2,
            floor=10,
        )
    )
    v = value(sujeto, comps)
    extremo = next(a for a in v.detail if a.comparable.ref == "extremo")
    assert not extremo.included
    assert extremo.exclusion_reason == "ajuste_excede_el_tope"


# ── 3. Sin dato, sin ajuste ──────────────────────────────────────────────


def test_sin_dato_sin_ajuste() -> None:
    """`condition=None` da coeficiente 1,00. Nunca se imputa un default
    optimista ni pesimista."""
    v = value(SUJETO, base_comps(10))
    incluidos = [a for a in v.detail if a.included]
    for a in incluidos:
        assert a.raw_price_per_m2 == a.adjusted_price_per_m2


def test_ajuste_es_relativo_al_sujeto() -> None:
    """Si sujeto y comparable están en el mismo estado, no hay ajuste — aunque
    ese estado no sea el de referencia."""
    sujeto = Property(surface_covered=D("70"), condition=Condition.A_REFACCIONAR)
    comps = [comp(f"c{i}", "175000", "70", condition=Condition.A_REFACCIONAR) for i in range(8)]
    v = value(sujeto, comps)
    a = next(x for x in v.detail if x.included)
    assert a.raw_price_per_m2 == a.adjusted_price_per_m2

    # Si los comparables están MEJOR que el sujeto, el sujeto vale MENOS que
    # ellos: su precio refleja una propiedad superior y hay que ajustarlo
    # hacia abajo. (La primera versión de este test tenía la desigualdad al
    # revés; el motor estaba bien.)
    comps2 = [comp(f"c{i}", "175000", "70", condition=Condition.MUY_BUENO) for i in range(8)]
    v2 = value(sujeto, comps2)
    assert v2.value_mid is not None and v.value_mid is not None
    assert v2.value_mid < v.value_mid
    # muy_bueno(1.00) / a_refaccionar(0.82) = 1.2195 -> 2500/1.2195 = 2050
    assert v2.price_per_m2 == D("2050")


# ── 4. La mediana resiste el outlier ─────────────────────────────────────


def test_mediana_resiste_outlier() -> None:
    """Inyectar un comparable a un precio absurdo mueve el resultado < 2%.
    Con media aritmética lo movería ~30%."""
    v_limpio = value(SUJETO, base_comps(10))
    comps = base_comps(10)
    comps.append(comp("absurdo", "700000", "70"))  # 10.000 USD/m2
    v_sucio = value(SUJETO, comps)

    assert v_limpio.value_mid is not None and v_sucio.value_mid is not None
    desvio = abs(v_sucio.value_mid - v_limpio.value_mid) / v_limpio.value_mid
    assert desvio < D("0.02"), f"la mediana se movió {desvio:.1%}"


def test_outlier_fuera_de_rango_absoluto_se_descarta() -> None:
    comps = base_comps(10)
    comps.append(comp("imposible", "3500000", "70"))  # 50.000 USD/m2
    v = value(SUJETO, comps)
    a = next(x for x in v.detail if x.comparable.ref == "imposible")
    assert not a.included
    assert a.exclusion_reason == "usd_m2_fuera_de_rango"


# ── 5. Ancho mínimo del rango ────────────────────────────────────────────


def test_rango_ancho_minimo() -> None:
    """Un set perfectamente homogéneo daría rango cero. El piso real es
    `uncertainty_half_width_pct`, no el `min_range_width_pct` de doc 05 §6.

    ⚠️ El assert era `ancho >= 0.079` sobre un ancho real del 40%: no podía
    fallar. El test llevaba el nombre de la regla de doc 05 §6 —"ancho mínimo
    8%, si p25 y p75 quedan más cerca se fuerza a ±4%"— y esa regla NO opera
    nunca: el código toma `max(mid*8%/2, mid*20%)`, así que el ±4% pierde
    siempre contra el ±20%.

    Un gate que pasa igual con `min_range_width_pct: 0` no protege esa regla.
    El invariante real es la IGUALDAD contra el parámetro que sí gobierna
    (H-05): así, bajar `uncertainty_half_width_pct` a 1 hace fallar este test,
    que es lo que un gate tiene que hacer.
    """
    from tasador.valuation.adjustments import load_config

    unc = D(str(load_config()["statistics"]["uncertainty_half_width_pct"])) / 100
    v = value(SUJETO, base_comps(12))
    assert v.value_mid is not None and v.value_low is not None and v.value_high is not None

    ancho = (v.value_high - v.value_low) / v.value_mid
    # Con un set homogéneo el p25-p75 es cero, así que gana la semi-amplitud
    # calibrada: el ancho es exactamente 2 veces ella. Con tolerancia de redondeo,
    # que es lo único que puede separarlos.
    assert abs(ancho - 2 * unc) < D("0.001"), (
        f"ancho {ancho:.4f} contra 2x uncertainty_half_width_pct = {2 * unc}"
    )


def test_el_piso_de_ultimo_recurso_de_doc_05_no_opera_hoy() -> None:
    """`min_range_width_pct` existe y no gana nunca.

    No es un bug: es el piso de último recurso, y sirve solo si algún día
    `uncertainty_half_width_pct` baja de la mitad de él —lo que pasaría si el
    MdAPE mejorara mucho—. Este test deja el hecho escrito para que nadie lo
    lea de doc 05 §6 y crea que el rango de un informe puede ser del 8%.
    """
    from tasador.valuation.adjustments import load_config

    st = load_config()["statistics"]
    piso_doc = D(str(st["min_range_width_pct"])) / 100 / 2
    unc = D(str(st["uncertainty_half_width_pct"])) / 100
    assert unc > piso_doc, (
        "`uncertainty_half_width_pct` bajó del piso de doc 05 §6: ahora sí manda "
        "el mínimo y `test_rango_ancho_minimo` mide otra cosa"
    )


def test_rango_ordenado() -> None:
    v = value(SUJETO, base_comps(10))
    assert v.value_low is not None and v.value_mid is not None and v.value_high is not None
    assert v.value_low <= v.value_mid <= v.value_high


# ── 6. Menos de 5 comparables ────────────────────────────────────────────


def test_menos_de_cinco_no_devuelve_precio() -> None:
    """LA regla dura. Un sistema que sabe decir 'no sé' es lo único que se
    puede poner delante de un cliente (doc 00 §2.2)."""
    v = value(SUJETO, base_comps(4))
    assert not v.ok
    assert v.value_mid is None
    assert v.insufficient_reason is InsufficientReason.POCOS_COMPARABLES
    assert v.comparables_used == 4


def test_sujeto_sin_superficie_no_devuelve_precio() -> None:
    v = value(Property(rooms=3), base_comps(10))
    assert not v.ok
    assert v.insufficient_reason is InsufficientReason.SIN_SUPERFICIE_SUJETO


# ── 7. Reproducibilidad ──────────────────────────────────────────────────


def test_reproducibilidad() -> None:
    """Mismo input -> mismo output, siempre. Sin esto el backtest no
    significa nada y el informe no es auditable."""
    comps = base_comps(11)
    resultados = {
        (
            value(SUJETO, comps).value_mid,
            value(SUJETO, comps).value_low,
            value(SUJETO, comps).price_per_m2,
        )
        for _ in range(30)
    }
    assert len(resultados) == 1


# ── 8. Confianza monótona ────────────────────────────────────────────────


def test_confianza_sube_con_mas_comparables() -> None:
    scores = []
    for n in (6, 9, 15):
        v = value(SUJETO, base_comps(n))
        assert v.confidence_score is not None
        scores.append(v.confidence_score)
    assert scores == sorted(scores), "más comparables nunca debe bajar la confianza"


def test_confianza_baja_con_mas_dispersion() -> None:
    homogeneo = base_comps(12, usd_m2=2500)
    disperso = [comp(f"d{i}", str((1500 + i * 250) * 70), "70") for i in range(12)]
    v1, v2 = value(SUJETO, homogeneo), value(SUJETO, disperso)
    assert v1.confidence_score is not None and v2.confidence_score is not None
    assert v2.confidence_score < v1.confidence_score


def test_confianza_baja_ensancha_el_rango() -> None:
    disperso = [comp(f"d{i}", str((900 + i * 400) * 70), "70", days=200) for i in range(6)]
    v = value(SUJETO, disperso)
    if v.ok and v.confidence is Confidence.BAJA:
        assert any("Rango ampliado" in n for n in v.notes)


def test_mayoria_manual_penaliza_la_confianza() -> None:
    """No porque el dato manual sea peor, sino porque no es verificable por un
    tercero (doc 14 §7)."""
    normales = base_comps(10)
    manuales = [comp(f"m{i}", "175000", "70", manual=True) for i in range(10)]
    v1, v2 = value(SUJETO, normales), value(SUJETO, manuales)
    assert v1.confidence_score is not None and v2.confidence_score is not None
    assert v2.confidence_score < v1.confidence_score


# ── 9. Caso realista ─────────────────────────────────────────────────────


def test_caso_realista_belgrano() -> None:
    """3 ambientes a refaccionar, 4to piso con ascensor, al frente.
    Comparables en mejor estado -> el valor debe quedar por debajo de la
    mediana cruda del set."""
    sujeto = Property(
        surface_covered=D("72"),
        surface_total=D("78.5"),
        rooms=3,
        age_years=25,
        floor_number=4,
        has_elevator=True,
        condition=Condition.A_REFACCIONAR,
        orientation=Orientation.FRENTE,
    )
    comps = [
        comp("c1", "195000", "71", condition=Condition.MUY_BUENO, age=20, days=40),
        comp("c2", "205000", "75", condition=Condition.MUY_BUENO, age=18, days=25),
        comp("c3", "188000", "70", condition=Condition.BUENO, age=30, days=60),
        comp("c4", "215000", "78", condition=Condition.EXCELENTE, age=12, days=15),
        comp("c5", "179000", "68", condition=Condition.BUENO, age=35, days=80),
        comp("c6", "199000", "73", condition=Condition.MUY_BUENO, age=22, days=35),
        comp("c7", "192000", "72", condition=Condition.BUENO, age=28, days=50),
    ]
    v = value(sujeto, comps)

    assert v.ok
    assert v.comparables_used >= 5
    # ponderada = 72 + (78.5-72)/2 = 75.25
    assert v.weighted_surface == D("75.3")

    mediana_cruda = D("2708")  # ~USD/m2 del set sin ajustar
    assert v.price_per_m2 is not None
    assert v.price_per_m2 < mediana_cruda, "a refaccionar debe valer menos que el set"

    # El cierre esperado siempre por debajo del precio de publicación.
    assert v.closing_high is not None and v.value_mid is not None
    assert v.closing_high < v.value_mid


def test_cochera_suma_valor_absoluto() -> None:
    """Una cochera no vale proporcionalmente más en un depto caro (doc 13 Q12)."""
    sin = value(Property(surface_covered=D("70")), base_comps(10))
    con = value(Property(surface_covered=D("70"), parking_spaces=1), base_comps(10))
    assert sin.value_mid is not None and con.value_mid is not None
    assert con.value_mid - sin.value_mid == D("12000")


def test_la_cochera_del_comparable_se_descuenta_antes_del_usd_m2() -> None:
    """Doc 05 §4.1: la cochera es un valor absoluto, no del m².

    Se hacía SOLO del lado del sujeto: al comparable con cochera se le calculaba
    el USD/m² sobre un precio que la incluye, así que su metro quedaba inflado y
    contaminaba la mediana — y después se le sumaba la cochera del sujeto
    encima. Doble conteo.
    """
    sujeto = Property(surface_covered=D("70"))
    sin_cochera = base_comps(10)  # 10 avisos a 2.500 USD/m2
    con_cochera = [
        Comparable(
            ref=f"p{i}",
            price=D("175000"),
            currency="USD",
            days_published=30,
            prop=Property(surface_covered=D("70"), parking_spaces=1),
        )
        for i in range(10)
    ]
    a, b = value(sujeto, sin_cochera), value(sujeto, con_cochera)
    assert a.price_per_m2 == D("2500")
    # (175.000 - 12.000) / 70 = 2.328,57 -> 2.329
    assert b.price_per_m2 == D("2329"), "la cochera del comparable no se descontó"
    assert b.price_per_m2 < a.price_per_m2


def test_el_tope_de_ajuste_incluye_la_antiguedad_del_aviso() -> None:
    """Doc 05 §4.2 regla 1: fuera de [0,75 · 1,25] se descarta.

    `listing_age_coef` se aplicaba DESPUÉS de calcular `capped`, así que el
    coeficiente que de verdad dividía al precio podía caer bajo el piso:
    0,7626 por 0,97 = 0,7397.
    """
    from tasador.valuation.adjustments import compute_adjustments, load_config

    cfg = load_config()
    sujeto = Property(surface_covered=D("70"))
    peor = Property(
        surface_covered=D("70"), condition=Condition.A_REFACCIONAR, orientation=Orientation.INTERNO
    )
    viejo = comp(
        "v",
        "175000",
        "70",
        condition=Condition.A_REFACCIONAR,
        orientation=Orientation.INTERNO,
        days=400,
    )

    total, detalle, capped = compute_adjustments(sujeto, peor, cfg, comparable=viejo)
    assert capped, f"el total real {total} está bajo el piso y no se marcó"
    assert "antiguedad_del_aviso" in detalle, "el ajuste no quedó registrado (doc 05 §4.2 r.3)"


def test_el_total_registrado_es_el_que_divide_al_precio() -> None:
    """Doc 05 §4.2 regla 3: "todo ajuste queda registrado". El `total` del
    detalle tiene que reproducir crudo/ajustado."""
    sujeto = Property(surface_covered=D("70"))
    comps = [
        comp(f"c{i}", "175000", "70", condition=Condition.BUENO, age=40, days=300) for i in range(8)
    ]
    v = value(sujeto, comps)
    for ac in v.detail:
        if not ac.included or ac.raw_price_per_m2 is None or ac.adjusted_price_per_m2 is None:
            continue
        registrado = D(str(ac.adjustments["total"]))
        real = ac.raw_price_per_m2 / ac.adjusted_price_per_m2
        assert abs(real - registrado) < D("0.001"), f"registrado {registrado} vs real {real}"


def test_las_cifras_publicadas_multiplican_entre_si() -> None:
    """El cliente que multiplica lo que ve tiene que llegar a lo que ve.

    Se calculaba con `usd_m2` y la superficie sin redondear, y se publicaban las
    dos redondeadas: hasta USD 30 de diferencia sobre 259.470.
    """
    sujeto = Property(surface_covered=D("72"), surface_total=D("78.5"))  # ponderada 75,25
    v = value(sujeto, base_comps(10, usd_m2=2711, m2=75))
    assert v.price_per_m2 is not None and v.weighted_surface is not None
    # El producto de las dos cifras publicadas, redondeado como se publica el
    # valor: 2.711 x 75,3 = 204.138,3 -> 204.138.
    esperado = (v.price_per_m2 * v.weighted_surface).quantize(D("1"))
    assert v.value_mid == esperado, (
        f"{v.price_per_m2} x {v.weighted_surface} = {esperado}, y el informe dice {v.value_mid}"
    )


def test_el_recorte_por_percentil_es_configurable_y_se_puede_apagar(tmp_path) -> None:
    """El paso vivía en el código, así que doc 05 §5 describía otro método.

    Con `trim_pct: 5` y n=8 saca exactamente 2 —el 25% que doc 05 §5 argumenta
    que no se tira— y con `0` no saca ninguno.
    """
    import yaml

    from tasador.valuation.adjustments import CONFIG_PATH, load_config

    comps = [comp(f"c{i}", str(175000 + i * 2000), "70") for i in range(8)]
    con = value(SUJETO, comps)
    assert sum(1 for a in con.detail if a.exclusion_reason == "recorte_p5_p95") == 2

    datos = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    datos["statistics"]["trim_pct"] = 0
    apagado = tmp_path / "adjustments.yaml"
    apagado.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    load_config.cache_clear()
    try:
        sin = value(SUJETO, comps, config_path=str(apagado))
        assert sin.comparables_used == 8
        assert not any(a.exclusion_reason for a in sin.detail if a.exclusion_reason)
    finally:
        load_config.cache_clear()


def test_metodo_versionado() -> None:
    """Sin versión del método, comparar dos corridas es autoengaño."""
    v = value(SUJETO, base_comps(10))
    assert v.method_version
    assert "+" in v.method_version


@pytest.mark.parametrize("n", [5, 6, 7, 8, 12, 25])
def test_no_explota_con_cualquier_cantidad(n: int) -> None:
    v = value(SUJETO, base_comps(n))
    assert v.ok
    assert v.value_low is not None and v.value_high is not None
    assert v.value_low <= v.value_high


# ── La relación entre el rango de cierre y el de publicación ─────────────
# El crítico adversarial marcó esto como contradicción sobre un informe real:
# "el mínimo de publicación es 239.899 y el mínimo de cierre 240.877; nadie
# publica por debajo de lo que espera cobrar".
#
# Tenía razón en que SE LEE MAL, pero el error estaba en el informe y no en el
# motor: son dos ejes distintos. El cierre responde "si publicás al precio
# sugerido, ¿cuánto cobrás?"; el rango de publicación, "cuánta incertidumbre
# tiene la estimación". Estos tests fijan cuál es el invariante que sí importa,
# para que nadie —incluido yo— lo "arregle" de nuevo.
def _set_disperso(n: int = 10) -> list[Comparable]:
    """Comparables bien dispersos: es cuando el rango se ensancha por
    incertidumbre y aparece la lectura confusa."""
    return [
        Comparable(
            ref=f"c{i}",
            price=D(str(150000 + i * 30000)),
            currency="USD",
            prop=Property(surface_covered=D("75")),
        )
        for i in range(n)
    ]


def test_el_cierre_esperado_siempre_esta_debajo_del_precio_sugerido():
    """EL invariante. Es lo que el cliente compara de verdad."""
    for v in (
        value(Property(surface_covered=D("75")), _set_disperso()),
        value(Property(surface_covered=D("75")), _set_disperso(6), relaxation_steps=4),
    ):
        assert v.ok
        assert v.closing_high is not None and v.value_mid is not None
        assert v.closing_low < v.closing_high < v.value_mid


def test_con_mucha_incertidumbre_el_piso_de_publicacion_puede_quedar_debajo_del_cierre():
    """NO es un bug: es la banda de incertidumbre (±20%) siendo más ancha que
    el descuento de cierre (5-15%). Queda fijado en un test para que se lea
    como decisión y no como descuido — y el informe tiene que presentarlo
    anclado al precio sugerido, nunca como dos rangos comparables."""
    v = value(Property(surface_covered=D("75")), _set_disperso(6), relaxation_steps=4)
    assert v.ok
    assert v.value_low < v.closing_low, (
        "si esto deja de pasar, revisar si cambió `uncertainty_half_width_pct`"
    )


# ── El corpus llega como TEXTO ───────────────────────────────────────────
def test_los_enteros_que_vienen_como_texto_del_corpus_no_rompen_el_nodo_7():
    """`corpus.listings.raw` guarda todo como string, a propósito.

    El nodo 2 hidrata los candidatos desde ahí, y hasta el 14/08 los decimales
    pasaban por `dec()` mientras que `age_years`, `floor_number` y
    `parking_spaces` no pasaban por nada: llegaban como `"17"`.

    Con los 24 avisos de Portal B nunca se vio, porque esas tarjetas no traían
    antigüedad. Con los 288 de Portal A —231 con `age`— el nodo 7 murió con
    `TypeError: '<=' not supported between instances of 'str' and 'int'`, que
    es el peor lugar donde caerse: el único nodo que no puede degradar, después
    de haber pagado la extracción de 60 avisos.

    Se prueba con strings porque es EXACTAMENTE la forma en que el dato llega.
    """
    from tasador.agents.nodes.valuation import _prop

    p = _prop(
        {
            "surface_covered": "78.5",
            "rooms": "3",
            "age_years": "17",
            "floor_number": "4",
            "has_elevator": "True",
            "parking_spaces": "1",
        }
    )
    assert p.age_years == 17
    assert p.floor_number == 4
    assert p.rooms == 3
    assert p.has_elevator is True
    assert p.parking_spaces == 1
    # Y lo que importa: que el motor pueda comparar sin explotar.
    v = value(p, _set_disperso())
    assert v.ok


def test_un_entero_ilegible_es_sin_dato_y_no_un_error():
    """Mismo criterio que `_enum` y que "sin dato, sin ajuste" (doc 05 §4.2):
    un valor que no se puede leer apaga el coeficiente, no rompe el informe."""
    from tasador.agents.nodes.valuation import _prop

    p = _prop({"surface_covered": "78.5", "age_years": "no sé", "floor_number": ""})
    assert p.age_years is None
    assert p.floor_number is None


# ── 7. Las reglas duras que el entorno no puede ablandar (H-09) ──────────
def test_el_minimo_de_comparables_no_se_puede_bajar_desde_el_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doc 05 §8 y doc 00 §2.2 presentan "menos de 5 comparables →
    INSUFFICIENT_DATA" como la regla que hace al sistema presentable ante un
    cliente. `settings.py` la definía como `Field(default=5, ge=3)`: un
    `MIN_COMPARABLES=3` en el `.env` la bajaba y nada avisaba.

    ADR-002 tiene un test que cierra esa misma puerta para el nodo 7. Acá no lo
    tenía, y el motivo por el que importa es el mismo.
    """
    from pydantic import ValidationError

    from tasador.settings import Settings, get_settings

    monkeypatch.setenv("MIN_COMPARABLES", "3")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()
    get_settings.cache_clear()


def test_subir_el_minimo_de_comparables_si_se_puede(monkeypatch: pytest.MonkeyPatch) -> None:
    """El autotest del de arriba: si el `Field` rechazara TODO override, el test
    anterior pasaría sin distinguir "no se puede ablandar" de "no se puede
    tocar". Endurecer la regla no la viola."""
    from tasador.settings import Settings, get_settings

    monkeypatch.setenv("MIN_COMPARABLES", "8")
    get_settings.cache_clear()
    assert Settings().min_comparables == 8
    get_settings.cache_clear()


def test_la_config_del_repo_respeta_la_regla() -> None:
    """Y que el `.env.example` no la baje: es lo que alguien copia."""
    from pathlib import Path

    from tasador.settings import get_settings

    assert get_settings().min_comparables >= 5

    ejemplo = Path(__file__).resolve().parents[1] / ".env.example"
    for linea in ejemplo.read_text(encoding="utf-8").splitlines():
        if linea.strip().startswith("MIN_COMPARABLES="):
            valor = linea.split("=", 1)[1].split("#")[0].strip()
            assert int(valor) >= 5, f".env.example baja la regla dura: {linea}"
