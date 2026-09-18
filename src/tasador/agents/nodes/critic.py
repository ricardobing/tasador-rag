"""Nodo 10 — `critic`. Este nodo es la diferencia entre un demo y un producto.

Dos fases, y el orden no es negociable:

**Fase A — verificación determinística, SIN LLM.** Se extraen todas las cifras
del markdown con expresiones regulares y se busca cada una en los datos del
informe. **Cualquier número no trazable es rechazo automático.** Sin apelación
y sin que ningún modelo opine: si el redactor escribió "USD 312.000" y ese
número no está en la valuación ni en la tabla de comparables, el informe no
sale.

Es la única defensa real contra la alucinación de cifras, porque no depende de
que un modelo se dé cuenta. Un LLM revisando a otro LLM comparte los mismos
puntos ciegos; una comparación contra un `set` de floats no.

**Fase B — crítica adversarial, con LLM.** Sobre un texto cuyas cifras ya son
todas trazables, se le pide al modelo caro que busque problemas de otro tipo:
afirmaciones que los datos no sostienen, limitaciones omitidas, un tono que
promete lo que no se puede prometer.

**Ciclo:** rechazo → vuelve al nodo 9 con la crítica. Máximo 2 reintentos. Al
tercero el informe sale **sin narrativa**: la tabla y el rango, con una nota.
Los números son correctos —salieron del nodo 7, determinístico—; lo que falló
es la redacción. Entregar los datos sin prosa es mejor que no entregar nada, y
mucho mejor que entregar prosa no verificada.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeResult
from tasador.agents.nodes.write import datos_del_informe
from tasador.agents.prompts import render
from tasador.agents.state import ReportState
from tasador.llm import LlmClient, LlmError, LlmValidationError, UsageLedger

log = structlog.get_logger()

# Cifras con separador de miles y decimales, en cualquiera de los dos formatos
# que aparecen en un texto argentino: 182.000 y 182,5.
_RE_NUMERO = re.compile(r"\d[\d.,]*")

# Números que NO hace falta que estén en los datos porque no son afirmaciones
# sobre esta propiedad: años, porcentajes de uso común y enteros chicos
# (cantidades de ambientes, de comparables, numeración de secciones).
_ANIOS = range(1900, 2101)
TOLERANCIA_ENTERO_CHICO = 100


class Hallazgo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gravedad: Literal["alta", "media", "baja"]
    problema: str = Field(max_length=400)
    cita_del_informe: str = Field(max_length=300)


class Critica(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aprueba: bool
    hallazgos: list[Hallazgo] = Field(default_factory=list, max_length=10)


def _a_decimal(crudo: str) -> Decimal | None:
    """ "182.000" → 182000 · "2.441,50" → 2441.5 · "0,31" → 0.31

    El formato argentino usa el punto como separador de miles y la coma como
    decimal. Interpretarlo al revés convertiría 182.000 en 182 y el verificador
    rechazaría informes correctos.
    """
    t = crudo.strip().rstrip(".,")
    if not t or not t[0].isdigit():
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") >= 1:
        partes = t.split(".")
        # "2.441" son miles; "75.3" es decimal. Se distingue por el largo del
        # último grupo: los miles siempre son de tres.
        t = "".join(partes) if all(len(p) == 3 for p in partes[1:]) else t
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _valores_permitidos(datos: dict[str, Any]) -> set[Decimal]:
    """Todas las cifras que el informe PUEDE mencionar.

    Se recorre el JSON entero: cualquier número que esté en los datos es
    citable. Lo que no esté, no.
    """
    permitidos: set[Decimal] = set()

    def _recorrer(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                _recorrer(v)
        elif isinstance(x, list):
            for v in x:
                _recorrer(v)
        elif isinstance(x, bool) or x is None:
            return
        elif isinstance(x, int | float | Decimal):
            permitidos.add(Decimal(str(x)))
        elif isinstance(x, str):
            if (d := _a_decimal(x)) is not None:
                permitidos.add(d)
            # Y también las cifras EMBEBIDAS en un texto de los datos.
            #
            # El caso que lo destapó: la dirección del sujeto es "Thames 1800",
            # el redactor la nombra —como corresponde— y la fase A rechazaba el
            # informe entero porque `1800` no era "trazable". Lo era: estaba en
            # los datos, adentro de un string. El extractor solo miraba strings
            # que fueran ENTERAMENTE un número.
            #
            # Costo del falso positivo: tres rechazos y el propietario recibe el
            # informe SIN NARRATIVA. Medido el 14/08 con el primer informe de
            # Palermo; con Belgrano no había pasado porque la altura de Cabildo
            # caía dentro de la tolerancia de otra cifra, por casualidad.
            #
            # No afloja la regla: lo que no está en los datos sigue sin poder
            # mencionarse. Lo que cambia es que "estar en los datos" ahora
            # incluye estar adentro de un texto de los datos, que es donde
            # viven las direcciones.
            else:
                for token in _RE_NUMERO.findall(x):
                    if (d2 := _a_decimal(token)) is not None:
                        permitidos.add(d2)

    _recorrer(datos)

    # ── Derivados, y son POCOS a propósito ───────────────────────────────
    #
    # ⚠️ Acá había `permitidos.add(v * 100)` para TODOS los valores, más
    # `v / 1000` para los mayores a 1.000. Medido el 15/08 sobre informes
    # reales, con la tolerancia del 1%:
    #
    #     solo las cifras de los datos        66 valores   cobertura 29,1%
    #     + v/1000                           106 valores   cobertura 30,0%
    #     + v*100                            132 valores   cobertura 49,7%
    #
    # O sea: **la mitad de los precios inventados entre USD 100.000 y 400.000
    # pasaba la fase A**, incluido el «USD 312.000» que el docstring de este
    # módulo pone como ejemplo de lo que NO puede pasar. La causa es que cada
    # USD/m² del set (2.000-5.000) por 100 cae exactamente en el rango de un
    # precio de departamento.
    #
    # La regla existía para la dispersión en porcentaje y para "USD 182 mil".
    # Se reemplaza por los derivados CONCRETOS de esos dos casos.
    for clave in ("dispersion",):
        v = (datos.get("valuacion") or {}).get(clave)
        if v is not None and (d := _a_decimal(str(v))) is not None:
            permitidos.add(d * 100)
    # "USD 182 mil" solo para las cifras de dinero del informe, no para todas.
    for v in _cifras_de_dinero(datos):
        permitidos.add((v / 1000).quantize(Decimal("0.1")))
    return permitidos


def _cifras_de_dinero(datos: dict[str, Any]) -> set[Decimal]:
    """Las cifras de dinero del informe: valor, rango, cierre y USD/m².

    Son las que el redactor puede escribir "en miles", y las únicas para las que
    ese derivado tiene sentido. Antes se generaba `v/1000` para cualquier número
    de los datos, incluidas las superficies y los ids.
    """
    v = datos.get("valuacion") or {}
    crudos = [
        *(v.get("precio_publicacion_sugerido") or {}).values(),
        *(v.get("rango_de_cierre_esperado") or {}).values(),
        v.get("usd_por_m2"),
    ]
    out: set[Decimal] = set()
    for x in crudos:
        if x is not None and (d := _a_decimal(str(x))) is not None:
            out.add(d)
    return out


# Las cifras que el informe presenta como EL valor de la propiedad.
#
# ⚠️ ACOTADA, y la primera versión no lo estaba.
#
# Decía `(?:valor|precio|estimación|…)[^.\n]{0,60}?(?:USD)\s*([\d.,]+)` y en el
# primer informe real rechazó tres veces por `3.839`, que es el USD/m² de un
# comparable en una frase como "los comparables van de 2.100 a 3.839 por m²".
# El informe salió sin narrativa. Es exactamente el costo asimétrico de la
# Etapa 3 §10.3: **rechazar de más cuesta el producto.**
#
# Lo que queda: solo frases que afirman el valor DEL SUJETO, y solo cifras del
# orden de un inmueble. Un USD/m² lo cubre `cifras_no_trazables`, que sí tiene
# la tabla entera de comparables como referencia.
_RE_VALOR_AFIRMADO = re.compile(
    r"(?:valor(?:\s+(?:de\s+la\s+propiedad|estimado|sugerido|medio))?"
    r"|precio\s+(?:sugerido|de\s+publicaci[oó]n|estimado)"
    r"|estimaci[oó]n|tasaci[oó]n)"
    r"[^.\n]{0,40}?"
    r"(?:USD|U\$S|d[oó]lares)\s*([\d.,]+)",
    re.IGNORECASE,
)

# Una cifra del sujeto es del orden de un inmueble. Por debajo es un USD/m², una
# expensa o una cantidad, y esos tienen su propio control.
MINIMO_VALOR_DE_INMUEBLE = Decimal("50000")

# Y si la frase habla de los comparables, no está afirmando el valor del sujeto.
_RE_HABLA_DE_COMPARABLES = re.compile(
    r"comparabl|avis[oa]|por\s*m2|por\s*m²|/\s*m2|/\s*m²|el\s+m2|el\s+m²", re.IGNORECASE
)


def cifras_clave_equivocadas(
    markdown: str, datos: dict[str, Any], *, tolerancia_pct: float = 0.1
) -> list[str]:
    """Las cifras que el texto presenta como EL valor y no coinciden con ninguna.

    ## Por qué hace falta además de `cifras_no_trazables`

    Aquella pregunta "¿este número está en algún lado de los datos?", y con 23
    comparables los datos tienen 66 números en el mismo orden de magnitud que el
    valor de la propiedad. Es una pregunta demasiado fácil de aprobar.

    Esta pregunta otra cosa: **cuando el texto AFIRMA un valor, ¿es el valor?**
    No alcanza con que el número exista en la tabla de comparables — el precio
    del comparable de la esquina no es el precio de esta propiedad.
    """
    permitidas = _cifras_de_dinero(datos)
    if not permitidas:
        return []
    tol = Decimal(str(tolerancia_pct)) / 100
    malas: list[str] = []
    for m in _RE_VALOR_AFIRMADO.finditer(markdown):
        n = _a_decimal(m.group(1))
        if n is None or n < MINIMO_VALOR_DE_INMUEBLE:
            continue
        # La ORACIÓN entera, para saber de qué está hablando.
        arranque = markdown.rfind(".", 0, m.start()) + 1
        fin = markdown.find(".", m.end())
        oracion = markdown[arranque : fin if fin != -1 else len(markdown)]
        if _RE_HABLA_DE_COMPARABLES.search(oracion):
            continue  # habla de los comparables, no del sujeto
        if any(abs(n - p) <= max(abs(p) * tol, Decimal("0.5")) for p in permitidas):
            continue
        malas.append(m.group(1).rstrip(".,"))
    return malas


def cifras_no_trazables(
    markdown: str, datos: dict[str, Any], *, tolerancia_pct: float = 1.0
) -> list[str]:
    """Las cifras del texto que NO existen en los datos. Sin LLM.

    Tolerancia por redondeo: el redactor puede escribir "USD 182.000" por
    181.950, y eso es correcto. Lo que no puede es escribir 312.000 cuando el
    valor es 284.715.
    """
    permitidos = _valores_permitidos(datos)
    tol = Decimal(str(tolerancia_pct)) / 100
    huerfanas: list[str] = []

    for crudo in _RE_NUMERO.findall(markdown):
        n = _a_decimal(crudo)
        if n is None:
            continue
        # Enteros chicos: cantidades, ambientes, pisos, numeración.
        if n == n.to_integral_value() and n <= TOLERANCIA_ENTERO_CHICO:
            continue
        if int(n) in _ANIOS and n == n.to_integral_value():
            continue
        if any(abs(n - p) <= max(abs(p) * tol, Decimal("0.5")) for p in permitidos):
            continue
        # Sin el punto final de la oración: la lista va al prompt del redactor
        # como "estas cifras no están en los datos", y "312.000." lo manda a
        # buscar un string que no escribió.
        huerfanas.append(crudo.rstrip(".,"))

    return huerfanas


def coherencia_estructural(datos: dict[str, Any]) -> list[str]:
    """Chequeos que no dependen del texto sino de los propios números."""
    problemas: list[str] = []
    p = (datos.get("valuacion") or {}).get("precio_publicacion_sugerido") or {}
    try:
        lo, mid, hi = (Decimal(str(p[k])) for k in ("minimo", "medio", "maximo"))
    except (KeyError, TypeError, InvalidOperation):
        return ["la valuación no trae un rango completo"]

    if not (lo <= mid <= hi):
        problemas.append(f"el rango no es coherente: {lo} / {mid} / {hi}")

    comps = datos.get("comparables") or {}
    if (comps.get("usados") or 0) > (comps.get("encontrados") or 0):
        problemas.append("se usaron más comparables de los que se encontraron")
    return problemas


async def critic(state: ReportState, cfg: NodeConfig) -> NodeResult:
    markdown = state.get("draft_md") or ""
    rechazos = state.get("critic_rejections", 0)
    maximo = int(cfg.param("max_rejections", 2))
    datos = datos_del_informe(state)

    if not markdown.strip():
        # Sin narrativa no hay nada que criticar. El informe sale con la tabla.
        return NodeResult(updates={"rehacer": False}, detail={"sin_narrativa": True})

    # ── Fase A: determinística ───────────────────────────────────────────
    tolerancia = float(cfg.param("numeric_tolerance_pct", 0.1))
    huerfanas = cifras_no_trazables(markdown, datos, tolerancia_pct=tolerancia)
    # Verificación POSICIONAL: no alcanza con que el número exista en los datos.
    # Cuando el texto afirma un valor, tiene que ser EL valor.
    equivocadas = cifras_clave_equivocadas(markdown, datos, tolerancia_pct=tolerancia)
    estructurales = coherencia_estructural(datos)
    detalle: dict[str, Any] = {
        "cifras_no_trazables": huerfanas[:10],
        "cifras_clave_equivocadas": equivocadas[:10],
        "problemas_estructurales": estructurales,
        "rechazos_previos": rechazos,
    }

    if huerfanas or equivocadas or estructurales:
        # Rechazo automático. Ningún modelo opina acá.
        motivo = ""
        if huerfanas:
            motivo += (
                "Estas cifras aparecen en el informe y NO están en los datos: "
                + ", ".join(huerfanas[:10])
                + ". Sacalas o reemplazalas por las cifras reales.\n"
            )
        if equivocadas:
            motivo += (
                "Estas cifras se presentan como el valor de la propiedad y no son "
                "ninguno de los valores calculados: " + ", ".join(equivocadas[:10]) + ".\n"
            )
        if estructurales:
            motivo += "Problemas de coherencia: " + "; ".join(estructurales)

        log.warning(
            "informe rechazado por la verificación determinística",
            report_id=state["report_id"],
            cifras=len(huerfanas),
            rechazos=rechazos,
        )
        detalle["veredicto"] = "RECHAZADO_FASE_A"
        return NodeResult(
            updates=_rechazo(state, motivo, rechazos, maximo),
            detail=detalle,
        )

    # ── Fase B: adversarial ──────────────────────────────────────────────
    ledger = UsageLedger()
    cliente = LlmClient()
    try:
        critica, usos = await cliente.structured(
            cfg.task or "critic",
            [
                {
                    "role": "user",
                    "content": render(
                        cfg.prompt or "critic/v1",
                        informe=markdown,
                        datos=json.dumps(datos, ensure_ascii=False, indent=2),
                    ),
                }
            ],
            Critica,
            temperature=float(cfg.param("temperature", 0.0)),
            max_tokens=int(cfg.param("max_tokens", 4096)),
            max_attempts=cfg.max_attempts,
            use_cache=not state.get("rehacer", False),
        )
        ledger.extend(usos)
    except (LlmValidationError, LlmError) as e:
        # El crítico caído NO aprueba por default: el informe sale con la
        # tabla y sin prosa. Aprobar por omisión sería justo lo contrario de
        # lo que este nodo existe para hacer.
        ledger.extend(getattr(e, "usos", []))
        log.warning("el crítico no respondió; informe sin narrativa", error=str(e)[:200])
        detalle["veredicto"] = "CRITICO_CAIDO"
        return NodeResult(
            updates={"draft_md": "", "rehacer": False},
            detail=detalle,
            usage=ledger,
        )
    finally:
        await cliente.close()

    graves = [h for h in critica.hallazgos if h.gravedad == "alta"]
    detalle["hallazgos"] = [h.model_dump() for h in critica.hallazgos]
    detalle["graves"] = len(graves)

    if not critica.aprueba and graves:
        detalle["veredicto"] = "RECHAZADO_FASE_B"
        motivo = "\n".join(f"- {h.problema} (sobre: «{h.cita_del_informe}»)" for h in graves)
        return NodeResult(
            updates=_rechazo(state, motivo, rechazos, maximo), detail=detalle, usage=ledger
        )

    detalle["veredicto"] = "APROBADO"
    return NodeResult(
        updates={
            "rehacer": False,
            "critique": {"aprueba": True, "hallazgos": detalle["hallazgos"]},
        },
        detail=detalle,
        usage=ledger,
    )


def _rechazo(state: ReportState, motivo: str, rechazos: int, maximo: int) -> dict[str, Any]:
    """Devuelve al redactor, o corta y emite sin narrativa."""
    if rechazos >= maximo:
        # Degradación, no falla: los números son correctos, la prosa no.
        log.warning(
            "el crítico rechazó demasiadas veces; informe sin narrativa",
            report_id=state["report_id"],
            rechazos=rechazos + 1,
        )
        return {
            "draft_md": "",
            "rehacer": False,
            "critic_rejections": rechazos + 1,
            "critique": {"aprueba": False, "feedback": motivo, "agotado": True},
        }
    return {
        "rehacer": True,
        "critic_rejections": rechazos + 1,
        "critique": {"aprueba": False, "feedback": motivo},
    }
