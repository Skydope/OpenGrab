"""E2E tests con yt-dlp real — descargan videos públicos de YouTube.

NO corren en CI normal (requieren red + ffmpeg). Se ejecutan con::

    pytest tests/ -v -m e2e

Cada test maneja graceful degradation: si YouTube no está accesible
(timeout, rate limit, error HTTP), hace skip en vez de fallar.
"""

from __future__ import annotations

import time

import pytest

from download import _fetch_info, _fetch_playlist

pytestmark = pytest.mark.e2e

# URLs públicas estables
_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
# Playlist pública pequeña (~5 videos)
_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"


# ------------------------------------------------------------------ T1 ----
def test_e2e_download_video(client, app_state):
    """Descarga real de video como MP4 vía el flujo completo de la API."""
    r = client.post("/api/jobs", json={"url": _VIDEO_URL, "quality": "best"})
    if r.status_code != 200:
        pytest.skip(f"YouTube no disponible: {r.json().get('detail', '')}")
    job_id = r.json()["job_id"]

    deadline = time.time() + 120
    job_done = False
    while time.time() < deadline:
        r2 = client.get(f"/api/jobs/{job_id}/file")
        if r2.status_code == 200:
            job_done = True
            break
        if r2.status_code == 409:
            time.sleep(2)
            continue
        pytest.fail(f"Job falló: status={r2.status_code}")
    if not job_done:
        pytest.fail("Timeout esperando descarga")

    assert r2.status_code == 200
    assert len(r2.content) > 0
    assert r2.headers["content-type"] == "video/mp4"
    cd = r2.headers["content-disposition"]
    assert "attachment" in cd
    assert "filename" in cd


# ------------------------------------------------------------------ T2 ----
def test_e2e_download_audio(client, app_state):
    """Descarga real de audio como MP3 vía el flujo completo de la API."""
    r = client.post("/api/jobs", json={"url": _VIDEO_URL, "quality": "audio"})
    if r.status_code != 200:
        pytest.skip(f"YouTube no disponible: {r.json().get('detail', '')}")
    job_id = r.json()["job_id"]

    deadline = time.time() + 120
    job_done = False
    while time.time() < deadline:
        r2 = client.get(f"/api/jobs/{job_id}/file")
        if r2.status_code == 200:
            job_done = True
            break
        if r2.status_code == 409:
            time.sleep(2)
            continue
        pytest.fail(f"Job falló: status={r2.status_code}")
    if not job_done:
        pytest.fail("Timeout esperando descarga")

    assert r2.status_code == 200
    assert len(r2.content) > 0
    assert r2.headers["content-type"] == "audio/mpeg"


# ------------------------------------------------------------------ T3 ----
def test_e2e_fetch_info():
    """_fetch_info sobre URL pública: title, duration, formats."""
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
    """_fetch_playlist sobre playlist pública: count > 0, videos con datos."""
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
    """Flujo completo: POST job → polling file → verifica Content-Disposition."""
    r = client.post("/api/jobs", json={"url": _VIDEO_URL, "quality": "best"})
    if r.status_code != 200:
        pytest.skip(f"YouTube no disponible: {r.json().get('detail', '')}")
    job_id = r.json()["job_id"]

    deadline = time.time() + 120
    job_done = False
    while time.time() < deadline:
        r2 = client.get(f"/api/jobs/{job_id}/file")
        if r2.status_code == 200:
            job_done = True
            break
        if r2.status_code == 409:
            time.sleep(2)
            continue
        pytest.fail(f"Job falló: status={r2.status_code}")
    if not job_done:
        pytest.fail("Timeout esperando descarga")

    cd = r2.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd  # RFC 5987
    assert "attachment" in cd
    assert "Content-Length" in r2.headers
    assert int(r2.headers["Content-Length"]) > 0
