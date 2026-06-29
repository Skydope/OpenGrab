"""Tests para GET /api/jobs: rehidratación de la UI con jobs en vuelo +
terminados recientes."""

import json
import time

import pytest

from db import Database
from models import Job
from state import AppState


@pytest.fixture
def st(tmp_path):
    db = Database(":memory:")
    s = AppState(db, tmp_path)
    yield s
    db.close()


def _job(jid, status, created, finished=0.0):
    j = Job(id=jid, created=created)
    j.status = status
    j.finished = finished
    return j


async def _call(st, recent=900.0):
    from routers.jobs import api_list_jobs

    resp = await api_list_jobs(recent=recent, _=None, state=st)
    return json.loads(bytes(resp.body))


@pytest.mark.asyncio
async def test_incluye_activos_y_terminados_recientes(st):
    now = time.time()
    st.jobs["dl"] = _job("dl", "downloading", created=now - 10)
    st.jobs["q"] = _job("q", "queued", created=now - 5)
    st.jobs["done_new"] = _job("done_new", "done", created=now - 30, finished=now - 20)
    st.jobs["done_old"] = _job("done_old", "done", created=now - 5000, finished=now - 4000)
    st.jobs["err_new"] = _job("err_new", "error", created=now - 40, finished=now - 15)

    out = await _call(st, recent=900.0)
    ids = [j["id"] for j in out]

    assert "dl" in ids and "q" in ids          # activos siempre
    assert "done_new" in ids and "err_new" in ids  # terminados recientes
    assert "done_old" not in ids               # viejo -> solo Historial


@pytest.mark.asyncio
async def test_orden_activos_primero_por_created_luego_finished_desc(st):
    now = time.time()
    st.jobs["a2"] = _job("a2", "downloading", created=now - 5)
    st.jobs["a1"] = _job("a1", "downloading", created=now - 50)
    st.jobs["f_old"] = _job("f_old", "done", created=now - 200, finished=now - 100)
    st.jobs["f_new"] = _job("f_new", "done", created=now - 200, finished=now - 30)

    ids = [j["id"] for j in await _call(st)]
    assert ids == ["a1", "a2", "f_new", "f_old"]


@pytest.mark.asyncio
async def test_recent_cero_solo_activos(st):
    now = time.time()
    st.jobs["dl"] = _job("dl", "downloading", created=now)
    st.jobs["done"] = _job("done", "done", created=now - 10, finished=now - 1)
    ids = [j["id"] for j in await _call(st, recent=0.0)]
    assert ids == ["dl"]


@pytest.mark.asyncio
async def test_contrato_de_campos(st):
    st.jobs["dl"] = _job("dl", "downloading", created=time.time())
    st.jobs["dl"].percent = 42.0
    st.jobs["dl"].speed = "1.2MiB/s"
    j = (await _call(st))[0]
    for k in ("id", "status", "percent", "speed", "eta", "note", "title",
              "filename", "filepath", "mime", "error", "created", "finished",
              "downloaded", "total"):
        assert k in j, f"falta campo {k}"
    assert j["percent"] == 42.0 and j["speed"] == "1.2MiB/s"
