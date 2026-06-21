import time
from unittest.mock import patch

import pytest


def test_rate_limiting_info(client, app_state):
    with patch("routes._fetch_info", return_value={"title": "Test", "duration": 60, "formats": []}):
        for i in range(10):
            r = client.get("/api/info?url=https://youtu.be/abc")
            assert r.status_code == 200, f"Request {i + 1} should pass"

        r = client.get("/api/info?url=https://youtu.be/abc")
        assert r.status_code == 429


def test_rate_limiting_jobs(client, app_state, monkeypatch):
    import routes
    monkeypatch.setattr(routes, "MAX_JOBS", 100)
    monkeypatch.setattr("routes._run_download", lambda *a, **kw: None)

    for i in range(5):
        r = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/abc", "quality": "best"},
        )
        assert r.status_code == 200, f"Request {i + 1} should pass"

    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/abc", "quality": "best"},
    )
    assert r.status_code == 429


def test_eviction_removes_old_jobs(client, app_state):
    from models import Job

    old_job = Job(id="old-job", created=0.0)
    old_job.status = "done"
    app_state.jobs["old-job"] = old_job

    recent_job = Job(id="recent-job", created=time.time())
    recent_job.status = "downloading"
    app_state.jobs["recent-job"] = recent_job

    count = app_state.evict_once()
    assert count == 1
    assert "old-job" not in app_state.jobs
    assert "recent-job" in app_state.jobs


@pytest.mark.asyncio
async def test_job_events_stream_lifecycle():
    import asyncio
    import json
    import tempfile
    from pathlib import Path

    from models import Job
    from routes import _job_events_stream
    from state import AppState

    tmp = Path(tempfile.mkdtemp())
    state = AppState(tmp, tmp / ".history.json")

    job = Job(id="sse-test", created=time.time())
    job.status = "starting"
    state.jobs["sse-test"] = job
    state.job_events["sse-test"] = asyncio.Event()

    events = []

    async def collect():
        async for raw in _job_events_stream(state, "sse-test"):
            data = json.loads(raw[6:])
            events.append(data)

    task = asyncio.create_task(collect())

    await asyncio.sleep(0.1)
    job.status = "downloading"
    job.percent = 42.0
    state.job_events["sse-test"].set()

    await asyncio.sleep(0.1)
    job.status = "done"
    job.filename = "video.mp4"
    state.job_events["sse-test"].set()

    await task

    assert len(events) >= 2
    statuses = [e["status"] for e in events]
    assert "downloading" in statuses
    assert "done" in statuses
    assert events[-1]["filename"] == "video.mp4"


@pytest.mark.asyncio
async def test_job_events_stream_error():
    import asyncio
    import json
    import tempfile
    from pathlib import Path

    from models import Job
    from routes import _job_events_stream
    from state import AppState

    tmp = Path(tempfile.mkdtemp())
    state = AppState(tmp, tmp / ".history.json")

    job = Job(id="sse-err", created=time.time())
    job.status = "starting"
    state.jobs["sse-err"] = job
    state.job_events["sse-err"] = asyncio.Event()

    events = []

    async def collect():
        async for raw in _job_events_stream(state, "sse-err"):
            data = json.loads(raw[6:])
            events.append(data)

    task = asyncio.create_task(collect())

    await asyncio.sleep(0.1)
    job.status = "error"
    job.error = "Something went wrong"
    state.job_events["sse-err"].set()

    await task

    assert len(events) >= 1
    assert events[-1]["status"] == "error"
    assert events[-1]["error"] == "Something went wrong"
