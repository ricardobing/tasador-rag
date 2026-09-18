"""Ingesta de los CSV/JSON que produce un recolector externo.

Un proceso ajeno a este repo deja en una carpeta un archivo por corrida
(`<portal>_venta_departamento_palermo_2026-08-14.csv`). Este módulo escanea la
carpeta, incorpora lo nuevo y **se puede volver a correr las veces que haga
falta**: es la forma que va a tener el corpus de crecer de acá en adelante, no
una carga de una sola vez.

## La decisión de diseño: cero lógica nueva

Lo que hace este módulo es traducir una fila de CSV a una `Card` y
después llamar a `motivo_descarte()` y `_upsert()` — el único camino de
ingesta que existe. No reimplementa el descarte temprano, ni la superficie
ponderada, ni el hash de contenido, ni el snapshot de precio.

Eso importa más de lo que parece. La Etapa 3 dejó un bug memorable de esta
familia: el eval del nodo 4 llamaba a `structured()` pelado, sin el reintento
que el nodo sí tenía, y medía un fragmento del sistema creyendo que medía el
sistema. Dos caminos de ingesta con dos reglas de descarte distintas serían el
mismo error, con la diferencia de que ensuciarían el corpus en vez de una
métrica.

## Idempotencia, en dos niveles

1. **Por archivo** — se guarda el SHA-256 en `corpus.ingest_runs.detail`. Un
   archivo ya procesado y sin cambios se saltea sin abrirlo dos veces.
2. **Por aviso** — `_upsert` compara `content_hash`. Si el aviso no cambió,
   solo actualiza `last_seen_at`; si cambió el precio, agrega un
   `listing_snapshot`. El historial de precios se construye solo, corrida a
   corrida, que es de donde sale la métrica de "cuántos bajaron" del nodo 8.

Un CSV nuevo del mismo barrio la semana que viene no duplica nada: actualiza lo
que sigue publicado y agrega lo que apareció.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.db.models import IngestItem, IngestRun
from tasador.ingest.cards import Card
from tasador.ingest.core import CaptureStats, _neighborhood_ids, _upsert, motivo_descarte

log = structlog.get_logger()

# El `portal` del CSV -> el vocabulario cerrado de `corpus.listings.source`.
#
# El mapeo NO vive en el código: sale de la variable de entorno `PORTALES`
# (`"nombre-en-el-csv=PORTAL_A,otro=PORTAL_B"`). Qué portales alimentan una
# instancia es una decisión de quien la opera, igual que el resto de la
# ingesta (doc 10 §2); el producto solo conoce los rótulos neutros. Los datasets
# públicos sí tienen nombre propio.
_PORTALES_FIJOS = {
    "mercadolibre": "MELI",
    "meli": "MELI",
    "properati": "PROPERATI",
}


def portales() -> dict[str, str]:
    """El mapeo completo: los públicos más los que declara el entorno."""
    from tasador.settings import get_settings

    out = dict(_PORTALES_FIJOS)
    for par in get_settings().portales.split(","):
        if "=" in par:
            nombre, fuente = par.split("=", 1)
            if nombre.strip() and fuente.strip():
                out[nombre.strip().lower()] = fuente.strip().upper()
    return out


# Las claves que hacen que un archivo sea "de avisos". Es lo mínimo con lo que
# `fila_a_card` puede producir algo: sin id no hay upsert idempotente y sin url
# no hay a dónde volver.
CLAVES_REQUERIDAS = ("portal_property_id",)
CLAVES_DE_URL = ("url", "canonical_url")


def parece_de_avisos(filas: list[dict[str, str]]) -> bool:
    """¿Este archivo tiene avisos, o es otra cosa que pasaba por ahí?

    El escaneo recursivo (14/08) empezó a encontrar los `metadata.json` de los
    perfiles de Chrome que usa el scraper. Sin este filtro cada uno producía un
    `IngestRun` con una fila en `PARSE_ERROR`: ruido en `/admin/fuentes`, que es
    justo la pantalla que existe para avisar cuando la ingesta se rompe. Un
    tablero con errores permanentes que no significan nada es un tablero que
    nadie mira.

    Se decide por CONTENIDO y no por la ruta ni el nombre. El scraper ya cambió
    su estructura de carpetas una vez —de `output/` a una por corrida— y va a
    volver a cambiarla; las claves del archivo, no.
    """
    if not filas:
        return False
    claves = set(filas[0])
    return all(k in claves for k in CLAVES_REQUERIDAS) and any(k in claves for k in CLAVES_DE_URL)


@dataclass(slots=True)
class ScanStats:
    archivos_vistos: int = 0
    archivos_nuevos: int = 0
    archivos_salteados: int = 0
    archivos_ajenos: int = 0
    archivos_con_error: int = 0
    filas: int = 0
    stats: CaptureStats = field(default_factory=CaptureStats)

    def resumen(self) -> str:
        s = self.stats
        return (
            f"{self.archivos_nuevos} archivos nuevos ({self.archivos_salteados} ya estaban) · "
            f"{self.filas} filas · {s.descartados} descartadas · "
            f"{s.nuevos} avisos nuevos · {s.actualizados} actualizados · "
            f"{s.sin_cambios} sin cambios"
        )


# ── Conversión de tipos ──────────────────────────────────────────────────
def _dec(v: str | None) -> Decimal | None:
    """Un CSV no tiene tipos: todo llega como texto, y "" no es 0.

    Devolver 0 ante un campo vacío sería el peor default posible acá: un precio
    de 0 pasa los CHECK de la base y produce un USD/m² de 0 que arrastra la
    mediana del barrio hacia abajo sin que nada se queje.
    """
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    try:
        return Decimal(v)
    except (InvalidOperation, ValueError):
        return None


def _int(v: str | None) -> int | None:
    d = _dec(v)
    return int(d) if d is not None else None


def _txt(v: str | None) -> str | None:
    return v.strip() or None if v else None


# El vocabulario de doc 05 §4.1. El portal escribe "Frente" y el motor espera
# "frente": `_enum(Orientation, "Frente")` devuelve None, así que sin
# normalizar el dato entra y no ajusta nada.
_ORIENTACIONES = {"frente", "contrafrente", "lateral", "interno"}


def _fecha(v: str | None) -> date | None:
    """`"2026-07-07"` -> `date(2026, 7, 7)`. `None` si no se puede leer.

    El scraper la emite en ISO —verificado sobre los 49 avisos vigentes que la
    traen, todos de Portal B— y se aceptan también los dos formatos que suelen
    aparecer si eso cambia. Una fecha ilegible es *sin dato*, no un error: mismo
    criterio que `_dec` y que "sin dato, sin ajuste" (doc 05 §4.2).

    Y **no se acepta una fecha futura**: sería un aviso publicado mañana, que es
    un error del origen, y produciría `days_published` negativo — que en
    `listing_age_coef` cae en la primera banda y en `f_freshness` da un valor
    fuera de rango.
    """
    t = _txt(v)
    if not t:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            d = datetime.strptime(t[:10], formato).replace(tzinfo=UTC).date()
        except ValueError:
            continue
        return None if d > datetime.now(UTC).date() else d
    return None


def _orientacion_del_portal(fila: dict[str, str]) -> str | None:
    """La orientación que declara el aviso, si está.

    ⚠️ **Sale de la columna `condition` del scraper, no de `orientation`.** No
    es un typo: el scraper llama `condition` a lo que publica Portal B como
    "Frente / Contrafrente / Lateral / Interno" —que es exactamente nuestra
    ORIENTACIÓN— y usa `orientation` para el punto cardinal ("E", "NO"), que
    no está en el vocabulario de doc 05 y no mueve ningún coeficiente.

    Medido el 14/08 sobre el corpus: 31 avisos traían la orientación declarada
    por el portal y ninguno llegaba al motor. Se leen las dos columnas y se
    acepta la que caiga en el vocabulario, así que si el scraper corrige el
    nombre mañana, esto sigue funcionando.
    """
    for columna in ("condition", "orientation"):
        v = _txt(fila.get(columna))
        if v and v.strip().lower() in _ORIENTACIONES:
            return v.strip().lower()
    return None


# El vocabulario del portal -> el de doc 05 §4.1. Solo equivalencias directas:
# "Regular" NO está y se deja sin mapear a propósito. Cae entre `bueno` (0,94) y
# `a_refaccionar` (0,82), y elegir cuál es inventar un 12% sobre el precio de
# alguien. Sin dato no hay ajuste, que es la regla del método.
_CONDICIONES = {
    "a estrenar": "a_estrenar",
    "estrenar": "a_estrenar",
    "excelente": "excelente",
    "muy bueno": "muy_bueno",
    "bueno": "bueno",
    "a refaccionar": "a_refaccionar",
    "a reciclar": "a_refaccionar",
    "refaccionar": "a_refaccionar",
}


def _condicion_del_portal(fila: dict[str, str]) -> str | None:
    """El ESTADO que declara el aviso, si está.

    ⚠️ Sale de la misma columna `condition` de la que
    `_orientacion_del_portal` saca la orientación, y no es una contradicción:
    **el scraper mete dos vocabularios en esa columna**. Medido sobre los 936
    avisos con ficha de detalle del 15/08:

        Excelente 401 · Muy Bueno 276 · Bueno 87 · A Refaccionar 14 · Regular 5
        Frente 50 · Contrafrente 20 · Interno 2 · Lateral 1
        Norte 44   (punto cardinal, no es ninguna de las dos cosas)

    Cada función se lleva lo que pertenece a SU vocabulario y descarta el
    resto. Hasta el 15/08 solo existía la de orientación, así que los 783
    estados reales se tiraban — y `condition` es el coeficiente más grande del
    método: de 1,15 a 0,82, un 40% de amplitud.

    Un aviso de la tarjeta (sin ficha) no trae estado: 0% contra 93,8%. Por eso
    la tanda enriquecida vale lo que vale.
    """
    v = _txt(fila.get("condition"))
    if not v:
        return None
    return _CONDICIONES.get(v.strip().lower())


def fila_a_card(fila: dict[str, str]) -> Card | None:
    """Una fila del CSV -> la misma tarjeta que produce el parser de HTML.

    Se reusa `Card` incluso para otros portales: lo único que aporta
    es la fórmula de superficie ponderada de doc 05 §2 y las propiedades
    derivadas, que son del MÉTODO y no del portal. Tener una clase por portal
    con la misma fórmula copiada es exactamente cómo dos verdades se separan.
    """
    source_id = _txt(fila.get("portal_property_id"))
    url = _txt(fila.get("canonical_url")) or _txt(fila.get("url"))
    if not source_id or not url:
        return None

    # `raw_data` trae el JSON crudo del scraper. Se conserva entero: reprocesar
    # es gratis, volver a scrapear cuesta y puede ya no estar publicado.
    crudo: dict[str, Any] = {}
    if bruto := _txt(fila.get("raw_data")):
        try:
            crudo = json.loads(bruto)
        except (json.JSONDecodeError, TypeError):
            crudo = {"raw_data_no_parseable": bruto[:500]}

    for campo in ("orientation", "condition", "floor", "amenities", "publication_date"):
        if v := _txt(fila.get(campo)):
            crudo[campo] = v

    return Card(
        orientation=_orientacion_del_portal(fila),
        # El estado declarado por el portal. Ver `_condicion_del_portal`: sale
        # de la MISMA columna que la orientación porque el scraper mezcla los
        # dos vocabularios ahí.
        condition=_condicion_del_portal(fila),
        # `detail_fetched` es lo que separa un aviso que sabe su estado de uno
        # que no. Ver el campo en `Card`.
        ficha_completa=(_txt(fila.get("detail_fetched")) or "").strip().lower() == "true",
        source_id=source_id,
        url=url,
        price=_dec(fila.get("price")),
        currency=_txt(fila.get("currency")),
        expenses_ars=_dec(fila.get("expenses"))
        if _txt(fila.get("expenses_currency")) == "ARS"
        else None,
        address=_txt(fila.get("address")),
        neighborhood_label=_txt(fila.get("neighborhood")),
        publisher=_txt(fila.get("publisher_name")),
        surface_covered=_dec(fila.get("covered_area")),
        surface_semi=_dec(fila.get("semi_covered_area")),
        surface_total=_dec(fila.get("total_area")),
        rooms=_int(fila.get("rooms")),
        bedrooms=_int(fila.get("bedrooms")),
        bathrooms=_int(fila.get("bathrooms")),
        age_years=_int(fila.get("age")),
        parking=_int(fila.get("parking_count")),
        # La fecha de publicación del portal. Iba solo a `raw.attrs` y no
        # llegaba a `listings.published_at`, apagando el coeficiente de
        # antigüedad del aviso, la frescura de la confianza y la regla
        # `aviso_vencido` — tres mecanismos, un campo (ver `Card`).
        published_at=_fecha(fila.get("publication_date")),
        title=_txt(fila.get("title")),
        # La descripción es lo que va a leer el nodo 4 para sacar estado,
        # orientación y piso. Sin ella el aviso entra al corpus pero no aporta
        # un solo coeficiente de ajuste.
        description=_txt(fila.get("description")),
        raw_attrs=crudo,
    )


def _portal_de(fila: dict[str, str], archivo: Path) -> str | None:
    """El portal sale de la columna; el nombre del archivo es el respaldo."""
    mapa = portales()
    if (p := _txt(fila.get("portal"))) and (s := mapa.get(p.lower())):
        return s
    for clave, valor in mapa.items():
        if archivo.name.lower().startswith(clave):
            return valor
    return None


def sha256_de(archivo: Path) -> str:
    h = hashlib.sha256()
    with archivo.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 16), b""):
            h.update(bloque)
    return h.hexdigest()


async def _ya_procesado(session: AsyncSession, sha: str) -> bool:
    """Por HASH y no por nombre de archivo.

    El scraper puede reescribir el mismo nombre con contenido nuevo (mismo
    barrio, otro día) o dejar dos nombres con el mismo contenido. El hash
    responde la pregunta que importa —¿ya vi ESTE contenido?— y el nombre no.
    """
    fila = (
        await session.execute(
            select(IngestRun.id).where(
                IngestRun.mode == "BACKFILL",
                IngestRun.status.in_(("OK", "PARTIAL")),
                IngestRun.detail["sha256"].astext == sha,
            )
        )
    ).first()
    return fila is not None


def leer_filas(archivo: Path) -> list[dict[str, str]]:
    """CSV, JSON o JSONL — el scraper emite los tres con el MISMO esquema.

    Verificado el 14/08: las 47 claves del `.json` son idénticas a las columnas
    del `.csv`. Aceptar los tres no es completismo: si mañana una tanda sale
    solo en `.jsonl` —porque el proceso se cortó a la mitad y ese es el formato
    que se escribe incremental— el corpus no se pierde por el formato.

    Todo se normaliza a `dict[str, str]` porque un CSV no tiene tipos y la
    conversión ya vive en `fila_a_card`. Un JSON con `null` y un CSV con `""`
    tienen que producir exactamente el mismo aviso, y la forma de garantizarlo
    es que lleguen iguales a la única función que convierte.
    """
    sufijo = archivo.suffix.lower()
    try:
        return _leer(archivo, sufijo)
    except (UnicodeDecodeError, json.JSONDecodeError):
        # NO es un error de este proceso: un archivo que ni siquiera decodifica
        # como UTF-8 nunca fue nuestro. Son los `.json` binarios de las
        # extensiones de Chrome que el scraper deja en su perfil, y el escaneo
        # recursivo los encuentra a montones (11 el 14/08).
        #
        # Contarlos como "errores" ensucia la traza de `/admin/fuentes`, que es
        # justo la pantalla que existe para avisar cuando la ingesta se rompe.
        # Un CSV NUESTRO corrupto sí decodifica y sí llega hasta el chequeo de
        # esquema, así que esto no esconde nada que importe.
        log.debug("archivo ilegible, no es nuestro", archivo=archivo.name)
        return []


def _leer(archivo: Path, sufijo: str) -> list[dict[str, str]]:
    if sufijo == ".csv":
        with archivo.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    crudas: list[Any]
    if sufijo == ".jsonl":
        crudas = []
        with archivo.open(encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    crudas.append(json.loads(linea))
    elif sufijo == ".json":
        with archivo.open(encoding="utf-8") as f:
            datos = json.load(f)
        crudas = datos if isinstance(datos, list) else [datos]
    else:
        return []

    return [
        {
            k: ("" if v is None else v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            for k, v in fila.items()
        }
        for fila in crudas
        if isinstance(fila, dict)
    ]


async def ingerir_archivo(
    session: AsyncSession, archivo: Path, *, forzar: bool = False
) -> tuple[IngestRun | None, CaptureStats]:
    """Un archivo -> el corpus. Devuelve `None` si ya estaba procesado."""
    st = CaptureStats(paginas=1)
    sha = sha256_de(archivo)

    if not forzar and await _ya_procesado(session, sha):
        return None, st

    filas = leer_filas(archivo)

    if not filas:
        # Pasa de verdad y hay que verlo: los CSV de Portal B del 14/08 tenían
        # 517 bytes de puro encabezado porque el portal devolvió CAPTCHA. Un
        # archivo vacío no es un error de este proceso, pero tampoco es normal.
        log.warning("archivo sin filas", archivo=archivo.name)
        return None, st

    if not parece_de_avisos(filas):
        # Ni `IngestRun` ni `IngestItem`: este archivo no es nuestro. Registrar
        # un error por algo que nunca pretendió ser un aviso ensucia la traza.
        log.debug("archivo ignorado, no tiene esquema de avisos", archivo=archivo.name)
        st.motivos["archivo_ajeno"] += 1
        return None, st

    fuente = _portal_de(filas[0], archivo) or "MANUAL"
    barrios = await _neighborhood_ids(session)

    run = IngestRun(
        source=fuente,
        # BACKFILL y no DISCOVER: los avisos no los descubrió este proceso.
        # Mentir sobre el modo rompe la lectura de `/admin/fuentes`, que usa
        # `blocked/discovered` para detectar que un portal cambió su defensa.
        mode="BACKFILL",
        status="RUNNING",
        detail={"archivo": archivo.name, "sha256": sha, "filas": len(filas)},
    )
    session.add(run)
    await session.flush()

    st.vistos = len(filas)
    for i, fila in enumerate(filas):
        card = fila_a_card(fila)
        if card is None:
            st.errores += 1
            session.add(
                IngestItem(
                    run_id=run.id,
                    url=_txt(fila.get("url")) or f"{archivo.name}#{i}",
                    status="PARSE_ERROR",
                    error="sin portal_property_id o sin url",
                )
            )
            continue

        # EL MISMO descarte temprano que la captura por HTML. El costo de un
        # aviso inservible no es la fila: es la extracción por LLM que se le va
        # a correr después y que nunca va a servir (doc 17 §5).
        if (motivo := motivo_descarte(card)) is not None:
            st.descartados += 1
            st.motivos[motivo] += 1
            continue

        await _upsert(session, _portal_de(fila, archivo) or fuente, card, barrios, st)

    run.discovered = st.vistos
    run.created = st.nuevos
    run.updated = st.actualizados
    run.skipped = st.descartados + st.sin_cambios
    run.errors = st.errores
    run.status = "OK" if not st.errores else "PARTIAL"
    run.finished_at = datetime.now(UTC)
    run.detail = {**run.detail, "motivos": dict(st.motivos)}
    await session.commit()
    return run, st


# Los tres formatos que emite el scraper, en orden de preferencia. Si una
# tanda tiene el mismo aviso en `.csv` y en `.json`, se ingiere UNA vez: son el
# mismo dato en dos envases y el `content_hash` lo confirmaría, pero pagar dos
# lecturas y dos `IngestRun` para llegar a "sin cambios" ensucia la traza de
# `/admin/fuentes`, que es de donde sale la salud de la ingesta.
FORMATOS = (".csv", ".jsonl", ".json")

# Archivos del scraper que NO son avisos. `_failures.json` es su registro de
# errores —CAPTCHA, timeouts— y tiene otro esquema por completo: si entrara,
# produciría 8 filas sin `portal_property_id` contadas como PARSE_ERROR, o sea
# ruido en la traza que parece un problema nuestro.
IGNORAR = ("_failures",)


def _archivos_a_ingerir(raiz: Path) -> list[Path]:
    """Los archivos con avisos, recorriendo subcarpetas y sin duplicar tandas.

    **Recursivo desde el 14/08.** El scraper dejó de usar una sola carpeta:
    ahora crea una por corrida (`output/`, `output_zp_t1/`, …). Con un `glob`
    plano, una tanda nueva en una carpeta nueva no se ingería y no había señal
    de nada — el escaneo decía "0 archivos nuevos" y era cierto para la carpeta
    que estaba mirando.

    Se deduplica por nombre-sin-extensión DENTRO de cada carpeta, quedándose
    con el primer formato disponible de `FORMATOS`.
    """
    por_tanda: dict[tuple[Path, str], Path] = {}
    for p in sorted(raiz.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in FORMATOS:
            continue
        if any(marca in p.stem for marca in IGNORAR):
            continue
        clave = (p.parent, p.stem)
        actual = por_tanda.get(clave)
        if actual is None or FORMATOS.index(p.suffix.lower()) < FORMATOS.index(
            actual.suffix.lower()
        ):
            por_tanda[clave] = p
    return sorted(por_tanda.values())


async def escanear(
    session: AsyncSession, carpeta: Path, *, forzar: bool = False, patron: str | None = None
) -> ScanStats:
    """Recorre la carpeta —y sus subcarpetas— e incorpora lo que no está.

    Pensado para correrse periódicamente (a mano o desde `ops/crontab`): el
    caso normal es que la mayoría de los archivos ya estén y solo entren los
    nuevos.

    `patron` fuerza un glob explícito y desactiva la detección de formatos. Es
    la salida para un caso puntual, no el camino normal.
    """
    total = ScanStats()
    # `glob` toca disco y esto es una corrutina: al hilo. Son milisegundos
    # sobre una carpeta local, pero este módulo lo puede llamar el worker —que
    # sí atiende otras cosas— y no solo un script que corre solo.
    import asyncio

    if patron:
        archivos = sorted(
            await asyncio.to_thread(lambda: [p for p in carpeta.rglob(patron) if p.is_file()])
        )
    else:
        archivos = await asyncio.to_thread(_archivos_a_ingerir, carpeta)
    total.archivos_vistos = len(archivos)

    for archivo in archivos:
        try:
            run, st = await ingerir_archivo(session, archivo, forzar=forzar)
        except Exception:
            log.exception("falló la ingesta del archivo", archivo=archivo.name)
            total.archivos_con_error += 1
            await session.rollback()
            continue

        if run is None:
            if st.motivos.get("archivo_ajeno"):
                total.archivos_ajenos += 1
            else:
                total.archivos_salteados += 1
            continue

        total.archivos_nuevos += 1
        total.filas += st.vistos
        total.stats.vistos += st.vistos
        total.stats.nuevos += st.nuevos
        total.stats.actualizados += st.actualizados
        total.stats.sin_cambios += st.sin_cambios
        total.stats.descartados += st.descartados
        total.stats.cambios_precio += st.cambios_precio
        total.stats.errores += st.errores
        total.stats.motivos.update(st.motivos)
        log.info("archivo ingerido", archivo=archivo.name, resumen=st.resumen())

    return total
