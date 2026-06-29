def test_require_auth_no_token(client_no_auth):
    """With no OPENGRAB_TOKEN set, all requests should pass."""
    r = client_no_auth.get("/api/info?url=http://x")
    assert r.status_code != 401


def test_require_auth_with_token_denied(client_with_token):
    """With token set, unauthenticated requests should be denied."""
    r = client_with_token.get("/api/info?url=http://x")
    assert r.status_code == 401


def test_debug_routes_requires_auth(client_with_token):
    assert client_with_token.get("/api/debug/routes").status_code == 401


def test_require_auth_bearer_header(client_with_token):
    """Bearer header should authenticate."""
    r = client_with_token.get(
        "/api/info?url=http://x",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code != 401


def test_require_auth_query_param(client_with_token):
    """Query param ?token= should authenticate."""
    r = client_with_token.get(
        "/api/info?url=http://x&token=test-token",
    )
    assert r.status_code != 401


def test_require_auth_invalid_token(client_with_token):
    """Wrong token should be denied."""
    r = client_with_token.get(
        "/api/info?url=http://x",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


def test_api_auth_endpoint_sets_cookie(client_with_token):
    """POST /api/auth with correct token should set cookie."""
    r = client_with_token.post(
        "/api/auth",
        json={"token": "test-token"},
    )
    assert r.status_code == 200
    assert "opengrab_token" in r.headers.get("set-cookie", "")


def test_api_auth_endpoint_rejects_bad_token(client_with_token):
    """POST /api/auth with wrong token should fail."""
    r = client_with_token.post(
        "/api/auth",
        json={"token": "wrong-token"},
    )
    assert r.status_code == 401


def test_api_auth_cookie_accepted(authed_client):
    """Cookie should authenticate subsequent requests."""
    r = authed_client.get("/api/info?url=http://x")
    assert r.status_code != 401


def test_api_logout(client_with_token):
    """POST /api/logout should clear cookie."""
    r = client_with_token.post("/api/logout")
    assert r.status_code == 200


def test_token_autogen_on_empty(monkeypatch):
    """Con OPENGRAB_TOKEN ausente/vacío y sin NO_AUTH, debe autogenerarse."""
    import sys
    monkeypatch.delenv("OPENGRAB_TOKEN", raising=False)
    monkeypatch.delenv("OPENGRAB_NO_AUTH", raising=False)
    for m in list(sys.modules):
        if m == "config":
            del sys.modules[m]
    import config
    assert config.TOKEN_WAS_GENERATED is True
    assert config.TOKEN != ""
    assert len(config.TOKEN) >= 16


def test_no_auth_escape_hatch(monkeypatch):
    """OPENGRAB_NO_AUTH=1 desactiva auth explícitamente."""
    import sys
    monkeypatch.delenv("OPENGRAB_TOKEN", raising=False)
    monkeypatch.setenv("OPENGRAB_NO_AUTH", "1")
    for m in list(sys.modules):
        if m == "config":
            del sys.modules[m]
    import config
    assert config.TOKEN == ""
    assert config.TOKEN_WAS_GENERATED is False


def test_no_localhost_bypass():
    """require_auth NO debe esquivar auth para client.host==127.0.0.1."""
    import sys
    for m in list(sys.modules):
        if m in ("config", "routers"):
            del sys.modules[m]
    import os
    os.environ["OPENGRAB_TOKEN"] = "real-token"
    os.environ.pop("OPENGRAB_NO_AUTH", None)
    import importlib
    import config
    import routers
    importlib.reload(config)
    importlib.reload(routers)

    from starlette.requests import Request
    from fastapi import HTTPException

    # Request crafteado DESDE 127.0.0.1, sin token
    scope = {
        "type": "http", "method": "GET", "path": "/api/info",
        "headers": [], "query_string": b"", "client": ("127.0.0.1", 5555),
    }
    req = Request(scope)
    try:
        routers.require_auth(req)
        assert False, "no debería pasar: 127.0.0.1 esquivó auth"
    except HTTPException as e:
        assert e.status_code == 401
