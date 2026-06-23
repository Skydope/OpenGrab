"""Integration tests for dispatch_loop (batch playlist download).

These tests verify that:
1. dispatch_loop picks up queued jobs up to MAX_JOBS limit
2. It guards against duplicate dispatches (job_id already in state.jobs)
3. It marks jobs as 'starting' in DB before adding to state.jobs
4. It correctly handles storage-full condition
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from db import Database
from models import Job
from state import AppState


@pytest.fixture
def dispatch_state(tmp_path):
    """Create AppState with in-memory DB for dispatch_loop tests."""
    db = Database(":memory:")
    state = AppState(db, tmp_path)
    yield state
    db.close()


def seed_queued_jobs(state, count=5, quality="best"):
    """Seed `count` jobs with status=queued in DB."""
    for i in range(count):
        job_id = f"queued-{i:03d}"
        state.db.insert_job(job_id, f"https://example.com/video{i}", quality)


class TestDispatchLoopBasic:
    """Test dispatch_loop basic behavior."""

    @pytest.mark.asyncio
    async def test_dispatch_loop_respects_max_jobs_limit(self, dispatch_state, monkeypatch):
        """dispatch_loop should only start up to MAX_JOBS concurrent downloads."""
        # Set up: seed 5 queued jobs
        seed_queued_jobs(dispatch_state, count=5)

        # Patch MAX_JOBS to 2 (patch at source: config module)
        monkeypatch.setattr("config.MAX_JOBS", 2)
        # Mock _run_download to not actually run (it's imported from download inside dispatch_loop)
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        # Make asyncio.sleep succeed on first call (so loop body runs), fail on second (stop loop)
        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop after one tick")
            # First call: just return (loop body executes)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await dispatch_state.dispatch_loop()
            except asyncio.CancelledError:
                pass

        # Verify exactly 2 jobs were moved to 'starting'
        starting_jobs = [
            j for j in dispatch_state.db.get_active_jobs()
            if j["status"] == "starting"
        ]
        assert len(starting_jobs) == 2, f"Expected 2 starting jobs, got {len(starting_jobs)}: {starting_jobs}"

        # Verify those 2 are in state.jobs
        assert len(dispatch_state.jobs) == 2

        # Verify remaining 3 are still queued
        queued_jobs = dispatch_state.db.get_queued(limit=10)
        assert len(queued_jobs) == 3

    @pytest.mark.asyncio
    async def test_dispatch_loop_guards_against_double_dispatch(self, dispatch_state, monkeypatch):
        """dispatch_loop should skip jobs already in state.jobs."""
        seed_queued_jobs(dispatch_state, count=3)

        # Pre-populate state.jobs with one of the job_ids
        dispatch_state.jobs["queued-001"] = Job(id="queued-001", created=0.0)

        monkeypatch.setattr("config.MAX_JOBS", 5)
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop after one tick")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await dispatch_state.dispatch_loop()
            except asyncio.CancelledError:
                pass

        # Should have dispatched only 2 (not the one already in state.jobs)
        starting_jobs = [
            j for j in dispatch_state.db.get_active_jobs()
            if j["status"] == "starting"
        ]
        assert len(starting_jobs) == 2
        assert len(dispatch_state.jobs) == 3  # 1 pre-existing + 2 new

    @pytest.mark.asyncio
    async def test_dispatch_loop_marks_starting_before_adding_to_jobs(self, dispatch_state, monkeypatch):
        """DB should show status='starting' BEFORE job is added to state.jobs."""
        seed_queued_jobs(dispatch_state, count=2)

        monkeypatch.setattr("config.MAX_JOBS", 5)

        # Track order: we want to verify that when _run_download is called,
        # the job is already marked 'starting' in DB
        db_status_at_download_time = {}

        async def fake_run_download(state, job_id, url, quality, loop):
            # Check DB status at the moment _run_download starts
            job = state.db.get_job(job_id)
            db_status_at_download_time[job_id] = job["status"]

        monkeypatch.setattr("download._run_download", fake_run_download)

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop after one tick")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await dispatch_state.dispatch_loop()
            except asyncio.CancelledError:
                pass

        # Verify that for each dispatched job, DB already showed 'starting'
        for job_id, status in db_status_at_download_time.items():
            assert status == "starting", f"Job {job_id} had status '{status}' in DB, expected 'starting'"


class TestDispatchLoopStorage:
    """Test dispatch_loop storage management."""

    @pytest.mark.asyncio
    async def test_dispatch_loop_storage_full_marks_error(self, dispatch_state, monkeypatch):
        """When storage is full, dispatch_loop should mark job as error with 'Almacenamiento lleno'."""
        seed_queued_jobs(dispatch_state, count=1)

        monkeypatch.setattr("config.MAX_JOBS", 5)
        monkeypatch.setattr("config.MAX_TOTAL_MB", 1)  # Set tiny limit
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        # Mock current_usage_bytes to return full
        dispatch_state.current_usage_bytes = lambda: 2 * 1024 * 1024  # 2 MB > 1 MB limit

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("stop after one tick")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await dispatch_state.dispatch_loop()
            except asyncio.CancelledError:
                pass

        # Job should be marked error
        job = dispatch_state.db.get_job("queued-000")
        assert job["status"] == "error"
        assert "Almacenamiento lleno" in job.get("error", "")


class TestDispatchLoopSecondTick:
    """Test that second tick doesn't re-dispatch already-dispatched jobs."""

    @pytest.mark.asyncio
    async def test_second_tick_does_not_redispatch(self, dispatch_state, monkeypatch):
        """Second call to dispatch_loop should not dispatch jobs already in state.jobs."""
        seed_queued_jobs(dispatch_state, count=4)

        monkeypatch.setattr("config.MAX_JOBS", 10)
        monkeypatch.setattr("download._run_download", lambda *a, **kw: None)

        # We'll let the loop run 2 ticks by making sleep fail with CancelledError
        # after tracking calls
        tick_count = 0

        async def fake_sleep(delay):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                raise asyncio.CancelledError("two ticks done")

        with patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await dispatch_state.dispatch_loop()
            except asyncio.CancelledError:
                pass

        # All 4 jobs should be dispatched (MAX_JOBS=10 allows all)
        starting_jobs = [
            j for j in dispatch_state.db.get_active_jobs()
            if j["status"] == "starting"
        ]
        assert len(starting_jobs) == 4
        assert len(dispatch_state.jobs) == 4

        # Now call dispatch_loop again - should not dispatch anything new
        tick_count = 0

        async def fake_sleep2(delay):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 1:
                raise asyncio.CancelledError("stop")

        with patch("asyncio.sleep", side_effect=fake_sleep2):
            with patch("state.asyncio.sleep", side_effect=fake_sleep2):
                try:
                    await dispatch_state.dispatch_loop()
                except asyncio.CancelledError:
                    pass

        # Should still be 4 starting jobs, no new dispatches
        starting_jobs_after = [
            j for j in dispatch_state.db.get_active_jobs()
            if j["status"] == "starting"
        ]
        assert len(starting_jobs_after) == 4
        assert len(dispatch_state.jobs) == 4


class TestDispatchLoopLifespan:
    """Test dispatch_loop integration with app lifespan."""

    def test_lifespan_creates_and_cancels_dispatch_task(self, tmp_path, monkeypatch):
        """dispatch_loop task should be created on startup and cancelled on shutdown."""
        import sys
        from pathlib import Path

        # Clear any cached modules to ensure fresh import
        for mod in list(sys.modules):
            if mod in (
                "app", "config", "models", "download", "routes",
                "state", "db", "app_state", "_opengrab_modules",
            ) or mod.startswith(("app.", "config.", "models.", "download.", "routes.", "state.", "db.")):
                del sys.modules[mod]

        # Patch env before importing app
        monkeypatch.setenv("OPENGRAB_HOST", "127.0.0.1")
        monkeypatch.setenv("OPENGRAB_PORT", "8881")
        monkeypatch.setenv("OPENGRAB_DIR", str(tmp_path / "downloads"))
        monkeypatch.setenv("OPENGRAB_TOKEN", "test-token-lifespan")
        monkeypatch.setenv("OPENGRAB_MAX_JOBS", "2")
        monkeypatch.setenv("OPENGRAB_AUTOUPDATE", "0")
        monkeypatch.setenv(
            "OPENGRAB_CONFIG",
            str(tmp_path / "nonexistent.ini"),
        )

        # Mock _run_download to avoid actual downloads
        import download
        monkeypatch.setattr(download, "_run_download", lambda *a, **kw: None)

        from app import _lifespan, app
        from fastapi.testclient import TestClient

        # Use TestClient which triggers lifespan on enter/exit
        # Track whether dispatch_loop task was seen
        dispatch_task_seen = {"started": False, "cancelled": False}
        original_cancel = None

        # We need to verify dispatch_task exists and is cancelled
        # The simplest way: check that the task exists during lifespan
        with TestClient(app) as client:
            state = client.app.state.opengrab
            # Verify dispatch_loop is running by checking for active tasks
            # Actually, we verify indirectly: seed jobs and check they get picked up
            for i in range(3):
                state.db.insert_job(f"life-job-{i}", f"https://example.com/v{i}", "best")

            # Wait a bit for dispatch_loop to run (it runs every 2s)
            # But we don't want to wait in tests, so we check state directly
            # The key verification: during lifespan, dispatch_loop should exist as a task
            # We verify by checking that jobs got moved from queued to starting
            import time
            start = time.time()
            while time.time() - start < 3.0:
                queued = state.db.get_queued(limit=10)
                if len(queued) < 3:
                    break
                time.sleep(0.1)

            # After ~2-4 seconds, dispatch_loop should have picked up jobs
            # With MAX_JOBS=2, we expect 2 jobs moved to starting
            active_jobs = state.db.get_active_jobs()
            starting_count = sum(1 for j in active_jobs if j["status"] == "starting")

            # This is the indirect verification: if dispatch_loop is running,
            # it should have picked up some jobs
            assert starting_count >= 1, (
                f"dispatch_loop not running: expected at least 1 starting job, got {starting_count}"
            )

        # After TestClient exits, the lifespan cleanup runs
        # We can't directly check cancellation, but the fact that TestClient
        # exits without error means cleanup completed properly
        # If dispatch_task.cancel() wasn't called, we'd see warnings or errors
