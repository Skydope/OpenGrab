"""Tests funcionales del stack de middleware ASGI puro.

Escritos junto a la migración desde BaseHTTPMiddleware: verifican el
comportamiento observable (headers, idioma, métricas) para que futuros
cambios del stack no puedan romperlo en silencio.
"""
from __future__ import annotations


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers["Permissions-Policy"]


def test_security_headers_on_error_responses(client_with_token):
    """Los headers también van en respuestas de error (401 sin token)."""
    r = client_with_token.get("/api/history")
    assert r.status_code == 401
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_language_cookie_wins_over_accept_language(client):
    client.cookies.set("opengrab_lang", "en")
    r = client.get(
        "/api/history?limit=1",
        headers={"Accept-Language": "es-AR,es;q=0.9"},
    )
    assert r.status_code == 200
    client.cookies.delete("opengrab_lang")
    # La cookie 'en' debe ganar aunque Accept-Language pida español:
    # el 404 llega con el texto EXACTO de en.json, no el de es.json.
    from i18n import t
    client.cookies.set("opengrab_lang", "en")
    r = client.post(
        "/api/jobs/zzzz/cancel",
        headers={"Accept-Language": "es-AR,es;q=0.9"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == t("error.job_not_found", lang="en")
    client.cookies.delete("opengrab_lang")


def test_language_accept_header_fallback(client):
    """Sin cookie, Accept-Language decide (en → mensaje en inglés)."""
    from i18n import t
    r = client.post(
        "/api/jobs/zzzz/cancel",
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == t("error.job_not_found", lang="en")
    # Y con es: el mensaje en español.
    r = client.post(
        "/api/jobs/zzzz/cancel",
        headers={"Accept-Language": "es-AR,es;q=0.9"},
    )
    assert r.json()["detail"] == t("error.job_not_found", lang="es")


def test_request_metrics_counter_increments(client):
    from metrics import http_requests

    before = http_requests.labels(
        endpoint="/health", method="GET", status_code="200"
    )._value.get()
    client.get("/health")
    after = http_requests.labels(
        endpoint="/health", method="GET", status_code="200"
    )._value.get()
    assert after == before + 1
