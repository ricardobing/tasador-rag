"""Nodo 6 — `curate`. Reglas duras primero, LLM juez después.

El orden importa y no es estético: **lo que se puede decidir con una regla no
se le pregunta a un modelo.** Es más barato, más rápido, determinístico y
auditable. Al juez le llega solo lo que sobrevivió, que es donde su criterio
agrega algo.

Este nodo es el que tiene que recortar la dispersión de Belgrano: 22
comparables del mismo barrio, mismos ambientes, que van de 1.742 a 6.600 USD/m².
Leyendo los avisos se ve por qué — **la cola cara son pozos y emprendimientos**
("entrega marzo 2027", "las fotos corresponden al departamento modelo"). El
precio de un pozo incluye plazo de obra y no es comparable con un usado
terminado.

Igual que en el nodo 4, **cada descarte del juez exige una cita textual que se
verifica contra el aviso sin LLM**. Un descarte sin cita no se aplica: el
informe muestra los excluidos con su motivo, y un cliente puede preguntar por
qué no se usó el aviso de la esquina.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeResult
from tasador.agents.nodes.extract import _normalizar_texto, _texto_del_aviso
from tasador.agents.prompts import render
from tasador.agents.state import Candidate, ReportState, dec
from tasador.llm import (
    AVISO_INJECTION,
    LlmClient,
    LlmError,
    LlmValidationError,
    UsageLedger,
    wrap_external,
)
from tasador.settings import get_settings
from tasador.valuation.adjustments import umbrales_de_plausibilidad

log = structlog.get_logger()

Motivo = Literal[
    "permuta_o_financiacion",
    "en_pozo_o_construccion",
    "precio_promocional",
    "tipologia_distinta",
    "descripcion_inconsistente",
]

# Reglas duras (doc 04, nodo 6). Sin LLM, sin discusión.
#
# Los umbrales salen de `config/adjustments.yaml`, que es la única fuente para
# los tres que los preguntan —la ingesta, este nodo y el motor— y no de
# literales acá (H-16).


class Veredicto(BaseModel):
    """Lo que el juez puede decir. Campos cerrados: no hay dónde inventar."""

    model_config = ConfigDict(extra="forbid")

    listing_ref: str
    descartar: bool
    motivo: Motivo | None = None
    # ⚠️ SIN default, o sea REQUERIDO en el JSON Schema.
    #
    # Era `cita: str | None = None`. Un campo con default no entra en `required`,
    # así que el modelo podía omitirlo sin violar el schema — y lo hacía.
    # Medido el 14/08 contra el golden set: el juez detectó **los 7 descartes,
    # los 7 correctos**, y `aplicar_veredicto` rechazó los 7 por venir sin cita.
    # Recall 0% con un juez que no se equivocó ni una vez.
    #
    # El costo era real y no cosmético: sin descartar los `en_pozo`, esos avisos
    # entran como comparables. Su precio incluye plazo de obra y se paga en
    # cuotas, así que no es comparable con una venta normal — en la Etapa 3,
    # sacarlos bajó el techo del rango un 13% sin mover el centro. O sea que el
    # informe salía con el techo inflado.
    #
    # Ahora el modelo TIENE que escribir algo. Si escribe una cita que no está
    # en el aviso, cae en la rama ruidosa de `aplicar_veredicto` (un `warning`,
    # no un `info`), que es exactamente para lo que el mecanismo existe: no para
    # que el modelo no mienta, sino para que cuando mienta se note.
    cita: str


class LoteCurado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    veredictos: list[Veredicto]


def _usd_m2(c: Candidate) -> Decimal | None:
    precio = dec(c.get("price"))
    sup = dec(c.get("surface_covered")) or dec(c.get("surface_total"))
    if precio and sup and sup > 0 and c.get("currency") == "USD":
        try:
            return precio / sup
        except (InvalidOperation, ZeroDivisionError):
            return None
    return None


def reglas_duras(c: Candidate, *, subject_address: str | None = None) -> str | None:
    """El motivo de descarte, o None si el aviso pasa. Determinístico.

    Devuelve el PRIMER motivo que aplica, de lo más barato a lo más caro de
    evaluar. El orden también es el de "qué tan obvio es": un aviso sin precio
    no necesita que se le mire la superficie.
    """
    u = umbrales_de_plausibilidad()
    if c.get("price") in (None, "") or dec(c.get("price")) in (None, Decimal(0)):
        return "sin_precio"
    if c.get("currency") != "USD":
        # El mercado de venta opera en dólares; convertir a un tipo de cambio
        # arbitrario mete más error del que resuelve (doc 05 §3).
        return "precio_en_ars"

    sup = dec(c.get("surface_covered")) or dec(c.get("surface_total"))
    if sup is None or sup < u.surface_min:
        return "sin_superficie"

    m2 = _usd_m2(c)
    if m2 is None or not (u.usd_m2_min <= m2 <= u.usd_m2_max):
        return "usd_m2_fuera_de_rango"

    dias = c.get("days_published")
    if dias is not None and dias > u.max_days_published:
        return "aviso_vencido"

    # Tasar con el propio aviso de la propiedad es circular.
    if (
        subject_address
        and c.get("address")
        and _normalizar_texto(str(c["address"])) == _normalizar_texto(subject_address)
    ):
        return "auto_referencia"

    # Un cluster aporta un solo comparable: el canónico (nodo 5).
    if c.get("cluster_id") and c.get("is_canonical") is False:
        return "duplicado_de_cluster"

    return None


def _payload(c: Candidate) -> dict[str, Any]:
    m2 = _usd_m2(c)
    datos = {
        k: c.get(k)
        for k in ("rooms", "surface_total", "surface_covered", "condition", "floor_number")
        if c.get(k) is not None
    }
    return {
        "ref": c["listing_id"],
        "precio": f"{float(dec(c.get('price')) or 0):,.0f}",
        "usd_m2": f"{float(m2):,.0f}" if m2 else "",
        "datos": ", ".join(f"{k}={v}" for k, v in datos.items()),
        # El texto de terceros va SIEMPRE delimitado (doc 10 §4.1).
        "descripcion": wrap_external(_texto_del_aviso(c)),
    }


def _resumen_sujeto(subject: dict[str, Any]) -> str:
    partes = [
        f"tipo: {subject.get('property_type') or 'departamento'}",
        f"barrio: {subject.get('neighborhood_name') or '?'}",
    ]
    if subject.get("rooms"):
        partes.append(f"{subject['rooms']} ambientes")
    if sup := (subject.get("surface_covered") or subject.get("surface_total")):
        partes.append(f"{sup} m²")
    if subject.get("condition"):
        partes.append(f"estado: {subject['condition']}")
    return " · ".join(partes)


def aplicar_veredicto(c: Candidate, v: Veredicto) -> str | None:
    """Aplica el veredicto del juez SI la cita está en el aviso.

    Devuelve el motivo aplicado, o None. Un descarte cuya cita no aparece en el
    texto **no se aplica**: el modelo estaría afirmando haber leído algo que no
    está, y sacar un comparable legítimo del set es peor que dejar uno dudoso —
    la mediana absorbe al dudoso, pero un informe sin comparables no sale.
    """
    if not v.descartar or v.motivo is None:
        return None
    if not v.cita or not v.cita.strip():
        log.info("descarte sin cita, no se aplica", ref=v.listing_ref, motivo=v.motivo)
        return None
    if _normalizar_texto(v.cita) not in _normalizar_texto(_texto_del_aviso(c)):
        log.warning(
            "el juez citó algo que no está en el aviso; no se descarta",
            ref=v.listing_ref,
            motivo=v.motivo,
            cita=v.cita[:80],
        )
        return None
    return v.motivo


async def curate(state: ReportState, cfg: NodeConfig) -> NodeResult:
    candidatos = list(state.get("candidates") or [])
    if not candidatos:
        return NodeResult(updates={}, detail={"avisos": 0, "motivo": "sin candidatos"})

    subject = state.get("subject") or {}
    motivos: dict[str, int] = {}

    # ── Fase 1: reglas duras ─────────────────────────────────────────────
    vivos: list[Candidate] = []
    for c in candidatos:
        if not c.get("included", True):
            continue  # ya lo descartó un nodo anterior
        motivo = reglas_duras(c, subject_address=subject.get("address_raw"))
        if motivo:
            c["included"] = False
            c["exclusion_reason"] = motivo
            motivos[motivo] = motivos.get(motivo, 0) + 1
        else:
            vivos.append(c)

    duros = len(candidatos) - len(vivos)

    # ── Fase 2: LLM juez sobre lo que sobrevivió ─────────────────────────
    ledger = UsageLedger()
    citas_falsas = descartes_llm = 0
    permitidos = set(cfg.param("motivos_permitidos") or [])

    if vivos:
        cliente = LlmClient()
        tam = int(cfg.param("max_llm_batch", 20))
        try:
            for i in range(0, len(vivos), tam):
                lote = vivos[i : i + tam]
                prompt = render(
                    cfg.prompt or "curator/v1",
                    aviso_injection=AVISO_INJECTION,
                    sujeto=_resumen_sujeto(subject),
                    avisos=[_payload(c) for c in lote],
                )
                try:
                    salida, usos = await cliente.structured(
                        cfg.task or "judge",
                        [{"role": "user", "content": prompt}],
                        LoteCurado,
                        temperature=float(cfg.param("temperature", 0.0)),
                        max_tokens=int(cfg.param("max_tokens", 8192)),
                        max_attempts=cfg.max_attempts,
                        provider_params=cfg.param("provider_params") or None,
                    )
                except (LlmValidationError, LlmError) as e:
                    # Sin juez, sobreviven todos los que pasaron las reglas
                    # duras. Degrada, no rompe.
                    ledger.extend(getattr(e, "usos", []))
                    log.warning("el juez no respondió", error=str(e)[:200])
                    continue

                ledger.extend(usos)
                por_ref = {c["listing_id"]: c for c in lote}
                for v in salida.veredictos:
                    cand = por_ref.get(v.listing_ref)
                    if cand is None:
                        continue
                    if v.motivo and v.motivo not in permitidos:
                        # El YAML manda: un motivo fuera de la lista no existe.
                        log.warning("motivo fuera del vocabulario", motivo=v.motivo)
                        continue
                    motivo = aplicar_veredicto(cand, v)
                    if motivo is None:
                        if v.descartar:
                            citas_falsas += 1
                        continue
                    cand["included"] = False
                    cand["exclusion_reason"] = motivo
                    motivos[motivo] = motivos.get(motivo, 0) + 1
                    descartes_llm += 1
        finally:
            await cliente.close()

    quedan = sum(1 for c in candidatos if c.get("included", True))
    s = get_settings()

    log.info(
        "curaduría terminada",
        report_id=state["report_id"],
        entraron=len(candidatos),
        por_reglas=duros,
        por_juez=descartes_llm,
        quedan=quedan,
    )

    return NodeResult(
        updates={
            "candidates": candidatos,
            "curation": {
                "quedan": quedan,
                "por_reglas": duros,
                "por_juez": descartes_llm,
            },
        },
        detail={
            "entraron": len(candidatos),
            "descartados_por_reglas": duros,
            "descartados_por_juez": descartes_llm,
            "descartes_sin_cita_verificable": citas_falsas,
            "quedan": quedan,
            "motivos": motivos,
            # Si esto es True, el nodo 7 va a cortar con INSUFFICIENT_DATA.
            # Vale registrarlo acá: es la última chance de ver POR QUÉ.
            "por_debajo_del_minimo": quedan < s.min_comparables,
            "fecha": datetime.now(UTC).date().isoformat(),
        },
        usage=ledger,
    )
