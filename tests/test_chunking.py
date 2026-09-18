"""Las reglas de corte, sin cargar el modelo: se cuenta por palabras."""

from __future__ import annotations

from tasador.rag.chunking import (
    Oraciones,
    Truncar,
    Ventana,
    chunkers,
    contar_palabras,
    encabezado,
    oraciones,
    por_version,
)

TEXTO = (
    "Departamento de 3 ambientes al frente. Piso 4 con ascensor. "
    "Cocina integrada y lavadero. Muy luminoso, a reciclar. "
    "Expensas bajas. Apto crédito.\n\n"
    "El edificio tiene 10 pisos y portería. Se acepta permuta por unidad menor."
)


def test_oraciones_no_corta_en_abreviaturas_ni_en_numeros():
    frases = oraciones("Av. Cabildo 2500, 3 amb. al frente. Piso 4 con ascensor. Vale 2.5 veces.")
    assert frases == [
        "Av. Cabildo 2500, 3 amb. al frente.",
        "Piso 4 con ascensor.",
        "Vale 2.5 veces.",
    ]


def test_oraciones_respeta_parrafos():
    assert oraciones("primero\n\nsegundo") == ["primero", "segundo"]
    assert oraciones("") == []


def test_truncar_da_un_solo_chunk_que_entra_en_el_tope():
    ch = Truncar(max_tokens=12)
    out = ch.chunk(TEXTO, "ENC", contar_palabras)
    assert len(out) == 1
    assert contar_palabras(out[0].text) <= 12
    assert out[0].text.startswith("ENC\n")


def test_truncar_nunca_devuelve_vacio_aunque_la_primera_oracion_no_entre():
    out = Truncar(max_tokens=2).chunk("Una oración larga que no entra.", "E", contar_palabras)
    assert len(out) == 1 and "Una oración" in out[0].text


def test_ventana_solapa_y_cubre_todo_el_texto():
    ch = Ventana(palabras=10, solape=3)
    out = ch.chunk(TEXTO, "", contar_palabras)
    palabras = TEXTO.split()
    assert out[0].text.split() == palabras[:10]
    assert out[1].text.split()[:3] == palabras[7:10], (
        "los 3 últimos del anterior abren el siguiente"
    )
    assert out[-1].text.split()[-1] == palabras[-1]
    assert [c.ix for c in out] == list(range(len(out)))


def test_oraciones_empaqueta_hasta_el_objetivo_y_no_pasa_el_maximo():
    ch = Oraciones(objetivo=14, maximo=20)
    out = ch.chunk(TEXTO, "ENC", contar_palabras)
    assert len(out) >= 2
    for c in out:
        assert c.token_count <= 20
        assert c.text.startswith("ENC\n"), "todo chunk lleva el encabezado"
    # Ninguna oración se parte: cada chunk termina en puntuación.
    assert all(c.text.rstrip().endswith((".", "!", "?")) for c in out)


def test_oraciones_solapa_la_ultima_oracion_del_chunk_anterior():
    ch = Oraciones(objetivo=9, maximo=30)
    out = ch.chunk(TEXTO, "", contar_palabras)
    ultima_del_primero = oraciones(out[0].text)[-1]
    assert oraciones(out[1].text)[0] == ultima_del_primero


def test_una_oracion_mas_larga_que_el_maximo_se_parte_por_palabras_y_no_tira_el_aviso():
    larga = " ".join(f"palabra{i}" for i in range(50))
    out = Oraciones(objetivo=10, maximo=12).chunk(larga, "", contar_palabras)
    assert sum(len(c.text.split()) for c in out) >= 50
    assert all(c.token_count <= 12 for c in out)


def test_texto_vacio_produce_un_chunk_con_solo_el_encabezado():
    for ch in chunkers().values():
        out = ch.chunk("", "Departamento · 3 ambientes", contar_palabras)
        assert len(out) == 1 and out[0].text.startswith("Departamento")


def test_las_versiones_son_distintas_llevan_el_tamano_y_se_resuelven_por_nombre():
    ch = chunkers(objetivo=100, maximo=120)
    versiones = {c.version for c in ch.values()}
    assert len(versiones) == 3
    assert ch["C"].version == "C-oraciones-100-v1"
    assert chunkers(objetivo=320, maximo=480)["C"].version != ch["C"].version
    assert por_version(chunkers()["C"].version).version == chunkers()["C"].version


def test_el_encabezado_redondea_el_usd_m2_a_la_centena_y_omite_lo_que_falta():
    e = encabezado(
        property_type="departamento",
        rooms=3,
        surface_m2="78.0",
        barrio="Palermo",
        floor_number=4,
        price="203500",
        condition="a_refaccionar",
    )
    assert (
        e == "Departamento · 3 ambientes · 78 m² · Palermo · piso 4 · a refaccionar · USD 2.600/m²"
    )
    assert (
        encabezado(property_type=None, rooms=1, surface_m2=None, barrio=None)
        == "Propiedad · monoambiente"
    )
    assert "planta baja" in encabezado(
        property_type="ph", rooms=2, surface_m2=50, barrio="x", floor_number=0
    )


def test_un_retoque_de_precio_chico_no_cambia_el_encabezado():
    a = encabezado(
        property_type="departamento", rooms=3, surface_m2=78, barrio="Palermo", price=203500
    )
    b = encabezado(
        property_type="departamento", rooms=3, surface_m2=78, barrio="Palermo", price=205000
    )
    assert a == b, "re-embeber por USD 1.500 de diferencia sería trabajo tirado"
