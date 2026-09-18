"""Ceder el paso cuando la máquina se queda sin memoria.

El 18/09, seis procesos del eval de recuperación en paralelo —cada uno con su
modelo de embeddings, su pool de 100-170 avisos y sus lotes del juez— dejaron
la máquina con 0,1 GB libres de 32. Un proceso que corre horas no puede
asumir que tiene la máquina para él.

`esperar_memoria()` bloquea hasta que quede al menos `minimo` de la RAM libre
(20% por defecto). Sin `psutil`: en Windows pregunta con `GlobalMemoryStatusEx`
vía `ctypes`; en Linux lee `/proc/meminfo`. Si no puede medir, no bloquea y
lo dice una vez.
"""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

import structlog

log = structlog.get_logger()

_AVISADO = False


def fraccion_libre() -> float | None:
    """RAM disponible / total, o None si no se puede medir en esta plataforma."""
    try:
        if (
            os.name == "nt"
        ):  # `os.name` y no `sys.platform`: mypy no lo "estrecha" y la rama Linux sigue viva

            class _Mem(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            m = _Mem()
            m.dwLength = ctypes.sizeof(_Mem)
            kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 — no existe fuera de Windows
            if not kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return None
            return m.ullAvailPhys / m.ullTotalPhys if m.ullTotalPhys else None
        with Path("/proc/meminfo").open(encoding="utf-8") as f:
            datos = {ln.split(":")[0]: int(ln.split()[1]) for ln in f if ":" in ln}
        return datos["MemAvailable"] / datos["MemTotal"]
    except (OSError, KeyError, ValueError, AttributeError):
        return None


def esperar_memoria(minimo: float = 0.20, *, cada: float = 5.0, maximo: float = 600.0) -> None:
    """Bloquea hasta que la fracción libre sea >= `minimo`, a lo sumo `maximo` s."""
    global _AVISADO
    t0 = time.monotonic()
    while True:
        libre = fraccion_libre()
        if libre is None:
            if not _AVISADO:
                log.warning("no se puede medir la memoria libre; sin guardia")
                _AVISADO = True
            return
        if libre >= minimo:
            return
        if time.monotonic() - t0 > maximo:
            log.warning(
                "memoria libre por debajo del mínimo; se sigue igual", libre=round(libre, 3)
            )
            return
        log.info("esperando memoria libre", libre=round(libre, 3), minimo=minimo)
        time.sleep(cada)
