def test_health(client, app_state):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "jobs_active" not in data  # ya no se expone (info leak)


def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "opengrab" in r.text


def test_index_injects_auth_flag(client):
    r = client.get("/")
    assert "__AUTH_REQUIRED__" not in r.text
    assert '"true"' in r.text or "true" in r.text


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

    assert workdir.exists()
    assert job.filepath != ""


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


def test_file_serving_path_traversal_blocked(client, app_state):
    from models import Job

    job = Job(id="traverse", created=1000000.0)
    job.status = "done"
    job.filepath = "/etc/passwd"
    job.filename = "passwd"
    job.mime = "text/plain"
    app_state.jobs["traverse"] = job

    r = client.get("/api/jobs/traverse/file")
    assert r.status_code == 403


def test_content_disposition_unicode_filename(client, app_state):
    from models import Job

    workdir = app_state.out_dir / "unicode_test"
    workdir.mkdir()
    test_file = workdir / "video.mp4"
    test_file.write_bytes(b"content")

    job = Job(id="unicode123", created=1000000.0)
    job.status = "done"
    job.filepath = str(test_file)
    job.filename = "video con caño.mp4"
    job.mime = "video/mp4"
    job.workdir = str(workdir)
    app_state.jobs["unicode123"] = job

    r = client.get("/api/jobs/unicode123/file")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "filename*=UTF-8''" in cd
    assert "C3%B1o" in cd


def test_content_disposition_control_chars_stripped(client, app_state):
    from models import Job

    workdir = app_state.out_dir / "newline_test"
    workdir.mkdir()
    test_file = workdir / "video.mp4"
    test_file.write_bytes(b"content")

    job = Job(id="ctrl123", created=1000000.0)
    job.status = "done"
    job.filepath = str(test_file)
    job.filename = "video\ncon\nsaltos.mp4"
    job.mime = "video/mp4"
    job.workdir = str(workdir)
    app_state.jobs["ctrl123"] = job

    r = client.get("/api/jobs/ctrl123/file")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "\n" not in cd
    assert "video" in cd


def test_health_public_no_jobs_active(client_no_auth):
    r = client_no_auth.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_engine_update_endpoint(client, monkeypatch):
    import engine_update

    called = {}

    def fake(force=False):
        called["force"] = force
        return {"updated": True, "version": "9999.1.1", "used_bundled": False}

    monkeypatch.setattr(engine_update, "check_and_update", fake)
    r = client.post("/api/engine/update")
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] is True
    assert body["version"] == "9999.1.1"
    assert called["force"] is True  # el botón manual fuerza el update


def test_engine_update_requires_auth(client_no_auth, monkeypatch):
    # Con NO_AUTH el endpoint responde sin token; solo verificamos que no rompe.
    import engine_update

    monkeypatch.setattr(
        engine_update, "check_and_update",
        lambda force=False: {"updated": False, "version": None, "used_bundled": True},
    )
    r = client_no_auth.post("/api/engine/update")
    assert r.status_code == 200


# --------------------------- channels CRUD ---------------------------------- #
def test_api_list_channels_empty(client):
    r = client.get("/api/channels")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_create_and_list_channel(client):
    r = client.post("/api/channels", json={
        "url": "https://youtube.com/@test",
        "quality": "720p",
        "interval_minutes": 120,
    })
    assert r.status_code == 200
    assert r.json()["id"] > 0

    r2 = client.get("/api/channels")
    assert r2.status_code == 200
    channels = r2.json()
    assert len(channels) == 1
    assert channels[0]["url"] == "https://youtube.com/@test"
    assert channels[0]["quality"] == "720p"


def test_api_update_channel(client):
    r = client.post("/api/channels", json={"url": "https://x.com/@ch-up"})
    cid = r.json()["id"]

    r2 = client.put(f"/api/channels/{cid}", json={"title": "Renamed", "enabled": 0})
    assert r2.status_code == 200

    channels = client.get("/api/channels").json()
    updated = next(c for c in channels if c["id"] == cid)
    assert updated["title"] == "Renamed"
    assert updated["enabled"] == 0


def test_api_delete_channel(client):
    r = client.post("/api/channels", json={"url": "https://x.com/@ch-del"})
    cid = r.json()["id"]

    r2 = client.delete(f"/api/channels/{cid}")
    assert r2.status_code == 200

    r3 = client.get("/api/channels")
    ids = [c["id"] for c in r3.json()]
    assert cid not in ids


def test_api_delete_nonexistent_channel(client):
    r = client.delete("/api/channels/99999")
    assert r.status_code == 404


def test_api_check_channel(client, monkeypatch):
    from download import _check_channel_watch

    def fake_check(state, channel):
        return 3

    monkeypatch.setattr("routes._check_channel_watch", fake_check)

    r = client.post("/api/channels", json={"url": "https://x.com/@ch-check"})
    cid = r.json()["id"]

    r2 = client.post(f"/api/channels/{cid}/check")
    assert r2.status_code == 200
    assert r2.json()["new_videos"] == 3
