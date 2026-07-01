"""Tests de cancelación de descargas: cancel_job (running/queued/noop) y el
abort de _run_download vía DownloadCancelled (hook y camino temprano)."""

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from db import Database
from models import Job
from state import AppState


@pytest.fixture
def st():
    tmp = Path(tempfile.mkdtemp())
    db = Database(":memory:")
    s = AppState(db, tmp)
    s.out_dir.mkdir(parents=True, exist_ok=True)
    yield s
    db.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ── cancel_job (state-level) ──────────────────────────────────────────────
def test_cancel_running_marca_bandera(st):
    st.jobs["r"] = Job(id="r", created=time.time())
    st.jobs["r"].status = "downloading"
    assert st.cancel_job("r") == "cancelling"
    assert "r" in st.cancel_requests


def test_cancel_queued_en_db_sin_thread(st):
    st.db.insert_job("q", "https://x/1", "best")   # status=queued, no está en st.jobs
    assert st.cancel_job("q") == "cancelled"
    assert st.db.get_job("q")["status"] == "cancelled"
    assert "q" not in st.cancel_requests           # no hay thread que abortar


def test_cancel_noop_si_no_existe_o_terminal(st):
    assert st.cancel_job("nope") == "noop"
    st.jobs["d"] = Job(id="d", created=time.time())
    st.jobs["d"].status = "done"
    st.db.insert_job("d", "https://x/1", "best")
    st.db.update_job("d", status="done")
    assert st.cancel_job("d") == "noop"


# ── _run_download abort ───────────────────────────────────────────────────
def _make_job(st, jid):
    st.jobs[jid] = Job(id=jid, created=time.time())
    st.job_events[jid] = asyncio.Event()
    st.db.insert_job(jid, "https://x/1", "best")
    return jid


def test_run_download_cancel_via_hook(st):
    """El usuario cancela mientras descarga: el hook ve la bandera y aborta."""
    from download import DownloadContext, _run_download
    jid = _make_job(st, "h")
    loop = asyncio.new_event_loop()

    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True):
            st.cancel_requests.add(jid)  # cancelación justo durante la descarga
            # disparar el progress hook -> debe raisear DownloadCancelled
            self.opts["progress_hooks"][0](
                {"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100}
            )
            raise AssertionError("el hook debió abortar antes de llegar acá")

    with patch("download.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYDL(opts)):
        _run_download(st, DownloadContext(job_id=jid, url="https://x/1", quality="best"), loop)
    loop.close()

    job = st.jobs[jid]
    assert job.status == "cancelled"
    assert job.finished > 0
    assert job.error == ""
    assert job.workdir == ""                       # husk desreferenciado
    assert jid not in st.cancel_requests           # bandera limpiada en finally
    assert st.db.get_job(jid)["status"] == "cancelled"
    assert any("opengrab_" in p for p in st._pending_cleanups)  # husk a limpiar


def test_run_download_cancel_temprano_no_arranca_ydl(st):
    """Cancelado antes de iniciar: aborta sin construir YoutubeDL."""
    from download import DownloadContext, _run_download
    jid = _make_job(st, "e")
    st.cancel_requests.add(jid)
    loop = asyncio.new_event_loop()

    with patch("download.yt_dlp.YoutubeDL") as ydl_cls:
        _run_download(st, DownloadContext(job_id=jid, url="https://x/1", quality="best"), loop)
    loop.close()

    assert ydl_cls.call_count == 0                 # nunca se construyó yt-dlp
    assert st.jobs[jid].status == "cancelled"
    assert st.db.get_job(jid)["status"] == "cancelled"
    assert jid not in st.cancel_requests


# ── endpoint + visibilidad en la lista ────────────────────────────────────
@pytest.mark.asyncio
async def test_endpoint_cancel_404_si_noop(st):
    from fastapi import HTTPException
    from routers.jobs import api_cancel_job
    with pytest.raises(HTTPException) as ei:
        await api_cancel_job("ghost", _=None, state=st)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_cancel_running_ok(st):
    from routers.jobs import api_cancel_job
    st.jobs["r"] = Job(id="r", created=time.time())
    st.jobs["r"].status = "downloading"
    out = await api_cancel_job("r", _=None, state=st)
    assert out == {"status": "cancelling"}
    assert "r" in st.cancel_requests


@pytest.mark.asyncio
async def test_lista_incluye_queued_de_db(st):
    import json
    from routers.jobs import api_list_jobs
    st.db.insert_job("qd", "https://x/1", "best")  # queued, sin spawnear
    resp = await api_list_jobs(recent=900.0, _=None, state=st)
    ids = [j["id"] for j in json.loads(bytes(resp.body))]
    assert "qd" in ids  # visible -> cancelable desde la cola
