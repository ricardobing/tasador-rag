"""Nodo 8 — `market_context`. El único nodo con una crew (ADR-001).

Contesta una pregunta genuinamente abierta: *"¿qué está pasando en el mercado
de este barrio?"*. El resto del grafo es una máquina de estados con reintentos
y salidas tempranas — eso es LangGraph, no una crew.

## Dos decisiones que definen este nodo

**1. Las métricas se calculan con SQL, no las busca un agente.** Doc 04 §8
plantea que las herramientas de la crew consulten la base. Acá las cuatro
consultas corren ANTES y de forma determinística, y a la crew le llega el
paquete de números ya hecho. El motivo es concreto: dejar que un modelo decida
si llama o no a una herramienta agrega latencia, costo y no-determinismo para
conseguir datos que **siempre** queremos. La parte abierta —interpretar esos
números— es lo que queda para la crew, y es donde aporta.

**2. Las herramientas nunca salen a la web.** Todo sale de nuestra base. Un
agente navegando internet traería cifras imposibles de auditar, y el nodo 10
las rechazaría igual: cada número del informe se verifica contra los datos.

## Por qué el contexto es estructurado y no solo prosa

Si la crew devolviera únicamente dos párrafos, cualquier cifra que mencionara
sería inverificable y el crítico rechazaría el informe entero. Por eso el nodo
devuelve **métricas + narrativa**: las métricas entran a `datos_del_informe` y
habilitan que el redactor las cite.

**Degrada, no rompe** (doc 04 §8): si la crew falla, el informe sale sin la
sección de contexto.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import NodeConfig
from tasador.agents.nodes.base import NodeResult
from tasador.agents.state import ReportState
from tasador.db.models import Listing, ListingSnapshot, MarketIndex, Neighborhood
from tasador.llm import UsageLedger
from tasador.settings import get_settings

log = structlog.get_logger()


# ── Las cuatro consultas. Determinísticas, sin LLM, sin red. ─────────────
async def indice_oficial(session: AsyncSession, barrio_id: uuid.UUID) -> dict[str, Any]:
    """El ancla: la serie oficial del GCBA para ese barrio.

    Es lo que hace auditable el contexto. Si nuestro corpus se despega de esta
    serie, o el corpus está sesgado o hay un bug (doc 03, `market_index`).
    """
    fila = (
        await session.execute(
            select(MarketIndex.usd_per_m2, MarketIndex.period, MarketIndex.sample_size)
            .where(MarketIndex.neighborhood_id == barrio_id, MarketIndex.source == "BADATA")
            .order_by(MarketIndex.period.desc())
            .limit(1)
        )
    ).first()
    if fila is None:
        return {"usd_m2": None, "periodo": None, "muestra": None}
    return {
        "usd_m2": float(fila[0]),
        "periodo": fila[1].isoformat(),
        "muestra": fila[2],
    }


async def stock_del_barrio(session: AsyncSession, barrio_id: uuid.UUID) -> dict[str, Any]:
    """Cuántos avisos vigentes hay y a cuánto está el metro cuadrado."""
    filas = (
        (
            await session.execute(
                select(Listing.price, Listing.raw["surface_weighted"].astext).where(
                    Listing.neighborhood_id == barrio_id,
                    Listing.active.is_(True),
                    Listing.currency == "USD",
                    Listing.price.is_not(None),
                )
            )
        )
        .tuples()
        .all()
    )

    valores: list[float] = []
    for precio, sup in filas:
        try:
            s = float(sup or 0)
            if s > 0 and precio:
                valores.append(float(precio) / s)
        except (TypeError, ValueError):
            continue

    if not valores:
        return {"stock_activo": len(filas), "usd_m2_mediano": None, "dispersion": None}

    valores.sort()
    n = len(valores)
    mediana = valores[n // 2] if n % 2 else (valores[n // 2 - 1] + valores[n // 2]) / 2
    desvios = sorted(abs(v - mediana) for v in valores)
    mad = desvios[n // 2] if n % 2 else (desvios[n // 2 - 1] + desvios[n // 2]) / 2
    return {
        "stock_activo": len(filas),
        "usd_m2_mediano": round(mediana),
        # MAD sobre mediana, el mismo criterio robusto que el nodo 7.
        "dispersion": round(mad / mediana, 3) if mediana else None,
    }


async def bajas_de_precio(
    session: AsyncSession, barrio_id: uuid.UUID, dias: int = 90
) -> dict[str, Any]:
    """Qué porcentaje de los avisos bajó de precio, y cuánto.

    Es el dato que más le sirve al propietario y que ningún portal publica:
    dice si el mercado está pidiendo lo que puede cobrar (doc 03,
    `listing_snapshots`).
    """
    desde = datetime.now(UTC) - timedelta(days=dias)
    filas = (
        (
            await session.execute(
                select(ListingSnapshot.listing_id, ListingSnapshot.price)
                .join(Listing, Listing.id == ListingSnapshot.listing_id)
                .where(
                    Listing.neighborhood_id == barrio_id,
                    Listing.active.is_(True),
                    ListingSnapshot.observed_at >= desde,
                    ListingSnapshot.currency == "USD",
                )
                .order_by(ListingSnapshot.listing_id, ListingSnapshot.observed_at)
            )
        )
        .tuples()
        .all()
    )

    por_aviso: dict[uuid.UUID, list[Decimal]] = {}
    for lid, precio in filas:
        if precio is not None:
            por_aviso.setdefault(lid, []).append(precio)

    con_historia = {k: v for k, v in por_aviso.items() if len(v) > 1}
    bajaron = [(v[0], v[-1]) for v in con_historia.values() if v[-1] < v[0]]

    if not con_historia:
        # Sin historial no se puede afirmar nada. NULL y no cero: "no lo
        # sabemos" y "nadie bajó" son cosas distintas.
        return {"avisos_con_historial": 0, "pct_bajaron": None, "baja_promedio_pct": None}

    caidas = [float((a - b) / a * 100) for a, b in bajaron if a]
    return {
        "avisos_con_historial": len(con_historia),
        "pct_bajaron": round(100 * len(bajaron) / len(con_historia)),
        "baja_promedio_pct": round(sum(caidas) / len(caidas), 1) if caidas else None,
    }


async def tiempo_en_mercado(session: AsyncSession, barrio_id: uuid.UUID) -> dict[str, Any]:
    """Mediana de días entre publicación y baja del aviso.

    `delisted_at` es el mejor proxy disponible de "tiempo hasta la venta": no
    existe registro público de operaciones cerradas en Argentina.
    """
    filas = (
        (
            await session.execute(
                select(Listing.published_at, Listing.delisted_at).where(
                    Listing.neighborhood_id == barrio_id,
                    Listing.delisted_at.is_not(None),
                    Listing.published_at.is_not(None),
                )
            )
        )
        .tuples()
        .all()
    )
    dias = sorted((d.date() - p).days for p, d in filas if d and p)
    if not dias:
        return {"avisos_dados_de_baja": 0, "dias_mediano": None}
    n = len(dias)
    return {
        "avisos_dados_de_baja": n,
        "dias_mediano": dias[n // 2] if n % 2 else (dias[n // 2 - 1] + dias[n // 2]) // 2,
    }


async def metricas_del_barrio(session: AsyncSession, barrio_id: uuid.UUID) -> dict[str, Any]:
    """Las cuatro consultas. Costo cero, sin LLM.

    **SECUENCIALES, no en paralelo.** Una `AsyncSession` de SQLAlchemy no es
    segura para operaciones concurrentes: con `asyncio.gather` sobre la misma
    sesión revienta con "this session is provisioning a new connection".
    Y no siempre — depende de si la conexión ya estaba abierta, así que en una
    prueba aislada pasa y en el grafo falla. Peor que un error consistente.

    No se pierde nada: son cuatro consultas locales de milisegundos. Si algún
    día pesan, cada una va con su propia sesión, no con `gather` sobre esta.
    """
    oficial = await indice_oficial(session, barrio_id)
    stock = await stock_del_barrio(session, barrio_id)
    bajas = await bajas_de_precio(session, barrio_id)
    tiempo = await tiempo_en_mercado(session, barrio_id)
    datos: dict[str, Any] = {
        "indice_oficial_gcba": oficial,
        "stock": stock,
        "bajas_de_precio_90d": bajas,
        "tiempo_en_mercado": tiempo,
    }

    # El chequeo de sesgo, calculado acá porque es la comparación que hace
    # creíble a todo lo demás.
    of, co = oficial.get("usd_m2"), stock.get("usd_m2_mediano")
    if of and co:
        datos["desvio_vs_oficial_pct"] = round((co - of) / of * 100, 1)

    return datos


# ── La crew ──────────────────────────────────────────────────────────────
def _textos(
    cfg: NodeConfig, barrio: str, metricas: dict[str, Any], analisis: str = ""
) -> dict[str, str]:
    """Las piezas del prompt del nodo 8, desde el archivo versionado.

    Los dos motores leen de acá. Antes cada uno tenía su copia en Python y eran
    paráfrasis distintas — ver la cabecera de `prompts/market_context/v1.jinja`.
    """
    import json

    from tasador.agents import prompts

    return prompts.secciones(
        cfg.prompt or "market_context/v1",
        barrio=barrio,
        datos=json.dumps(metricas, ensure_ascii=False, indent=2),
        analisis=analisis,
    )


def _construir_crew(
    cfg: NodeConfig, barrio: str, metricas: dict[str, Any], step_callback: Any = None
) -> Any:
    """Dos agentes: uno lee los números, otro los cuenta.

    El LLM apunta a NUESTRO gateway (ADR-003) y se le inyecta el cliente httpx
    instrumentado, así el costo de la crew entra en `report_events` como el de
    cualquier otro nodo.
    """
    import os

    s = get_settings()

    # CrewAI guarda memoria y telemetría en disco y por defecto va a $HOME. En
    # el contenedor `/home/app` no existe y el usuario no puede crearlo:
    # `PermissionError` al CONSTRUIR la crew, antes de la primera llamada.
    #
    # ⚠️ La palanca NO es `CREWAI_STORAGE_DIR`: pese al nombre, esa variable es
    # el NOMBRE de la app, no la ruta (`crewai_core/paths.py`). La ruta la
    # resuelve `appdirs`, que en Linux mira `XDG_DATA_HOME`. Poner la primera
    # no arregla nada y parece que sí.
    # ⚠️ TELEMETRÍA APAGADA, y no es solo por el permiso.
    #
    # CrewAI trae un `trace_listener` que escribe un token de autenticación en
    # $HOME y manda trazas afuera. En el contenedor eso revienta con
    # `PermissionError: /home/app` —el usuario no puede escribir ahí— pero el
    # motivo de fondo es otro: este sistema procesa direcciones y datos de
    # propiedades de clientes, y doc 10 §3 es explícito sobre qué sale del
    # sistema y hacia dónde. La telemetría de una librería no está en esa lista.
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

    # Y el almacenamiento propio de la crew, al volumen escribible. La palanca
    # es `XDG_DATA_HOME` (appdirs); `CREWAI_STORAGE_DIR` pese al nombre es el
    # NOMBRE de la app, no la ruta (`crewai_core/paths.py`).
    almacen = s.data_path / "crewai"
    almacen.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_DATA_HOME", str(almacen))
    os.environ.setdefault("CREWAI_STORAGE_DIR", "tasador")

    from crewai import Agent, Crew, Task
    from crewai.llm import LLM

    llm = LLM(
        model=f"openai/{cfg.task or 'judge'}",
        base_url=f"{s.litellm_base_url.rstrip('/')}/v1",
        api_key=s.litellm_master_key.get_secret_value(),
        temperature=float(cfg.param("temperature", 0.2)),
        max_tokens=int(cfg.param("max_tokens", 2048)),
    )

    t = _textos(cfg, barrio, metricas)

    analista = Agent(
        role=t["analista.role"],
        goal=t["analista.goal"],
        backstory=t["analista.backstory"],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )
    redactor = Agent(
        role=t["redactor.role"],
        goal=t["redactor.goal"],
        backstory=t["redactor.backstory"],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    analizar = Task(
        description=t["analista.task"],
        expected_output=t["analista.expected_output"],
        agent=analista,
    )
    redactar = Task(
        description=t["redactor.task"],
        expected_output=t["redactor.expected_output"],
        agent=redactor,
        # CrewAI le pasa la salida de `analizar` por contexto; por eso la
        # sección `redactor.task` renderiza sin `analisis`.
        context=[analizar],
    )

    return Crew(
        agents=[analista, redactor],
        tasks=[analizar, redactar],
        verbose=False,
        step_callback=step_callback,
    )


def _mensaje(t: dict[str, str], agente: str) -> str:
    """Las cuatro piezas de un agente, en el único mensaje que acepta un
    `chat.completions`. CrewAI las quiere separadas; acá se concatenan — mismo
    texto, misma versión, mismo hash."""
    return "\n\n".join(
        t[f"{agente}.{p}"] for p in ("role", "backstory", "task") if t.get(f"{agente}.{p}")
    )


async def _secuencial(
    cfg: NodeConfig, barrio: str, metricas: dict[str, Any]
) -> tuple[str, list[Any]]:
    """Los dos agentes, encadenados con nuestro cliente. El motor por defecto.

    Con las herramientas ya movidas a SQL determinístico, la "crew" del doc 04
    es exactamente esto: un analista que lee los números y un redactor que los
    cuenta. Hacerlo con el cliente propio mantiene la contabilidad de costo por
    llamada, que es la razón por la que este camino es el default —
    ver `_con_crewai` para el porqué.
    """
    from tasador.llm import LlmClient

    cliente = LlmClient()
    usos: list[Any] = []
    try:
        analisis, u1 = await cliente.complete(
            cfg.task or "judge",
            [{"role": "user", "content": _mensaje(_textos(cfg, barrio, metricas), "analista")}],
            temperature=float(cfg.param("temperature", 0.2)),
            max_tokens=int(cfg.param("max_tokens", 2048)),
        )
        usos.append(u1)

        # El redactor secuencial no tiene el `context=[analizar]` de CrewAI: el
        # análisis se le pasa dentro de la misma sección del prompt.
        narrativa, u2 = await cliente.complete(
            cfg.task or "judge",
            [
                {
                    "role": "user",
                    "content": _mensaje(
                        _textos(cfg, barrio, metricas, analisis=analisis), "redactor"
                    ),
                }
            ],
            temperature=float(cfg.param("temperature", 0.2)),
            max_tokens=int(cfg.param("max_tokens", 2048)),
        )
        usos.append(u2)
    finally:
        await cliente.close()

    return narrativa.strip(), usos


async def precios_del_gateway(task: str) -> tuple[Decimal, Decimal]:
    """Precio por token de una tarea, preguntado al gateway.

    LiteLLM es la única fuente de verdad de precios (ADR-003): están declarados
    en `config/litellm.yaml` como `model_info` y se leen de `/model/info`. Esto
    existe para poder costear a CrewAI, que no nos deja ver sus respuestas y
    por lo tanto no trae el `x-litellm-response-cost` de cada llamada.

    Devuelve (0, 0) si el gateway no sabe el precio. Un costo en cero es una
    señal de "no medido", no de "gratis" — y se informa como tal.
    """
    import httpx

    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{s.litellm_base_url.rstrip('/')}/model/info",
                headers={"Authorization": f"Bearer {s.litellm_master_key.get_secret_value()}"},
            )
            r.raise_for_status()
            for m in r.json().get("data", []):
                if m.get("model_name") == task:
                    info = m.get("model_info") or {}
                    return (
                        Decimal(str(info.get("input_cost_per_token") or 0)),
                        Decimal(str(info.get("output_cost_per_token") or 0)),
                    )
    except Exception:
        log.warning("no se pudo leer el precio del gateway", task=task, exc_info=True)
    return Decimal(0), Decimal(0)


async def _con_crewai(
    cfg: NodeConfig, barrio: str, metricas: dict[str, Any]
) -> tuple[str, list[Any]]:
    """El mismo trabajo con CrewAI. Opcional: `engine: crewai` en agents.yaml.

    ⚠️ **Con este motor se pierde el costo por llamada.** CrewAI construye sus
    propios clientes HTTP y no acepta que se le inyecte el nuestro: pide un
    `httpx.Client` y un `httpx.AsyncClient` con el mismo parámetro, así que no
    hay forma de cubrir los dos. Medido el 14/08.

    Como los headers de LiteLLM viajan en esa respuesta y nosotros no la vemos,
    el nodo 8 sería el único del grafo cuyo gasto no aparece en
    `core.report_events`, y el costo por informe dejaría de ser una suma para
    volver a ser una estimación. Por eso el default es `secuencial`.

    Se deja disponible porque la decisión es del que opera, no del código: si
    mañana la crew crece a un trabajo genuinamente multi-agente —con
    delegación real entre roles— el trade-off puede darse vuelta.
    """
    from tasador.llm import LlmUsage

    pasos: list[str] = []

    def _paso(salida: Any) -> None:
        """Se llama en cada paso de un agente. Es la única ventana que hay a lo
        que la crew está haciendo por dentro."""
        pasos.append(type(salida).__name__)

    task = cfg.task or "judge"
    crew = _construir_crew(cfg, barrio, metricas, step_callback=_paso)

    t0 = datetime.now(UTC)
    resultado = await crew.kickoff_async()
    ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)

    # CrewAI no expone el costo, pero sí los tokens y **cuántas llamadas hizo**.
    # El precio sale del gateway, que es la fuente de verdad (ADR-003).
    m = crew.usage_metrics
    ci, co = await precios_del_gateway(task)
    costo = ci * Decimal(m.prompt_tokens or 0) + co * Decimal(m.completion_tokens or 0)

    uso = LlmUsage(
        task=task,
        provider_model=f"crewai:{task}",
        tokens_in=m.prompt_tokens or 0,
        tokens_out=m.completion_tokens or 0,
        cost_usd=costo,
        duration_ms=ms,
        # Una sola entrada agregada: CrewAI no da el desglose por llamada.
        attempt=m.successful_requests or 1,
    )
    log.info(
        "crew terminada",
        llamadas=m.successful_requests,
        pasos=len(pasos),
        tokens_in=m.prompt_tokens,
        tokens_out=m.completion_tokens,
        tokens_razonamiento=getattr(m, "reasoning_tokens", None),
        costo_usd=float(costo),
    )
    return str(resultado).strip(), [uso]


async def market_context(state: ReportState, cfg: NodeConfig) -> NodeResult:
    from tasador.db.base import get_session_factory

    subject = state.get("subject") or {}
    barrio_id = subject.get("neighborhood_id")
    if not barrio_id:
        return NodeResult(updates={}, detail={"motivo": "sin barrio"})

    async with get_session_factory()() as session:
        metricas = await metricas_del_barrio(session, uuid.UUID(str(barrio_id)))
        barrio = (
            await session.execute(
                select(Neighborhood.name).where(Neighborhood.id == uuid.UUID(str(barrio_id)))
            )
        ).scalar_one_or_none() or "el barrio"

    contexto: dict[str, Any] = {"barrio": barrio, **metricas}
    ledger = UsageLedger()
    motor = str(cfg.param("engine", "secuencial"))
    error: str | None = None

    # ── La parte abierta: interpretar ────────────────────────────────────
    try:
        if motor == "crewai":
            narrativa, usos = await _con_crewai(cfg, barrio, metricas)
        else:
            narrativa, usos = await _secuencial(cfg, barrio, metricas)
        ledger.extend(usos)
        contexto["narrativa"] = narrativa
    except Exception as e:
        # Degrada: el informe sale sin la sección de contexto (doc 04 §8).
        # Pero el MOTIVO va al detalle del evento, no solo al contexto: un nodo
        # que degrada sin decir por qué deja la traza inservible justo cuando
        # más se la necesita. Pasó: `con_narrativa: false` y ni una pista.
        log.warning("el contexto de mercado falló", error=f"{type(e).__name__}: {e}"[:250])
        contexto["narrativa"] = None
        error = f"{type(e).__name__}: {e}"[:400]

    return NodeResult(
        updates={"market_context": contexto},
        detail={
            "motor": motor,
            # Cuántas llamadas al modelo hizo este nodo. Es la métrica que
            # separa a los dos motores: el secuencial hace exactamente 2
            # (analista + redactor); CrewAI hace más, porque además de las
            # tareas corre su propio andamiaje. Con `engine: crewai` sale de
            # `usage_metrics.successful_requests`; con `secuencial`, de
            # nuestra contabilidad.
            "llamadas": sum(u.attempt for u in ledger.usos)
            if motor == "crewai"
            else len(ledger.usos),
            "tokens_in": ledger.tokens_in,
            "tokens_out": ledger.tokens_out,
            "barrio": barrio,
            "stock_activo": metricas["stock"]["stock_activo"],
            "usd_m2_corpus": metricas["stock"]["usd_m2_mediano"],
            "usd_m2_oficial": metricas["indice_oficial_gcba"]["usd_m2"],
            "desvio_vs_oficial_pct": metricas.get("desvio_vs_oficial_pct"),
            "con_narrativa": bool(contexto.get("narrativa")),
            "error": error,
        },
        usage=ledger,
    )
