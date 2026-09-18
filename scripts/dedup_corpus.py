"""Duplicados en TODO el corpus, no solo dentro de un informe.

    uv run python scripts/dedup_corpus.py --barrio Palermo
    uv run python scripts/dedup_corpus.py --barrio Palermo --aplicar
    uv run python scripts/dedup_corpus.py            # todos los barrios

## Por qué hace falta esto además del nodo 5

El nodo 5 desduplica los candidatos de UN informe: 20-60 avisos, todos-contra-
todos, y el costo no importa. Este script mira el corpus entero, y ahí la
pregunta es otra — cuántas propiedades ÚNICAS hay realmente detrás de los
avisos.

La respuesta cambia números que ya se están reportando. "477 avisos vigentes en
Palermo" no es lo mismo que "477 propiedades": un departamento republicado por
tres inmobiliarias pesa TRIPLE en la mediana del barrio, y esa mediana es el
ancla del contexto de mercado del nodo 8 y del chequeo de sesgo contra el GCBA.

## El bloqueo, y por qué no es opcional

Todos-contra-todos es O(n²). Con 4.007 avisos de Palermo son 8 millones de
pares; con los ~88.000 de CABA serían **3.900 millones**: no es lento, es
imposible.

Se bloquea por `(barrio, CALLE)` — ver `_bloques` para por qué la calle y no
los ambientes, y qué se pierde. Medido sobre Palermo:

```
    todos contra todos   8.026.021 pares
    con bloqueo            149.370 pares en 279 bloques   (98,1% menos)
    tiempo                      20 segundos
```

## El número de duplicados depende del tamaño del corpus, y mucho

```
     591 avisos   ->   3,1% duplicados
   4.007 avisos   ->  33,3% duplicados   ·  2.672 propiedades reales
```

No es que el detector haya mejorado: con más avisos de la misma calle hay más
chances de que el mismo inmueble aparezca publicado varias veces. **Medir
duplicados sobre una muestra chica subestima siempre**, y por un factor que no
es chico.

Importa porque un departamento republicado tres veces pesa TRIPLE en la mediana
del barrio si no se detecta, y esa mediana es el ancla del nodo 8 y del chequeo
de sesgo contra el GCBA.

## Las capas son LAS MISMAS que las del nodo 5

`_capa_1`, `_capa_2` y `_zona_gris` se importan, no se reimplementan. Dos
implementaciones del criterio de duplicado darían dos respuestas distintas a
"¿cuántas propiedades hay?" según quién pregunte.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from itertools import combinations
from typing import Any

from tasador.agents.config import load_agents_config
from tasador.agents.nodes.dedup import (
    _capa_1,
    _capa_2,
    _zona_gris,
    agrupar_duplicados,
    clave_direccion,
    elegir_canonico,
)
from tasador.agents.state import Candidate
from tasador.cli import run


async def _cargar(barrio: str | None) -> list[Candidate]:
    from sqlalchemy import select

    from tasador.db.base import get_session_factory
    from tasador.db.models import Listing, Neighborhood

    async with get_session_factory()() as session:
        q = (
            select(Listing, Neighborhood.name)
            .join(Neighborhood, Neighborhood.id == Listing.neighborhood_id)
            .where(Listing.active.is_(True), Listing.price.is_not(None))
        )
        if barrio:
            q = q.where(Neighborhood.name == barrio)
        filas = (await session.execute(q)).all()

    return [
        {
            "listing_id": str(li.id),
            "source": li.source,
            "source_id": li.source_id,
            "address": li.address_raw,
            "price": str(li.price),
            "currency": li.currency or "USD",
            "rooms": (li.raw or {}).get("rooms"),
            "surface_total": (li.raw or {}).get("surface_total"),
            "surface_covered": (li.raw or {}).get("surface_covered"),
            "description": li.description,
            "url": li.url,
            "barrio": nombre,
            "included": True,
        }  # type: ignore[misc]
        for li, nombre in filas
    ]


def _bloques(avisos: list[Candidate]) -> dict[Any, list[Candidate]]:
    """Por CALLE, no por ambientes.

    ## La primera versión bloqueaba por `(barrio, ambientes)` y no bloqueaba nada

    Los avisos sin `rooms` iban a TODOS los bloques de su barrio, con este
    comentario: *"son pocos y el costo se banca"*. Era cierto con 591 avisos y
    falso con 4.007 — la mayoría de la tanda del 15/08 no declara ambientes.
    Resultado medido:

        bloques: 8  ·  tamaños: [3805, 3792, 3768, ...]  ·  56.310.504 pares

    O sea: ocho copias del corpus entero. El análisis no terminaba en 10
    minutos. **Una heurística de tamaño calibrada contra los datos de ayer es
    una bomba de tiempo**, y el comentario que la justificaba envejeció peor
    que el código.

    ## Por qué la calle es la clave correcta

    No es solo que sea más chica: es que **es la que corresponde al criterio**.
    Las tres capas comparan direcciones —capa 1 exige la misma cuadra, capa 2
    exige trigram ≥ 0,85 sobre esa clave— así que dos avisos que puedan llegar
    a ser el mismo inmueble están en la misma calle casi por definición.

    **Lo que se pierde, dicho:** un par en calles DISTINTAS ya no se compara
    nunca. La capa 3 los habría mirado si la similitud daba ≥ 0,45 —«Cerviño»
    contra «Cervino 3900», errores de tipeo—. A cambio, el análisis pasa de no
    terminar a correr en segundos. Es un intercambio explícito y reversible:
    `--sin-bloqueo` compara todos contra todos.
    """
    from tasador.agents.nodes.dedup import clave_cuadra

    por_clave: dict[Any, list[Candidate]] = defaultdict(list)
    for a in avisos:
        # La clave de cuadra sin el número: «PARAGUAY 4000» -> «PARAGUAY».
        cuadra = clave_cuadra(a.get("address"))
        calle = (
            cuadra.rsplit(" ", 1)[0] if cuadra and cuadra.rsplit(" ", 1)[-1].isdigit() else cuadra
        )
        por_clave[(a.get("barrio"), calle)].append(a)
    return por_clave


def _analizar(avisos: list[Candidate], cfg: Any, *, bloquear: bool = True) -> dict[str, Any]:
    bloques = _bloques(avisos) if bloquear else {"todos": avisos}
    pares_evaluados = 0
    pares: set[tuple[str, str]] = set()
    por_capa = {"capa_1_exacta": 0, "capa_2_fuzzy": 0}
    zona_gris: list[tuple[Candidate, Candidate]] = []

    for grupo in bloques.values():
        for a, b in combinations(grupo, 2):
            pares_evaluados += 1
            clave = tuple(sorted((str(a["listing_id"]), str(b["listing_id"]))))
            if clave in pares:
                continue
            if _capa_1(a, b, cfg):
                por_capa["capa_1_exacta"] += 1
                pares.add(clave)  # type: ignore[arg-type]
            elif _capa_2(a, b, cfg):
                por_capa["capa_2_fuzzy"] += 1
                pares.add(clave)  # type: ignore[arg-type]
            elif _zona_gris(a, b):
                zona_gris.append((a, b))

    refs = [str(a["listing_id"]) for a in avisos]
    grupos = agrupar_duplicados(refs, pares)
    por_id = {str(a["listing_id"]): a for a in avisos}

    return {
        "avisos": len(avisos),
        "bloques": len(bloques),
        "pares_evaluados": pares_evaluados,
        "pares_todos_contra_todos": len(avisos) * (len(avisos) - 1) // 2,
        "por_capa": por_capa,
        "grupos": [[por_id[r] for r in g] for g in grupos],
        "zona_gris": zona_gris,
    }


def _reportar(res: dict[str, Any], mostrar: int) -> None:
    grupos = res["grupos"]
    duplicados = sum(len(g) - 1 for g in grupos)
    unicas = res["avisos"] - duplicados

    print("\n" + "═" * 70)
    print("DUPLICADOS EN EL CORPUS")
    print("═" * 70)
    print(f"  avisos vigentes        {res['avisos']:>7}")
    print(f"  propiedades únicas     {unicas:>7}   ← lo que hay de verdad")
    print(f"  avisos duplicados      {duplicados:>7}   ({duplicados / res['avisos']:.1%})")
    print(f"  grupos                 {len(grupos):>7}")
    print()
    print(f"  capa 1 (dir+sup+amb)   {res['por_capa']['capa_1_exacta']:>7} pares")
    print(f"  capa 2 (trigram+precio){res['por_capa']['capa_2_fuzzy']:>7} pares")
    print(f"  zona gris (al juez)    {len(res['zona_gris']):>7} pares")
    print()
    ahorro = 1 - res["pares_evaluados"] / max(res["pares_todos_contra_todos"], 1)
    print(f"  pares evaluados        {res['pares_evaluados']:>7}  en {res['bloques']} bloques")
    print(f"  todos contra todos     {res['pares_todos_contra_todos']:>7}  ({ahorro:.1%} menos)")

    # Lo que de verdad interesa: cuántos son ENTRE portales. Un duplicado
    # dentro del mismo portal suele ser el mismo aviso republicado; entre
    # portales es la inmobiliaria compartiendo la propiedad, que es el caso
    # que infla la mediana del barrio.
    entre_portales = [g for g in grupos if len({str(a["source"]) for a in g}) > 1]
    print(f"\n  grupos ENTRE portales  {len(entre_portales):>7} de {len(grupos)}")

    if grupos:
        print(f"\n{'─' * 70}\nPRIMEROS {min(mostrar, len(grupos))} GRUPOS\n")
        for g in sorted(grupos, key=len, reverse=True)[:mostrar]:
            canonico = elegir_canonico(g)
            print(f"  {len(g)} avisos · clave «{clave_direccion(g[0].get('address'))}»")
            for a in g:
                marca = "★" if a is canonico else " "
                print(
                    f"    {marca} {a['source']!s:<10}{str(a.get('address') or 's/d')[:34]:<36}"
                    f"USD {int(float(a['price'])):>9,}".replace(",", ".")
                    + f"  {a.get('rooms') or '?'} amb"
                )
            print()


async def _main(args: argparse.Namespace) -> int:
    cfg = load_agents_config().node("dedup_cluster")
    avisos = await _cargar(args.barrio)
    if not avisos:
        print("No hay avisos vigentes con precio.")
        return 2

    res = _analizar(avisos, cfg, bloquear=not args.sin_bloqueo)
    _reportar(res, args.mostrar)

    nuevos: set[tuple[str, str]] = set()
    if args.juez and res["zona_gris"]:
        nuevos = await _juzgar(res["zona_gris"], cfg, args.lote, args.paralelo)
        if nuevos:
            # Se re-arman los grupos con los pares que agregó el juez. NO se
            # suman "a mano" al conteo: un par nuevo puede unir dos grupos que
            # ya existían, y contar pares en vez de componentes daría un número
            # distinto del que después se escribe en la base.
            from tasador.agents.nodes.dedup import agrupar_duplicados as _agrupar

            previos = {
                tuple(sorted((str(g[0]["listing_id"]), str(o["listing_id"]))))
                for g in res["grupos"]
                for o in g[1:]
            }
            refs = [str(a["listing_id"]) for a in avisos]
            todos = previos | nuevos
            por_id = {str(a["listing_id"]): a for a in avisos}
            res["grupos"] = [[por_id[r] for r in g] for g in _agrupar(refs, todos)]
            print(f"\n{'─' * 70}\nCON EL JUEZ (capa 3)\n")
            _reportar(res, args.mostrar)
    elif args.zona_gris and res["zona_gris"]:
        print(f"{'─' * 70}\nZONA GRIS — {len(res['zona_gris'])} pares que decidiría el juez\n")
        for a, b in res["zona_gris"][: args.mostrar]:
            print(f"  {str(a.get('address'))[:32]:<34} vs {str(b.get('address'))[:32]}")

    if args.aplicar:
        n = await _aplicar(res["grupos"], avisos, del_juez=nuevos)
        print(f"{n} avisos marcados con su `cluster_id` en la base.")
    else:
        print("\n(solo análisis; con --aplicar se escriben los cluster_id)")
    return 0


async def _juzgar(
    pares: list[tuple[Candidate, Candidate]], cfg: Any, tam: int, paralelo: int = 6
) -> set[tuple[str, str]]:
    """La capa 3 sobre TODA la zona gris, en lotes PARALELOS.

    El nodo 5 la acota a `max_llm_pairs` (15) porque dentro de un informe el
    juez está en el camino crítico. Acá corre offline sobre el corpus entero:
    el tope no aplica y lo que importa es no dejar pares sin decidir.

    ## Por qué en paralelo, medido

    La primera versión iba secuencial con lotes de 10. Sobre los 282 pares del
    corpus del 14/08:

        ~4 minutos por lote  ·  12 lotes en 50 min  ·  timeout a partir del 13
        -> 120 de 282 pares resueltos, y casi dos horas para el resto

    El diagnóstico importa porque cambia qué hay que arreglar a escala: el
    cuello NO es el número de pares —el bloqueo por (barrio, ambientes) ya lo
    baja 59%— sino **la latencia por llamada**. Un lote de 10 pares son 20
    descripciones de 700 caracteres; el juez razona sobre todo eso y se pasa
    del timeout de 120 s.

    Dos palancas, las dos necesarias:

      · lotes MÁS CHICOS  -> cada llamada entra en el timeout
      · lotes EN PARALELO -> el tiempo total deja de ser la suma

    Es exactamente lo que ya se había hecho en el nodo 4 con
    `max_concurrent_batches` (253 s -> 90 s, informe Etapa 3 §6). El mismo
    problema apareció dos veces; la segunda vez había un precedente.

    `no_se` NO agrupa, igual que en el nodo. El criterio es asimétrico y no
    cambia por correr offline: fusionar dos inmuebles que no lo son borra un
    comparable legítimo; no fusionar dos que sí lo son deja uno de más, que la
    mediana diluye.
    """
    import asyncio

    from tasador.agents.nodes.dedup import LoteDedup, _payload_par
    from tasador.agents.prompts import render
    from tasador.llm import AVISO_INJECTION, LlmClient, LlmError, LlmValidationError

    por_ref = {str(a["listing_id"]): a for par in pares for a in par}
    lotes = [pares[i : i + tam] for i in range(0, len(pares), tam)]
    limite = asyncio.Semaphore(paralelo)
    cliente = LlmClient()

    iguales: set[tuple[str, str]] = set()
    costo = 0.0
    veredictos = {"mismo": 0, "distinto": 0, "no_se": 0}
    fallados = 0
    hechos = 0

    async def _un_lote(n: int, lote: list[tuple[Candidate, Candidate]]) -> None:
        nonlocal costo, fallados, hechos
        async with limite:
            try:
                salida, usos = await cliente.structured(
                    cfg.task or "judge",
                    [
                        {
                            "role": "user",
                            "content": render(
                                cfg.prompt or "dedup_judge/v1",
                                aviso_injection=AVISO_INJECTION,
                                pares=[_payload_par(a, b) for a, b in lote],
                            ),
                        }
                    ],
                    LoteDedup,
                    temperature=float(cfg.param("temperature", 0.0)),
                    max_tokens=int(cfg.param("max_tokens", 8192)),
                )
            except (LlmValidationError, LlmError) as e:
                # Un lote que falla no voltea a los otros: son independientes y
                # los anteriores ya se pagaron. Pero SE CUENTA: un par que no
                # se juzgó no puede confundirse con uno juzgado como distinto.
                fallados += 1
                hechos += 1
                print(f"  lote {n:>2}/{len(lotes)}: FALLÓ ({type(e).__name__})")
                return

        costo += float(sum(u.cost_usd for u in usos))
        for p in salida.pares:
            veredictos[p.veredicto] = veredictos.get(p.veredicto, 0) + 1
            if p.veredicto != "mismo" or "|" not in p.par:
                continue
            ra, rb = p.par.split("|", 1)
            if ra in por_ref and rb in por_ref:
                iguales.add(tuple(sorted((ra, rb))))  # type: ignore[arg-type]
        hechos += 1
        print(f"  lote {n:>2}/{len(lotes)}: {len(salida.pares)} veredictos   ({hechos} listos)")

    print(f"\nJuzgando {len(pares)} pares · {len(lotes)} lotes de {tam} · {paralelo} en paralelo")
    try:
        await asyncio.gather(*(_un_lote(i + 1, lote) for i, lote in enumerate(lotes)))
    finally:
        await cliente.close()

    juzgados = sum(veredictos.values())
    print(
        f"\n  mismo {veredictos['mismo']} · distinto {veredictos['distinto']} · "
        f"no_se {veredictos['no_se']}  ·  USD {costo:.6f}"
    )
    if fallados:
        # No se silencia. Un par sin juzgar NO es un par juzgado como distinto,
        # y el número de duplicados que se reporta abajo sería un piso más bajo
        # de lo que ya es.
        print(
            f"  ⚠️  {fallados} de {len(lotes)} lotes fallaron: "
            f"{len(pares) - juzgados} pares quedaron SIN JUZGAR"
        )
    return iguales


async def _aplicar(
    grupos: list[list[Candidate]],
    avisos: list[Candidate],
    *,
    del_juez: set[tuple[str, str]] | None = None,
) -> int:
    """Escribe `listings.cluster_id`. Un grupo = un `listing_clusters`.

    NO borra ni desactiva ningún aviso: el duplicado sigue existiendo, con su
    precio y su URL. Es un HECHO —esa inmobiliaria publicó eso— y borrarlo
    perdería información. Lo que cambia es que el nodo 2 cuente una vez por
    cluster en vez de una vez por aviso.

    ## Dos cosas que esta función no hacía

    **Limpia el agrupamiento anterior del alcance analizado.** Antes solo
    agregaba: correr el script dos veces dejaba los `listing_clusters` viejos
    huérfanos. Medido el 15/08: 1.627 filas en la tabla y 1.139 `cluster_id`
    distintos en `listings` — **488 clusters sin un solo miembro**. Y peor: un
    aviso que dejaba de ser duplicado se quedaba con su `cluster_id` viejo.

    **Marca `match_method`.** El comentario decía *"los que decida el juez se
    marcarán distinto y así se puede auditar cuáles dependieron de un modelo"* y
    escribía `EXACT_ADDR` para todos. Ahora un grupo que contiene al menos un
    par que aportó el juez se marca `LLM`, que es el valor que el CHECK de
    `corpus.listing_clusters` ya permitía.
    """
    import uuid as _uuid

    from sqlalchemy import delete, exists, select, update

    from tasador.db.base import get_session_factory
    from tasador.db.models import Listing, ListingCluster

    del_juez = del_juez or set()
    ids_alcance = [_uuid.UUID(str(a["listing_id"])) for a in avisos]
    tocados = 0

    async with get_session_factory()() as session:
        # 1. Soltar el agrupamiento anterior de TODO lo analizado, no solo de lo
        #    que vuelve a agruparse: un aviso que dejó de ser duplicado tiene que
        #    quedar suelto.
        viejos = (
            (
                await session.execute(
                    select(Listing.cluster_id)
                    .where(Listing.id.in_(ids_alcance), Listing.cluster_id.is_not(None))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        await session.execute(
            update(Listing).where(Listing.id.in_(ids_alcance)).values(cluster_id=None)
        )
        if viejos:
            await session.execute(delete(ListingCluster).where(ListingCluster.id.in_(viejos)))
        await session.flush()

        # 2. Escribir el agrupamiento nuevo.
        for grupo in grupos:
            canonico = elegir_canonico(grupo)
            refs = {str(a["listing_id"]) for a in grupo}
            uso_juez = any(a in refs and b in refs for a, b in del_juez)
            cluster = ListingCluster(
                canonical_id=_uuid.UUID(str(canonico["listing_id"])),
                member_count=len(grupo),
                match_method="LLM" if uso_juez else "EXACT_ADDR",
            )
            session.add(cluster)
            await session.flush()
            await session.execute(
                update(Listing)
                .where(Listing.id.in_([_uuid.UUID(r) for r in refs]))
                .values(cluster_id=cluster.id)
            )
            tocados += len(grupo)

        # 3. Y los huérfanos de corridas anteriores: filas de `listing_clusters`
        #    que ningún aviso referencia. El paso 1 solo alcanza a los que este
        #    alcance tenía asignados; los de una corrida vieja sobre otro barrio
        #    quedaban para siempre. Medido el 15/08: 1.627 filas para 1.139
        #    clusters vivos.
        huerfanos = await session.execute(
            delete(ListingCluster).where(~exists().where(Listing.cluster_id == ListingCluster.id))
        )
        await session.commit()

    print(f"  clusters anteriores borrados: {len(viejos)}")
    print(f"  clusters huérfanos borrados:  {huerfanos.rowcount}")
    return tocados


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--barrio", default=None)
    ap.add_argument("--mostrar", type=int, default=8)
    ap.add_argument("--zona-gris", action="store_true", help="listar los pares dudosos")
    ap.add_argument(
        "--juez",
        action="store_true",
        help="resolver la zona gris con el LLM juez (capa 3). Cuesta centavos",
    )
    # 4 y no 10: un lote de 10 son 20 descripciones de 700 caracteres y el
    # juez se pasa del timeout de 120 s. Medido el 14/08.
    ap.add_argument("--lote", type=int, default=4, help="pares por llamada al juez")
    ap.add_argument("--paralelo", type=int, default=6, help="lotes simultáneos")
    ap.add_argument(
        "--sin-bloqueo",
        action="store_true",
        help="comparar TODOS contra todos. O(n^2): con 4.000 avisos son 8 millones de pares",
    )
    ap.add_argument("--aplicar", action="store_true", help="escribir los cluster_id en la base")
    return run(_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
