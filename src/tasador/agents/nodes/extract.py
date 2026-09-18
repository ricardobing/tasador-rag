"""Nodo 4 — `extract_features`. El de mayor impacto medido.

El backtest de la Etapa 2 dejó una conclusión incómoda: **el motor de ajustes
se midió apagado**. BA Data no trae estado, antigüedad, orientación ni piso, así
que los coeficientes valieron 1,00 en las 1.500 corridas. Este nodo los
enciende, sacando esos campos de la prosa del aviso.

Y hay evidencia fresca de que hace falta: los 22 comparables de Belgrano
capturados de Portal B van de 1.742 a 6.600 USD/m² — 3,8x de diferencia, mismo
barrio, mismos ambientes, superficie parecida. Sin saber cuál está a
refaccionar y cuál es a estrenar, esa dispersión es irreducible.

## La cita textual

Cada campo que mueve el precio (`condition`, `orientation`, `floor_number`,
`has_elevator`, `age_years`) viene con el fragmento del aviso que lo justifica,
y **ese fragmento se verifica contra el texto original sin LLM**. Si el modelo
no puede señalar dónde lo leyó, o señala algo que no está, el campo pierde
confianza y no ajusta el precio.

Es la diferencia entre confiar en un modelo y poder auditarlo. Un
`condition: "a_refaccionar"` sin cita verificable es una opinión; con la cita
"necesita refacción en cocina y baño" presente en el aviso, es un dato.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeResult
from tasador.agents.prompts import render
from tasador.agents.state import Candidate, ReportState
from tasador.db.models import ListingFeatures
from tasador.geocoding import sin_tildes
from tasador.llm import (
    AVISO_INJECTION,
    LlmClient,
    LlmError,
    LlmUsage,
    LlmValidationError,
    UsageLedger,
    wrap_external,
)

log = structlog.get_logger()

PropertyType = Literal[
    "departamento", "casa", "ph", "local", "oficina", "cochera", "terreno", "galpon", "otro"
]
Condition = Literal["a_estrenar", "excelente", "muy_bueno", "bueno", "a_refaccionar"]
Orientation = Literal["frente", "contrafrente", "lateral", "interno"]

# Los cinco campos que mueven el precio y por lo tanto exigen cita textual.
# La lista NO es arbitraria: es exactamente el conjunto de entradas de los
# coeficientes de doc 05 §4.1.
CON_CITA = ("condition", "orientation", "floor_number", "has_elevator", "age_years")

# Confianza según qué tan verificable resultó el campo.
CONF_VERIFICADA = 1.0  # el modelo citó y la cita está en el aviso
CONF_SIN_CITA = 0.4  # dio un valor pero no pudo señalar de dónde
CONF_CITA_FALSA = 0.0  # citó algo que NO está en el aviso


class FeaturesExtraidas(BaseModel):
    """Lo que el modelo puede devolver por aviso. Campos cerrados: no hay
    ningún campo de texto libre donde inyectar una instrucción."""

    model_config = ConfigDict(extra="forbid")

    listing_ref: str

    property_type: PropertyType | None = None
    rooms: int | None = Field(default=None, ge=1, le=20)
    bedrooms: int | None = Field(default=None, ge=0, le=12)
    bathrooms: int | None = Field(default=None, ge=0, le=10)
    surface_total: float | None = Field(default=None, gt=0, le=5000)
    surface_covered: float | None = Field(default=None, gt=0, le=5000)

    # ── los que mueven el precio: valor + de dónde lo sacó ──
    condition: Condition | None = None
    condition_cita: str | None = None
    orientation: Orientation | None = None
    orientation_cita: str | None = None
    floor_number: int | None = Field(default=None, ge=-5, le=200)
    floor_cita: str | None = None
    has_elevator: bool | None = None
    elevator_cita: str | None = None
    age_years: int | None = Field(default=None, ge=0, le=200)
    age_cita: str | None = None

    parking_spaces: int | None = Field(default=None, ge=0, le=10)
    balcony: bool | None = None
    amenities: list[str] = Field(default_factory=list, max_length=15)
    expenses_ars: float | None = Field(default=None, ge=0, le=100_000_000)
    credit_eligible: bool | None = None


class LoteExtraido(BaseModel):
    model_config = ConfigDict(extra="forbid")

    avisos: list[FeaturesExtraidas]


def _cita_de(campo: str) -> str:
    return {
        "floor_number": "floor_cita",
        "has_elevator": "elevator_cita",
        "age_years": "age_cita",
    }.get(campo, f"{campo}_cita")


def _normalizar_texto(s: str) -> str:
    """Para comparar una cita con el aviso: sin tildes, minúsculas, espacios
    colapsados. Un modelo que reescribe «refacción» como «refaccion» citó
    bien; no vamos a castigarlo por un acento."""
    return " ".join(sin_tildes(s or "").lower().split())


def verificar_citas(f: FeaturesExtraidas, texto_aviso: str) -> dict[str, float]:
    """Confianza por campo, verificada CONTRA EL AVISO y sin LLM.

    Esta función es la que convierte la salida de un modelo en un dato
    auditable. No le pregunta al modelo qué tan seguro está —los modelos son
    malos estimando su propia confianza— sino que **comprueba** si lo que dijo
    haber leído está efectivamente escrito.
    """
    fuente = _normalizar_texto(texto_aviso)
    confianzas: dict[str, float] = {}

    for campo in CON_CITA:
        if getattr(f, campo) is None:
            continue  # sin valor no hay nada que verificar
        cita = getattr(f, _cita_de(campo), None)
        if not cita or not cita.strip():
            confianzas[campo] = CONF_SIN_CITA
            continue
        confianzas[campo] = (
            CONF_VERIFICADA if _normalizar_texto(cita) in fuente else CONF_CITA_FALSA
        )

    return confianzas


def _texto_del_aviso(c: Candidate) -> str:
    partes = [c.get("address") or "", c.get("description") or ""]
    return "\n".join(p for p in partes if p)


def content_hash(c: Candidate) -> str:
    """Identidad del CONTENIDO que se le manda al modelo.

    Si dos avisos tienen el mismo texto, la extracción es la misma y no se
    paga dos veces. Y si un aviso cambia su descripción, el hash cambia y se
    reprocesa solo.
    """
    return hashlib.sha256(_texto_del_aviso(c).encode("utf-8")).hexdigest()


def _dec(v: float | None) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError):
        return None


def _aplicar(
    c: Candidate, f: FeaturesExtraidas, confianzas: dict[str, float], umbral: float
) -> None:
    """Vuelca lo extraído al candidato. NO pisa lo que ya venía del portal.

    El dato estructurado del portal es un HECHO que publicó la inmobiliaria;
    lo del modelo es una INTERPRETACIÓN del texto. Ante conflicto gana el
    hecho. El modelo llena huecos, no corrige.
    """
    simples = (
        "property_type",
        "rooms",
        "bedrooms",
        "bathrooms",
        "parking_spaces",
        "balcony",
        "credit_eligible",
    )
    for campo in simples:
        if c.get(campo) is None and (v := getattr(f, campo)) is not None:
            c[campo] = v  # type: ignore[literal-required]

    for campo in ("surface_total", "surface_covered", "expenses_ars"):
        if c.get(campo) is None and (v := _dec(getattr(f, campo))) is not None:
            c[campo] = str(v)  # type: ignore[literal-required]

    if f.amenities and not c.get("amenities"):
        c["amenities"] = [a.strip().lower() for a in f.amenities if a and a.strip()][:15]

    # Los que mueven el precio SOLO entran si superan el umbral de confianza.
    # "Sin dato, sin ajuste" (doc 05 §4.2) vale también para lo que un modelo
    # dijo sin poder mostrar dónde lo leyó.
    for campo in CON_CITA:
        valor = getattr(f, campo)
        if valor is None or c.get(campo) is not None:
            continue
        conf = confianzas.get(campo, 0.0)
        # La confianza se guarda SIEMPRE, supere el umbral o no: el informe
        # tiene que poder mostrar "esto lo dijo el modelo pero no lo usamos".
        c.setdefault("field_confidence", {})[campo] = conf
        if conf >= umbral:
            c[campo] = valor  # type: ignore[literal-required]

    c["feature_source"] = "extracted"


async def _persistir(
    session: AsyncSession,
    listing_id: str,
    f: FeaturesExtraidas,
    confianzas: dict[str, float],
    modelo: str,
    version: str,
    umbral: float,
) -> None:
    """Guarda en `corpus.listing_features`.

    Tabla aparte de `listings` a propósito: `listings` es el HECHO,
    `listing_features` una INTERPRETACIÓN con su versión de modelo y prompt.
    Cuando mejore el extractor se reprocesa esto sin tocar el crudo.
    """
    fila = (
        await session.execute(
            select(ListingFeatures).where(ListingFeatures.listing_id == listing_id)
        )
    ).scalar_one_or_none()

    # Solo se guardan los campos que superaron el umbral: la tabla es la que
    # después alimenta al nodo 2 y al 7, y un dato flojo ahí contamina todo lo
    # que venga después.
    def confiable(campo: str) -> Any:
        v = getattr(f, campo)
        return v if confianzas.get(campo, 1.0) >= umbral else None

    promedio = (
        Decimal(str(round(sum(confianzas.values()) / len(confianzas), 2))) if confianzas else None
    )
    datos: dict[str, Any] = {
        "property_type": f.property_type,
        "rooms": f.rooms,
        "bedrooms": f.bedrooms,
        "bathrooms": f.bathrooms,
        "surface_total": _dec(f.surface_total),
        "surface_covered": _dec(f.surface_covered),
        "floor_number": confiable("floor_number"),
        "age_years": confiable("age_years"),
        "condition": confiable("condition"),
        "orientation": confiable("orientation"),
        "has_elevator": confiable("has_elevator"),
        "parking_spaces": f.parking_spaces,
        "balcony": f.balcony,
        "amenities": [a.strip().lower() for a in f.amenities][:15],
        "expenses_ars": _dec(f.expenses_ars),
        "credit_eligible": f.credit_eligible,
        "extractor_model": modelo,
        "extractor_version": version,
        "confidence": promedio,
        # Un aviso con una cita falsa se marca para revisión humana: el modelo
        # afirmó haber leído algo que no está.
        "needs_review": any(v == CONF_CITA_FALSA for v in confianzas.values()),
        "extracted_at": datetime.now(UTC),
    }

    if fila is None:
        session.add(ListingFeatures(listing_id=listing_id, **datos))
    else:
        for k, v in datos.items():
            setattr(fila, k, v)


def _payload(c: Candidate) -> dict[str, str]:
    """Lo que ve el modelo de un aviso. El texto de terceros va SIEMPRE
    envuelto en el bloque delimitado (doc 10 §4.1)."""
    conocidos = {
        k: c.get(k)
        for k in ("rooms", "surface_total", "surface_covered", "property_type")
        if c.get(k) is not None
    }
    return {
        "ref": c["listing_id"],
        "titulo": "",
        "datos_del_portal": ", ".join(f"{k}={v}" for k, v in conocidos.items()),
        "descripcion": wrap_external(_texto_del_aviso(c)),
    }


async def extraer_lotes(
    cliente: LlmClient,
    cfg: NodeConfig,
    pendientes: list[Candidate],
    *,
    task: str | None = None,
    plantilla: str | None = None,
    tamano: int | None = None,
    use_cache: bool = True,
    al_completar: Callable[[list[Candidate], LoteExtraido | None, list[LlmUsage]], Awaitable[None]]
    | None = None,
) -> tuple[list[tuple[LoteExtraido | None, list[LlmUsage]]], list[list[Candidate]]]:
    """Extrae en lotes paralelos y **reintenta los avisos que el modelo omitió**.

    Vive acá afuera y no adentro del nodo por una razón que costó una tarde:
    `scripts/eval_extraccion.py` llamaba a `structured()` pelado y por lo tanto
    **medía un fragmento del sistema, no el sistema**. Le faltaba justo el
    reintento de omitidos, que es lo que en la práctica lleva la cobertura de
    ~70% a 100%. Una evaluación que no ejercita el mismo camino que producción
    mide otra cosa y no avisa.

    `al_completar` se llama **apenas termina cada lote**, no al final. El nodo
    lo usa para guardar y commitear ese lote.

    Por qué: el 14/08, con el corpus de Palermo recién cargado, un informe con
    60 candidatos se pasó del `job_timeout` de arq durante la extracción. Como
    la persistencia estaba TODA después del `gather`, se perdieron los ~40
    avisos ya extraídos y pagados, y el reintento los volvió a pagar. El caché
    por aviso —"un aviso ya extraído no se vuelve a procesar nunca"— solo vale
    si alguien lo escribió: mientras el trabajo vive únicamente en memoria, el
    caché es una promesa.

    El eval no pasa callback y no cambia: sigue consumiendo lo que se devuelve.
    """
    tam = tamano or int(cfg.param("batch_size", 6))
    paralelo = int(cfg.param("max_concurrent_batches", 4))
    modelo = task or cfg.task or "extractor"
    prompt_name = plantilla or cfg.prompt or "extractor/v1"
    limite = asyncio.Semaphore(paralelo)

    async def _procesar(lote: list[Candidate]) -> tuple[LoteExtraido | None, list[LlmUsage]]:
        """Un lote = una llamada. Los lotes son independientes, así que van en
        paralelo: 253 s -> ~90 s en un informe de 22 avisos (medido)."""
        prompt = render(
            prompt_name,
            aviso_injection=AVISO_INJECTION,
            anio_actual=datetime.now(UTC).year,
            avisos=[_payload(c) for c in lote],
        )
        salida: tuple[LoteExtraido | None, list[LlmUsage]]
        async with limite:
            try:
                salida = await cliente.structured(
                    modelo,
                    [{"role": "user", "content": prompt}],
                    LoteExtraido,
                    temperature=float(cfg.param("temperature", 0.0)),
                    max_tokens=int(cfg.param("max_tokens", 16384)),
                    max_attempts=cfg.max_attempts,
                    use_cache=use_cache,
                    provider_params=cfg.param("provider_params") or None,
                )
            except (LlmValidationError, LlmError) as e:
                # El lote queda sin features. NO rompe el informe: esos avisos
                # entran sin atributos y el nodo 6 decide qué hacer con ellos.
                log.warning("lote sin extraer", error=str(e)[:200], avisos=len(lote))
                salida = (None, list(getattr(e, "usos", [])))

        if al_completar is not None:
            # FUERA del semáforo: guardar no compite por el cupo de llamadas al
            # modelo. Y en su propio try, porque un fallo al persistir no puede
            # tirar abajo una extracción que ya se pagó.
            try:
                await al_completar(lote, salida[0], salida[1])
            except Exception:
                log.exception("no se pudo persistir un lote ya extraído", avisos=len(lote))
        return salida

    lotes = [pendientes[i : i + tam] for i in range(0, len(pendientes), tam)]
    salidas = await asyncio.gather(*(_procesar(lote) for lote in lotes))

    # Un modelo al que se le mandan 6 avisos puede devolver 2 sin dar ninguna
    # señal de error: la salida valida contra el schema igual. MEDIDO el 13/08
    # sobre el golden set: la mitad de los lotes devolvía 1 de 4.
    rondas = int(cfg.param("max_rondas_omitidos", 2))
    for _ in range(rondas):
        devueltos = {f.listing_ref for s, _ in salidas if s for f in s.avisos}
        faltantes = [c for lote in lotes for c in lote if c["listing_id"] not in devueltos]
        if not faltantes:
            break
        log.warning("el modelo omitió avisos; reintentando", omitidos=len(faltantes))
        # Los reintentos van de a UNO: si el modelo se saltó avisos con un lote
        # de 6, insistir con 6 repite el problema.
        relotes = [[c] for c in faltantes]
        salidas = [*salidas, *await asyncio.gather(*(_procesar(lote) for lote in relotes))]
        lotes = [*lotes, *relotes]

    return list(salidas), lotes


async def extract_features(state: ReportState, cfg: NodeConfig) -> NodeResult:
    from tasador.db.base import get_session_factory

    candidatos = list(state.get("candidates") or [])
    if not candidatos:
        return NodeResult(updates={}, detail={"avisos": 0, "motivo": "sin candidatos"})

    umbral = float(cfg.param("min_field_confidence", 0.7))
    usar_cache = bool(cfg.param("cache_by_content_hash", True))
    ledger = UsageLedger()
    cliente = LlmClient()

    por_ref = {c["listing_id"]: c for c in candidatos}
    pendientes = [c for c in candidatos if c.get("feature_source") != "extracted"]

    extraidos = fallados = citas_falsas = reintentos = 0
    modelo_real = "?"
    session_factory = get_session_factory()

    async def _guardar_lote(
        lote: list[Candidate], salida: LoteExtraido | None, usos: list[LlmUsage]
    ) -> None:
        """Se llama apenas termina cada lote. Guarda y commitea ESE lote.

        Una sesión por lote y no una compartida: los lotes corren en paralelo y
        una `AsyncSession` no es concurrente. Abrir una por lote es más barato
        que perder la extracción de sesenta avisos por un timeout.
        """
        nonlocal extraidos, fallados, citas_falsas, reintentos, modelo_real

        ledger.extend(usos)
        # Reintentos POR LOTE, no acumulados: si un lote necesitó dos llamadas
        # eso es un reintento, no uno por cada lote que vino después.
        reintentos += max(0, len(usos) - 1)
        if usos:
            modelo_real = usos[-1].provider_model
        if salida is None:
            fallados += len(lote)
            return

        async with session_factory() as session:
            for f in salida.avisos:
                c = por_ref.get(f.listing_ref)
                if c is None:
                    # El modelo devolvió un ref que no le mandamos.
                    log.warning("listing_ref desconocido", ref=f.listing_ref[:60])
                    continue
                confianzas = verificar_citas(f, _texto_del_aviso(c))
                citas_falsas += sum(1 for v in confianzas.values() if v == CONF_CITA_FALSA)
                _aplicar(c, f, confianzas, umbral)
                await _persistir(
                    session,
                    c["listing_id"],
                    f,
                    confianzas,
                    modelo_real,
                    cfg.prompt or "extractor/v1",
                    umbral,
                )
                extraidos += 1
            await session.commit()

    try:
        _, lotes = await extraer_lotes(
            cliente, cfg, pendientes, use_cache=usar_cache, al_completar=_guardar_lote
        )
    finally:
        await cliente.close()

    # Lo que el modelo se saltó incluso después del reintento. Se cuenta
    # aparte de `fallados` porque es otra patología: no es que la llamada se
    # cayó, es que devolvió menos de lo pedido sin avisar.
    omitidos = max(0, len(pendientes) - extraidos - fallados)

    con_estado = sum(1 for c in candidatos if c.get("condition"))
    con_piso = sum(1 for c in candidatos if c.get("floor_number") is not None)

    return NodeResult(
        updates={
            "candidates": candidatos,
            "extraction": {
                "extraidos": extraidos,
                "fallados": fallados,
                "omitidos_por_el_modelo": omitidos,
                "modelo": modelo_real,
            },
        },
        detail={
            "avisos": len(pendientes),
            "extraidos": extraidos,
            "fallados": fallados,
            "omitidos_por_el_modelo": omitidos,
            "citas_falsas": citas_falsas,
            # Lo que de verdad importa: cuántos coeficientes se ENCENDIERON.
            "con_condition": con_estado,
            "con_floor_number": con_piso,
            "lotes": len(lotes),
            "reintentos_de_validacion": reintentos,
        },
        usage=ledger,
    )
