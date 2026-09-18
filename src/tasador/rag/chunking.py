"""Cómo se parte un aviso para embeberlo — doc 18 §3.2 (ADR-011).

Tres estrategias, las tres evaluadas contra el mismo set. Existen las tres
porque la pregunta "¿el chunking aporta?" solo se contesta con la línea de
base al lado:

    A  truncar     un chunk, cortado al tope de tokens. La línea de base honesta.
    B  ventana     ventanas fijas de N palabras con solape. Lo que hace un tutorial.
    C  oraciones   por oraciones y párrafos, empaquetadas hasta un objetivo de
                   tokens, con solape de una oración. La propuesta.

**Cada chunk lleva un encabezado estructurado** —tipo, ambientes, superficie,
barrio, piso, USD/m²— armado desde las columnas, no desde el texto. Un
fragmento que dice «a reciclar, muy luminoso» no sabe de qué propiedad habla;
con el encabezado, sí. Es *contextual chunking* sin llamar a un LLM: el
contexto ya lo tenemos, en columnas.

El conteo de tokens se inyecta (`contar`). En producción es el tokenizador del
modelo de embeddings; en los tests, contar palabras. Así las reglas de corte
se prueban sin cargar 2 GB de modelo.

**El aviso es el documento padre.** Ningún chunk mezcla texto de dos avisos,
y la recuperación devuelve avisos, no chunks (`retriever.py`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

Contar = Callable[[str], int]

# Fin de oración: puntuación seguida de espacio y mayúscula/dígito, o salto de
# línea. El punto tiene que ir seguido de espacio Y de una mayúscula o dígito,
# o de un salto de párrafo: "2.5 veces" no corta.
_FIN_DE_ORACION = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÚÑ¿¡\d])|\n{2,}|\n(?=\S)")
# Abreviaturas del rubro cuyo punto NO termina una oración ("Av. Cabildo",
# "3 amb. al frente", "Dpto. B"). Se protegen antes de cortar y se restauran.
_ABREVIATURAS = (
    "av", "avda", "dr", "dra", "sr", "sra", "gral", "pte", "amb", "dpto", "dto", "depto",
    "cdad", "esq", "n", "nro", "ing", "arq", "cel", "tel", "aprox",
)  # fmt: skip
_RE_ABREV = re.compile(r"\b(" + "|".join(_ABREVIATURAS) + r")\.", re.IGNORECASE)
_MARCA = chr(0x2024)  # ONE DOT LEADER: un "punto" que no es punto


def oraciones(texto: str) -> list[str]:
    """Oraciones/párrafos no vacíos, con espacios colapsados."""
    protegido = _RE_ABREV.sub(lambda m: m.group(1) + _MARCA, texto or "")
    partes = _FIN_DE_ORACION.split(protegido)
    return [" ".join(p.replace(_MARCA, ".").split()) for p in partes if p and p.strip()]


def contar_palabras(texto: str) -> int:
    return len(texto.split())


@dataclass(slots=True, frozen=True)
class Chunk:
    ix: int
    text: str  # lo que se embebe: encabezado + fragmento
    token_count: int


class _Chunker(Protocol):
    @property
    def version(self) -> str: ...

    def chunk(self, texto: str, encabezado: str, contar: Contar) -> list[Chunk]: ...


def _con_encabezado(encabezado: str, fragmento: str) -> str:
    return f"{encabezado}\n{fragmento}" if encabezado else fragmento


@dataclass(slots=True, frozen=True)
class Truncar:
    """A · un solo chunk. Si no entra, se corta por oraciones hasta que entre."""

    max_tokens: int = 512
    version: str = "A-truncar-v1"

    def chunk(self, texto: str, encabezado: str, contar: Contar) -> list[Chunk]:
        frases = oraciones(texto)
        if not frases:
            return [Chunk(0, _con_encabezado(encabezado, ""), contar(encabezado))]
        cuerpo: list[str] = []
        for f in frases:
            candidato = _con_encabezado(encabezado, " ".join([*cuerpo, f]))
            if cuerpo and contar(candidato) > self.max_tokens:
                break
            cuerpo.append(f)
        t = _con_encabezado(encabezado, " ".join(cuerpo))
        return [Chunk(0, t, contar(t))]


@dataclass(slots=True, frozen=True)
class Ventana:
    """B · ventanas fijas de palabras con solape. Corta frases por la mitad, a
    propósito: es la comparación."""

    palabras: int = 220
    solape: int = 40
    version: str = "B-ventana-v1"

    def chunk(self, texto: str, encabezado: str, contar: Contar) -> list[Chunk]:
        ws = (texto or "").split()
        if not ws:
            return [Chunk(0, _con_encabezado(encabezado, ""), contar(encabezado))]
        paso = max(1, self.palabras - self.solape)
        out: list[Chunk] = []
        inicio = 0
        while inicio < len(ws):
            frag = " ".join(ws[inicio : inicio + self.palabras])
            t = _con_encabezado(encabezado, frag)
            out.append(Chunk(len(out), t, contar(t)))
            if inicio + self.palabras >= len(ws):
                break
            inicio += paso
        return out


@dataclass(slots=True, frozen=True)
class Oraciones:
    """C · empaqueta oraciones hasta `objetivo` tokens; nunca pasa `maximo`;
    solapa la última oración del chunk anterior para no perder una referencia
    («…con balcón. El mismo da al frente.»)."""

    objetivo: int = 320
    maximo: int = 480
    version: str = "C-oraciones-v1"

    def chunk(self, texto: str, encabezado: str, contar: Contar) -> list[Chunk]:
        frases = oraciones(texto)
        if not frases:
            return [Chunk(0, _con_encabezado(encabezado, ""), contar(encabezado))]
        out: list[Chunk] = []
        actual: list[str] = []

        def cerrar() -> None:
            t = _con_encabezado(encabezado, " ".join(actual))
            out.append(Chunk(len(out), t, contar(t)))

        for f in frases:
            # Una oración sola que no entra en el máximo se parte por palabras:
            # es raro (un párrafo sin puntos) y no puede tirar el aviso entero.
            piezas = (
                [f]
                if contar(_con_encabezado(encabezado, f)) <= self.maximo
                else _partir(f, encabezado, contar, self.maximo)
            )
            for pieza in piezas:
                candidato = _con_encabezado(encabezado, " ".join([*actual, pieza]))
                if actual and contar(candidato) > self.objetivo:
                    cerrar()
                    # Solape: la última oración del chunk que se cerró.
                    actual = [actual[-1], pieza]
                    if contar(_con_encabezado(encabezado, " ".join(actual))) > self.maximo:
                        actual = [pieza]
                else:
                    actual.append(pieza)
        if actual:
            cerrar()
        return out


def _partir(frase: str, encabezado: str, contar: Contar, maximo: int) -> list[str]:
    ws = frase.split()
    piezas: list[str] = []
    actual: list[str] = []
    for w in ws:
        if actual and contar(_con_encabezado(encabezado, " ".join([*actual, w]))) > maximo:
            piezas.append(" ".join(actual))
            actual = [w]
        else:
            actual.append(w)
    if actual:
        piezas.append(" ".join(actual))
    return piezas


Chunker = Truncar | Ventana | Oraciones

CHUNKERS: dict[str, Chunker] = {
    "A": Truncar(),
    "B": Ventana(),
    "C": Oraciones(),
}


def por_version(version: str) -> Chunker:
    for c in CHUNKERS.values():
        if c.version == version:
            return c
    raise KeyError(f"chunker desconocido: {version}")


# ── El encabezado ────────────────────────────────────────────────────────


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (ArithmeticError, ValueError):
        return None


def encabezado(
    *,
    property_type: str | None,
    rooms: int | None,
    surface_m2: Any,
    barrio: str | None,
    floor_number: int | None = None,
    price: Any = None,
    condition: str | None = None,
) -> str:
    """Los hechos del aviso que el texto no repite, en una línea estable.

    Se embebe delante de cada chunk. El USD/m² va redondeado a la centena: el
    número exacto no ayuda a la similitud y cambia con cada retoque de precio,
    lo que obligaría a re-embeber por nada.
    """
    partes: list[str] = [(property_type or "propiedad").capitalize()]
    if rooms:
        partes.append(f"{rooms} ambientes" if rooms != 1 else "monoambiente")
    sup = _num(surface_m2)
    if sup and sup > 0:
        partes.append(f"{sup:.0f} m²")
    if barrio:
        partes.append(barrio)
    if floor_number is not None:
        partes.append("planta baja" if floor_number == 0 else f"piso {floor_number}")
    if condition:
        partes.append(condition.replace("_", " "))
    precio = _num(price)
    if precio and sup and sup > 0:
        usd_m2 = int(round(float(precio / sup) / 100.0) * 100)
        partes.append(f"USD {usd_m2:,}/m²".replace(",", "."))
    return " · ".join(partes)
