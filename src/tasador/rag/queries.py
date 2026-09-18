"""El sujeto → el texto de la consulta semántica — doc 18 §3.4.

Se arma con el mismo formato que el encabezado de los chunks (`chunking.
encabezado`), para que la consulta y los pasajes hablen el mismo idioma, y se
le agregan las notas del agente si existen.

Las notas son lo único que un formulario no tiene y un aviso sí: «balcón
corrido, cocina a reciclar, cañerías a revisar». Hoy no salen de la máquina
—`runner._subject_dict` las deja fuera del estado del grafo por doc 10 §3— y
acá tampoco: el embedding es local. Es la primera vez que ese texto puede
influir en qué comparables se eligen sin romper la regla de privacidad.
"""

from __future__ import annotations

from typing import Any

from tasador.rag.chunking import encabezado

_ESTADO_EN_TEXTO = {
    "a_estrenar": "a estrenar",
    "excelente": "excelente estado",
    "muy_bueno": "muy buen estado",
    "bueno": "buen estado",
    "a_refaccionar": "a refaccionar",
}
_ORIENTACION_EN_TEXTO = {
    "frente": "al frente",
    "contrafrente": "al contrafrente",
    "lateral": "lateral",
    "interno": "interno",
}


def texto_de_consulta(subject: dict[str, Any], *, con_notas: bool = True) -> str:
    """Encabezado estructurado + los atributos que un aviso diría en prosa +
    las notas. Sin precio: el sujeto no tiene uno, es lo que se busca."""
    sup = subject.get("surface_covered") or subject.get("surface_total")
    partes = [
        encabezado(
            property_type=subject.get("property_type"),
            rooms=subject.get("rooms"),
            surface_m2=sup,
            barrio=subject.get("neighborhood_name"),
            floor_number=subject.get("floor_number"),
            condition=subject.get("condition"),
        )
    ]
    prosa: list[str] = []
    if (c := subject.get("condition")) in _ESTADO_EN_TEXTO:
        prosa.append(_ESTADO_EN_TEXTO[c])
    if (o := subject.get("orientation")) in _ORIENTACION_EN_TEXTO:
        prosa.append(_ORIENTACION_EN_TEXTO[o])
    if subject.get("has_elevator") is True:
        prosa.append("con ascensor")
    elif subject.get("has_elevator") is False:
        prosa.append("sin ascensor")
    if (edad := subject.get("age_years")) is not None:
        prosa.append("a estrenar" if int(edad) == 0 else f"{int(edad)} años de antigüedad")
    if subject.get("parking_spaces"):
        prosa.append("con cochera")
    if amen := subject.get("amenities"):
        prosa.append("amenities: " + ", ".join(str(a) for a in amen[:6]))
    if prosa:
        partes.append(". ".join(p[0].upper() + p[1:] for p in prosa) + ".")
    if con_notas and (notas := (subject.get("notes") or "").strip()):
        partes.append(notas[:1500])
    return "\n".join(partes)
