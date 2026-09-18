"""Utilidades comunes a los scripts de línea de comandos."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """`asyncio.run` con el event loop correcto en Windows.

    En Windows el default es `ProactorEventLoop` y psycopg en modo async no
    funciona con él (`InterfaceError: Psycopg cannot use the
    'ProactorEventLoop'`). Los scripts se corren también desde Windows, así
    que hay que fijarlo acá.

    En Linux (los contenedores) es un no-op.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)
