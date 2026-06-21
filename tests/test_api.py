def test_health(client, app_state):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "jobs_active" in data
    assert isinstance(data["jobs_active"], int)


def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "opengrab" in r.text


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


def test_api_jobs_file_serves_and_cleans(client, app_state):
    import tempfile
    from pathlib import Path

    from models import Job

    workdir = Path(tempfile.mkdtemp(prefix="opengrab_", dir=app_state.out_dir))
    test_file = workdir / "test.mp4"
    test_file.write_bytes(b"fake video content")

    job = Job(id="test123", created=1000000.0)
    job.status = "done"
    job.filepath = str(test_file)
    job.filename = "test.mp4"
    job.mime = "video/mp4"
    job.workdir = str(workdir)
    app_state.jobs["test123"] = job

    r = client.get("/api/jobs/test123/file")
    assert r.status_code == 200
    assert r.content == b"fake video content"
    assert r.headers["content-type"] == "video/mp4"
    assert "attachment" in r.headers.get("content-disposition", "")

    assert not workdir.exists()
    assert job.filepath == ""


def test_api_jobs_file_not_done(client, app_state):
    from models import Job

    job = Job(id="pending", created=1000000.0)
    job.status = "downloading"
    app_state.jobs["pending"] = job

    r = client.get("/api/jobs/pending/file")
    assert r.status_code == 409


def test_api_jobs_file_missing(client, app_state):
    from pathlib import Path

    from models import Job

    workdir = app_state.out_dir / "nonexistent_workdir"

    job = Job(id="missing", created=1000000.0)
    job.status = "done"
    job.filepath = str(workdir / "ghost.mp4")
    job.filename = "ghost.mp4"
    job.workdir = str(workdir)
    app_state.jobs["missing"] = job

    r = client.get("/api/jobs/missing/file")
    assert r.status_code == 410


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers.get("permissions-policy", "")


def test_content_disposition_escape(client, app_state):
    from models import Job

    workdir = app_state.out_dir / "escape_test"
    workdir.mkdir()
    test_file = workdir / "video.mp4"
    test_file.write_bytes(b"content")

    job = Job(id="escape123", created=1000000.0)
    job.status = "done"
    job.filepath = str(test_file)
    job.filename = 'video"quote.mp4'
    job.mime = "video/mp4"
    job.workdir = str(workdir)
    app_state.jobs["escape123"] = job

    r = client.get("/api/jobs/escape123/file")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert '\\"' in cd
    assert 'video\\"quote.mp4' in cd
