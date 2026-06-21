def test_require_auth_no_token(client):
    """With no OPENGRAB_TOKEN set, all requests should pass."""
    r = client.get("/api/info?url=http://x")
    assert r.status_code != 401


def test_require_auth_with_token_denied(client_with_token):
    """With token set, unauthenticated requests should be denied."""
    r = client_with_token.get("/api/info?url=http://x")
    assert r.status_code == 401


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
