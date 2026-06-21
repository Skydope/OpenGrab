def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "jobs_active" in data


def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "ytgrab" in r.text


def test_index_injects_auth_flag(client):
    r = client.get("/")
    assert "__AUTH_REQUIRED__" not in r.text
    assert '"false"' in r.text or "false" in r.text


def test_api_info_invalid_url(client):
    r = client.get("/api/info?url=not-a-youtube-url")
    assert r.status_code == 400


def test_api_info_no_url(client):
    r = client.get("/api/info")
    assert r.status_code in (400, 422)


def test_api_jobs_invalid_quality(client):
    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/abc", "quality": "4k"},
    )
    assert r.status_code == 400


def test_api_jobs_invalid_url(client):
    r = client.post(
        "/api/jobs",
        json={"url": "not-a-url", "quality": "best"},
    )
    assert r.status_code == 400


def test_api_jobs_nonexistent(client):
    r = client.get("/api/jobs/nonexistent/events")
    assert r.status_code == 404


def test_api_jobs_file_nonexistent(client):
    r = client.get("/api/jobs/nonexistent/file")
    assert r.status_code == 404


def test_api_history(client):
    r = client.get("/api/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_static_css(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


def test_static_alpine(client):
    r = client.get("/static/alpine.min.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
