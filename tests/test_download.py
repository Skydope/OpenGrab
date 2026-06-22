import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models import Job
from state import AppState


@pytest.fixture
def dl_state():
    tmp = Path(tempfile.mkdtemp())
    state = AppState(tmp, tmp / ".history.json")
    state.out_dir.mkdir(parents=True, exist_ok=True)
    yield state
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


def _make_job(state, job_id="test-1"):
    state.jobs[job_id] = Job(id=job_id, created=time.time())
    state.job_events[job_id] = asyncio.Event()
    return job_id


def _mock_ydl(info):
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.return_value = info
    return ydl


# ------------------------------------------------------------------ T1 ----
def test_run_download_video_success(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t1")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    video = wd / "Test.mp4"
    video.write_bytes(b"fake mp4")
    info = {"title": "Test", "requested_downloads": [{"filepath": str(video)}]}

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "done"
    assert job.percent == 100.0
    assert job.filename == "Test.mp4"
    assert job.mime == "video/mp4"
    assert job.filepath == str(video)
    assert len(dl_state.history) == 1


# ------------------------------------------------------------------ T2 ----
def test_run_download_audio_success(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t2")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    audio = wd / "Song.mp3"
    audio.write_bytes(b"fake mp3")
    info = {"title": "Song", "requested_downloads": [{"filepath": str(audio)}]}

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        _run_download(dl_state, jid, "https://youtu.be/abc", "audio", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "done"
    assert job.mime == "audio/mpeg"


# ------------------------------------------------------------------ T3 ----
def test_run_download_fallback_glob(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t3")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    video = wd / "Fallback.mp4"
    video.write_bytes(b"fake")
    info = {"title": "Fallback"}

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)), \
         patch("download.tempfile.mkdtemp", return_value=str(wd)):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "done"
    assert job.filename == "Fallback.mp4"


# ------------------------------------------------------------------ T4 ----
def test_run_download_extract_info_none(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t4")
    ydl = _mock_ydl(None)
    ydl.extract_info.return_value = None

    with patch("download.yt_dlp.YoutubeDL", return_value=ydl):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "error"
    assert "no devolvió" in job.error


# ------------------------------------------------------------------ T5 ----
def test_run_download_no_files(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t5")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    info = {"title": "Ghost"}

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "error"
    assert "No se generó" in job.error


# ------------------------------------------------------------------ T6 ----
def test_run_download_file_not_found(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t6")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    ghost = wd / "missing.mp4"
    info = {"title": "Missing", "requested_downloads": [{"filepath": str(ghost)}]}

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "error"
    assert "Archivo no encontrado" in job.error


# ------------------------------------------------------------------ T7 ----
def test_run_download_size_enforcement(dl_state, monkeypatch):
    import download
    from download import _run_download

    monkeypatch.setattr(download, "MAX_SIZE_MB", 1)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t7")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    big = wd / "big.mp4"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    info = {"title": "Big", "requested_downloads": [{"filepath": str(big)}]}

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "error"
    assert "supera el limite" in job.error
    assert not big.exists()


# ------------------------------------------------------------------ T8 ----
def test_run_download_hook_percent(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "t8")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    video = wd / "Hook.mp4"
    video.write_bytes(b"fake")

    captured_hook = []

    class HookYDL:
        def __init__(self, opts):
            captured_hook.extend(opts.get("progress_hooks", []))
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def extract_info(self, url, download=True):
            for h in captured_hook:
                h({
                    "status": "downloading",
                    "total_bytes": 2000000,
                    "downloaded_bytes": 1000000,
                    "_speed_str": "10MiB/s",
                    "_eta_str": "5s",
                })
                h({"status": "finished"})
            return {
                "title": "Hook",
                "requested_downloads": [{"filepath": str(video)}],
            }

    with patch("download.yt_dlp.YoutubeDL", HookYDL):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "done"
    assert job.downloaded == 1000000
    assert job.total == 2000000
    assert job.speed == "10MiB/s"
    assert job.eta == "5s"


# ----------------------- A.1: mapeo de errores --------------------------- #
import download


def test_friendly_error_maps_403():
    msg = download._friendly_error(Exception("ERROR: unable to download: HTTP Error 403: Forbidden"))
    assert "rechaz" in msg.lower()
    assert "403" not in msg  # no filtra el error técnico crudo


def test_friendly_error_private_video():
    assert "privado" in download._friendly_error(Exception("ERROR: Private video")).lower()


def test_friendly_error_geo():
    assert "regi" in download._friendly_error(
        Exception("This video is not available in your country")
    ).lower()


def test_friendly_error_network():
    assert "red" in download._friendly_error(Exception("<urlopen error timed out>")).lower()


def test_friendly_error_unknown_passthrough():
    msg = download._friendly_error(Exception("boom interno raro 12345"))
    assert "boom interno raro 12345" in msg


def test_friendly_error_truncates_long_unknown():
    msg = download._friendly_error(Exception("x" * 500))
    assert len(msg) <= 300
