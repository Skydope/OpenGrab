"""Tests for state.py: evict_once, evict_loop, and watch_loop invariants.

Cubre los cuerpos de los loops de fondo — el subsistema mas fragil del repo
donde ya cazamos dos bugs (el overshoot de MAX_JOBS y la persistencia de jobs
en error). El patron de test es el mismo que test_dispatch.py: mock de
asyncio.sleep con CancelledError para detener el loop despues de N ticks.
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from db import Database
from models import Job
from state import AppState


@pytest.fixture
def loop_state(tmp_path):
    """AppState con DB en memoria para tests de eviction y loops."""
    db = Database(":memory:")
    state = AppState(db, tmp_path)
    yield state
    db.close()


# --------------------------------------------------------------------------- #
# evict_once — limpieza periodica de jobs viejos de memoria
# --------------------------------------------------------------------------- #

class TestEvictOnce:
    """Verifica que evict_once remueve jobs done/error viejos, respeta el
    cutoff, limpia workdirs y llama a prune_history."""

    def test_removes_old_done_jobs(self, loop_state):
        """Jobs con status='done' mas viejos que cutoff_age se evacuan."""
        old = Job(id="old-done", created=time.time() - 7200)
        old.status = "done"
        loop_state.jobs["old-done"] = old
        loop_state.job_events["old-done"] = asyncio.Event()

        evicted = loop_state.storage.evict_once(cutoff_age=3600)
        assert evicted == 1
        assert "old-done" not in loop_state.jobs
        assert "old-done" not in loop_state.job_events

    def test_removes_old_error_jobs(self, loop_state):
        """Jobs con status='error' mas viejos que cutoff_age se evacuan."""
        old = Job(id="old-error", created=time.time() - 7200)
        old.status = "error"
        loop_state.jobs["old-error"] = old

        evicted = loop_state.storage.evict_once(cutoff_age=3600)
        assert evicted == 1
        assert "old-error" not in loop_state.jobs

    def test_keeps_recent_jobs(self, loop_state):
        """Jobs recientes (< cutoff_age) se conservan en memoria."""
        recent = Job(id="recent", created=time.time() - 60)
        recent.status = "done"
        loop_state.jobs["recent"] = recent

        evicted = loop_state.storage.evict_once(cutoff_age=3600)
        assert evicted == 0
        assert "recent" in loop_state.jobs

    def test_keeps_active_jobs_regardless_of_age(self, loop_state):
        """Jobs activos (queued/starting/downloading/processing) no se evacuan
        aunque sean viejos. Solo done y error compiten para eviction."""
        for status in ("queued", "starting", "downloading", "processing"):
            j = Job(id=f"act-{status}", created=time.time() - 7200)
            j.status = status
            loop_state.jobs[f"act-{status}"] = j

        evicted = loop_state.storage.evict_once(cutoff_age=3600)
        assert evicted == 0
        for status in ("queued", "starting", "downloading", "processing"):
            assert f"act-{status}" in loop_state.jobs

    def test_cleans_workdir_of_evicted_job(self, loop_state):
        """Si un job evicted tenia workdir existente, se borra con shutil.rmtree."""
        wd = loop_state.out_dir / "opengrab_wd_test"
        wd.mkdir(parents=True)
        (wd / "temp.bin").write_bytes(b"\x00" * 256)

        old = Job(id="old-done", created=time.time() - 7200)
        old.status = "done"
        old.workdir = str(wd)
        loop_state.jobs["old-done"] = old

        evicted = loop_state.storage.evict_once(cutoff_age=3600)
        assert evicted == 1
        assert not wd.exists()

    def test_workdir_missing_is_noop(self, loop_state):
        """Si el workdir ya no existe, no falla. Solo se saltea el rmtree."""
        old = Job(id="old-done", created=time.time() - 7200)
        old.status = "done"
        old.workdir = "/tmp/nonexistent_opengrab_wd"
        loop_state.jobs["old-done"] = old

        evicted = loop_state.storage.evict_once(cutoff_age=3600)
        assert evicted == 1

    def test_calls_prune_history_even_when_no_evictions(self, loop_state, monkeypatch):
        """prune_history se llama SIEMPRE, incluso si no se evacuo ningun job."""
        calls = []
        monkeypatch.setattr(loop_state.db, "prune_history",
                            lambda keep: calls.append(keep) or 0)

        loop_state.storage.evict_once(cutoff_age=3600)
        assert len(calls) == 1
        assert calls[0] == loop_state.resolve("history_max", 500, int)[0]

    def test_eviction_count_matches_removed(self, loop_state):
        """El valor de retorno refleja exactamente有多少 jobs se borraron."""
        for i in range(3):
            j = Job(id=f"old-{i}", created=time.time() - 7200)
            j.status = "done"
            loop_state.jobs[f"old-{i}"] = j

        evicted = loop_state.storage.evict_once(cutoff_age=3600)
        assert evicted == 3
        assert len(loop_state.jobs) == 0


# --------------------------------------------------------------------------- #
# evict_loop — wrapper asyncio que llama a evict_once cada 300s
# --------------------------------------------------------------------------- #
# evict_loop — wrapper asyncio que llama a evict_once cada 300s
# --------------------------------------------------------------------------- #

class TestEvictLoop:
    """Verifica que evict_loop invoca evict_once periodicamente."""

    @pytest.mark.asyncio
    async def test_evict_loop_calls_evict_once(self, loop_state, monkeypatch):
        """Despues de un tick (sleep), evict_once se ejecuto al menos una vez."""
        old = Job(id="old-done", created=time.time() - 7200)
        old.status = "done"
        loop_state.jobs["old-done"] = old

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop after one tick")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await loop_state.storage.evict_loop()
            except asyncio.CancelledError:
                pass

        assert "old-done" not in loop_state.jobs
        assert call_count == 2  # entro, ejecuto, volvio a dormir


# --------------------------------------------------------------------------- #
# watch_loop — scheduler de canales (cada 60s)
# --------------------------------------------------------------------------- #

class TestWatchLoop:
    """Verifica que watch_loop chequea canales en intervalo, despacha videos
    nuevos y respeta la deduplication (downloaded + active jobs)."""

    @pytest.mark.asyncio
    async def test_dispatches_new_videos(self, loop_state, monkeypatch):
        """Canal sin last_checked (due inmediato): los videos nuevos se
        despachan como jobs."""
        ch_id = loop_state.db.insert_channel(
            "https://youtube.com/@test", quality="best",
        )
        loop_state.db.update_channel(ch_id, enabled=True, interval_minutes=60)

        monkeypatch.setattr(
            "download._check_channel_watch",
            lambda *a, **kw: [{
                "url": "https://youtube.com/watch?v=abc",
                "extractor": "youtube",
                "video_id": "abc123",
                "title": "Test Video",
            }],
        )
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop after one tick")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await loop_state.watch_loop()
            except asyncio.CancelledError:
                pass

        active = loop_state.db.get_active_jobs()
        assert len(active) == 1
        assert active[0]["url"] == "https://youtube.com/watch?v=abc"
        assert active[0]["quality"] == "best"
        assert active[0]["extractor"] == "youtube"
        assert active[0]["video_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_skips_already_downloaded_video(self, loop_state, monkeypatch):
        """Video ya registrado en downloaded_urls no se re-despacha."""
        ch_id = loop_state.db.insert_channel(
            "https://youtube.com/@test", quality="best",
        )
        loop_state.db.update_channel(ch_id, enabled=True, interval_minutes=60)
        monkeypatch.setattr(loop_state.db, "is_downloaded",
                            lambda e, v: True)

        monkeypatch.setattr(
            "download._check_channel_watch",
            lambda *a, **kw: [{
                "url": "https://youtube.com/watch?v=abc",
                "extractor": "youtube",
                "video_id": "abc123",
                "title": "Already Got It",
            }],
        )
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await loop_state.watch_loop()
            except asyncio.CancelledError:
                pass

        active = loop_state.db.get_active_jobs()
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_skips_when_interval_not_elapsed(self, loop_state, monkeypatch):
        """Canal con last_checked reciente no se chequea hasta que pase el
        intervalo."""
        ch_id = loop_state.db.insert_channel(
            "https://youtube.com/@test", quality="best",
        )
        loop_state.db.update_channel(ch_id, enabled=True, interval_minutes=60)
        loop_state.db.touch_channel(ch_id)

        check_calls = []
        monkeypatch.setattr(
            "download._check_channel_watch",
            lambda *a, **kw: check_calls.append(True) or [],
        )
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await loop_state.watch_loop()
            except asyncio.CancelledError:
                pass

        assert len(check_calls) == 0

    @pytest.mark.asyncio
    async def test_skips_active_job_for_same_video(self, loop_state, monkeypatch):
        """Video con job activo (mismo extractor + video_id) no se re-despacha."""
        ch_id = loop_state.db.insert_channel(
            "https://youtube.com/@test", quality="best",
        )
        loop_state.db.update_channel(ch_id, enabled=True, interval_minutes=60)
        loop_state.db.insert_job(
            "existing-job", "https://youtube.com/watch?v=abc", "best",
        )
        loop_state.db.update_job(
            "existing-job", extractor="youtube", video_id="abc123",
            status="downloading",
        )

        monkeypatch.setattr(
            "download._check_channel_watch",
            lambda *a, **kw: [{
                "url": "https://youtube.com/watch?v=abc",
                "extractor": "youtube",
                "video_id": "abc123",
                "title": "Already Active",
            }],
        )
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await loop_state.watch_loop()
            except asyncio.CancelledError:
                pass

        active = loop_state.db.get_active_jobs()
        assert len(active) == 1
        assert active[0]["id"] == "existing-job"

    @pytest.mark.asyncio
    async def test_handles_channel_check_error(self, loop_state, monkeypatch):
        """Si _check_channel_watch lanza, el loop sigue (no crashea)."""
        ch_id = loop_state.db.insert_channel(
            "https://youtube.com/@test", quality="best",
        )
        loop_state.db.update_channel(ch_id, enabled=True, interval_minutes=60)

        monkeypatch.setattr(
            "download._check_channel_watch",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await loop_state.watch_loop()
            except asyncio.CancelledError:
                pass

        # El loop sobrevivio al error; el canal sigue sin tener last_checked
        # (touch_channel no se llamo porque _check_channel_watch fallo)
        active = loop_state.db.get_active_jobs()
        assert len(active) == 0


# --------------------------------------------------------------------------- #
# _track_task + _spawn_download — consolidated spawn helpers
# --------------------------------------------------------------------------- #


class TestTrackTask:
    """Verifica que _track_task agrega la task a running_tasks y la limpia
    via done_callback al completar."""

    def test_adds_and_discards(self, loop_state):
        async def dummy():
            pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            task = loop.create_task(dummy())
            loop_state._track_task(task)

            assert task in loop_state.running_tasks

            loop.run_until_complete(task)
            # done_callback se ejecuta en el loop — corremos un tick extra
            loop.run_until_complete(asyncio.sleep(0))

            assert task not in loop_state.running_tasks
        finally:
            loop.close()


class TestSpawnDownload:
    """Verifica que _spawn_download crea el Job en memoria, asigna un Event
    y lanza la descarga sin tocar la DB."""

    @pytest.mark.asyncio
    async def test_creates_job_and_event(self, loop_state, monkeypatch):
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        loop_state._spawn_download("test-spawn-1", "http://x.com/v", "best")

        assert "test-spawn-1" in loop_state.jobs
        job = loop_state.jobs["test-spawn-1"]
        assert job.id == "test-spawn-1"
        assert job.status == "queued"

        assert "test-spawn-1" in loop_state.job_events
        assert isinstance(loop_state.job_events["test-spawn-1"], asyncio.Event)

        # Cleanup — la DB no fue tocada (no hay insert_job)
        loop_state.jobs.pop("test-spawn-1", None)
        loop_state.job_events.pop("test-spawn-1", None)


# --------------------------------------------------------------------------- #
# current_usage_bytes — TTL cache
# --------------------------------------------------------------------------- #


    class TestUsageCache:
        def test_cold_cache_scans(self, loop_state):
            (loop_state.out_dir / "a.bin").write_bytes(b"x" * 100)
            assert loop_state.storage.current_usage_bytes() == 100

        def test_cached_within_ttl(self, loop_state, monkeypatch):
            calls = []
            monkeypatch.setattr(loop_state.storage, "_scan_usage_bytes",
                                lambda: calls.append(1) or 42)

            assert loop_state.storage.current_usage_bytes() == 42
            assert len(calls) == 1

            assert loop_state.storage.current_usage_bytes() == 42
            assert len(calls) == 1  # cached, no second scan

        def test_rescans_after_ttl_expiry(self, loop_state, monkeypatch):
            calls = []
            monkeypatch.setattr(loop_state.storage, "_scan_usage_bytes",
                                lambda: calls.append(1) or 42)
            t0 = 0.0
            monkeypatch.setattr("time.monotonic", lambda: t0)

            assert loop_state.storage.current_usage_bytes(max_age=1.0) == 42
            assert len(calls) == 1

            t0 = 2.0  # TTL expired
            assert loop_state.storage.current_usage_bytes(max_age=1.0) == 42
            assert len(calls) == 2  # re-scanned

        def test_scan_usage_bytes_counts_recursive(self, loop_state):
            (loop_state.out_dir / "a.bin").write_bytes(b"x" * 100)
            sub = loop_state.out_dir / "sub"
            sub.mkdir()
            (sub / "b.bin").write_bytes(b"x" * 200)
            assert loop_state.storage._scan_usage_bytes() >= 300

        def test_invalidated_after_clear_history(self, loop_state, monkeypatch):
            calls = []
            monkeypatch.setattr(loop_state.storage, "_scan_usage_bytes",
                                lambda: calls.append(1) or 42)

            assert loop_state.storage.current_usage_bytes() == 42
            assert len(calls) == 1

            # Simular clear_all_history — el metodo invalida el cache
            with loop_state.storage._usage_lock:
                loop_state.storage._usage_cache_ts = 0.0

            assert loop_state.storage.current_usage_bytes() == 42
            assert len(calls) == 2  # re-scanned tras invalidar timestamp

    def test_rescans_after_ttl_expiry(self, loop_state, monkeypatch):
        calls = []
        monkeypatch.setattr(loop_state.storage, "_scan_usage_bytes",
                            lambda: calls.append(1) or 42)
        t0 = 0.0
        monkeypatch.setattr("time.monotonic", lambda: t0)

        assert loop_state.storage.current_usage_bytes(max_age=1.0) == 42
        assert len(calls) == 1

        t0 = 2.0  # TTL expired
        assert loop_state.storage.current_usage_bytes(max_age=1.0) == 42
        assert len(calls) == 2  # re-scanned

    def test_scan_usage_bytes_counts_recursive(self, loop_state):
        (loop_state.out_dir / "a.bin").write_bytes(b"x" * 100)
        sub = loop_state.out_dir / "sub"
        sub.mkdir()
        (sub / "b.bin").write_bytes(b"x" * 200)
        assert loop_state.storage._scan_usage_bytes() >= 300
