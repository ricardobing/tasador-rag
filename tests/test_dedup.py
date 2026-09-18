"""Nodo 5: las dos capas determinísticas y la elección del canónico.

Sin red. El caso que más se testea es el que más se confunde en la realidad:
**dos unidades distintas del mismo edificio**. Fusionarlas borra un comparable
legítimo, y en un set chico eso puede dejar el informe sin datos para emitirse.
"""

from __future__ import annotations

from typing import Any

from tasador.agents.config import load_agents_config
from tasador.agents.nodes.dedup import (
    _capa_1,
    _capa_2,
    _zona_gris,
    agrupar_duplicados,
    clave_cuadra,
    clave_direccion,
    elegir_canonico,
    misma_unidad,
    similitud,
    unidad_de,
)
from tasador.agents.state import Candidate

CFG = load_agents_config().node("dedup_cluster")


def c(ref: str, **kw: Any) -> Candidate:
    base: dict[str, Any] = {
        "listing_id": ref,
        "source": "PORTAL_B",
        "price": "250000",
        "currency": "USD",
        "address": "Zabala 1851",
        "surface_total": "93",
        "rooms": 3,
    }
    return {**base, **kw}  # type: ignore[return-value]


# ── Normalización de direcciones ─────────────────────────────────────────
def test_el_al_de_los_portales_no_rompe_la_comparacion():
    """Portal B publica "Cabildo al 2200" para no dar la altura exacta. Es la
    forma más común del corpus y tiene que colisionar con "Cabildo 2200"."""
    assert clave_direccion("Av. Cabildo al 2200") == clave_direccion("AV CABILDO 2200")


def test_ignora_tildes_y_puntuacion():
    assert clave_direccion("Luis María Campos 1.400") == clave_direccion("LUIS MARIA CAMPOS 1400")


def test_direcciones_distintas_no_colisionan():
    assert clave_direccion("Zabala 1851") != clave_direccion("Zabala 1800")


def test_similitud_de_trigramas():
    assert similitud("ZABALA 1851", "ZABALA 1851") == 1.0
    assert similitud("ZABALA 1851", "ZABALA 1800") > 0.5
    assert similitud("ZABALA 1851", "MIGUELETES 2200") < 0.2


# ── Capa 1: exacta ───────────────────────────────────────────────────────
def test_capa_1_agrupa_el_mismo_inmueble():
    assert _capa_1(c("a"), c("b"))


def test_capa_1_no_agrupa_dos_unidades_del_mismo_edificio():
    """El caso borde de doc 04: misma dirección, distinta superficie."""
    assert not _capa_1(c("a", surface_total="93"), c("b", surface_total="120"))


def test_capa_1_no_agrupa_si_cambian_los_ambientes():
    assert not _capa_1(c("a", rooms=3), c("b", rooms=2))


def test_capa_1_tolera_dos_metros_de_diferencia():
    """Los portales redondean distinto la misma unidad."""
    assert _capa_1(c("a", surface_total="93"), c("b", surface_total="94.5"))
    assert not _capa_1(c("a", surface_total="93"), c("b", surface_total="96"))


def test_capa_1_sin_superficie_no_decide():
    """Sin superficie no hay forma de distinguir unidades del mismo edificio."""
    assert not _capa_1(c("a", surface_total=None), c("b"))


# ── Capa 2: fuzzy + precio ───────────────────────────────────────────────
def test_capa_2_agrupa_direcciones_parecidas_con_precio_parecido():
    a = c("a", address="Av. Cabildo al 2200", price="250000")
    b = c("b", address="Av Cabildo 2200", price="255000")
    assert _capa_2(a, b, CFG)


def test_capa_2_no_agrupa_si_el_precio_se_va():
    """Mismo edificio, precios muy distintos: son unidades distintas."""
    a = c("a", address="Av. Cabildo al 2200", price="250000")
    b = c("b", address="Av Cabildo 2200", price="400000")
    assert not _capa_2(a, b, CFG)


# ── Zona gris ────────────────────────────────────────────────────────────
def test_la_zona_gris_deja_pasar_lo_dudoso():
    a = c("a", address="Zabala 1851", surface_total="93")
    b = c("b", address="Zabala al 1800", surface_total="95")
    assert _zona_gris(a, b)


def test_la_zona_gris_no_molesta_al_juez_con_lo_obvio():
    """El juez es caro. Superficies muy distintas se resuelven sin él."""
    a = c("a", address="Zabala 1851", surface_total="93")
    b = c("b", address="Zabala al 1800", surface_total="150")
    assert not _zona_gris(a, b)
    # Y direcciones de otra calle tampoco llegan.
    assert not _zona_gris(c("a", address="Zabala 1851"), c("b", address="Migueletes 2200"))


# ── Agrupamiento: enlace COMPLETO, no transitividad ──────────────────────
#
# ⚠️ Acá había un test que afirmaba lo contrario:
#
#     def test_si_a_es_b_y_b_es_c_los_tres_son_el_mismo():
#         grupos = _componentes(["a","b","c","d"], {("a","b"), ("b","c")})
#         assert len(grupos) == 1 and set(grupos[0]) == {"a","b","c"}
#
# Y estaba mal — el test, no el código de alrededor. La transitividad vale para
# una IDENTIDAD; el criterio de duplicado es una relación con TOLERANCIA (±5% de
# precio, ±2 m²), y encadenar una relación con tolerancia es clustering de
# enlace simple. Medido sobre lo que esa versión escribió en el corpus: un
# cluster de 198 avisos —de 23 a 73 m², de USD 111.000 a 436.900— donde solo el
# 7,6% de los pares cumplía el criterio.
#
# Este proyecto tiene tres antecedentes de "el test tenía razón y el código
# estaba bien". Este es el caso inverso, y por eso el reemplazo va con el número
# que lo justifica al lado.


def test_una_cadena_no_es_un_cluster():
    """A≈B y B≈C no hace que A≈C: es el encadenamiento que borraba comparables."""
    grupos = agrupar_duplicados(["a", "b", "c", "d"], {("a", "b"), ("b", "c")})
    assert all(len(g) == 2 for g in grupos), f"se encadenó: {grupos}"
    assert {tuple(g) for g in grupos} <= {("a", "b"), ("b", "c")}
    # Y `d`, que no se parece a nadie, no entra a ningún lado.
    assert not any("d" in g for g in grupos)


def test_si_todos_se_parecen_entre_si_es_un_solo_cluster():
    """El caso legítimo: el mismo inmueble publicado por tres inmobiliarias."""
    grupos = agrupar_duplicados(["a", "b", "c"], {("a", "b"), ("b", "c"), ("a", "c")})
    assert len(grupos) == 1
    assert set(grupos[0]) == {"a", "b", "c"}


def test_un_aviso_pertenece_a_un_solo_cluster():
    """`listings.cluster_id` es UNA columna: no puede estar en dos grupos."""
    grupos = agrupar_duplicados(["a", "b", "c", "d"], {("a", "b"), ("c", "d"), ("b", "c")})
    vistos = [r for g in grupos for r in g]
    assert len(vistos) == len(set(vistos)), f"un aviso en dos grupos: {grupos}"


def test_el_agrupamiento_es_determinístico():
    """Dos corridas sobre los mismos pares tienen que dar los mismos grupos:
    si no, `cluster_id` cambiaría en cada `--aplicar` y la serie de
    "propiedades únicas" sería ruido."""
    refs = ["e", "a", "d", "b", "c"]
    pares = {("a", "b"), ("b", "c"), ("a", "c"), ("d", "e")}
    primero = agrupar_duplicados(refs, pares)
    for _ in range(5):
        assert agrupar_duplicados(list(reversed(refs)), set(pares)) == primero


def test_los_solitarios_no_forman_cluster():
    assert agrupar_duplicados(["a", "b"], set()) == []


def test_el_piso_solo_no_alcanza_para_fusionar_en_una_cuadra():
    """«Piso 5» en una cuadra no identifica un inmueble: la cuadra son 100
    números y varios edificios, y cada uno tiene su piso 5.

    Medido el 15/08: los 32 clusters que quedaban con el precio al doble tenían
    TODOS unidad declarada, y todas eran solo el piso — «Honduras 3700, Piso 5»
    con precios de USD 54.000 y USD 530.000 en el mismo grupo.
    """
    from tasador.agents.nodes.dedup import unidad_especifica

    assert not unidad_especifica("Honduras 3700, Piso 5")
    assert unidad_especifica("Honduras 3700, Piso 5 Depto B")
    assert unidad_especifica("Honduras 3700 5°B")

    barato = c("a", address="Honduras 3700, Piso 5", price="54000", surface_total="35")
    caro = c("b", address="Honduras 3700, Piso 5", price="530000", surface_total="120")
    assert not _capa_1(barato, caro, CFG), "el piso solo no puede fusionar precios 9,8x"

    # Con el depto SÍ: es el mismo inmueble en dos portales, y ahí el precio y
    # la superficie pueden diferir legítimamente.
    a = c("a", address="Honduras 3700, Piso 5 Depto B", price="250000", surface_total="93")
    b = c("b", address="Honduras al 3700, Piso 5 Depto B", price="265000", surface_total="99")
    assert _capa_1(a, b, CFG)


def test_un_grupo_demasiado_grande_no_se_fusiona_ni_se_recorta():
    """33 avisos donde TODOS los pares matchean no es un inmueble: es un pozo.

    Medido el 15/08 sobre el corpus: 52 cliques de hasta 33 miembros —unidades
    de la misma cuadra, misma superficie ±2 m², mismo precio ±5%—. Elegir 8 al
    azar y fusionarlas es peor que no fusionar ninguna: borra comparables
    legítimos, que es lo que el criterio asimétrico del módulo evita.
    """
    from tasador.agents.nodes.dedup import MAX_MIEMBROS_POR_CLUSTER as TOPE

    refs = [f"u{i}" for i in range(TOPE + 3)]
    todos = {(a, b) for i, a in enumerate(refs) for b in refs[i + 1 :]}
    assert agrupar_duplicados(refs, todos) == [], "un edificio entero no puede ser un cluster"

    # Y justo en el tope sí se fusiona: el corte es el declarado, no uno más acá.
    justos = refs[:TOPE]
    en_el_tope = {(a, b) for i, a in enumerate(justos) for b in justos[i + 1 :]}
    grupos = agrupar_duplicados(justos, en_el_tope)
    assert len(grupos) == 1 and len(grupos[0]) == TOPE


# ── Canónico ─────────────────────────────────────────────────────────────
def test_el_canonico_es_el_mas_completo():
    """Completitud y NO precio: elegir el más barato o el más caro sesgaría la
    mediana en una dirección conocida, que es justo lo que este nodo evita."""
    pobre = c("pobre", price="200000")
    rico = c("rico", price="300000", condition="muy_bueno", floor_number=5, has_elevator=True)
    assert elegir_canonico([pobre, rico])["listing_id"] == "rico"


def test_a_igual_completitud_gana_el_mas_reciente():
    viejo = c("viejo", days_published=200)
    nuevo = c("nuevo", days_published=10)
    assert elegir_canonico([viejo, nuevo])["listing_id"] == "nuevo"


# ── La altura redondeada de los portales ─────────────────────────────────
def test_capa_1_no_fusiona_dos_unidades_de_la_misma_cuadra_con_precios_distintos():
    """EL caso que apareció el 14/08 con 477 avisos de Palermo.

    Los portales REDONDEAN la altura a la centena para que no se pueda saltear
    a la inmobiliaria: 73% de las direcciones de Portal A y 65% de las de
    Portal B terminan en "00". Así que dirección igual = misma CUADRA, no mismo
    edificio.

    Con los 22 avisos de Belgrano de un solo portal no se veía, porque las
    superficies alcanzaban para separar. Acá la capa 1 fusionó «Arenales 3800»
    de USD 400.000 con otro de USD 229.000.
    """
    caro = c("a", address="Arenales 3800", price="400000", rooms=None)
    barato = c("b", address="Arenales al 3800", price="229000", rooms=3)
    assert not _capa_1(caro, barato), "74% de diferencia de precio no es el mismo inmueble"


def test_capa_1_sigue_fusionando_el_mismo_inmueble_en_dos_portales():
    """El contrapeso: agregar el precio no puede romper el caso que la capa 1
    existe para resolver. Dos inmobiliarias publicando lo mismo ponen el mismo
    precio, porque es el que fijó el propietario."""
    en_portal_a = c("a", source="PORTAL_A", address="Báez 500", price="141000", rooms=1)
    en_portal_b = c("b", source="PORTAL_B", address="Baez al 500", price="141000", rooms=1)
    assert _capa_1(en_portal_a, en_portal_b)


def test_capa_1_tolera_una_diferencia_chica_de_precio():
    """Un portal redondea a 141.000 y el otro publica 139.000: es el mismo
    aviso. La tolerancia es la misma que la capa 2 (±5%)."""
    assert _capa_1(c("a", price="141000"), c("b", price="139000"))
    assert not _capa_1(c("a", price="141000"), c("b", price="120000"))


def test_sin_precio_no_se_fusiona():
    """Ante la falta del único dato que separa "el mismo inmueble" de "dos
    unidades de la misma cuadra", distinto. El criterio del nodo 5 es
    asimétrico a propósito (doc 04): fusionar de más borra un comparable
    legítimo y puede dejar el informe sin datos."""
    assert not _capa_1(c("a", price=None), c("b"))
    assert not _capa_1(c("a"), c("b", price=None))


# ── La altura es APROXIMADA, y la unidad es lo que decide ────────────────
def test_la_altura_aproximada_del_portal_no_separa_el_mismo_inmueble():
    """«Perú 1355» se publica como «Perú al 1300». Es EL caso normal, no un
    borde: 73% de las direcciones de Portal A y 65% de las de Portal B
    terminan en "00".

    Comparando la altura exacta esos dos avisos nunca colisionaban, así que se
    perdían duplicados en silencio. La clave es la CUADRA."""
    assert clave_cuadra("Perú 1355") == clave_cuadra("Perú al 1300")
    assert clave_cuadra("Zabala 1851") == clave_cuadra("Zabala al 1800")
    # Y dos cuadras distintas siguen siendo distintas.
    assert clave_cuadra("Perú 1355") != clave_cuadra("Perú al 1400")


def test_la_unidad_no_ensucia_la_clave_de_la_cuadra():
    """Portal A publica «Guise 1900, Piso 4» en 75 de sus 293 avisos. Con la
    unidad pegada a la clave, ese aviso no matcheaba con «Guise 1900» ni con
    ningún otro: la unidad ROMPÍA el match en vez de informarlo."""
    assert clave_cuadra("Guise 1900, Piso 4") == clave_cuadra("Guise 1900")


def test_la_unidad_se_extrae_en_las_tres_formas_que_usan_los_portales():
    assert unidad_de("Perú al 1300, Piso 5 Departamento B") == "5B"
    assert unidad_de("Guise 1900, Piso 4") == "4"
    assert unidad_de("Zabala 1851 4°B") == "4B"
    assert unidad_de("Migueletes 2306") is None
    # Las dos formas que aparecieron al mirar el corpus real y que la primera
    # versión no capturaba: el orden invertido y la unidad numérica.
    assert unidad_de('Soldado de la Independencia al 1200 - 7° Piso "A"') == "7A"
    assert unidad_de("Manuel Ugarte 2500 - 9°08") == "908"
    assert unidad_de("Mendoza 2196 - Piso 2 - Unidad A") == "2A"


def test_dos_unidades_de_la_misma_cuadra_son_distintas_aunque_todo_coincida():
    """«Perú al 1300 Piso 5 Depto B» y «Perú al 1300 Piso 3 Depto B».

    En una torre, las unidades de la misma línea son idénticas salvo por el
    piso: misma superficie, mismos ambientes, y a veces el mismo precio. La
    unidad declarada manda sobre todo lo demás."""
    a = c("a", address="Perú al 1300, Piso 5 Departamento B", price="200000")
    b = c("b", address="Perú al 1300, Piso 3 Departamento B", price="200000")
    assert not _capa_1(a, b)
    # Y ni siquiera se le pregunta al juez: no hay nada que dudar.
    assert not _zona_gris(a, b)


def test_la_misma_unidad_en_dos_portales_si_es_el_mismo_inmueble():
    """El contrapeso. Dos portales pueden publicar el mismo depto con
    superficies distintas —uno cuenta el balcón y el otro no— y con precios
    actualizados en días distintos. Si los dos declaran la misma unidad de la
    misma cuadra, es el mismo inmueble."""
    a = c("a", source="PORTAL_A", address="Guise 1900, Piso 4", surface_total="93")
    b = c(
        "b", source="PORTAL_B", address="Guise al 1900, Piso 4", surface_total="99", price="255000"
    )
    # ⚠️ Lo resuelve la CAPA 2, no la 1, y el cambio es del 15/08.
    #
    # «Piso 4» sin depto no es una unidad específica: en una cuadra —100
    # números, varios edificios— cada uno tiene su piso 4, y el atajo de la
    # capa 1 fusionaba sin mirar precio. Eran los 32 clusters que quedaban con
    # el precio al doble (ver `unidad_especifica`).
    #
    # Este par sigue siendo el mismo inmueble y **se sigue agrupando**: la capa
    # 2 lo toma porque la superficie cae dentro del ±10% (93 vs 99, el balcón) y
    # el precio dentro del ±5%. El invariante que importa es que se agrupen, no
    # cuál de las dos capas lo hace.
    assert not _capa_1(a, b, CFG), "el piso solo ya no alcanza para el atajo"
    assert _capa_2(a, b, CFG), "pero superficie y precio sí lo resuelven"


def test_sin_unidad_declarada_se_decide_por_los_otros_datos():
    """ "No sé" no es "no". Si uno de los dos no declara la unidad, la decisión
    vuelve a superficie, ambientes y precio."""
    a = c("a", address="Perú al 1300, Piso 5 Departamento B")
    b = c("b", address="Perú al 1300")
    assert misma_unidad(a, b) is None
    assert _capa_1(a, b), "mismo precio y superficie: se fusiona"
    assert not _capa_1(a, c("b", address="Perú al 1300", price="400000"))


def test_capa_2_no_fusiona_dos_unidades_del_mismo_edificio():
    """La regresión que introdujo la clave por cuadra, con datos reales.

    Al pasar la clave a la CUADRA, dos avisos de la misma cuadra pasaron a
    tener similitud de trigramas 1,0, y la capa 2 —que nunca miró superficie—
    degeneró en "misma cuadra + precio ±5%".

        Zabala 1851     USD 589.258   93 m²   ┐ el mismo inmueble
        Zabala al 1800  USD 589.258   93 m²   ┘
        Zabala al 1800  USD 610.400  105 m²   ← otra unidad

    589.258 vs 610.400 son 3,6%: entraba. Es EXACTAMENTE el caso que la Etapa 3
    §11.1 celebró haber resuelto, y que la capa 1 resolvía sola.
    """
    cfg = load_agents_config().node("dedup_cluster")
    uno = c("a", address="Zabala 1851", price="589258", surface_total="93", rooms=3)
    otro = c("b", address="Zabala al 1800", price="610400", surface_total="105", rooms=3)
    assert not _capa_2(uno, otro, cfg), "93 vs 105 m² son dos unidades distintas"


def test_capa_2_tolera_que_un_portal_cuente_el_balcon_y_el_otro_no():
    """El contrapeso, y por eso la tolerancia es porcentual y no de ±2 m².

    El mismo depto publicado con 93 y 99 m² existe en el corpus: uno cuenta el
    balcón. Con ±2 m² se separaban; con ±10% se juntan.
    """
    cfg = load_agents_config().node("dedup_cluster")
    uno = c("a", source="PORTAL_A", address="Báez 500", price="141000", surface_total="93")
    otro = c("b", source="PORTAL_B", address="Baez al 500", price="141000", surface_total="99")
    assert _capa_2(uno, otro, cfg)


def test_sin_superficie_la_capa_2_no_fusiona():
    cfg = load_agents_config().node("dedup_cluster")
    uno = c("a", address="Báez 500", surface_total=None)
    assert not _capa_2(uno, c("b", address="Baez al 500"), cfg)
