"""Contrato de autenticación: TODA ruta de la API exige token salvo exenciones explícitas.

Este test enumera las rutas de la app dinámicamente, así que cualquier endpoint
futuro queda cubierto automáticamente: si alguien agrega una ruta y se olvida
del ``Depends(require_auth)``, este test falla y obliga a decidir — o se agrega
el guard, o se agrega la ruta a EXEMPT con una justificación.

Motivación: GET /api/jobs/{job_id}/file/{filename} shippeó sin auth porque el
guard es opt-in por endpoint y ningún test verificaba el contrato completo.
"""
from __future__ import annotations

import re

from fastapi.routing import APIRoute

# Rutas que NO exigen auth, con justificación:
#   /                    → sirve el index; la UI pide token vía /api/auth.
#   /health              → healthcheck de Docker/Uptime Kuma (sin datos).
#   /metrics             → scrape de Prometheus (solo counters agregados).
#   /api/auth            → es el endpoint que ENTREGA la sesión.
#   /api/logout          → borra la cookie; sin efecto sobre datos.
#   /openapi.json, /docs, /redoc → defaults de FastAPI (exponen el esquema,
#       no datos; endurecerlos es una decisión separada, ver issue tracker).
EXEMPT: frozenset[str] = frozenset({
    "/",
    "/health",
    "/metrics",
    "/api/auth",
    "/api/logout",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
})

_PARAM = re.compile(r"\{[^}]+\}")


def _iter_api_routes(app):
    """Aplana las rutas de la app, atravesando routers incluidos.

    En FastAPI >= 0.130 ``include_router`` deja objetos ``_IncludedRouter``
    en ``app.routes`` (con el APIRouter real en ``original_router``) en lugar
    de aplanar las APIRoute directamente, así que se recorre en profundidad.
    """
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):  # _IncludedRouter
            stack.extend(route.original_router.routes)
        elif hasattr(route, "routes"):  # Router/Mount anidado
            stack.extend(route.routes)


def test_all_routes_require_auth(client_with_token):
    """Sin credenciales, toda ruta no exenta responde 401 (nunca 2xx/otros)."""
    app = client_with_token.app
    failures: list[str] = []
    for route in _iter_api_routes(app):
        if route.path in EXEMPT:
            continue
        url = _PARAM.sub("contracttest", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            if method in ("POST", "PUT", "PATCH"):
                r = client_with_token.request(method, url, json={})
            else:
                r = client_with_token.request(method, url)
            if r.status_code != 401:
                failures.append(f"{method} {route.path} -> {r.status_code}")
    assert not failures, (
        "Rutas que no exigen auth (agregar Depends(require_auth) "
        "o justificar en EXEMPT):\n  " + "\n  ".join(failures)
    )


def test_exempt_routes_do_not_require_auth(client_with_token):
    """Las exenciones son deliberadas: no deben devolver 401 sin token."""
    app = client_with_token.app
    known = {r.path for r in _iter_api_routes(app)}
    for path in sorted(EXEMPT & known):
        r = client_with_token.get(path)
        assert r.status_code != 401, f"{path} exige auth pero está en EXEMPT"


def _all_paths(app) -> set[str]:
    """Todos los paths registrados, incluidas las Route planas de Starlette
    (docs/openapi) que no son APIRoute."""
    paths: set[str] = set()
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        if hasattr(route, "original_router"):
            stack.extend(route.original_router.routes)
        elif hasattr(route, "routes"):
            stack.extend(route.routes)
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
    return paths


def test_exempt_list_has_no_stale_entries(client_with_token):
    """Si una ruta exenta desaparece de la app, limpiar EXEMPT."""
    stale = EXEMPT - _all_paths(client_with_token.app)
    assert not stale, f"Entradas de EXEMPT que ya no existen en la app: {stale}"
