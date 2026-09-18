"""Nodo 5 — `dedup_cluster`. El bug silencioso más peligroso del sistema.

Si el mismo departamento está publicado en dos portales y los dos entran como
comparables, **pesa doble en la mediana**. Con tres portales, triple. Y no hay
ningún síntoma: el informe sale, los números cierran, y el precio está sesgado
hacia el inmueble que más veces se publicó (doc 13, R4).

**Cascada de tres capas, de barata a cara** (doc 04, nodo 5):

  1. Dirección normalizada exacta + superficie ±2 m² + mismos ambientes.
     Determinístico, sin dudas, costo cero.
  2. Trigram sobre la dirección + precio dentro del ±5%. Alta confianza.
  3. LLM juez, SOLO para los pares que quedaron en zona gris.

El caso borde que hay que respetar: **dos unidades distintas del mismo
edificio**. Misma dirección, distinto piso o superficie. Por eso la capa 1 exige
superficie Y ambientes, y por eso un par con la misma dirección pero superficie
muy distinta ni siquiera baja a la capa 3 — se resuelve como "distintos".

Un cluster aporta **un solo comparable**: el canónico, que se elige por
completitud de datos y después por recencia.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from functools import lru_cache
from itertools import combinations
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeResult
from tasador.agents.prompts import render
from tasador.agents.state import Candidate, ReportState, dec
from tasador.llm import AVISO_INJECTION, LlmClient, LlmError, LlmValidationError, UsageLedger

log = structlog.get_logger()

# "1.400" -> "1400", pero "Av. Cabildo" no se toca: el punto solo desaparece
# cuando separa dígitos de un número, que es el formato de miles argentino.
_RE_MILES = re.compile("([0-9])[.]([0-9]{3})")
_SIN_PUNTO = r"\1\2"


class ParJuzgado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    par: str  # "ref_a|ref_b", como se lo mandamos
    veredicto: Literal["mismo", "distinto", "no_se"]
    motivo: str = Field(max_length=300)


class LoteDedup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pares: list[ParJuzgado]


def clave_direccion(texto: str | None) -> str:
    """Dirección comparable: sin tildes, sin puntuación, en mayúsculas.

    "Av. Cabildo al 2200" y "AV CABILDO 2200" son la misma cuadra. No se usa
    `normalizar_direccion` porque acá no interesa desglosar calle y altura,
    sino tener una clave estable para agrupar.
    """
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Primero el punto de miles DENTRO de un número: "Luis María Campos 1.400"
    # y "Luis Maria Campos 1400" son la misma dirección, y si se reemplaza el
    # punto por espacio quedan "1 400" y "1400", que no colisionan. Lo detectó
    # un test antes de que lo hiciera un informe.
    s = _RE_MILES.sub(_SIN_PUNTO, s)
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    # "al 2200" y "2200" tienen que colisionar: el "al" es ruido del portal.
    palabras = [p for p in s.upper().split() if p != "AL"]
    return " ".join(palabras)


# La unidad, en las tres formas en que los portales la escriben. `4°B` ya lo
# cubre `geocoding._RE_UNIDAD`; estas dos son las de Portal A, que publica
# "Guise 1900, Piso 4" en 75 de sus 293 avisos.
_RE_PISO = re.compile(r"\bPISO\s+(\d{1,3})\b")
# El orden invertido: «7° Piso "A"». Existe en el corpus y no es raro.
_RE_PISO_INVERTIDO = re.compile(r"\b(\d{1,3})\s*[°º]\s*PISO\b")
_RE_DEPTO = re.compile(r"\b(?:DEPTO|DEPARTAMENTO|DPTO|UNIDAD|UF)\s*\.?\s*([A-Z0-9]{1,4})\b")
# El depto entre comillas después del piso: «7° Piso "A"».
_RE_DEPTO_COMILLAS = re.compile(r"PISO\s*[\"'“”]([A-Z0-9]{1,4})[\"'“”]")
# Unidad numérica pegada al piso: «9°08» = piso 9, depto 08.
_RE_UNIDAD_NUMERICA = re.compile(r"\b(\d{1,3})\s*[°º]\s*(\d{1,3})\b")

# 100: la cuadra. Es la unidad en que los portales redondean.
CUADRA = 100


@lru_cache(maxsize=20_000)
def _piso_y_depto(texto: str | None) -> tuple[str | None, str | None]:
    """`Piso 5 Depto B` -> `("5", "B")`. Cada parte por separado.

    Se devuelven separadas porque **no valen lo mismo**: ver `unidad_especifica`.
    """
    from tasador.geocoding import normalizar_direccion

    if not texto:
        return None, None
    d = normalizar_direccion(texto)
    if d.unidad:  # formato "4°B": el piso pegado a la letra
        u = d.unidad.replace(" ", "")
        cabeza = "".join(ch for ch in u if ch.isdigit())
        cola = u[len(cabeza) :]
        return cabeza or None, cola or None

    plano = " ".join(sin_tildes_upper(texto).split())

    # «9°08» primero: si matchea, los dos números son piso y depto y no hay que
    # dejar que otro patrón se quede con uno solo.
    if m := _RE_UNIDAD_NUMERICA.search(plano):
        return m.group(1), m.group(2)

    piso = None
    for patron in (_RE_PISO, _RE_PISO_INVERTIDO):
        if m := patron.search(plano):
            piso = m.group(1)
            break

    depto = None
    for patron in (_RE_DEPTO, _RE_DEPTO_COMILLAS):
        if m := patron.search(plano):
            depto = m.group(1)
            break

    return piso, depto


def unidad_de(texto: str | None) -> str | None:
    """`Piso 5 Depto B` -> `"5B"`. `None` si el aviso no la declara.

    Es el único dato que distingue con certeza dos avisos de la misma cuadra.
    Y no lo declara casi nadie: 75 de 293 en Portal A, 8 de 298 en Portal B.
    Las columnas `floor` y `apartment` del scraper vienen vacías en los 591
    avisos del corpus, así que la unidad solo vive en el texto de la dirección.
    """
    piso, depto = _piso_y_depto(texto)
    if piso or depto:
        return f"{piso or ''}{depto or ''}"
    return None


def unidad_especifica(texto: str | None) -> bool:
    """¿La unidad identifica un INMUEBLE, o solo un piso?

    ⚠️ La distinción no es sutil y costó 32 clusters mal fusionados.

    La clave de agrupamiento es la **cuadra** (`clave_cuadra`), porque los
    portales publican la altura redondeada: «Perú 1355» sale como «Perú al
    1300». Una cuadra son 100 números y **varios edificios**, y cada uno tiene
    su piso 5.

    `_capa_1` cortocircuitaba con `misma_unidad(a, b) is True` sin mirar precio
    ni superficie —correcto para «Piso 5 Depto B», que identifica un inmueble—
    y con «Piso 5» a secas fusionaba departamentos de edificios distintos.
    Medido el 15/08 sobre el corpus reagrupado: los 32 clusters que quedaban con
    el precio al doble tenían TODOS unidad declarada, y todas eran solo el piso:

        n=3  USD  54.000 -> 530.000  (9,8x)  «Honduras 3700, Piso 5»
        n=2  USD 139.000 -> 730.000  (5,3x)  «Cabello 3900, Piso 1»
        n=7  USD  68.750 -> 350.000  (5,1x)  «Manuel Nicolas Savio 400, Piso 1»

    Con el depto la unidad es específica y el atajo vale. Sin él, se decide por
    superficie, ambientes y precio como cualquier otro par.
    """
    _, depto = _piso_y_depto(texto)
    return depto is not None


def sin_tildes_upper(s: str) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


# ⚠️ CACHEADAS, y no es una micro-optimización.
#
# `_capa_1`, `_capa_2` y `_zona_gris` llaman a estas funciones para CADA PAR, y
# los pares son O(n²) dentro de cada bloque. Con los 22 avisos de un informe no
# se nota; con los 4.007 de Palermo del 15/08 son ~1,4 millones de llamadas
# para 4.007 direcciones distintas, y `normalizar_direccion` no es barata.
#
# Medido: el análisis del corpus se pasaba de 550 s y no terminaba.
#
# Son funciones PURAS de un string, así que cachear no cambia ningún resultado.
# El tope de 20.000 entradas cubre el corpus entero de CABA con margen.
@lru_cache(maxsize=20_000)
def clave_cuadra(texto: str | None) -> str:
    """Calle + CUADRA, no calle + altura exacta.

    **Esta es la corrección que faltaba, y va en el sentido contrario al de la
    otra.** Los portales publican la altura aproximada: "Perú 1355" sale como
    "Perú al 1300". Comparando la altura exacta, esos dos avisos —que son el
    mismo inmueble— nunca colisionaban:

        clave_direccion("Perú 1355")     -> "PERU 1355"
        clave_direccion("Perú al 1300")  -> "PERU 1300"     ✗ distintos

    Y al mismo tiempo la unidad ROMPÍA el match, porque quedaba pegada a la
    clave:

        clave_direccion("Guise 1900, Piso 4") -> "GUISE 1900 PISO 4"
        clave_direccion("Guise 1900")         -> "GUISE 1900"   ✗ distintos

    O sea que se perdían duplicados por los dos lados. Redondear a la cuadra y
    sacar la unidad de la clave arregla los dos: la unidad pasa a ser un dato
    aparte, que es lo que es — ver `misma_unidad`.
    """
    from tasador.geocoding import normalizar_direccion

    d = normalizar_direccion(texto or "")
    calle = clave_direccion(d.calle)
    if not d.altura:
        return calle
    try:
        cuadra = (int(d.altura) // CUADRA) * CUADRA
    except ValueError:
        return calle
    return f"{calle} {cuadra}"


def misma_unidad(a: Candidate, b: Candidate) -> bool | None:
    """`True` / `False` / `None` si alguno de los dos no la declara.

    Tres estados y no dos, porque "no sé" no es "no". Dos avisos en
    «Perú al 1300, Piso 5 Depto B» y «Perú al 1300, Piso 3 Depto B» son
    DISTINTOS con certeza; si uno no dice la unidad, hay que decidir por otro
    lado.
    """
    ua, ub = unidad_de(a.get("address")), unidad_de(b.get("address"))
    if ua is None or ub is None:
        return None
    return ua == ub


def trigramas(s: str) -> set[str]:
    t = f"  {s} "
    return {t[i : i + 3] for i in range(len(t) - 2)}


def similitud(a: str, b: str) -> float:
    """Jaccard sobre trigramas — el mismo criterio que `pg_trgm`.

    Se implementa en Python y no en SQL porque los candidatos ya están en
    memoria: mandarlos a la base para compararlos entre sí sería un viaje de
    ida y vuelta por nada.
    """
    ta, tb = trigramas(a), trigramas(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _sup(c: Candidate) -> Decimal | None:
    return dec(c.get("surface_covered")) or dec(c.get("surface_total"))


def _capa_1(a: Candidate, b: Candidate, cfg: NodeConfig | None = None) -> bool:
    """Dirección idéntica + superficie ±2 m² + mismos ambientes + MISMO PRECIO.

    ⚠️ El precio entró el 14/08 y no es un refinamiento: sin él esta capa
    fusiona propiedades distintas.

    **Los portales redondean la altura a la centena.** Medido sobre el corpus:
    73% de las direcciones de Portal A y 65% de las de Portal B terminan en
    "00" — "Paraguay 4000", "Báez 500". Lo hacen a propósito, para que no se
    pueda saltear a la inmobiliaria. Consecuencia: `clave_direccion` igual
    significa **misma CUADRA**, no mismo edificio, y ni siquiera la misma
    torre.

    Con los 22 avisos de Belgrano de un solo portal esto no se veía: las
    superficies alcanzaban para separar. Con 477 avisos de Palermo de dos
    portales, la capa 1 fusionó «Arenales 3800» de USD 400.000 con otro de
    USD 229.000 —dos unidades distintas de la misma cuadra— y «Paraguay 4000»
    juntó cuatro avisos con cuatro precios distintos.

    El precio es la señal que faltaba y es la correcta: dos avisos del MISMO
    inmueble publicados por dos inmobiliarias tienen el mismo precio, porque es
    el que fijó el propietario. Dos unidades parecidas de la misma cuadra, no.

    El criterio sigue siendo asimétrico (doc 04, nodo 5): ante la duda,
    DISTINTO. Fusionar dos inmuebles que no lo son borra un comparable legítimo
    y puede dejar el informe sin datos; no fusionar dos que sí lo son deja uno
    de más, que la mediana diluye.
    """
    # La CUADRA, no la altura exacta: "Perú 1355" y "Perú al 1300" son el mismo
    # inmueble con la altura aproximada por el portal.
    if clave_cuadra(a.get("address")) != clave_cuadra(b.get("address")):
        return False

    # La unidad manda sobre todo lo demás cuando los dos la declaran. «Piso 5
    # Depto B» y «Piso 3 Depto B» son distintos aunque coincidan superficie,
    # ambientes y precio — y coinciden seguido, porque en una torre las
    # unidades de la misma línea son idénticas salvo por el piso.
    match misma_unidad(a, b):
        case False:
            return False
        case True if unidad_especifica(a.get("address")) and unidad_especifica(b.get("address")):
            # Misma cuadra Y misma unidad ESPECÍFICA (con depto, no solo el
            # piso): es el mismo inmueble. No se le exige superficie ni precio,
            # porque dos portales pueden publicar el mismo depto con superficies
            # distintas —uno cuenta el balcón y el otro no— y con precios
            # actualizados en días distintos.
            return True
        case _:
            # Ninguno la declara, o los dos declaran solo el piso. Un «Piso 5»
            # en una cuadra con varios edificios no identifica nada: se decide
            # por superficie, ambientes y precio como cualquier otro par.
            # Ver `unidad_especifica` para los 32 clusters que esto arregla.
            pass

    sa, sb = _sup(a), _sup(b)
    if sa is None or sb is None or abs(sa - sb) > 2:
        return False
    # Ambientes distintos = unidades distintas del mismo edificio.
    if a.get("rooms") and b.get("rooms") and a["rooms"] != b["rooms"]:
        return False
    return _mismo_precio(a, b, cfg)


def _mismo_precio(a: Candidate, b: Candidate, cfg: NodeConfig | None = None) -> bool:
    """Precio dentro de la tolerancia. **Sin precio no se fusiona.**

    Un aviso sin precio no aporta la única señal que separa "el mismo inmueble
    en dos portales" de "dos unidades de la misma cuadra". Ante la falta de
    dato, distinto.
    """
    pa, pb = dec(a.get("price")), dec(b.get("price"))
    if pa is None or pb is None or pa == 0:
        return False
    tol = Decimal(str(cfg.param("price_tolerance_pct", 5) if cfg else 5)) / 100
    return abs(pa - pb) / pa <= tol


def _capa_2(a: Candidate, b: Candidate, cfg: NodeConfig) -> bool:
    """Trigram alto + superficie parecida + precio dentro del ±5%.

    ⚠️ **La superficie entró el 14/08 y es lo que impide una regresión.**

    Al pasar la clave a la CUADRA, dos avisos de la misma cuadra tienen
    similitud de trigramas 1,0 — antes «ZABALA 1851» y «ZABALA 1800» daban
    menos. O sea que esta capa degeneró en "misma cuadra + precio ±5%", que
    fusiona unidades distintas:

        Zabala 1851     USD 589.258   93 m²  ┐ el mismo inmueble
        Zabala al 1800  USD 589.258   93 m²  ┘
        Zabala al 1800  USD 610.400  105 m²  ← otra unidad, y se fusionaba

    Es **exactamente** el caso que el informe de la Etapa 3 §11.1 registró como
    bien resuelto —"los dos avisos de Zabala son del mismo edificio pero
    unidades distintas (93 vs 105 m²)"—. Lo resolvía la capa 1, que sí mira
    superficie; la capa 2 nunca la miró y con la clave vieja no hacía falta.

    La tolerancia es en PORCENTAJE y no en m² fijos: dos portales publican el
    mismo depto con superficies distintas porque uno cuenta el balcón y el otro
    no (93 y 99 en el corpus). ±2 m² separaría esos dos; ±10% los junta y
    separa el 93 vs 105.
    """
    umbral = float(cfg.param("trigram_threshold", 0.85))
    if similitud(clave_cuadra(a.get("address")), clave_cuadra(b.get("address"))) < umbral:
        return False
    if misma_unidad(a, b) is False:
        return False
    if not _superficie_parecida(a, b, cfg):
        return False
    return _mismo_precio(a, b, cfg)


def _superficie_parecida(a: Candidate, b: Candidate, cfg: NodeConfig | None = None) -> bool:
    """Superficie dentro de la tolerancia porcentual. **Sin superficie, no.**

    Mismo criterio asimétrico que el precio: ante la falta del dato que
    separaría dos unidades, distinto.
    """
    sa, sb = _sup(a), _sup(b)
    if sa is None or sb is None or sa <= 0:
        return False
    tol = Decimal(str(cfg.param("surface_tolerance_pct", 10) if cfg else 10)) / 100
    return abs(sa - sb) / sa <= tol


def _zona_gris(a: Candidate, b: Candidate, *, min_similitud: float = 0.45) -> bool:
    """Parecidos pero no lo bastante como para decidir sin criterio.

    Se acota fuerte a propósito: el juez es caro y la mayoría de los pares de
    un set de comparables son obviamente distintos.
    """
    # Unidades declaradas y distintas: no hay nada que dudar ni que preguntarle
    # al juez.
    if misma_unidad(a, b) is False:
        return False
    sim = similitud(clave_cuadra(a.get("address")), clave_cuadra(b.get("address")))
    if sim < min_similitud:
        return False
    sa, sb = _sup(a), _sup(b)
    # Superficies muy distintas: son unidades distintas del mismo edificio y
    # no hace falta molestar al juez.
    return not (
        sa is not None and sb is not None and sa > 0 and abs(sa - sb) / sa > Decimal("0.25")
    )


def elegir_canonico(grupo: list[Candidate]) -> Candidate:
    """El representante del cluster: primero completitud, después recencia.

    Completitud y no precio: elegir el más barato o el más caro sesgaría la
    mediana en una dirección conocida, que es exactamente lo que este nodo
    existe para evitar.
    """
    campos = (
        "condition",
        "orientation",
        "floor_number",
        "has_elevator",
        "age_years",
        "surface_covered",
        "rooms",
    )

    def puntaje(c: Candidate) -> tuple[int, int]:
        completos = sum(1 for k in campos if c.get(k) is not None)
        # `days_published` chico = más reciente. Se invierte para maximizar.
        frescura = -(c.get("days_published") or 9999)
        return (completos, frescura)

    return max(grupo, key=puntaje)


# Un inmueble republicado por varias inmobiliarias son 2-5 avisos. Por encima de
# esto no hay UN inmueble: hay un edificio o un emprendimiento.
#
# ⚠️ Y cuando pasa, el grupo se DESCARTA ENTERO, no se recorta.
#
# Medido el 15/08 con enlace completo sobre el corpus: quedaban 52 cliques de
# hasta 33 miembros — o sea 528 pares que TODOS cumplen el criterio. Eso no es
# un error del agrupamiento: son 33 unidades de la misma cuadra, con la misma
# superficie ±2 m² y el mismo precio ±5%, que es exactamente cómo se publica un
# pozo. Las capas no las pueden distinguir con los datos que hay.
#
# Recortar a los primeros 8 sería elegir 8 al azar y fusionarlas, que es peor
# que no fusionar ninguna: el criterio del módulo es asimétrico —fusionar dos
# inmuebles que no lo son BORRA un comparable legítimo— y acá la duda es máxima.
MAX_MIEMBROS_POR_CLUSTER = 8


def agrupar_duplicados(refs: list[str], pares: set[tuple[str, str]]) -> list[list[str]]:
    """Grupos donde **todos los pares** se parecen entre sí (enlace completo).

    ## Por qué no es transitivo, que es como estaba

    La versión anterior era union-find: *"si A=B y B=C, los tres son el mismo
    inmueble"*. Eso es cierto para una identidad y **falso para una relación con
    tolerancia**. Con ±5% de precio y ±2 m² de superficie, A≈B≈C≈D encadena
    extremos que no se parecen en nada — es clustering de enlace simple, y
    encadena por definición.

    Medido sobre los clusters que la versión anterior escribió en el corpus:

        n=198  pares posibles 19.503  pares que MATCHEAN 1.480 →  7,6%
        n= 94  pares posibles  4.371  pares que MATCHEAN   531 → 12,2%
        n= 74  pares posibles  2.701  pares que MATCHEAN   549 → 20,3%

    El de 198 iba de 23 a 73 m² y de USD 111.000 a 436.900, y el nodo 2 tomaba
    UN candidato de todo eso. 3.470 avisos vigentes quedaban invisibles para
    todo informe; en Palermo, el 35% del universo.

    Va en el mismo sentido que el criterio asimétrico del módulo: **fusionar dos
    inmuebles que no lo son borra un comparable legítimo**, y encadenar es la
    forma más eficiente de fusionar de más.

    ## El algoritmo

    Extracción golosa de cliques: se toma el aviso con más vecinos, se le suman
    los vecinos que se parecen a TODOS los ya elegidos, y se saca el grupo del
    juego. Es determinístico —el orden es por grado y después por ref— y a esta
    escala (grupos reales de 2-5) el resultado goloso coincide con el óptimo.

    Un aviso pertenece a un solo grupo: `listings.cluster_id` es una columna.
    """
    vecinos: dict[str, set[str]] = {r: set() for r in refs}
    for a, b in pares:
        if a in vecinos and b in vecinos:
            vecinos[a].add(b)
            vecinos[b].add(a)

    def por_grado(x: str) -> tuple[int, str]:
        return (-len(vecinos[x]), x)

    usados: set[str] = set()
    grupos: list[list[str]] = []
    descartados: list[int] = []
    for r in sorted(refs, key=por_grado):
        if r in usados or not vecinos[r]:
            continue
        grupo = [r]
        for candidato in sorted(vecinos[r] - usados - {r}, key=por_grado):
            # Enlace COMPLETO: tiene que parecerse a todos los que ya están.
            if all(candidato in vecinos[m] for m in grupo):
                grupo.append(candidato)
        if len(grupo) < 2:
            continue
        if len(grupo) > MAX_MIEMBROS_POR_CLUSTER:
            # Se descarta ENTERO y sus miembros salen del juego: quedan como
            # avisos independientes, que es lo correcto para unidades distintas
            # de un edificio.
            #
            # Salen del juego (y no quedan libres) a propósito. Si volvieran al
            # pool, el siguiente vértice formaría otro clique con un subconjunto
            # de los MISMOS avisos y habría que descartarlo de nuevo: probado, el
            # corpus daba 618 descartes sobre 8.490 avisos. Y peor: en algún
            # momento quedaría un subconjunto de 8 que sí se fusionaría, que es
            # exactamente el "elegir 8 al azar" que este tope evita.
            log.warning(
                "grupo demasiado grande para ser un inmueble: no se fusiona",
                miembros=len(grupo),
                tope=MAX_MIEMBROS_POR_CLUSTER,
                ejemplo=grupo[0],
            )
            usados.update(grupo)
            descartados.append(len(grupo))
            continue
        usados.update(grupo)
        grupos.append(sorted(grupo))
    if descartados:
        log.info(
            "grupos descartados por tamaño",
            cuantos=len(descartados),
            avisos_afectados=sum(descartados),
            mayor=max(descartados),
        )
    return grupos


def _payload_par(a: Candidate, b: Candidate) -> dict[str, Any]:
    def resumen(c: Candidate) -> str:
        partes = [f"dirección: {c.get('address')}", f"precio: USD {c.get('price')}"]
        for k in ("rooms", "surface_total", "surface_covered", "floor_number"):
            if c.get(k) is not None:
                partes.append(f"{k}: {c[k]}")
        return " · ".join(partes)

    return {
        "id": f"{a['listing_id']}|{b['listing_id']}",
        "a": resumen(a),
        "b": resumen(b),
        "desc_a": (a.get("description") or "")[:700],
        "desc_b": (b.get("description") or "")[:700],
    }


async def dedup_cluster(state: ReportState, cfg: NodeConfig) -> NodeResult:
    candidatos = [c for c in state.get("candidates") or [] if c.get("included", True)]
    if len(candidatos) < 2:
        return NodeResult(updates={}, detail={"pares": 0, "motivo": "menos de 2 candidatos"})

    por_ref = {c["listing_id"]: c for c in candidatos}
    iguales: set[tuple[str, str]] = set()
    por_capa = {"capa_1_exacta": 0, "capa_2_fuzzy": 0, "capa_3_juez": 0}
    grises: list[tuple[Candidate, Candidate]] = []

    for a, b in combinations(candidatos, 2):
        if _capa_1(a, b, cfg):
            iguales.add((a["listing_id"], b["listing_id"]))
            por_capa["capa_1_exacta"] += 1
        elif _capa_2(a, b, cfg):
            iguales.add((a["listing_id"], b["listing_id"]))
            por_capa["capa_2_fuzzy"] += 1
        elif _zona_gris(a, b, min_similitud=float(cfg.param("gray_zone_similarity", 0.45))):
            grises.append((a, b))

    # ── Capa 3: el juez, solo para la zona gris y con tope ───────────────
    ledger = UsageLedger()
    tope = int(cfg.param("max_llm_pairs", 15))
    consultados = grises[:tope]
    if len(grises) > tope:
        # NO se silencia: que un par no se haya evaluado tiene que verse.
        log.warning("pares dudosos por encima del tope", total=len(grises), tope=tope)

    if consultados:
        cliente = LlmClient()
        try:
            salida, usos = await cliente.structured(
                cfg.task or "judge",
                [
                    {
                        "role": "user",
                        "content": render(
                            cfg.prompt or "dedup_judge/v1",
                            aviso_injection=AVISO_INJECTION,
                            pares=[_payload_par(a, b) for a, b in consultados],
                        ),
                    }
                ],
                LoteDedup,
                temperature=float(cfg.param("temperature", 0.0)),
                max_tokens=int(cfg.param("max_tokens", 8192)),
                max_attempts=cfg.max_attempts,
            )
            ledger.extend(usos)
            for p in salida.pares:
                # `no_se` NO agrupa: ante la duda son distintos. Fusionar dos
                # inmuebles que no lo son borra un comparable legítimo; no
                # fusionar dos que sí lo son deja uno de más, que la mediana
                # diluye. Las consecuencias no son simétricas.
                if p.veredicto != "mismo" or "|" not in p.par:
                    continue
                ra, rb = p.par.split("|", 1)
                if ra in por_ref and rb in por_ref:
                    iguales.add((ra, rb))
                    por_capa["capa_3_juez"] += 1
        except (LlmValidationError, LlmError) as e:
            ledger.extend(getattr(e, "usos", []))
            log.warning("el juez de dedup no respondió", error=str(e)[:200])
        finally:
            await cliente.close()

    # ── Clusters y canónicos ─────────────────────────────────────────────
    grupos = agrupar_duplicados([c["listing_id"] for c in candidatos], iguales)
    duplicados = 0
    for i, grupo in enumerate(grupos):
        miembros = [por_ref[r] for r in grupo]
        canonico = elegir_canonico(miembros)
        for c in miembros:
            c["cluster_id"] = f"cluster-{i}"
            c["is_canonical"] = c["listing_id"] == canonico["listing_id"]
            if not c["is_canonical"]:
                # El nodo 6 lo descarta con `duplicado_de_cluster`; acá solo se
                # marca, para que la decisión quede en un solo lugar.
                duplicados += 1

    log.info(
        "deduplicación",
        report_id=state["report_id"],
        candidatos=len(candidatos),
        clusters=len(grupos),
        duplicados=duplicados,
    )

    return NodeResult(
        updates={
            "candidates": state.get("candidates") or [],
            "clusters": {"grupos": len(grupos), "duplicados": duplicados},
        },
        detail={
            "candidatos": len(candidatos),
            "pares_evaluados": len(candidatos) * (len(candidatos) - 1) // 2,
            "clusters": len(grupos),
            "duplicados_marcados": duplicados,
            "por_capa": por_capa,
            "pares_dudosos": len(grises),
            "pares_al_juez": len(consultados),
            "pares_sin_evaluar_por_tope": max(0, len(grises) - tope),
        },
        usage=ledger,
    )
