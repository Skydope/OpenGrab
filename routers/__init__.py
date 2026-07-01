from __future__ import annotations

# Utilidades compartidas por los routers: rate limiter, auth, resolución de
# estado e index HTML cacheado.
import logging
import secrets

from fastapi import HTTPException, Request
from slowapi import Limiter

from config import TOKEN, TRUST_XFF, _STATIC_DIR
from state import AppState
from i18n import t

log = logging.getLogger("opengrab")

def _client_key(request: Request) -> str:
    if TRUST_XFF:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"

limiter = Limiter(key_func=_client_key, default_limits=["30/minute"])

def get_state(request: Request) -> AppState:
    return request.app.state.opengrab  # type: ignore[no-any-return]

def require_auth(request: Request) -> None:
    if not TOKEN:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:], TOKEN):
        return
    if secrets.compare_digest(request.query_params.get("token", ""), TOKEN):
        return
    if secrets.compare_digest(request.cookies.get("opengrab_token", ""), TOKEN):
        return
    raise HTTPException(401, t("error.token_required"))

# _INDEX_HTML
try:
    _INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
except (FileNotFoundError, OSError):
    _INDEX_HTML = (
        "<!doctype html><html><body><h1>OpenGrab</h1>"
        "<p>static/index.html not found.</p></body></html>"
    )


# re-export for convenience
__all__ = [
    "_INDEX_HTML", "get_state", "limiter", "log", "require_auth",
]
