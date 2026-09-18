"""Resolución de barrios: por nombre, por texto libre, por coordenadas y por
cercanía.

Sin base de datos: se arma el índice a mano con centroides REALES, los mismos
que quedaron en `corpus.neighborhoods` tras geocodificarlos.
"""

from __future__ import annotations

import uuid

from tasador.corpus.resolve import BarrioRef, Barrios, clave

# Centroides medidos con Nominatim el 13/08 y validados por distancia entre
# barrios que se tocan (1.500-3.100 m).
BELGRANO = BarrioRef(uuid.UUID(int=1), "Belgrano", "CABA", -34.5614, -58.4560)
NUNEZ = BarrioRef(uuid.UUID(int=2), "Núñez", "CABA", -34.5455, -58.4620)
COLEGIALES = BarrioRef(uuid.UUID(int=3), "Colegiales", "CABA", -34.5748, -58.4497)
LUGANO = BarrioRef(uuid.UUID(int=4), "Villa Lugano", "CABA", -34.6753, -58.4715)
SIN_CENTRO = BarrioRef(uuid.UUID(int=5), "Barrio Nuevo", "CABA")


def _barrios() -> Barrios:
    todos = [BELGRANO, NUNEZ, COLEGIALES, LUGANO, SIN_CENTRO]
    indice = {clave(b.name): b for b in todos}
    indice[clave("Belgrano C")] = BELGRANO
    indice[clave("Bajo Belgrano")] = BELGRANO
    indice[clave("Nunez")] = NUNEZ
    return Barrios(todos=todos, _por_clave=indice)


def test_clave_ignora_tildes_mayusculas_y_espacios():
    assert clave("  Núñez  ") == clave("nunez") == "NUNEZ"


def test_resuelve_por_nombre_exacto():
    assert _barrios().por_nombre("Belgrano") is BELGRANO


def test_resuelve_por_alias():
    """Los portales inventan sub-barrios que no existen en la nomenclatura
    oficial del GCBA."""
    assert _barrios().por_nombre("Belgrano C") is BELGRANO


def test_resuelve_la_etiqueta_compuesta_de_los_portales():
    """Portal B manda "Belgrano, Capital Federal"; Portal A pone el sub-barrio
    primero. Se prueba cada parte, de la más específica a la más general."""
    b = _barrios()
    assert b.por_nombre("Belgrano, Capital Federal") is BELGRANO
    assert b.por_nombre("Departamento en Venta en Belgrano C, Belgrano") is BELGRANO


def test_no_inventa_un_barrio():
    assert _barrios().por_nombre("Villa Inexistente") is None
    assert _barrios().por_nombre(None) is None


def test_encuentra_el_barrio_dentro_de_una_direccion():
    assert _barrios().en_texto("Av. Cabildo 2530, Belgrano, CABA") is BELGRANO


def test_gana_el_nombre_mas_largo():
    """Sin esto "Belgrano" le ganaría a "Bajo Belgrano" y se perdería
    precisión. Acá los dos apuntan al mismo barrio, pero la regla importa
    cuando un alias largo apunta a otro lado."""
    b = _barrios()
    assert b.en_texto("Vivo en Bajo Belgrano hace años") is BELGRANO


def test_texto_sin_barrio_da_none():
    assert _barrios().en_texto("Una dirección cualquiera 123") is None


def test_asigna_por_centroide_mas_cercano():
    b = _barrios()
    r = b.por_coordenadas(-34.5620, -58.4570)  # a metros de Belgrano
    assert r is not None
    assert r[0] is BELGRANO
    assert r[1] < 200


def test_no_asigna_si_esta_demasiado_lejos():
    """Decir "está en este barrio" cuando el centroide más cercano queda a
    30 km es adivinar, y un informe sobre la zona equivocada es peor que
    ningún informe."""
    assert _barrios().por_coordenadas(-34.9000, -58.0000) is None


def test_cercanos_incluye_al_propio_barrio():
    cercanos = _barrios().cercanos(BELGRANO.id, radio_m=3500)
    assert BELGRANO in cercanos


def test_cercanos_respeta_el_radio():
    b = _barrios()
    pegados = b.cercanos(BELGRANO.id, radio_m=3500)
    assert NUNEZ in pegados  # 1,8 km
    assert LUGANO not in pegados  # 13 km

    zona = b.cercanos(BELGRANO.id, radio_m=20000)
    assert LUGANO in zona


def test_cercanos_viene_ordenado_por_distancia():
    """Si después hay que recortar, se recorta por lo más lejano — que es lo
    que menos aporta."""
    cercanos = _barrios().cercanos(BELGRANO.id, radio_m=20000)
    assert cercanos[0] is BELGRANO
    assert cercanos.index(NUNEZ) < cercanos.index(LUGANO)


def test_un_barrio_sin_centroide_no_rompe_la_cercania():
    """59 de 59 tienen centroide hoy, pero un barrio nuevo cargado a mano
    puede no tenerlo todavía."""
    b = _barrios()
    assert b.cercanos(SIN_CENTRO.id, radio_m=5000) == [SIN_CENTRO]
    # Y no aparece como candidato de otro, porque no se sabe dónde está.
    assert SIN_CENTRO not in b.cercanos(BELGRANO.id, radio_m=50000)


def test_por_id():
    assert _barrios().por_id(BELGRANO.id) is BELGRANO
    assert _barrios().por_id(uuid.UUID(int=99)) is None
