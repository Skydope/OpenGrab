#!/usr/bin/env python3
"""Smoke de frescura del motor: ¿el yt-dlp instalado todavía extrae?

Sondea una extracción real (``_fetch_info`` sobre una URL pública estable) y
emite un veredicto clasificado. El punto es distinguir tres mundos que en CI se
confunden y, si no se separan, vuelven al job un semáforo roto inútil:

- ``healthy``     (exit 0)  — extrajo bien: formats + título presentes.
- ``unavailable`` (exit 75) — YouTube nos bloqueó/limitó la IP (típico en runners
                              de datacenter: "Sign in to confirm you're not a
                              bot", HTTP 429, DNS, timeout). NO es culpa del pin;
                              el workflow lo trata como neutral, no como falla.
- ``broken``      (exit 1)  — el extractor falló de verdad (campo no extraíble,
                              JSON cambiado) o devolvió info incompleta. Esto sí
                              es rot del motor → falla dura + issue.

El exit 75 es ``EX_TEMPFAIL`` (sysexits.h): "fallo temporal, reintentá".

``classify`` es una función pura: toda la lógica de decisión vive ahí y se testea
sin red (tests/test_engine_smoke.py). El CLI solo hace I/O y traduce a exit code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Códigos de salida (sysexits.h)
EXIT_HEALTHY = 0
EXIT_BROKEN = 1
EXIT_UNAVAILABLE = 75  # EX_TEMPFAIL

# URL pública histórica y estable (primer video de YouTube, "Me at the zoo").
DEFAULT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Marcadores de "no disponible / bloqueado" en el mensaje de error. Heurística
# deliberadamente chica y documentada: si aparece alguno, asumimos bloqueo/red
# (neutral) en vez de rot del extractor. Degrada de forma segura: clasificar de
# más un broken como unavailable solo pierde UNA alerta (la próxima corrida o un
# usuario lo agarran); el inverso daría un falso rojo, que es lo que evitamos.
_UNAVAILABLE_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "http error 429",
    "too many requests",
    "http error 403",
    "http error 503",
    "unable to download webpage",  # wrapper típico del bot-check / red
    "temporary failure in name resolution",
    "getaddrinfo",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "remote end closed",
    # Fallos de transporte TLS/DNS: ambientales (proxy, cert, red), no rot del
    # extractor. Un "certificate verify failed" jamás significa "YouTube cambió
    # el HTML"; tratarlo como broken abriría un issue falso.
    "certificate",
    "ssl:",
    "ssl error",
    "tlsv1",
    "name or service not known",
)


def classify(info: dict[str, Any] | None, error: str | None) -> tuple[str, int]:
    """Decide veredicto + exit code a partir del resultado de la extracción.

    Función pura: ``info`` es lo que devolvió ``_fetch_info`` (o None) y
    ``error`` es el mensaje de la excepción (o None si no hubo).
    """
    if error is not None:
        low = error.lower()
        if any(marker in low for marker in _UNAVAILABLE_MARKERS):
            return "unavailable", EXIT_UNAVAILABLE
        # "unable to extract <campo>" y amigos → rot real del extractor.
        return "broken", EXIT_BROKEN

    if info is None:
        return "broken", EXIT_BROKEN

    formats = info.get("formats") or []
    if not info.get("title") or not formats:
        # Extrajo pero incompleto: el extractor anda a medias → tratamos como rot.
        return "broken", EXIT_BROKEN

    return "healthy", EXIT_HEALTHY


def _probe(url: str) -> tuple[dict[str, Any] | None, str | None, str]:
    """Corre la extracción real. Devuelve (info, error, yt_dlp_version)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import yt_dlp  # type: ignore[import-untyped]

    from download import _fetch_info

    version = getattr(yt_dlp, "version", None)
    yt_version = getattr(version, "__version__", "unknown") if version else "unknown"
    try:
        return _fetch_info(url), None, yt_version
    except Exception as exc:  # clasificamos por mensaje, no por tipo
        return None, f"{type(exc).__name__}: {exc}", yt_version


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    url = argv[0] if argv else DEFAULT_URL

    info, error, yt_version = _probe(url)
    verdict, code = classify(info, error)

    report = {
        "verdict": verdict,
        "yt_dlp_version": yt_version,
        "url": url,
        "title": (info or {}).get("title"),
        "n_formats": len((info or {}).get("formats") or []),
        "error": error,
    }
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
