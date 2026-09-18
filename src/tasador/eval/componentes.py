"""Persistencia y comparación de los evals de componente — doc 09 §3.3.

Los scripts `eval_extraccion.py` y `eval_curaduria.py` ya medían bien; lo que
faltaba era que el número quedara en algún lado. Sin eso,
`--fail-on-regression` sobre `GOLDEN_SET` no tenía contra qué comparar y el
gate devolvía rojo siempre — que es honesto, pero inútil.

La regla de comparación es la misma que la del backtest y por el mismo motivo:
**se compara contra la corrida anterior del mismo componente y la misma
métrica, no contra un umbral escrito a mano.** Un umbral en un YAML envejece; se
actualiza cuando alguien se acuerda, y mientras tanto el gate pasa por inercia.

Los objetivos de doc 09 §3.3 sí están, pero como piso absoluto y no como vara
de progreso: sirven para decir "esto todavía no está listo", no para decir "esto
mejoró".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tasador.agents.config import load_agents_config
from tasador.db.models import ComponentRun
from tasador.settings import get_settings

log = structlog.get_logger()

# doc 09 §3.3. Son PISOS, no metas móviles.
OBJETIVOS: dict[tuple[str, str], Decimal] = {
    ("extraccion", "exactitud"): Decimal("0.92"),
    ("curaduria", "recall"): Decimal("0.90"),
    ("curaduria", "precision"): Decimal("0.90"),
    ("dedup", "precision"): Decimal("0.95"),
}


@dataclass(slots=True)
class Medicion:
    componente: str
    metrica: str
    valor: Decimal
    n: int
    prompt: str | None = None
    task: str | None = None
    detalle: dict[str, object] | None = None


async def guardar(
    session: AsyncSession, m: Medicion, *, git_ref: str | None = None
) -> ComponentRun:
    s = get_settings()
    fila = ComponentRun(
        componente=m.componente,
        metrica=m.metrica,
        valor=m.valor,
        n=m.n,
        objetivo=OBJETIVOS.get((m.componente, m.metrica)),
        engine_version=s.engine_version,
        prompt_bundle_version=load_agents_config().bundle_hash,
        prompt=m.prompt,
        task=m.task,
        detalle=dict(m.detalle or {}),
        git_ref=git_ref,
    )
    session.add(fila)
    await session.commit()
    return fila


# Cuántas corridas previas se resumen para tener con qué comparar.
#
# **MEDIDO el 14/08 y es el número que más cambia cómo se lee este eval:**
# cuatro corridas del eval de extracción, sin tocar una línea de código ni de
# prompt, dieron
#
#     60,8%   77,0%   75,7%   78,4%      ->  17,6 puntos de AMPLITUD
#
# El 68% que figura en el informe de la Etapa 3 es una muestra de esa
# distribución. No estaba mal medido: estaba mal interpretado, por todos —
# incluido el propio informe, que ya avisaba "son UNA corrida; no alcanzan para
# declarar una mejora chica" y aun así el número se citó como si fuera EL
# número.
#
# Consecuencia práctica: comparar una corrida contra la anterior no puede
# decidir nada. Se compara contra la MEDIANA de las últimas, que es lo único
# que tiene sentido con esta varianza.
VENTANA = 5


async def anterior(session: AsyncSession, componente: str, metrica: str) -> ComponentRun | None:
    """La corrida previa del MISMO componente y la MISMA métrica.

    No se filtra por `prompt`: comparar la v2 contra la v2 anterior escondería
    justo lo que se quiere ver cuando alguien cambia de prompt.
    """
    return (
        await session.execute(
            select(ComponentRun)
            .where(ComponentRun.componente == componente, ComponentRun.metrica == metrica)
            .order_by(ComponentRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def ultimas(
    session: AsyncSession, componente: str, metrica: str, *, k: int = VENTANA
) -> list[ComponentRun]:
    return list(
        (
            await session.execute(
                select(ComponentRun)
                .where(ComponentRun.componente == componente, ComponentRun.metrica == metrica)
                .order_by(ComponentRun.created_at.desc())
                .limit(k)
            )
        )
        .scalars()
        .all()
    )


def resumen(filas: list[ComponentRun]) -> tuple[Decimal, Decimal] | None:
    """`(mediana, amplitud)` de un conjunto de corridas, o None si no hay.

    La amplitud se reporta SIEMPRE al lado de la mediana. Una mediana sola
    invita a leerla como si fuera un número estable, y acá no lo es.
    """
    if not filas:
        return None
    valores = sorted(f.valor for f in filas)
    n = len(valores)
    mediana = valores[n // 2] if n % 2 else (valores[n // 2 - 1] + valores[n // 2]) / 2
    return mediana, valores[-1] - valores[0]


@dataclass(slots=True)
class Veredicto:
    lineas: list[str]
    regresion: bool
    bajo_objetivo: bool


def comparar(
    m: Medicion,
    previas: list[ComponentRun],
    *,
    minimo_para_comparar: int = 3,
) -> Veredicto:
    """Compara contra la MEDIANA de las corridas previas, no contra la última.

    Y **no declara regresión hasta tener con qué**: con menos de
    `minimo_para_comparar` corridas previas, o con una amplitud que se come el
    delta, informa y no corta.

    La tolerancia no es un número elegido: sale de la amplitud medida. Si las
    últimas cinco corridas van de 60,8% a 78,4%, una baja de 5 pp está adentro del
    ruido y llamarlo regresión enseñaría a ignorar el gate — que es la única
    forma real de romper un control de calidad.
    """
    objetivo = OBJETIVOS.get((m.componente, m.metrica))
    lineas = [f"{m.componente}/{m.metrica}: {m.valor:.1%} sobre {m.n} casos"]
    bajo_objetivo = objetivo is not None and m.valor < objetivo
    if objetivo is not None:
        marca = "✅" if not bajo_objetivo else "❌"
        lineas.append(f"  {marca} objetivo doc 09 §3.3: {objetivo:.0%}")

    r = resumen(previas)
    if r is None:
        lineas.append("  (sin corridas previas: esta queda como línea de base)")
        return Veredicto(lineas, regresion=False, bajo_objetivo=bajo_objetivo)

    mediana, amplitud = r
    delta_pp = (m.valor - mediana) * 100
    lineas.append(
        f"  mediana de las últimas {len(previas)}: {mediana:.1%} "
        f"(amplitud {amplitud * 100:.1f} pp)  ->  {delta_pp:+.1f} pp"
    )

    if any(p.n != m.n for p in previas):
        # Cambió el tamaño del set: deja de ser peras con peras. Se avisa y NO
        # se corta, porque agrandar el golden set es exactamente lo que hay que
        # hacer y no puede costar un gate en rojo.
        lineas.append("  ⚠️  el tamaño del set cambió: el delta no es comparable")
        return Veredicto(lineas, regresion=False, bajo_objetivo=bajo_objetivo)

    if len(previas) < minimo_para_comparar:
        lineas.append(
            f"  ⚠️  hacen falta {minimo_para_comparar} corridas previas para decidir; "
            f"hay {len(previas)}. Se informa y no se corta."
        )
        return Veredicto(lineas, regresion=False, bajo_objetivo=bajo_objetivo)

    # El umbral ES la amplitud observada, con un piso de 2 pp para que un set
    # que algún día sea estable no quede sin gate.
    umbral_pp = max(float(amplitud) * 100, 2.0)
    regresion = delta_pp < -umbral_pp
    if regresion:
        lineas.append(f"  🔴 EMPEORÓ más que la amplitud del ruido ({umbral_pp:.1f} pp)")
    elif delta_pp < 0:
        lineas.append(f"  ⚠️  bajó {abs(delta_pp):.1f} pp, dentro del ruido ({umbral_pp:.1f} pp)")
    else:
        lineas.append(f"  ✅ subió {delta_pp:.1f} pp (ruido: ±{umbral_pp:.1f} pp)")
    return Veredicto(lineas, regresion=regresion, bajo_objetivo=bajo_objetivo)
