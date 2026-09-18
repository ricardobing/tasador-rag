"""La ingesta de los CSV del scraper — `tasador.ingest.csv_scan`.

Lo que se prueba acá es la traducción CSV -> tarjeta, que es donde vive todo el
riesgo: un CSV no tiene tipos y todo llega como texto. Lo demás —descarte
temprano, upsert, snapshot de precio— es el MISMO código que la captura por
HTML y ya tiene sus tests; volver a probarlo acá sería probar dos veces lo
mismo y, peor, invitaría a que mañana sean dos implementaciones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tasador.ingest.core import CAMPOS_DEL_HASH, CAMPOS_DEL_RAW, _content_hash, _raw
from tasador.ingest.csv_scan import _portal_de, fila_a_card, motivo_descarte, sha256_de

FILA = {
    "portal": "portal_a",
    "portal_property_id": "18799423",
    "url": "https://portal-a.example/depto--18799423",
    "canonical_url": "https://portal-a.example/depto--18799423",
    "title": "Piso alto con vista",
    "price": "240000",
    "currency": "USD",
    "address": "Thames 1800",
    "neighborhood": "Palermo",
    "covered_area": "78.0",
    "total_area": "",
    "rooms": "3",
    "age": "17",
    "description": "Impecable, contrafrente, piso 4 con ascensor.",
    "expenses": "180000",
    "expenses_currency": "ARS",
    "raw_data": '{"card": {"href": "/depto--18799423"}}',
}


def test_una_fila_completa_se_convierte_en_una_tarjeta_usable():
    card = fila_a_card(FILA)
    assert card is not None
    assert card.price == Decimal("240000")
    assert card.rooms == 3
    assert card.age_years == 17
    assert card.surface_weighted == Decimal("78.0")
    assert motivo_descarte(card) is None


def test_un_campo_vacio_es_none_y_no_cero():
    """El peor default posible sería 0: un precio de 0 pasa los CHECK de la
    base y arrastra la mediana del barrio hacia abajo sin que nada se queje."""
    card = fila_a_card({**FILA, "price": "", "rooms": "", "age": "  "})
    assert card is not None
    assert card.price is None
    assert card.rooms is None
    assert card.age_years is None
    assert motivo_descarte(card) == "sin_precio"


def test_una_fila_sin_id_o_sin_url_no_produce_tarjeta():
    assert fila_a_card({**FILA, "portal_property_id": ""}) is None
    assert fila_a_card({**FILA, "url": "", "canonical_url": ""}) is None


def test_el_descarte_temprano_es_el_del_nucleo_de_la_ingesta():
    """No se reimplementa: se importa. Dos caminos de ingesta con dos reglas de
    descarte distintas ensucian el corpus sin avisar."""
    from tasador.ingest.core import motivo_descarte as original

    assert motivo_descarte is original


def test_un_aviso_en_pesos_se_descarta():
    card = fila_a_card({**FILA, "currency": "ARS"})
    assert card is not None
    assert motivo_descarte(card) == "precio_no_usd"


def test_un_usd_m2_implausible_se_descarta():
    """USD 240.000 en 18 m² son 13.333 USD/m²: fuera de rango."""
    card = fila_a_card({**FILA, "covered_area": "18"})
    assert card is not None
    assert motivo_descarte(card) == "usd_m2_fuera_de_rango"


def test_el_raw_data_ilegible_no_rompe_la_fila():
    """Un JSON roto del scraper no puede costar el aviso entero: se guarda un
    recorte y se sigue."""
    card = fila_a_card({**FILA, "raw_data": "{no es json"})
    assert card is not None
    assert "raw_data_no_parseable" in card.raw_attrs


def test_el_portal_sale_de_la_columna_y_el_nombre_del_archivo_es_el_respaldo(monkeypatch):
    """El mapeo nombre→rótulo NO vive en el código: lo declara quien opera la
    instancia, por entorno. El producto solo conoce los rótulos neutros."""
    from tasador.settings import get_settings

    monkeypatch.setattr(get_settings(), "portales", "portal_a=PORTAL_A, portal_b=PORTAL_B")
    archivo = Path("portal_b_venta_departamento_palermo_2026-08-14.csv")
    assert _portal_de(FILA, archivo) == "PORTAL_A"
    assert _portal_de({**FILA, "portal": ""}, archivo) == "PORTAL_B"
    assert _portal_de({**FILA, "portal": ""}, Path("otra_cosa.csv")) is None


def test_sin_mapeo_de_portales_solo_se_reconocen_los_datasets_publicos(monkeypatch):
    from tasador.settings import get_settings

    monkeypatch.setattr(get_settings(), "portales", "")
    assert _portal_de(FILA, Path("x.csv")) is None
    assert _portal_de({**FILA, "portal": "properati"}, Path("x.csv")) == "PROPERATI"


def test_el_sha256_identifica_el_contenido_y_no_el_nombre(tmp_path: Path):
    """La idempotencia es por HASH: el scraper puede reescribir el mismo nombre
    con contenido nuevo, o dejar dos nombres con el mismo contenido."""
    a = tmp_path / "uno.csv"
    b = tmp_path / "dos.csv"
    a.write_text("portal,price\nportal_a,1\n", encoding="utf-8")
    b.write_text("portal,price\nportal_a,1\n", encoding="utf-8")
    assert sha256_de(a) == sha256_de(b)
    b.write_text("portal,price\nportal_a,2\n", encoding="utf-8")
    assert sha256_de(a) != sha256_de(b)


# ── El escaneo: recursivo, multi-formato, y sabe qué NO es suyo ──────────
def test_los_tres_formatos_producen_la_misma_fila(tmp_path: Path):
    """El scraper emite csv, json y jsonl con el MISMO esquema (verificado: las
    47 claves coinciden). Un `null` de JSON y un `""` de CSV tienen que llegar
    iguales a `fila_a_card`, o el mismo aviso daría dos resultados según el
    envase con que llegó."""
    import json

    from tasador.ingest.csv_scan import leer_filas

    (tmp_path / "a.csv").write_text(
        "portal_property_id,url,price,rooms\n1,https://x/1,240000,\n", encoding="utf-8"
    )
    (tmp_path / "b.json").write_text(
        json.dumps(
            [{"portal_property_id": "1", "url": "https://x/1", "price": 240000, "rooms": None}]
        ),
        encoding="utf-8",
    )
    (tmp_path / "c.jsonl").write_text(
        json.dumps(
            {"portal_property_id": "1", "url": "https://x/1", "price": 240000, "rooms": None}
        )
        + "\n",
        encoding="utf-8",
    )

    a, b, c = (leer_filas(tmp_path / n)[0] for n in ("a.csv", "b.json", "c.jsonl"))
    assert a == b == c
    assert a["rooms"] == "", "un null de JSON tiene que llegar como el vacío de un CSV"


def test_una_tanda_en_tres_formatos_se_ingiere_una_sola_vez(tmp_path: Path):
    """Mismo dato en tres envases: tres `IngestRun` para llegar a "sin cambios"
    ensucian la traza de `/admin/fuentes`, que es de donde sale la salud de la
    ingesta."""
    from tasador.ingest.csv_scan import _archivos_a_ingerir

    for ext in ("csv", "json", "jsonl"):
        (tmp_path / f"portal_a_palermo.{ext}").write_text("[]", encoding="utf-8")

    elegidos = _archivos_a_ingerir(tmp_path)
    assert len(elegidos) == 1
    assert elegidos[0].suffix == ".csv", "el csv gana por orden de preferencia"


def test_el_escaneo_entra_a_las_subcarpetas(tmp_path: Path):
    """El scraper dejó de usar una sola carpeta: ahora crea una por corrida
    (`output/`, `output_zp_t1/`). Con un glob plano, una tanda nueva en una
    carpeta nueva no se ingería y el resumen decía "0 archivos nuevos" — cierto
    para la carpeta que estaba mirando, y engañoso."""
    from tasador.ingest.csv_scan import _archivos_a_ingerir

    (tmp_path / "output").mkdir()
    (tmp_path / "output_zp_t1").mkdir()
    (tmp_path / "output" / "a.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "output_zp_t1" / "b.csv").write_text("x\n", encoding="utf-8")

    assert {p.name for p in _archivos_a_ingerir(tmp_path)} == {"a.csv", "b.csv"}


def test_el_registro_de_errores_del_scraper_no_se_ingiere(tmp_path: Path):
    """`_failures.json` tiene otro esquema: entraría como 8 filas en
    PARSE_ERROR, o sea un problema nuestro que no lo es."""
    from tasador.ingest.csv_scan import _archivos_a_ingerir

    (tmp_path / "portal_a_palermo.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "portal_a_palermo_failures.json").write_text("[]", encoding="utf-8")

    assert [p.name for p in _archivos_a_ingerir(tmp_path)] == ["portal_a_palermo.csv"]


def test_un_archivo_ajeno_se_reconoce_por_su_contenido():
    """Por CONTENIDO y no por la ruta: el scraper ya cambió su estructura de
    carpetas una vez y va a volver a cambiarla; las claves del archivo, no.

    El caso real: los `metadata.json` de los perfiles de Chrome del scraper,
    que el escaneo recursivo empezó a encontrar.
    """
    from tasador.ingest.csv_scan import parece_de_avisos

    assert parece_de_avisos([{"portal_property_id": "1", "url": "https://x/1"}])
    assert parece_de_avisos([{"portal_property_id": "1", "canonical_url": "https://x/1"}])
    assert not parece_de_avisos([{"content_version": "1", "name": "crx_cache"}])
    assert not parece_de_avisos([])


def test_las_cocheras_del_portal_se_guardan_donde_el_nodo_2_las_lee():
    """`parking` de la tarjeta -> `parking_spaces` en `raw`.

    El dato estaba en la base, completo, con el nombre de la tarjeta. El nodo
    2 hace `de_crudo("parking_spaces")` y no lo encontraba: un renombre de una
    palabra que apagó un coeficiente entero.
    """
    card = fila_a_card({**FILA, "parking_count": "2"})
    assert card is not None
    assert card.parking == 2
    assert _raw(card)["parking_spaces"] == "2"
    assert "parking" not in _raw(card), "una sola clave, o quedan dos verdades"


def test_la_orientacion_declarada_por_el_portal_llega_normalizada():
    """El scraper la publica en la columna `condition` —no es un typo suyo ni
    nuestro: Portal B rotula "Frente/Contrafrente" ahí— y en mayúscula. El
    motor espera el vocabulario de doc 05 en minúscula: `_enum(Orientation,
    "Frente")` devuelve None, así que sin normalizar el dato entra y no ajusta.
    """
    card = fila_a_card({**FILA, "condition": "Frente"})
    assert card is not None
    assert card.orientation == "frente"
    assert _raw(card)["orientation"] == "frente"


def test_el_punto_cardinal_no_es_una_orientacion():
    """La columna `orientation` del scraper trae "E", "NO": no está en el
    vocabulario de doc 05 y no mueve ningún coeficiente. Aceptarlo llenaría el
    campo con un valor que el motor descarta igual, y taparía el hueco real."""
    card = fila_a_card({**FILA, "orientation": "E"})
    assert card is not None
    assert card.orientation is None


def test_se_leen_las_dos_columnas_por_si_el_scraper_corrige_el_nombre():
    card = fila_a_card({**FILA, "orientation": "Contrafrente"})
    assert card is not None
    assert card.orientation == "contrafrente"


def test_el_valor_que_llega_al_motor_es_el_del_enum():
    """El puente completo: fila -> tarjeta -> raw -> Property. Si algún día se
    renombra una clave, esto falla acá y no en un informe."""
    from tasador.agents.nodes.valuation import _prop
    from tasador.valuation.models import Orientation

    card = fila_a_card({**FILA, "condition": "Contrafrente", "parking_count": "1"})
    assert card is not None

    prop = _prop(dict(_raw(card)))
    assert prop.orientation is Orientation.CONTRAFRENTE
    assert prop.parking_spaces == 1
    assert prop.age_years == 17


def test_el_hash_cubre_todo_lo_que_se_persiste_y_mueve_el_precio():
    """El gate del `content_hash`.

    `_upsert` no escribe si el hash coincide. Un campo que se persiste y NO
    entra en el hash queda congelado con su primer valor: el portal lo cambia,
    nosotros no nos enteramos. Y arreglar un mapeo no rellena nada, porque
    reingerir se saltea las filas una por una. Las dos cosas pasaron el 14/08.
    """
    base = fila_a_card(FILA)
    assert base is not None
    original = _content_hash(base)

    for columna, valor in (
        ("parking_count", "2"),
        ("condition", "Contrafrente"),
        ("age", "40"),
        ("bathrooms", "3"),
        ("bedrooms", "4"),
        ("expenses", "999999"),
        ("price", "300000"),
        ("covered_area", "95"),
        ("rooms", "5"),
    ):
        otra = fila_a_card({**FILA, columna: valor})
        assert otra is not None
        assert _content_hash(otra) != original, (
            f"cambiar '{columna}' tiene que cambiar el hash, o el dato nuevo nunca se escribe"
        )


def test_un_retoque_de_redaccion_no_cambia_el_hash():
    """La otra mitad de la regla, y es igual de importante: si la descripción
    entrara al hash, cada corrección de estilo del anunciante dispararía una
    extracción por LLM que se paga y no cambia ningún número."""
    base = fila_a_card(FILA)
    otra = fila_a_card({**FILA, "description": "Impecable. Contrafrente. Piso 4, con ascensor."})
    assert base is not None and otra is not None
    assert _content_hash(base) == _content_hash(otra)


def test_todo_campo_del_raw_tiene_un_nombre_declarado():
    """`CAMPOS_DEL_RAW` es el contrato con el nodo 2, escrito en un solo lugar
    para poder leerlo de un vistazo cuando algo no ajusta."""
    card = fila_a_card({**FILA, "parking_count": "1", "condition": "Frente"})
    assert card is not None
    declarados = set(CAMPOS_DEL_RAW.values()) | {"surface_weighted", "attrs"}
    assert set(_raw(card)) <= declarados


# ── El contrato del otro lado: la tarjeta ────────────────────────────────
def test_toda_tarjeta_cumple_el_contrato_del_raw():
    """`_raw` y `_content_hash` resuelven con `getattr(card, attr, None)`.

    Un atributo que la tarjeta no tenga **no falla**: no se guarda y no entra al
    hash, en silencio. `Card` no tenía `age_years` ni `orientation`, y
    las dos están en `CAMPOS_DEL_HASH`: para un aviso de ese portal, el hash de
    contenido no cubría antigüedad ni orientación — el portal las cambiaba y
    nosotros leíamos "sin cambios".

    Es el bug que se arregló el 14/08 ("el content_hash tiene que cubrir todo lo
    que se persiste"), con el arreglo aplicado a una sola de las dos clases.
    """
    from tasador.ingest.cards import Card
    from tasador.ingest.core import campos_del_contrato

    faltan: dict[str, list[str]] = {}
    for clase in (Card,):
        atributos = set(getattr(clase, "__dataclass_fields__", {})) | {
            n for n in dir(clase) if isinstance(getattr(clase, n, None), property)
        }
        ausentes = sorted(a for a in campos_del_contrato() if a not in atributos)
        if ausentes:
            faltan[clase.__name__] = ausentes
    assert not faltan, (
        f"estas tarjetas no exponen atributos del contrato y se guardan/hashean "
        f"como ausentes, en silencio: {faltan}"
    )


def test_la_fecha_de_publicacion_llega_a_la_tarjeta():
    """Iba solo a `raw.attrs` y apagaba TRES mecanismos: el coeficiente de
    antigüedad del aviso (doc 05 §4.1), la frescura del score de confianza y la
    regla `aviso_vencido` del nodo 6."""
    card = fila_a_card({**FILA, "publication_date": "2026-07-07"})
    assert card is not None
    assert card.published_at == datetime(2026, 7, 7, tzinfo=UTC).date()
    assert "published_at" in CAMPOS_DEL_HASH, "un cambio de fecha tiene que mover el hash"


def test_una_fecha_ilegible_o_futura_es_sin_dato():
    """Una fecha futura daría `days_published` negativo, que cae en la primera
    banda de `listing_age_coef` y rompe `f_freshness`."""
    manana = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    for valor in ("", "ayer", "0000-00-00", manana):
        card = fila_a_card({**FILA, "publication_date": valor})
        assert card is not None
        assert card.published_at is None, valor


def test_cambiar_la_fecha_cambia_el_content_hash():
    a = fila_a_card({**FILA, "publication_date": "2026-07-07"})
    b = fila_a_card({**FILA, "publication_date": "2026-05-01"})
    assert a is not None and b is not None
    assert _content_hash(a) != _content_hash(b)


# ── El rótulo de barrio, que cada portal escribe distinto ────────────────
def test_el_barrio_se_reconoce_en_los_dos_formatos_de_rotulo():
    """Los dos portales rotulan el barrio de forma incompatible.

    Portal B:  "Belgrano, Capital Federal"
    Portal A: "Departamento en Venta en Palermo, Capital Federal"

    El segundo no tiene NINGUNA parte que sea un barrio: una es la operación y
    la otra la ciudad. Hasta el 15/08 devolvía None y el aviso entraba sin
    `neighborhood_id`; el nodo 2 filtra por esa columna, así que era un aviso
    que no existía para ningún informe.
    """
    from tasador.ingest.core import _match_neighborhood

    # Las claves van como las deja `_norm_key`: mayúsculas, sin tildes.
    barrios = {"PALERMO": "P", "BELGRANO": "B", "PALERMO CHICO": "P", "VILLA URQUIZA": "VU"}

    assert _match_neighborhood("Belgrano, Capital Federal", barrios) == "B"
    assert _match_neighborhood("Departamento en Venta en Palermo, Capital Federal", barrios) == "P"
    assert _match_neighborhood("Departamento en Venta en Palermo Chico, Palermo", barrios) == "P"
    assert _match_neighborhood("PH en Venta en Villa Urquiza", barrios) == "VU"
    assert _match_neighborhood("Departamento en Venta en Mataderos", barrios) is None
    assert _match_neighborhood(None, barrios) is None


def test_el_sufijo_mas_largo_gana():
    """ "Palermo Chico" y "Palermo" son barrios distintos en la tabla (el
    segundo es el padre del primero). Si ganara el sufijo corto, todo Palermo
    Chico se contaría como Palermo y el `parent_id` no serviría de nada."""
    from tasador.ingest.core import _match_neighborhood

    barrios = {"PALERMO": "P", "PALERMO CHICO": "PCHICO"}
    assert _match_neighborhood("Departamento en Venta en Palermo Chico", barrios) == "PCHICO"


# ── El estado del portal, que se tiraba entero (15/08) ───────────────────
def test_el_estado_declarado_por_el_portal_llega_al_motor():
    """`condition` es el coeficiente MÁS GRANDE del método: de 1,15
    (a estrenar) a 0,82 (a refaccionar), un 40% de amplitud.

    El scraper lo publica en la columna `condition` y de ahí solo se rescataba
    la orientación, así que los 783 estados reales de la tanda enriquecida se
    tiraban. Un aviso entraba al corpus con el estado en la mano y el motor no
    lo veía.
    """
    from tasador.valuation.models import Condition

    for texto, esperado in (
        ("Excelente", "excelente"),
        ("Muy Bueno", "muy_bueno"),
        ("BUENO", "bueno"),
        ("A Refaccionar", "a_refaccionar"),
        ("A Estrenar", "a_estrenar"),
    ):
        card = fila_a_card({**FILA, "condition": texto})
        assert card is not None
        assert card.condition == esperado, texto
        # Y que el valor sea el que el motor sabe leer, no uno parecido.
        assert Condition(card.condition)
        assert _raw(card)["condition"] == esperado


def test_regular_no_se_mapea_y_es_deliberado():
    """ "Regular" cae entre `bueno` (0,94) y `a_refaccionar` (0,82). Elegir cuál
    es inventar un 12% sobre el precio de alguien. Sin dato no hay ajuste."""
    card = fila_a_card({**FILA, "condition": "Regular"})
    assert card is not None
    assert card.condition is None


def test_la_misma_columna_da_estado_y_orientacion_sin_pisarse():
    """El scraper mete dos vocabularios en `condition`. Medido sobre los 936
    avisos con ficha del 15/08: 783 estados, 73 orientaciones y 44 puntos
    cardinales. Cada función se lleva lo suyo y descarta el resto."""
    estado = fila_a_card({**FILA, "condition": "Excelente"})
    orient = fila_a_card({**FILA, "condition": "Contrafrente"})
    cardinal = fila_a_card({**FILA, "condition": "Norte"})
    assert estado is not None and orient is not None and cardinal is not None

    assert (estado.condition, estado.orientation) == ("excelente", None)
    assert (orient.condition, orient.orientation) == (None, "contrafrente")
    # El punto cardinal no es ninguna de las dos cosas.
    assert (cardinal.condition, cardinal.orientation) == (None, None)


def test_cambiar_el_estado_cambia_el_content_hash():
    """Sin esto, reingerir para rellenar el mapeo nuevo no escribiría NADA: el
    upsert corta antes de tocar la fila cuando el hash no cambió. Es el bug
    del 14/08, y el arreglo de hoy lo habría repetido."""
    a = fila_a_card(FILA)
    b = fila_a_card({**FILA, "condition": "Excelente"})
    assert a is not None and b is not None
    assert _content_hash(a) != _content_hash(b)
    assert "condition" in CAMPOS_DEL_HASH


def test_la_ficha_completa_se_marca_para_poder_elegirla():
    """`detail_fetched` separa un aviso que sabe su estado de uno que no
    (93,8% contra 0%). Se persiste como flag para que el nodo 2 pueda decidir."""
    from tasador.ingest.core import FICHA_COMPLETA, _flags

    con = fila_a_card({**FILA, "detail_fetched": "true"})
    sin = fila_a_card({**FILA, "detail_fetched": "false"})
    vacio = fila_a_card({**FILA})
    assert con is not None and sin is not None and vacio is not None

    assert con.ficha_completa is True
    assert sin.ficha_completa is False
    assert vacio.ficha_completa is False

    assert FICHA_COMPLETA in _flags(con)
    assert FICHA_COMPLETA not in _flags(sin)
