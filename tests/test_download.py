import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db import Database
from models import Job
from state import AppState


@pytest.fixture
def dl_state():
    tmp = Path(tempfile.mkdtemp())
    db = Database(":memory:")
    state = AppState(db, tmp)
    state.out_dir.mkdir(parents=True, exist_ok=True)
    yield state
    db.close()
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


def _make_job(state, job_id="test-1"):
    state.jobs[job_id] = Job(id=job_id, created=time.time())
    state.job_events[job_id] = asyncio.Event()
    state.db.insert_job(job_id, "https://x.com/1", "best")
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
    assert len(dl_state.get_history()) == 1


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


# ------- _fetch_playlist: includes unavailable entries ------- #
def test_fetch_playlist_includes_unavailable():
    from download import _fetch_playlist

    info = {
        "title": "Test Playlist",
        "entries": [
            {"title": "Video OK", "url": "https://x.com/1", "id": "v1"},
            {"title": "Video Privado", "url": "", "id": "v2"},
            None,
            {"title": "Otro OK", "webpage_url": "https://x.com/3", "id": "v3"},
        ],
    }
    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        result = _fetch_playlist("https://x.com/playlist?list=test")

    assert result["count"] == 3  # 3 valid entries (not None)
    videos = result["videos"]
    assert len(videos) == 3
    assert videos[0]["title"] == "Video OK"
    assert videos[0]["unavailable"] is False
    assert videos[1]["title"] == "Video Privado"
    assert videos[1]["unavailable"] is True
    assert videos[1]["url"] == ""
    assert videos[2]["title"] == "Otro OK"
    assert videos[2]["unavailable"] is False


# --------------------- watch mode: channel check ------------------------ #
def test_check_channel_watch_finds_new_videos(dl_state, monkeypatch):
    from download import _check_channel_watch

    playlist = {
        "title": "Test Channel",
        "count": 2,
        "videos": [
            {"title": "V1", "url": "https://x.com/1", "extractor": "youtube", "video_id": "vid1"},
            {"title": "V2", "url": "https://x.com/2", "extractor": "youtube", "video_id": "vid2"},
        ],
    }

    monkeypatch.setattr("download._fetch_playlist", lambda url: playlist)

    cid = dl_state.db.insert_channel("https://x.com/@test", "best")
    channel = {"id": cid, "url": "https://x.com/@test", "quality": "best"}
    videos = _check_channel_watch(dl_state, channel)
    assert len(videos) == 2
    assert videos[0]["url"] == "https://x.com/1"
    assert videos[0]["extractor"] == "youtube"
    assert videos[0]["video_id"] == "vid1"
    # No debe haber creado jobs ni registrado descargas
    assert not dl_state.db.is_downloaded("youtube", "vid1")
    assert not dl_state.db.is_downloaded("youtube", "vid2")
    assert len(dl_state.jobs) == 0


def test_check_channel_watch_skips_downloaded(dl_state, monkeypatch):
    from download import _check_channel_watch

    dl_state.db.insert_job("j1", "https://x.com/1", "best")
    dl_state.db.record_download("youtube", "vid1", "j1")

    playlist = {
        "title": "TC",
        "count": 2,
        "videos": [
            {"title": "V1", "url": "https://x.com/1", "extractor": "youtube", "video_id": "vid1"},
            {"title": "V2", "url": "https://x.com/2", "extractor": "youtube", "video_id": "vid2"},
        ],
    }

    monkeypatch.setattr("download._fetch_playlist", lambda url: playlist)

    cid = dl_state.db.insert_channel("https://x.com/@test", "best")
    channel = {"id": cid, "url": "https://x.com/@test", "quality": "best"}
    videos = _check_channel_watch(dl_state, channel)
    assert len(videos) == 1
    assert videos[0]["video_id"] == "vid2"


def test_check_channel_watch_skips_active_job(dl_state, monkeypatch):
    from download import _check_channel_watch

    dl_state.db.insert_job("j1", "https://x.com/1", "best", status="downloading")
    dl_state.db.update_job("j1", extractor="youtube", video_id="vid1")

    playlist = {
        "title": "TC",
        "count": 2,
        "videos": [
            {"title": "V1", "url": "https://x.com/1", "extractor": "youtube", "video_id": "vid1"},
            {"title": "V2", "url": "https://x.com/2", "extractor": "youtube", "video_id": "vid2"},
        ],
    }

    monkeypatch.setattr("download._fetch_playlist", lambda url: playlist)

    channel = {"id": 1, "url": "https://x.com/@test", "quality": "best"}
    videos = _check_channel_watch(dl_state, channel)
    assert len(videos) == 1
    assert videos[0]["video_id"] == "vid2"


def test_check_channel_watch_handles_fetch_error(dl_state, monkeypatch):
    from download import _check_channel_watch

    def fail(url):
        raise RuntimeError("boom")

    monkeypatch.setattr("download._fetch_playlist", fail)

    channel = {"id": 1, "url": "https://x.com/@test", "quality": "best"}
    videos = _check_channel_watch(dl_state, channel)
    assert videos == []


# ---------------- _run_download: dedup en camino de éxito ----------------- #
def test_run_download_records_dedup(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "dedup-ok")
    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    video = wd / "Test.mp4"
    video.write_bytes(b"fake mp4")
    info = {
        "title": "Test",
        "extractor_key": "youtube",
        "id": "abc123",
        "requested_downloads": [{"filepath": str(video)}],
    }

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    assert dl_state.jobs[jid].status == "done"
    assert dl_state.db.is_downloaded("youtube", "abc123")
    j = dl_state.db.get_job(jid)
    assert j["extractor"] == "youtube"
    assert j["video_id"] == "abc123"


def test_run_download_does_not_record_on_error(dl_state, monkeypatch):
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "dedup-fail")
    ydl = _mock_ydl(None)
    ydl.extract_info.return_value = None

    with patch("download.yt_dlp.YoutubeDL", return_value=ydl):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    assert dl_state.jobs[jid].status == "error"
    assert not dl_state.db.is_downloaded("youtube", "abc123")


def test_run_download_persists_error_status_to_db(dl_state, monkeypatch):
    """Un job que falla debe quedar status='error' en la DB, no 'queued'.

    Si quedara 'queued', el dispatch_loop lo re-despacharia tras evict_once
    (regresion: descarga fantasma ~1h despues de un fallo manual).
    """
    import config
    from download import _run_download

    monkeypatch.setattr(config, "MAX_SIZE_MB", 0)
    loop = asyncio.new_event_loop()

    jid = _make_job(dl_state, "err-persist")
    assert dl_state.db.get_job(jid)["status"] == "queued"

    def boom(*a, **kw):
        raise RuntimeError("403 Forbidden")

    with patch("download.yt_dlp.YoutubeDL", side_effect=boom):
        _run_download(dl_state, jid, "https://youtu.be/abc", "best", loop)
    loop.close()

    j = dl_state.db.get_job(jid)
    assert j["status"] == "error"
    assert j["error"]  # mensaje friendly no vacio
    # Y, lo importante: ya no es candidato para el dispatch_loop.
    assert jid not in [r["id"] for r in dl_state.db.get_queued(limit=10)]


# --------------------- secure delete ----------------------------------- #
def test_secure_delete_file_three_pass(tmp_path):
    from state import AppState

    f = tmp_path / "secret.bin"
    f.write_bytes(b"A" * 5000)
    assert f.exists()
    AppState._secure_delete_file(str(f))
    assert not f.exists()


def test_secure_delete_file_noop_on_missing(tmp_path):
    from state import AppState

    AppState._secure_delete_file(str(tmp_path / "ghost.bin"))


def test_secure_delete_workdir_recursive(tmp_path):
    from state import AppState

    wd = tmp_path / "opengrab_test"
    wd.mkdir()
    (wd / "a.bin").write_bytes(b"x" * 100)
    (wd / "b.bin").write_bytes(b"y" * 200)
    sub = wd / "sub"
    sub.mkdir()
    (sub / "c.bin").write_bytes(b"z" * 300)
    AppState._secure_delete_workdir(str(wd))
    assert not wd.exists()


# --------------------- history management ------------------------------ #
def test_delete_history_entry_removes_from_db_and_ram(dl_state, tmp_path):
    dl_state.db.insert_job("h1", "https://x.com/1", "best", status="done")
    dl_state.db.update_job("h1", filepath=str(tmp_path / "video.mp4"), workdir=str(tmp_path / "opengrab_wd"))

    f = tmp_path / "video.mp4"
    f.write_bytes(b"fake")
    wd = tmp_path / "opengrab_wd"
    wd.mkdir()

    assert dl_state.delete_history_entry("h1") is True
    assert dl_state.db.get_job("h1") is None


def test_delete_history_entry_nonexistent(dl_state):
    assert dl_state.delete_history_entry("phantom") is False


def test_clear_all_history(dl_state):
    dl_state.db.insert_job("a", "u", "best", status="done")
    dl_state.db.insert_job("b", "u", "best", status="error")
    dl_state.db.insert_job("c", "u", "best", status="interrupted")
    dl_state.db.insert_job("d", "u", "best", status="downloading")

    count = dl_state.clear_all_history()
    assert count == 3
    assert dl_state.db.get_job("d") is not None


# --------------------- storage ----------------------------------------- #
def test_list_storage(dl_state):
    info = dl_state.list_storage()
    assert "total_usage_bytes" in info
    assert "workdirs" in info
    assert "loose_files" in info
    assert "db_size_bytes" in info


def test_cleanup_storage_dry_run(dl_state):
    import time
    wd = dl_state.out_dir / "opengrab_old"
    wd.mkdir()
    (wd / "f.bin").write_bytes(b"x" * 1000)
    # make it look old
    old = time.time() - 50 * 3600
    os.utime(str(wd), (old, old))

    result = dl_state.cleanup_storage(max_age_hours=24, dry_run=True)
    assert result["dry_run"] is True
    assert result["would_clean"] >= 1
    assert result["freed_bytes"] >= 1000
    assert wd.exists()


def test_cleanup_storage_deletes_old_workdirs(dl_state):
    import time
    wd = dl_state.out_dir / "opengrab_ancient"
    wd.mkdir()
    (wd / "f.bin").write_bytes(b"x" * 500)
    old = time.time() - 50 * 3600
    os.utime(str(wd), (old, old))

    result = dl_state.cleanup_storage(max_age_hours=24)
    assert result["cleaned"] >= 1
    assert result["freed_bytes"] >= 500
    assert not wd.exists()


def test_cleanup_storage_keeps_recent_workdirs(dl_state):
    wd = dl_state.out_dir / "opengrab_fresh"
    wd.mkdir()
    (wd / "f.bin").write_bytes(b"x" * 100)

    result = dl_state.cleanup_storage(max_age_hours=24)
    assert wd.exists()
