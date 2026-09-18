"""«Preguntale al informe» — doc 18 §5 (ADR-013).

El propietario pregunta *«¿por qué no usaron el de Thames?»* o *«¿por qué la
mediana y no el promedio?»*. La respuesta existe —en `report_comparables` y en
la metodología— pero hay que saber dónde mirar. Esto es un RAG chico y
verificable sobre un corpus que ya es nuestro:

    pregunta → embebido local → híbrido sobre (hechos del informe y metodología)
            → top-k → prompt con los fragmentos numerados
            → respuesta con citas obligatorias [C-07] [Met §4.2]
            → VERIFICACIÓN SIN LLM → respuesta | rechazo

Lo que lo hace de este proyecto es la verificación, no el prompt:

1. **Toda cita tiene que existir** entre los fragmentos que se le pasaron.
   El schema exige `citas` (sin default: la lección de `cita: str | None`).
2. **Toda cifra de la respuesta pasa por `cifras_no_trazables`**, la misma
   función de la fase A del crítico, contra los fragmentos citados. Se reusa,
   no se reescribe.
3. **Rechazo por falta de evidencia, sin llamar al modelo**: si el mejor
   puntaje denso no llega al umbral, la respuesta es «eso no está en este
   informe». El umbral se calibra con las preguntas sin respuesta del eval.
4. Ninguna cifra del informe la produce este módulo: las lee. El precio sigue
   saliendo del nodo 7.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from tasador.llm import (
    AVISO_INJECTION,
    LlmClient,
    LlmError,
    LlmUsage,
    LlmValidationError,
    wrap_external,
)
from tasador.rag.embedder import Embedder
from tasador.rag.retriever import fusionar_rrf

log = structlog.get_logger()

Fuente = Literal["informe", "metodologia"]

RUTA_METODOLOGIA = Path(__file__).resolve().parents[3] / "docs" / "05-metodologia-de-valuacion.md"


@dataclass(slots=True, frozen=True)
class Fragmento:
    id: str
    texto: str
    fuente: Fuente


# ── Los hechos del informe, como texto citable ───────────────────────────

_MOTIVO_EN_TEXTO = {
    "en_pozo_o_construccion": "está en pozo o en construcción: su precio incluye el plazo de obra",
    "permuta_o_financiacion": "acepta permuta o financiación, que distorsiona el precio publicado",
    "precio_promocional": "tiene un precio promocional o por tiempo limitado",
    "tipologia_distinta": "es de otra tipología",
    "descripcion_inconsistente": "su descripción es inconsistente con sus datos",
    "aviso_vencido": "el aviso está vencido (más de 180 días publicado)",
    "duplicado_de_cluster": "es el mismo inmueble que otro comparable ya considerado",
    "ajuste_excede_el_tope": "llevarlo hasta la propiedad pedía un ajuste mayor al tope de ±25%",
    "usd_m2_fuera_de_rango": "su USD/m² está fuera del rango plausible",
    "sin_precio_o_superficie_en_usd": "no tiene precio en dólares o superficie",
}


def _plata(v: Any) -> str:
    try:
        return f"{float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def fragmentos_del_informe(informe: dict[str, Any]) -> list[Fragmento]:
    """De la respuesta de `GET /v1/reports/{id}?incluir=descartados` a hechos.

    Un hecho por comparable (usado o descartado, con el motivo en castellano),
    uno para la valuación, uno para la confianza, uno para el contexto de
    mercado y uno para la propiedad. Todo lo que dice sale de columnas: ningún
    texto de tercero entra sin `wrap_external`.
    """
    out: list[Fragmento] = []
    v = informe.get("valuation") or {}
    p = v.get("suggested_listing_price") or {}
    c = v.get("expected_closing_range") or {}
    if p:
        out.append(
            Fragmento(
                "V",
                "Valuación: precio de publicación sugerido USD "
                f"{_plata(p.get('mid'))} (rango USD {_plata(p.get('low'))} a USD "
                f"{_plata(p.get('high'))}); rango esperado de cierre USD {_plata(c.get('low'))} "
                f"a USD {_plata(c.get('high'))}; USD {_plata(v.get('price_per_m2'))} por m² sobre "
                f"{_plata(v.get('weighted_surface'))} m² de superficie ponderada. Los valores son "
                "precios de publicación, no de escrituración.",
                "informe",
            )
        )
    conf = informe.get("confidence") or {}
    if conf:
        notas = "; ".join(str(n) for n in (conf.get("notes") or []))
        out.append(
            Fragmento(
                "CONF",
                f"Confianza del informe: {conf.get('level') or '—'} (score {conf.get('score')})."
                + (f" Notas: {notas}." if notas else ""),
                "informe",
            )
        )
    comps = informe.get("comparables") or {}
    if comps:
        out.append(
            Fragmento(
                "N",
                f"Comparables: se encontraron {comps.get('found')} avisos, se usaron "
                f"{comps.get('used')} y se descartaron {comps.get('excluded')}.",
                "informe",
            )
        )
    for i, it in enumerate(comps.get("items") or [], 1):
        cid = f"C-{i:02d}"
        base = (
            f"Comparable {cid}: {it.get('address') or 'sin dirección'} ({it.get('source')}), "
            f"USD {_plata(it.get('price'))}, {_plata(it.get('surface_weighted'))} m²"
            + (f", {it.get('rooms')} ambientes" if it.get("rooms") else "")
            + f", USD {_plata(it.get('raw_price_per_m2'))}/m² crudo"
        )
        if it.get("included"):
            ajustado = _plata(it.get("adjusted_price_per_m2"))
            texto = base + f", USD {ajustado}/m² ajustado. Usado en la valuación."
            ajustes = it.get("adjustments") or {}
            if isinstance(ajustes, dict) and ajustes:
                partes = [f"{k} {v_}" for k, v_ in ajustes.items() if k != "total"]
                if partes:
                    texto += " Ajustes: " + ", ".join(str(x) for x in partes[:6]) + "."
        else:
            motivo = it.get("exclusion_reason") or "sin motivo declarado"
            texto = base + f". Descartado: {_MOTIVO_EN_TEXTO.get(motivo, motivo)} ({motivo})."
        out.append(Fragmento(cid, texto, "informe"))
    if (m := informe.get("market_context")) and isinstance(m, dict):
        lineas = [f"{k}: {v_}" for k, v_ in m.items() if v_ not in (None, "", [], {})]
        if lineas:
            out.append(
                Fragmento(
                    "M",
                    "Contexto de mercado del barrio: " + "; ".join(lineas[:12]) + ".",
                    "informe",
                )
            )
    return out


_ENCABEZADO = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)


def fragmentos_de_metodologia(ruta: Path | None = None) -> list[Fragmento]:
    """Doc 05 partido por encabezado: acá el chunking por estructura sí es el
    de manual — un documento largo con secciones donde partir por título
    conserva el sentido y partir cada 500 caracteres no."""
    ruta = ruta or RUTA_METODOLOGIA
    if not ruta.exists():
        log.warning("sin metodología para el QA", ruta=str(ruta))
        return []
    texto = ruta.read_text(encoding="utf-8")
    marcas = list(_ENCABEZADO.finditer(texto))
    out: list[Fragmento] = []
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        cuerpo = " ".join(texto[m.end() : fin].split())
        if len(cuerpo) < 40:
            continue
        titulo = m.group(2).strip()
        num = re.match(r"(\d+(?:\.\d+)?)", titulo)
        fid = f"Met §{num.group(1)}" if num else f"Met {titulo[:30]}"
        out.append(Fragmento(fid, f"Metodología, {titulo}: {cuerpo[:1800]}", "metodologia"))
    return out


# ── El índice: denso + léxico, en memoria ────────────────────────────────

_PALABRA = re.compile(r"[a-záéíóúñü0-9]{3,}", re.IGNORECASE)
# Palabras que aparecen en cualquier pregunta y en cualquier hecho: no son
# evidencia de nada. Con ellas, "¿cuál es el precio del alquiler?" matchea
# "precio" en todos los comparables.
_STOPWORDS = {
    "que", "cual", "cuál", "como", "cómo", "por", "para", "los", "las", "una", "uno", "del",
    "con", "sin", "sobre", "entre", "hay", "esta", "está", "este", "esa", "ese", "son", "fue",
    "informe", "propiedad", "aviso", "avisos", "comparable", "comparables", "valor", "precio",
    "usd", "cuanto", "cuánto", "cuantos", "cuántos", "tiene", "tienen", "puede", "más", "mas",
}  # fmt: skip


def _tokens(texto: str) -> set[str]:
    return set(_PALABRA.findall(texto.lower()))


def _coseno(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=True))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


@dataclass(slots=True)
class Indice:
    fragmentos: list[Fragmento]
    vectores: list[list[float]]
    tokens: list[set[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = [_tokens(f.texto) for f in self.fragmentos]

    @classmethod
    async def construir(cls, fragmentos: list[Fragmento], embedder: Embedder) -> Indice:
        vectores = await embedder.pasajes([f.texto for f in fragmentos])
        return cls(fragmentos, vectores)

    def buscar(
        self, vector: list[float], pregunta: str, *, k: int = 8, rrf_k: int = 60
    ) -> tuple[list[Fragmento], float, float]:
        """Top-k por RRF de denso y léxico, y las dos MEJORES señales (para el
        umbral): el mejor coseno y el mejor solapamiento léxico."""
        denso = {i: _coseno(vector, v) for i, v in enumerate(self.vectores)}
        q = _tokens(pregunta) - _STOPWORDS
        lexico = {i: len(q & t) / (len(q) or 1) for i, t in enumerate(self.tokens) if q & t}
        rd = [str(i) for i, _ in sorted(denso.items(), key=lambda kv: -kv[1])]
        rl = [str(i) for i, _ in sorted(lexico.items(), key=lambda kv: -kv[1])]
        fusion = fusionar_rrf([rd, rl], k=rrf_k)
        top = sorted(fusion.items(), key=lambda kv: -kv[1])[:k]
        mejor = max(denso.values()) if denso else 0.0
        mejor_lex = max(lexico.values()) if lexico else 0.0
        return [self.fragmentos[int(i)] for i, _ in top], mejor, mejor_lex


# ── La respuesta y su verificación ───────────────────────────────────────


class RespuestaQA(BaseModel):
    """Campos cerrados. `citas` es REQUERIDO: sin default no hay forma de
    omitirlo y que el JSON valide igual."""

    model_config = ConfigDict(extra="forbid")

    respuesta: str = Field(max_length=1500)
    citas: list[str] = Field(max_length=8)
    sin_evidencia: bool


def verificar(
    respuesta: RespuestaQA, permitidos: dict[str, str], *, tolerancia_pct: float = 0.1
) -> list[str]:
    """Los problemas que hacen que la respuesta NO salga. Sin LLM."""
    from tasador.agents.nodes.critic import cifras_no_trazables

    problemas: list[str] = []
    if respuesta.sin_evidencia:
        return problemas
    if not respuesta.citas:
        problemas.append("la respuesta no cita ningún fragmento")
    desconocidas = [c for c in respuesta.citas if c not in permitidos]
    if desconocidas:
        problemas.append(f"cita fragmentos que no se le pasaron: {desconocidas}")
    citados = [permitidos[c] for c in respuesta.citas if c in permitidos]
    huerfanas = cifras_no_trazables(
        respuesta.respuesta, {"fragmentos": citados}, tolerancia_pct=tolerancia_pct
    )
    if huerfanas:
        problemas.append(f"cifras que no están en los fragmentos citados: {huerfanas[:6]}")
    return problemas


@dataclass(slots=True)
class ResultadoQA:
    respuesta: str
    citas: list[Fragmento]
    rechazada: bool
    motivo: str | None
    mejor_coseno: float
    usos: list[LlmUsage] = field(default_factory=list)
    intentos: int = 0

    @property
    def cost_usd(self) -> float:
        return float(sum((u.cost_usd for u in self.usos), start=0))


RECHAZO = "Eso no está en este informe: no encontré evidencia para responderlo."


async def responder(
    pregunta: str,
    indice: Indice,
    *,
    embedder: Embedder,
    cliente: LlmClient,
    task: str = "judge",
    prompt: str = "qa/v1",
    umbral: float = 0.40,
    umbral_lexico: float = 0.34,
    k: int = 8,
    max_intentos: int = 2,
) -> ResultadoQA:
    """Dos compuertas antes del modelo y dos verificaciones después.

    El umbral denso solo, medido el 18/09 con MiniLM sobre 26 preguntas: las
    que tienen respuesta van de 0,43 a 0,74 y las que no, de 0,32 a 0,51 — no
    separan. Lo que sí separa es la combinación: una pregunta sobre «Charcas
    4900» tiene solapamiento léxico alto con SU comparable aunque el coseno sea
    0,43. Se rechaza sin modelo solo cuando fallan las dos señales; el resto lo
    decide el modelo con `sin_evidencia` y, después, la verificación.
    """
    from tasador.agents.prompts import render

    vector = await embedder.consulta(pregunta)
    fragmentos, mejor, mejor_lex = indice.buscar(vector, pregunta, k=k)
    if not fragmentos or (mejor < umbral and mejor_lex < umbral_lexico):
        # Sin evidencia no se llama al modelo: no hay nada que pueda decir que
        # no sea inventado.
        return ResultadoQA(
            RECHAZO,
            [],
            True,
            f"coseno {mejor:.3f} < {umbral} y léxico {mejor_lex:.2f} < {umbral_lexico}",
            mejor,
        )

    permitidos = {f.id: f.texto for f in fragmentos}
    usos: list[LlmUsage] = []
    feedback: str | None = None
    for intento in range(1, max_intentos + 1):
        contenido = render(
            prompt,
            aviso_injection=AVISO_INJECTION,
            pregunta=wrap_external(pregunta),
            fragmentos=fragmentos,
            feedback=feedback,
        )
        try:
            salida, u = await cliente.structured(
                task,
                [{"role": "user", "content": contenido}],
                RespuestaQA,
                temperature=0.0,
                max_tokens=1200,
                use_cache=feedback is None,
            )
        except (LlmValidationError, LlmError) as e:
            usos.extend(getattr(e, "usos", []))
            return ResultadoQA(
                RECHAZO, [], True, f"el modelo no respondió: {str(e)[:120]}", mejor, usos, intento
            )
        usos.extend(u)
        if salida.sin_evidencia:
            return ResultadoQA(
                RECHAZO, [], True, "el modelo no encontró evidencia", mejor, usos, intento
            )
        problemas = verificar(salida, permitidos)
        if not problemas:
            citas = [f for f in fragmentos if f.id in salida.citas]
            return ResultadoQA(salida.respuesta, citas, False, None, mejor, usos, intento)
        feedback = "; ".join(problemas)
        log.info("respuesta rechazada por la verificación", problemas=problemas, intento=intento)
    return ResultadoQA(
        RECHAZO, [], True, f"no pasó la verificación: {feedback}", mejor, usos, max_intentos
    )


def hash_de(fragmentos: list[Fragmento]) -> str:
    return hashlib.sha256("\n".join(f.id + f.texto for f in fragmentos).encode()).hexdigest()[:16]
