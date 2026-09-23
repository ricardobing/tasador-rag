"""Nodo 11 — `render_pdf`. Determinístico, sin LLM, costo $0.

Markdown + datos → HTML con Jinja → PDF con WeasyPrint.

**Está partido en dos a propósito.** `informe_html()` es una función pura que
se puede testear en cualquier lado; `html_a_pdf()` es la que necesita las
librerías nativas de GTK. En Windows la segunda no corre —falta
`libgobject-2.0-0`, medido el 14/08— y sí corre en el contenedor Linux. Si
fuera una sola función, no habría forma de testear el armado del documento sin
el entorno completo.

Se eligió WeasyPrint sobre Playwright (doc 04, nodo 11): para un documento de
texto y tablas alcanza de sobra y evita meter un Chromium de 300 MB en la
imagen. El costo es esta dependencia nativa.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeError, NodeResult
from tasador.agents.state import ReportState
from tasador.settings import get_settings

log = structlog.get_logger()

# parents[4]: este módulo está un nivel más adentro que `prompts.py`
# (agents/nodes/ contra agents/). Con [3] apuntaba a `src/templates`.
TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "templates"


def _plata(v: Any) -> str:
    """182000 → "182.000". Separador de miles argentino y sin decimales: es un
    documento que lee una persona."""
    try:
        return f"{Decimal(str(v)):,.0f}".replace(",", ".")
    except (InvalidOperation, TypeError, ValueError):
        return "—"


def _decimal2(v: Any) -> str | None:
    """0.775 → "0,78": el score de confianza es una fracción, no un importe."""
    try:
        return f"{Decimal(str(v)):.2f}".replace(".", ",")
    except (InvalidOperation, TypeError, ValueError):
        return None


def _num(v: Any) -> str | None:
    try:
        return f"{Decimal(str(v)):,.0f}".replace(",", ".")
    except (InvalidOperation, TypeError, ValueError):
        return None


def markdown_a_html(md: str) -> str:
    """Markdown → HTML.

    `markdown-it-py` en modo `commonmark` a propósito: sin HTML embebido. El
    markdown lo escribió un LLM a partir de texto de terceros, y aunque el
    crítico ya verificó las cifras, dejar pasar HTML crudo sería abrir una
    puerta que no necesitamos (doc 10 §4.2).
    """
    from markdown_it import MarkdownIt

    # `html: False` NO viene por defecto en commonmark: el preset deja pasar
    # HTML crudo y un `<script>` del markdown llegaba entero al PDF. Lo
    # agarró un test antes que un informe.
    md_it = MarkdownIt("commonmark", {"html": False})
    html: str = md_it.render(md or "")
    return html


_ESTADOS = {
    "a_estrenar": "a estrenar",
    "excelente": "excelente",
    "muy_bueno": "muy bueno",
    "bueno": "bueno",
    "regular": "regular",
    "a_refaccionar": "a refaccionar",
}


# Los motivos de descarte del motor (engine.py, curate) en castellano llano.
# Mismo mapa que el front (`web/src/lib/api.ts`): el propietario lee el PDF.
MOTIVO_DE_DESCARTE: dict[str, str] = {
    "permuta_o_financiacion": "ofrecía permuta o financiación especial",
    "en_pozo_o_construccion": "en pozo o en construcción",
    "descripcion_inconsistente": "la descripción no cierra con los datos",
    "tipologia_distinta": "es de otra tipología",
    "precio_promocional": "precio promocional",
    "sin_precio": "sin precio publicado",
    "precio_no_usd": "publicado en pesos",
    "sin_superficie": "sin superficie declarada",
    "sin_direccion": "sin dirección",
    "usd_m2_fuera_de_rango": "USD/m² fuera de rango plausible",
    "duplicado": "duplicado de otro aviso",
    "duplicado_de_cluster": "duplicado de otro aviso del mismo inmueble",
    "outlier_estadistico": "descartado por razones estadísticas",
    "recorte_p5_p95": "fuera del rango p5 a p95 de precios ajustados",
    "recorte_p10_p90": "fuera del rango p10 a p90 de precios ajustados",
    "ajuste_excede_el_tope": "el ajuste necesario supera el tope de ±25 %",
    "sin_precio_o_superficie_en_usd": "sin precio en USD o sin superficie",
    "descartado_sin_motivo": "descartado",
}


def _barrio_si_no_esta(direccion: str | None, barrio: str | None) -> str | None:
    """La dirección normalizada suele traer el barrio («Cerviño 4400, Palermo»):
    no repetirlo al lado."""
    if not barrio or not direccion:
        return barrio
    return None if barrio.lower() in direccion.lower() else barrio


def _datos_propiedad(subject: dict[str, Any]) -> list[dict[str, Any]]:
    """La ficha de la propiedad tasada, en el orden en que la lee una persona.

    Un dato que el agente no declaró se imprime como «sin declarar», no se
    omite: el propietario tiene que ver qué se usó y qué no (v1 empezaba por
    la cifra sin decir siquiera cuántos ambientes tenía la propiedad).
    """

    def m2(v: Any) -> str | None:
        n = _num(v)
        return f"{n} m²" if n else None

    def si_no(v: Any) -> str | None:
        if v is None:
            return None
        return "sí" if v else "no"

    piso = subject.get("floor_number")
    condicion = subject.get("condition")
    estado = _ESTADOS.get(str(condicion or ""), condicion) if condicion else None
    edad = subject.get("age_years")
    antiguedad = f"{edad} años" if edad is not None else None
    cocheras = subject.get("parking_spaces")
    expensas = subject.get("expenses_ars")
    return [
        {"k": "Tipo", "v": subject.get("property_type") or None},
        {"k": "Ambientes", "v": subject.get("rooms")},
        {"k": "Dormitorios", "v": subject.get("bedrooms")},
        {"k": "Baños", "v": subject.get("bathrooms")},
        {"k": "Superficie total", "v": m2(subject.get("surface_total"))},
        {"k": "Superficie cubierta", "v": m2(subject.get("surface_covered"))},
        {"k": "Estado", "v": estado},
        {"k": "Orientación", "v": subject.get("orientation")},
        {"k": "Piso", "v": (f"{piso}" if piso not in (None, "") else None)},
        {"k": "Ascensor", "v": si_no(subject.get("has_elevator"))},
        {"k": "Antigüedad", "v": antiguedad},
        {"k": "Cocheras", "v": (str(cocheras) if cocheras else None)},
        {"k": "Expensas", "v": (f"ARS {_plata(expensas)}" if expensas else None)},
    ]


def informe_html(state: ReportState, *, org: str = "Tasador", template: str = "informe/v2") -> str:
    """Arma el HTML del informe. Función pura: sin base, sin red, sin GTK."""
    v = state.get("valuation") or {}
    subject = state.get("subject") or {}
    s = get_settings()

    # La dirección del comparable vive en el candidato (nodo 2), no en el
    # detalle de la valuación: sin este mapa la tabla imprimía ids.
    direcciones = {
        str(c.get("listing_id")): c.get("address") for c in state.get("candidates") or []
    }
    comparables = [
        {
            "source": d.get("source"),
            "direccion": d.get("direccion")
            or direcciones.get(str(d.get("listing_id")))
            or d.get("listing_id", "")[:8],
            "precio": _plata(d.get("snapshot_price")),
            "superficie": _num(d.get("snapshot_surface")),
            "usd_m2": _num(d.get("raw_price_per_m2")),
            "usd_m2_ajustado": _num(d.get("adjusted_price_per_m2")),
            "included": bool(d.get("included")),
            "motivo": MOTIVO_DE_DESCARTE.get(
                d.get("exclusion_reason") or "", d.get("exclusion_reason") or "descartado"
            ),
        }
        # Primero los usados: es lo que sostiene el número. Los descartados van
        # después pero VAN, con su motivo (doc 03 §3.6).
        for d in sorted(v.get("detail", []), key=lambda x: not x.get("included"))
    ]

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        autoescape=True,  # acá SÍ: esto es HTML de verdad
        trim_blocks=True,
        lstrip_blocks=True,
    )
    plantilla = env.get_template(f"{template}.html.jinja")

    return plantilla.render(
        org=org,
        datos_propiedad=_datos_propiedad(subject),
        confianza_score=_decimal2(v.get("confidence_score")),
        usd_m2_sugerido=_plata(v.get("price_per_m2")) if v.get("price_per_m2") else None,
        report_id=state.get("report_id", ""),
        fecha=datetime.now(UTC).strftime("%d/%m/%Y"),
        direccion=subject.get("address_raw") or "—",
        barrio=_barrio_si_no_esta(subject.get("address_raw"), subject.get("neighborhood_name")),
        moneda=v.get("currency") or "USD",
        valor_medio=_plata(v.get("value_mid")),
        valor_min=_plata(v.get("value_low")),
        valor_max=_plata(v.get("value_high")),
        cierre_min=_plata(v.get("closing_low")),
        cierre_max=_plata(v.get("closing_high")),
        confianza=v.get("confidence") or "—",
        confianza_baja=v.get("confidence") == "BAJA",
        comparables=comparables,
        comparables_usados=v.get("comparables_used") or 0,
        comparables_encontrados=v.get("comparables_found") or 0,
        # El markdown ya pasó por el crítico: cada cifra existe en los datos.
        narrativa_html=_Seguro(markdown_a_html(state.get("draft_md") or "")),
        engine_version=s.engine_version,
        method_version=s.method_version,
        prompt_bundle_version=state.get("prompt_bundle_version", ""),
    )


class _Seguro(str):
    """Marca un string como HTML ya renderizado, para que Jinja no lo escape.

    Se usa SOLO con la salida de `markdown_a_html`, que es CommonMark sin HTML
    embebido. Todo lo demás que entra a la plantilla se escapa.
    """

    def __html__(self) -> str:
        return str(self)


def html_a_pdf(html: str) -> bytes:
    """HTML → PDF. Necesita las librerías nativas de GTK (no corre en Windows)."""
    from weasyprint import HTML

    pdf: bytes = HTML(string=html).write_pdf()
    return pdf


async def render_pdf(state: ReportState, cfg: NodeConfig) -> NodeResult:
    from sqlalchemy import select

    from tasador.db.base import get_session_factory
    from tasador.db.models import Organization, ReportArtifact

    if not (state.get("valuation") or {}).get("value_mid"):
        # Sin valor no hay PDF. No es un error: es un INSUFFICIENT_DATA que ya
        # cortó antes; este nodo ni debería haberse alcanzado.
        return NodeResult(updates={}, detail={"motivo": "sin valuación"})

    factory = get_session_factory()
    async with factory() as session:
        org = (
            await session.execute(
                select(Organization.name).where(Organization.id == state["org_id"])
            )
        ).scalar_one_or_none()

    html = informe_html(
        state, org=org or "Tasador", template=str(cfg.param("template", "informe/v2"))
    )

    try:
        pdf = html_a_pdf(html)
    except OSError as e:
        # Falta GTK. Degrada: el informe existe igual, sin PDF.
        raise NodeError("PDF_SIN_LIBRERIAS", f"WeasyPrint no pudo cargar sus librerías: {e}") from e

    sha = hashlib.sha256(pdf).hexdigest()
    # `artifacts_path` y NO `data_path / "artifacts"`: es el volumen que la API
    # y el worker comparten. Con lo anterior, en producción el PDF quedaba en la
    # capa efímera del worker y la API devolvía 404 siempre.
    destino = get_settings().artifacts_path
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / f"{state['report_id']}.pdf"
    ruta.write_bytes(pdf)

    async with factory() as session:
        fila = (
            await session.execute(
                select(ReportArtifact).where(ReportArtifact.report_id == state["report_id"])
            )
        ).scalar_one_or_none()
        datos = {
            "pdf_path": str(ruta),
            "pdf_bytes": len(pdf),
            "sha256": sha,
            "template_version": str(cfg.param("template", "informe/v2")),
        }
        if fila is None:
            session.add(ReportArtifact(report_id=state["report_id"], **datos))
        else:
            for k, val in datos.items():
                setattr(fila, k, val)
        await session.commit()

    log.info("pdf generado", report_id=state["report_id"], bytes=len(pdf), sha=sha[:12])
    return NodeResult(
        updates={},
        detail={"bytes": len(pdf), "sha256": sha[:16], "ruta": str(ruta)},
    )
