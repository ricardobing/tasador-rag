"""Indexa el corpus en `corpus.listing_chunks` — doc 18, Fase 2.

    uv run python scripts/embed_corpus.py --dry-run          # cuántos tokens, cuántos chunks
    uv run python scripts/embed_corpus.py --chunker C        # indexar (incremental)
    uv run python scripts/embed_corpus.py --chunker A --barrio Belgrano --limite 200

Idempotente: sin cambios en el corpus, la segunda corrida escribe 0 filas. Se
guarda lote a lote. `--dry-run` corre el chunker con el tokenizador real y no
escribe: es la medición de la Fase 2 ("¿cuántos avisos no entran en 512
tokens?") antes de decidir nada.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tasador.cli import run
from tasador.rag.chunking import CHUNKERS
from tasador.rag.embedder import Embedder
from tasador.rag.indexer import indexar


async def _main(args: argparse.Namespace) -> int:
    from tasador.db.base import get_session_factory

    chunker = CHUNKERS[args.chunker]
    embedder = Embedder(args.modelo) if args.modelo else Embedder()
    t0 = time.perf_counter()
    await embedder.precargar()
    print(f"modelo {embedder.model} cargado en {time.perf_counter() - t0:.1f} s")

    async with get_session_factory()() as session:
        t1 = time.perf_counter()
        st = await indexar(
            session,
            chunker=chunker,
            embedder=embedder,
            barrio=args.barrio,
            limite=args.limite,
            lote_avisos=args.lote,
            solo_activos=not args.todos,
            dry_run=args.dry_run,
        )
    dt = time.perf_counter() - t1

    print(f"{'DRY-RUN · ' if args.dry_run else ''}{chunker.version} · {st.resumen()} · {dt:.0f} s")
    if st.tokens:
        toks = sorted(st.tokens)
        p = lambda q: toks[min(len(toks) - 1, int(q * len(toks)))]  # noqa: E731
        print(
            f"tokens por chunk: p50 {p(0.5)} · p90 {p(0.9)} · p99 {p(0.99)} · máx {toks[-1]} · "
            f"media {statistics.fmean(toks):.0f}"
        )
        print(
            f"chunks por aviso: {len(toks) / max(1, st.avisos_vistos):.2f} · "
            f"chunks > 512 tokens: {sum(1 for t in toks if t > 512)}"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--chunker", choices=sorted(CHUNKERS), default="C")
    ap.add_argument("--modelo", default=None, help="por defecto, settings.embedding_model")
    ap.add_argument("--barrio", default=None)
    ap.add_argument("--limite", type=int, default=None)
    ap.add_argument("--lote", type=int, default=50, help="avisos por commit")
    ap.add_argument("--todos", action="store_true", help="también los inactivos (históricos)")
    ap.add_argument("--dry-run", action="store_true")
    return run(_main(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
