"""E2E tests con yt-dlp real — descargan videos publicos de YouTube.

NO corren en CI normal (requieren red + ffmpeg). Se ejecutan con::

    pytest tests/ -v -m e2e

Cada test maneja graceful degradation: si YouTube no esta accesible
(timeout, rate limit, error HTTP), hace skip en vez de fallar.

Usan quality="worst" para minimizar el tamano de descarga (~200 KB),
no quality="best". Los archivos descargados se borran al terminar el test.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from download import _fetch_info, _fetch_playlist

pytestmark = pytest.mark.e2e

# URLs publicas estables
_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
# Playlist publica pequena (~5 videos)
_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"

_TIMEOUT = 90
_QUALITY = "worst"


def _poll_until_done(client, job_id: str, timeout: float = _TIMEOUT):
    """Pollea GET /api/jobs/{job_id}/file hasta 200 (devuelve response) o
    falla con pytest.fail si el job falla o se vence el timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}/file")
        if r.status_code == 200:
            return r
        if r.status_code == 409:
            time.sleep(1)
            continue
        pytest.fail(f"Job fallo: status={r.status_code}")
    pytest.fail("Timeout esperando descarga")


def _cleanup_job(app_state, job_id: str) -> None:
    """Remueve el job de la vista y borra su archivo si existe."""
    job = app_state.jobs.get(job_id)
    if job and job.filepath:
        fp = Path(job.filepath)
        if fp.exists():
            fp.unlink(missing_ok=True)
    app_state.dismiss_job_from_view(job_id)


# ------------------------------------------------------------------ T1 ----
def test_e2e_download_video(client, app_state):
    """Descarga real de video como MP4 via el flujo completo de la API."""
    r = client.post("/api/jobs", json={"url": _VIDEO_URL, "quality": _QUALITY})
    if r.status_code != 200:
        pytest.skip(f"YouTube no disponible: {r.json().get('detail', '')}")
    job_id = r.json()["job_id"]

    r2 = _poll_until_done(client, job_id)
    assert r2.status_code == 200
    assert len(r2.content) > 0
    assert r2.headers["content-type"] == "video/mp4"
    cd = r2.headers["content-disposition"]
    assert "attachment" in cd
    assert "filename" in cd

    _cleanup_job(app_state, job_id)


# ------------------------------------------------------------------ T2 ----
def test_e2e_download_audio(client, app_state):
    """Descarga real de audio como MP3 via el flujo completo de la API."""
    r = client.post("/api/jobs", json={"url": _VIDEO_URL, "quality": "audio"})
    if r.status_code != 200:
        pytest.skip(f"YouTube no disponible: {r.json().get('detail', '')}")
    job_id = r.json()["job_id"]

    r2 = _poll_until_done(client, job_id)
    assert r2.status_code == 200
    assert len(r2.content) > 0
    assert r2.headers["content-type"] == "audio/mpeg"

    _cleanup_job(app_state, job_id)


# ------------------------------------------------------------------ T3 ----
def test_e2e_fetch_info():
    """_fetch_info sobre URL publica: title, duration, formats."""
    try:
        info = _fetch_info(_VIDEO_URL)
    except Exception as e:
        pytest.skip(f"YouTube no accesible: {e}")

    assert info is not None
    assert info.get("title")
    assert info.get("duration", 0) > 0
    formats = info.get("formats") or []
    assert len(formats) > 0


# ------------------------------------------------------------------ T4 ----
def test_e2e_fetch_playlist():
    """_fetch_playlist sobre playlist publica: count > 0, videos con datos."""
    try:
        info = _fetch_playlist(_PLAYLIST_URL)
    except Exception as e:
        pytest.skip(f"YouTube no accesible: {e}")

    assert info is not None
    assert info.get("count", 0) > 0
    videos = info.get("videos") or []
    assert len(videos) == info["count"]
    for v in videos:
        assert v.get("url")
        assert v.get("title")


# ------------------------------------------------------------------ T5 ----
def test_e2e_full_flow_content_disposition(client, app_state):
    """Flujo completo: POST job -> polling file -> verifica Content-Disposition."""
    r = client.post("/api/jobs", json={"url": _VIDEO_URL, "quality": _QUALITY})
    if r.status_code != 200:
        pytest.skip(f"YouTube no disponible: {r.json().get('detail', '')}")
    job_id = r.json()["job_id"]

    r2 = _poll_until_done(client, job_id)
    cd = r2.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd  # RFC 5987
    assert "attachment" in cd
    assert "Content-Length" in r2.headers
    assert int(r2.headers["Content-Length"]) > 0

    _cleanup_job(app_state, job_id)
