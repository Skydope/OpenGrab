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
        return [{"url": "https://x.com/1", "extractor": "yt", "video_id": "v1", "title": "Test"}]

    monkeypatch.setattr("routes._check_channel_watch", fake_check)

    r = client.post("/api/channels", json={"url": "https://x.com/@ch-check"})
    cid = r.json()["id"]

    r2 = client.post(f"/api/channels/{cid}/check")
    assert r2.status_code == 200
    data = r2.json()
    assert data["new_videos"] == 1
    assert len(data["videos"]) == 1
    assert data["videos"][0]["title"] == "Test"


# --------------------- history delete API ------------------------------ #
def test_api_delete_history_entry_removes_from_list(client):
    # 1. Create a job and mark it done
    r = client.post("/api/jobs", json={"url": "https://example.com/v", "quality": "best"})
    job_id = r.json()["job_id"]
    client.app.state.opengrab.db.update_job(
        job_id, status="done", completed=99999, title="Test",
        filename="t.mp4", filepath="/tmp/t.mp4", mime="video/mp4", size=100,
    )
    # 2. Verify it appears in history
    r = client.get("/api/history?limit=50")
    ids = [e["job_id"] for e in r.json()]
    assert job_id in ids, "Debe aparecer en el historial"

    # 3. Delete the entry
    d = client.delete(f"/api/history/{job_id}")
    assert d.status_code == 200
    assert d.json()["ok"] is True

    # 4. Verify it's gone from history
    r = client.get("/api/history?limit=50")
    ids_after = [e["job_id"] for e in r.json()]
    assert job_id not in ids_after, "La entrada borrada NO debe reaparecer en el historial"


def test_api_delete_history_nonexistent(client):
    d = client.delete("/api/history/phantom123")
    assert d.status_code == 404


def test_api_clear_history(client):
    d = client.delete("/api/history")
    assert d.status_code == 200
    assert d.json()["ok"] is True


# --------------------- storage API ------------------------------------- #
def test_api_storage(client):
    r = client.get("/api/storage")
    assert r.status_code == 200


def test_api_storage_cleanup(client):
    r = client.post("/api/storage/cleanup")
    assert r.status_code == 200
    data = r.json()
    assert "cleaned" in data
    assert "freed_bytes" in data


# ------------------------- batch playlist download ---------------------------- #
def test_batch_download_creates_queued_jobs(client, app_state):
    """POST /api/playlist/download with 3 URLs creates 3 jobs in DB with status queued."""
    urls = [
        "https://youtube.com/watch?v=abc1",
        "https://youtube.com/watch?v=abc2",
        "https://youtube.com/watch?v=abc3",
    ]
    r = client.post("/api/playlist/download", json={"urls": urls, "quality": "720p"})
    assert r.status_code == 200
    data = r.json()
    assert data["queued"] == 3
    assert len(data["job_ids"]) == 3
    assert data["skipped"] == []

    # Verify jobs are in DB with status queued (NOT in state.jobs)
    for job_id in data["job_ids"]:
        job = app_state.db.get_job(job_id)
        assert job is not None
        assert job["status"] == "queued"
        assert job["url"] in urls
        assert job["quality"] == "720p"
        # state.jobs should NOT have these yet (dispatch_loop handles that)
        assert job_id not in app_state.jobs


def test_batch_download_caps_at_100_urls(client, app_state):
    """POST with 150 URLs returns 100 queued, 50 skipped with reason 'limite de batch (100)'."""
    # Create 150 URLs
    urls = [f"https://youtube.com/watch?v=video{i:03d}" for i in range(150)]
    r = client.post("/api/playlist/download", json={"urls": urls, "quality": "best"})
    assert r.status_code == 200
    data = r.json()

    assert data["queued"] == 100
    assert len(data["job_ids"]) == 100
    assert len(data["skipped"]) == 50

    # All skipped should have reason "limite de batch (100)"
    for skip in data["skipped"]:
        assert skip["reason"] == "limite de batch (100)"
        assert "youtube.com" in skip["url"]


def test_batch_download_invalid_urls_skipped(client):
    """POST with invalid URLs returns them in skipped list."""
    urls = [
        "https://youtube.com/watch?v=valid1",
        "not-a-url",
        "ftp://invalid-scheme.com/video",
        "https://youtube.com/watch?v=valid2",
    ]
    r = client.post("/api/playlist/download", json={"urls": urls, "quality": "best"})
    assert r.status_code == 200
    data = r.json()

    assert data["queued"] == 2
    assert len(data["skipped"]) == 2

    skipped_urls = {s["url"] for s in data["skipped"]}
    assert "not-a-url" in skipped_urls
    assert "ftp://invalid-scheme.com/video" in skipped_urls

    for skip in data["skipped"]:
        assert skip["reason"] == "URL invalida"


def test_batch_download_invalid_quality_returns_400(client):
    """POST with invalid quality returns 400."""
    r = client.post(
        "/api/playlist/download",
        json={"urls": ["https://youtube.com/watch?v=abc"], "quality": "invalid"},
    )
    assert r.status_code == 400


def test_batch_download_rate_limited(client):
    """Three fast POSTs to /api/playlist/download: first two succeed, third gets 429 (2/min limit)."""
    import time

    urls = ["https://youtube.com/watch?v=batch1"]

    # First two requests should succeed
    r1 = client.post("/api/playlist/download", json={"urls": urls, "quality": "best"})
    assert r1.status_code == 200

    r2 = client.post("/api/playlist/download", json={"urls": urls, "quality": "best"})
    assert r2.status_code == 200

    # Third request immediately should be rate limited (2/min limit)
    r3 = client.post("/api/playlist/download", json={"urls": urls, "quality": "best"})
    assert r3.status_code == 429


# ------------------------- batch status endpoint ----------------------------- #
def test_batch_status_returns_mixed_memory_and_db(client, app_state):
    """batch-status returns jobs from both state.jobs (memory) and DB."""
    from models import Job

    # Create a job in state.jobs (like a running download)
    job_in_memory = Job(id="memory-job", created=1000.0)
    job_in_memory.status = "downloading"
    job_in_memory.percent = 42.0
    job_in_memory.speed = "1.5MB/s"
    job_in_memory.eta = "00:30"
    job_in_memory.error = ""
    job_in_memory.filename = "video1.mp4"
    job_in_memory.title = "Video One"
    app_state.jobs["memory-job"] = job_in_memory

    # Create a job only in DB (finished/completed)
    db_job_id = "db-job-123"
    app_state.db.insert_job(db_job_id, "https://youtube.com/watch?v=abc", "720p")
    app_state.db.update_job(
        db_job_id,
        status="done",
        title="Video Two",
        filename="video2.mp4",
        completed=99999,
    )

    # batch-status should return both
    r = client.get("/api/jobs/batch-status?ids=memory-job,db-job-123")
    assert r.status_code == 200
    data = r.json()

    assert len(data) == 2

    # Find each job's data
    memory_data = next((j for j in data if j["job_id"] == "memory-job"), None)
    db_data = next((j for j in data if j["job_id"] == "db-job-123"), None)

    assert memory_data is not None
    assert memory_data["status"] == "downloading"
    assert memory_data["percent"] == 42.0
    assert memory_data["speed"] == "1.5MB/s"
    assert memory_data["eta"] == "00:30"
    assert memory_data["error"] == ""
    assert memory_data["filename"] == "video1.mp4"
    assert memory_data["title"] == "Video One"

    assert db_data is not None
    assert db_data["status"] == "done"
    assert db_data["percent"] == 100.0
    assert db_data["filename"] == "video2.mp4"
    assert db_data["title"] == "Video Two"


def test_batch_status_unknown_ids_returns_empty(client, app_state):
    """batch-status with unknown IDs still returns results (not 404)."""
    r = client.get("/api/jobs/batch-status?ids=unknown1,unknown2")
    assert r.status_code == 200
    data = r.json()

    # Should return the unknown IDs with empty/default status
    assert len(data) == 2

    unknown_ids = {j["job_id"] for j in data}
    assert unknown_ids == {"unknown1", "unknown2"}


# ----------------------------- /api/settings -------------------------------- #
def test_get_settings_returns_all_keys(client):
    """GET /api/settings devuelve las 8 keys del catálogo."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    keys = {item["key"] for item in data}
    expected = {
        "max_jobs", "max_total_mb", "max_size_mb", "history_max",
        "quality_default", "theme", "library_dir", "name_template",
    }
    assert keys == expected


def test_get_settings_has_required_fields(client):
    """Cada setting tiene value, origin, locked, scope."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    for item in r.json():
        assert "key" in item
        assert "value" in item
        assert "origin" in item
        assert "locked" in item
        assert "scope" in item
        assert item["origin"] in ("env", "ini", "table", "default")
        assert isinstance(item["locked"], bool)


def test_get_settings_max_jobs_locked_by_env(client):
    """OPENGRAB_MAX_JOBS=1 en env → max_jobs origin=env y locked=True."""
    r = client.get("/api/settings")
    assert r.status_code == 200
    item = next(i for i in r.json() if i["key"] == "max_jobs")
    assert item["origin"] == "env"
    assert item["locked"] is True


def test_put_settings_locked_key_returns_400(client, app_state):
    """PUT con key locked (origin=env) retorna 400."""
    r = client.put("/api/settings", json={"max_jobs": "99"})
    assert r.status_code == 400
    assert "locked" in r.text.lower() or "max_jobs" in r.text


def test_put_settings_unlocked_key_updates_table(client, app_state, monkeypatch):
    """PUT theme (sin env/ini) persiste en la tabla.

    theme no se写入 ini por tests anteriores porque es string y no coincide con
    history_max (que sí se写入). Mockeamos _write_setting_to_ini para test puro de tabla.
    """
    import sys
    sys.modules["routes"]._write_setting_to_ini = lambda *a, **k: True
    r = client.put("/api/settings", json={"theme": "dark"})
    assert r.status_code == 200, f"Got {r.status_code}: {r.json()}"
    assert "theme" in r.json()["updated"]
    assert app_state.db.get_setting("theme") == "dark"


def test_put_settings_casts_to_int(client, app_state):
    """PUT con int en JSON se guarda correctamente (theme usa strings)."""
    import sys
    sys.modules["routes"]._write_setting_to_ini = lambda *a, **k: True
    r = client.put("/api/settings", json={"theme": "light"})
    assert r.status_code == 200
    assert app_state.db.get_setting("theme") == "light"


def test_put_settings_unknown_key_returns_error(client):
    """PUT con key desconocida retorna error en details."""
    r = client.put("/api/settings", json={"unknown_key": "value"})
    assert r.status_code == 400
    data = r.json()
    assert "error" in data or "detail" in data


def test_put_settings_invalid_type_returns_400(client):
    """PUT con tipo invalido retorna 400."""
    r = client.put("/api/settings", json={"max_jobs": "not-an-int"})
    assert r.status_code == 400


def test_put_settings_no_body_returns_400(client):
    """PUT sin body retorna 400."""
    r = client.put("/api/settings")
    assert r.status_code == 400

