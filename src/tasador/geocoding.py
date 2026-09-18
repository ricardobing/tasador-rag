"""Geocodificación con Nominatim (OSM).

Gratis, sin API key y self-hosteable, que es por qué se eligió sobre Google
(doc 04, nodo 1). A cambio impone una política de uso que hay que respetar de
verdad, porque si no bloquean la IP:

  · máximo 1 request por segundo
  · User-Agent propio e identificable (uno genérico es motivo de bloqueo)
  · cachear del lado del cliente

Las tres están implementadas acá. El caché es en disco y no en Redis a
propósito: la geocodificación de una dirección **no caduca** —Cabildo 2530 va a
seguir estando donde está— y sobrevivir a un `docker compose down -v` vale más
que la elegancia.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from tasador.settings import get_settings

log = structlog.get_logger()


# Abreviaturas reales de los avisos y del panel. Se expanden porque Nominatim
# resuelve mucho mejor "Avenida Cabildo" que "Av Cabildo", y porque el mismo
# criterio se usa para comparar direcciones en el nodo 5 (dedup).
ABREVIATURAS = {
    "AV": "AVENIDA",
    "AVDA": "AVENIDA",
    "AVE": "AVENIDA",
    "GRAL": "GENERAL",
    "GRAL.": "GENERAL",
    "PJE": "PASAJE",
    "DR": "DOCTOR",
    "DRA": "DOCTORA",
    "PTE": "PRESIDENTE",
    "STA": "SANTA",
    "STO": "SANTO",
    "SAN MARTIN": "SAN MARTIN",
    "CNEL": "CORONEL",
    "ING": "INGENIERO",
    "ALM": "ALMIRANTE",
    "TTE": "TENIENTE",
    "BLVD": "BOULEVARD",
    "BV": "BOULEVARD",
}

# "al 2200" es como los portales publican una altura aproximada para no dar la
# dirección exacta. Es la forma MÁS común en Portal B (medido: 24 de 24 avisos
# de Belgrano la usan) y hay que entenderla, no descartarla.
_RE_AL_NUMERO = re.compile(r"\bAL\s+(\d{1,5})\b")
_RE_NUMERO = re.compile(r"\b(\d{1,5})\b")
_RE_UNIDAD = re.compile(r"\b(\d{1,3})\s*[°ºo]\s*([A-Z]{1,3})\b")


def sin_tildes(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


@dataclass(frozen=True, slots=True)
class DireccionNormalizada:
    original: str
    normalizada: str
    calle: str | None = None
    altura: str | None = None
    unidad: str | None = None
    altura_aproximada: bool = False

    @property
    def para_geocodificar(self) -> str:
        """Sin la unidad: a Nominatim el 4°B no le dice nada y empeora el match."""
        if self.calle and self.altura:
            return f"{self.calle} {self.altura}"
        return self.calle or self.normalizada


def normalizar_direccion(crudo: str) -> DireccionNormalizada:
    """Desarma una dirección de texto libre. Determinístico y sin red.

    No usa LLM a propósito: es un problema resuelto con reglas, y el nodo 1
    tiene que costar $0 (doc 04, nodo 1).
    """
    texto = " ".join(sin_tildes(crudo or "").upper().split())
    texto = texto.replace(",", " ").replace(".", ". ")
    texto = " ".join(texto.split())

    unidad = None
    if m := _RE_UNIDAD.search(texto):
        unidad = f"{m.group(1)}{m.group(2)}"
        texto = texto.replace(m.group(0), " ").strip()

    palabras = [ABREVIATURAS.get(p.rstrip("."), p.rstrip(".")) for p in texto.split()]
    normalizada = " ".join(w for w in palabras if w)

    altura = None
    aproximada = False
    if m := _RE_AL_NUMERO.search(normalizada):
        altura, aproximada = m.group(1), True
        calle = normalizada[: m.start()].strip()
    elif m := _RE_NUMERO.search(normalizada):
        altura = m.group(1)
        calle = normalizada[: m.start()].strip()
    else:
        calle = normalizada

    return DireccionNormalizada(
        original=crudo,
        normalizada=normalizada,
        calle=calle or None,
        altura=altura,
        unidad=unidad,
        altura_aproximada=aproximada,
    )


# Caja del AMBA (CABA + primer y segundo cordón). Cualquier resultado fuera de
# acá es un homónimo de otra provincia, no una coordenada nuestra.
#
# No es una optimización: es la diferencia entre "este barrio queda a 3 km" y
# "este barrio queda en Salta". Sin esta guarda, dos de 59 centroides entraron
# mal y NADIE se hubiera enterado hasta ver comparables absurdos en un informe.
AMBA_BBOX = (-35.10, -34.20, -59.10, -58.10)  # lat_min, lat_max, lng_min, lng_max


def dentro_del_amba(lat: float, lng: float) -> bool:
    lat_min, lat_max, lng_min, lng_max = AMBA_BBOX
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


Tipo = Literal["direccion", "lugar"]

# Se sube cuando cambia CÓMO se elige un resultado. Invalida los negativos
# cacheados, que pueden ser culpa de la lógica vieja y no de OSM.
#   1 -> primer resultado por `importance`
#   2 -> filtro por clase (lugar) / por calle y altura (dirección)
VERSION_SELECCION = 2

# Un barrio o localidad es un límite administrativo o un lugar poblado. Ni un
# comercio, ni un hospital, ni un faro — aunque se llamen igual.
CLASES_DE_LUGAR = frozenset({"place", "boundary"})


def _elegir_lugar(resultados: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Para barrios: filtro por CLASE de entidad.

    Un growshop llamado "Olivos" no es el barrio Olivos: el nombre coincide y
    la entidad no tiene nada que ver. Acá el tipo de entidad es la verificación.
    """
    candidatos = [
        r
        for r in resultados
        if (r.get("category") or r.get("class")) in CLASES_DE_LUGAR
        and dentro_del_amba(float(r["lat"]), float(r["lon"]))
    ]
    if not candidatos:
        return None
    return max(candidatos, key=lambda r: float(r.get("importance") or 0.0))


def _elegir_direccion(
    resultados: list[dict[str, Any]], calle: str | None, altura: str | None
) -> dict[str, Any] | None:
    """Para direcciones: filtro por CALLE Y ALTURA, no por clase de entidad.

    Acá el criterio se invierte respecto de los barrios, y el motivo es real:
    el único resultado de OSM para "Avenida Cabildo 2530" es un local de
    comidas rápidas. Su clase es `amenity`, pero está EN Avenida Cabildo 2530 —
    su `address` lo dice, y las coordenadas son las correctas. Un comercio en
    una dirección sí está en esa dirección; un comercio con nombre de barrio no
    es ese barrio.

    Filtrar por clase acá rechazaba direcciones perfectamente normales: toda
    dirección cuyo único registro en OSM sea un negocio. Medido el 13/08.
    """
    calle_n = _clave_calle(calle)
    puntuados: list[tuple[float, dict[str, Any]]] = []

    for r in resultados:
        if not dentro_del_amba(float(r["lat"]), float(r["lon"])):
            continue
        dir_ = r.get("address") or {}
        via = _clave_calle(dir_.get("road") or "")
        # Sin calle que verificar no hay forma de saber si el resultado es el
        # pedido: se prefiere no responder antes que responder cualquier cosa.
        if calle_n and via and not (calle_n in via or via in calle_n):
            continue
        puntaje = float(r.get("importance") or 0.0)
        if altura and str(dir_.get("house_number") or "") == str(altura):
            puntaje += 10  # la altura exacta manda sobre cualquier otra cosa
        if calle_n and via:
            puntaje += 5
        puntuados.append((puntaje, r))

    if not puntuados:
        return None
    return max(puntuados, key=lambda p: p[0])[1]


def _clave_calle(s: str | None) -> str:
    """Normaliza un nombre de calle para compararlo. "Avenida Crámer" y
    "AVENIDA CRAMER" son la misma calle."""
    limpio = "".join(c for c in sin_tildes(s or "") if c.isalnum() or c.isspace())
    return " ".join(limpio.upper().split())


def _elegir(
    resultados: list[dict[str, Any]],
    tipo: Tipo,
    *,
    calle: str | None = None,
    altura: str | None = None,
) -> dict[str, Any] | None:
    """Elige el resultado correcto. El criterio DEPENDE de qué se buscó.

    La caja del AMBA aplica a los dos casos: sin ella, "San Isidro" resuelve a
    Salta. Pero el segundo filtro es distinto — ver cada función.
    """
    if tipo == "lugar":
        return _elegir_lugar(resultados)
    return _elegir_direccion(resultados, calle, altura)


@dataclass(frozen=True, slots=True)
class Ubicacion:
    lat: float
    lng: float
    display_name: str = ""
    # Lo que OSM cree que es el barrio. Es una PISTA para resolverlo, no la
    # verdad: OSM usa sus propios nombres y a veces devuelve el sub-barrio.
    barrio_osm: str | None = None
    fuente: str = "NOMINATIM"
    confianza: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lat": self.lat,
            "lng": self.lng,
            "display_name": self.display_name,
            "barrio_osm": self.barrio_osm,
            "fuente": self.fuente,
            "confianza": self.confianza,
        }


class Geocoder:
    """Cliente de Nominatim con caché en disco y 1 req/s.

    `offline=True` responde solo desde el caché y nunca sale a la red: es como
    corren los tests, sin depender de un servicio externo ni ensuciar el
    rate limit de OSM.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        offline: bool = False,
        min_interval: float = 1.1,
    ) -> None:
        s = get_settings()
        self._base = s.nominatim_url.rstrip("/")
        self._ua = s.nominatim_user_agent
        # `settings.data_path` decide dónde se puede escribir: en el
        # contenedor /app es solo lectura y el volumen va en /data/raw.
        self._cache = cache_dir or (s.data_path / "geocode")
        self._cache.mkdir(parents=True, exist_ok=True)
        self._offline = offline
        self._min_interval = min_interval
        self._ultimo = 0.0
        self._lock = asyncio.Lock()

    def _archivo(self, clave: str) -> Path:
        h = hashlib.sha256(clave.encode("utf-8")).hexdigest()[:20]
        return self._cache / f"{h}.json"

    def _leer_cache(self, clave: str) -> Ubicacion | str | None:
        """Devuelve la ubicación, `"NEGATIVO"` si ya sabemos que no resuelve, o
        None si nunca se consultó.

        Cachear los negativos importa: sin eso, una dirección mal escrita se
        vuelve a consultar en cada reintento y se come el rate limit.

        **Pero un negativo caduca cuando cambia la lógica de selección.** Un
        "no encontré" viejo puede ser culpa nuestra y no de OSM: "Av. Cabildo
        2530" quedó cacheada como negativa solo porque el filtro por clase de
        entidad estaba mal, y el caché la mantenía inaccesible aun después de
        arreglarlo. Los POSITIVOS no caducan: una coordenada no cambia.
        """
        p = self._archivo(clave)
        if not p.exists():
            return None
        datos = json.loads(p.read_text(encoding="utf-8"))
        if not datos.get("encontrado"):
            if int(datos.get("version_seleccion", 0)) < VERSION_SELECCION:
                return None  # se vuelve a consultar con la lógica nueva
            return "NEGATIVO"
        u = datos["ubicacion"]
        return Ubicacion(**u)

    def _escribir_cache(self, clave: str, u: Ubicacion | None) -> None:
        self._archivo(clave).write_text(
            json.dumps(
                {
                    "consulta": clave,
                    "encontrado": u is not None,
                    "version_seleccion": VERSION_SELECCION,
                    "ubicacion": u.as_dict() if u else None,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    async def _esperar_turno(self) -> None:
        """1 req/s es la política de OSM y se respeta de verdad."""
        transcurrido = time.monotonic() - self._ultimo
        if transcurrido < self._min_interval:
            await asyncio.sleep(self._min_interval - transcurrido)
        self._ultimo = time.monotonic()

    async def geocodificar(
        self,
        consulta: str,
        *,
        ciudad: str = "CABA",
        provincia: str | None = None,
        tipo: Tipo = "direccion",
        calle: str | None = None,
        altura: str | None = None,
    ) -> Ubicacion | None:
        # La provincia va en la clave y en la consulta: sin ella, Nominatim
        # devuelve homónimos de otras provincias. Medido el 13/08 —
        # "San Isidro" resolvió a Salta y "San Fernando" a Córdoba.
        partes = [consulta, ciudad, provincia, "Argentina"]
        texto = ", ".join(p for p in partes if p)
        clave = f"{tipo}|{texto}".upper()

        cacheado = self._leer_cache(clave)
        if cacheado == "NEGATIVO":
            return None
        if isinstance(cacheado, Ubicacion):
            return cacheado

        if self._offline:
            log.debug("geocoder offline y sin caché", consulta=clave)
            return None

        # Una sola consulta a la vez: el rate limit es por cliente, no por
        # corrutina.
        params: dict[str, str] = {
            "q": texto,
            "format": "jsonv2",
            "addressdetails": "1",
            # 10 y no 1: el primer resultado se ordena por `importance`, y una
            # calle homónima de otro partido le gana a la localidad que
            # buscamos. Medido: "Olivos" con limit=1 devolvía una calle de
            # La Matanza a 20 km.
            "limit": "10",
            "countrycodes": "ar",
        }
        if tipo == "lugar":
            # Restringe a entidades de asentamiento. Sin esto, "Olivos"
            # matchea una clínica, un growshop y un faro — los tres con ese
            # nombre y ninguno es el barrio.
            params["featureType"] = "settlement"

        async with self._lock:
            await self._esperar_turno()
            try:
                async with httpx.AsyncClient(timeout=20, headers={"User-Agent": self._ua}) as c:
                    r = await c.get(f"{self._base}/search", params=params)
                    r.raise_for_status()
                    resultados = r.json()
            except Exception:
                # Que Nominatim esté caído NO se cachea como negativo: sería
                # envenenar el caché con un problema temporal.
                log.warning("nominatim falló", consulta=clave, exc_info=True)
                return None

        elegido = _elegir(resultados, tipo, calle=calle, altura=altura)
        if elegido is None:
            self._escribir_cache(clave, None)
            return None

        dir_ = elegido.get("address") or {}
        u = Ubicacion(
            lat=float(elegido["lat"]),
            lng=float(elegido["lon"]),
            display_name=elegido.get("display_name", "")[:300],
            barrio_osm=(
                dir_.get("neighbourhood") or dir_.get("suburb") or dir_.get("city_district")
            ),
            fuente="NOMINATIM",
            confianza=float(elegido.get("importance") or 0.0),
        )
        self._escribir_cache(clave, u)
        return u


def distancia_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """Haversine, en metros.

    Se implementa acá en vez de usar `earthdistance` de Postgres porque esa
    extensión no está instalada y agregarla obligaría a recrear el volumen.
    Para distancias de barrio (< 20 km) el error de la fórmula es
    despreciable frente al de usar centroides.
    """
    from math import asin, cos, radians, sin, sqrt

    r = 6371008.8
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return int(2 * r * asin(sqrt(a)))
