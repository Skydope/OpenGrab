import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


def test_rate_limiting_info(client, app_state):
    with patch("routes._fetch_info", return_value={"title": "Test", "duration": 60, "formats": []}):
        for i in range(10):
            r = client.get("/api/info?url=https://youtu.be/abc")
            assert r.status_code == 200, f"Request {i + 1} should pass"

        r = client.get("/api/info?url=https://youtu.be/abc")
        assert r.status_code == 429


def test_rate_limiting_active(client, app_state, monkeypatch):
    """El rate limiting de jobs está activo (respuesta 200 o 429, no 500)."""
    monkeypatch.setattr("routes._run_download", lambda *a, **kw: None)
    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/abc", "quality": "best"},
    )
    assert r.status_code in (200, 429), f"Unexpected status: {r.status_code}"


def test_hot_reload_via_db(client, app_state):
    """state.resolve() lee valores de la tabla de settings (hot-reload path).

    Limpia max_total_mb del ini en memoria para asegurar que la tabla se lee.
    """
    import config as _config
    _config._ini.pop("max_total_mb", None)
    app_state.db.set_setting("max_total_mb", "8888")
    val, origin = app_state.resolve("max_total_mb", 0, int)
    assert val == 8888
    assert origin == "table"


def test_finalize_desktop_moves_file(client, app_state, monkeypatch):
    """IS_DESKTOP=true: _finalize_desktop mueve archivo a library_dir."""
    import config
    import tempfile
    from models import Job

    # Crear estructura de archivos
    library_dir = Path(tempfile.mkdtemp(prefix="opengrab_lib_"))
    workdir = Path(tempfile.mkdtemp(prefix="opengrab_work_"))
    video_file = workdir / "video.mp4"
    video_file.write_bytes(b"fake video content")

    # Configurar state con library_dir
    monkeypatch.setattr(config, "IS_DESKTOP", True)
    app_state.db.set_setting("library_dir", str(library_dir))
    app_state.db.set_setting("name_template", "{title}")

    # Crear job en memoria
    job = Job(id="finalize-test", created=time.time())
    job.filepath = str(video_file)
    app_state.jobs["finalize-test"] = job

    info = {
        "title": "Test Video",
        "uploader": "Test Channel",
        "upload_date": "20250623",
        "extractor_key": "Test",
        "id": "test123",
        "resolution": "1920x1080",
        "formats": [{"vcodec": "avc1", "filesize": 1000, "resolution": "1920x1080"}],
    }

    # Llamar finalize_desktop
    app_state._finalize_desktop("finalize-test", workdir, video_file, info, "best")

    # El archivo debe haber sido movido
    expected = library_dir / "Test Video.mp4"
    assert expected.exists(), f"Expected {expected} to exist, library_dir contents: {list(library_dir.iterdir())}"
    assert not video_file.exists(), "Original file should have been moved"
    assert job.filepath == str(expected)
