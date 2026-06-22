"""Tests de la capa de acceso SQLite (db.py). Corren en :memory: / temp, sin tocar nada real."""

from __future__ import annotations

import time

import pytest

from db import ACTIVE_STATUSES, SCHEMA_VERSION, Database


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


# ------------------------------- schema ---------------------------------- #
def test_schema_version_set(db):
    assert db.schema_version() == SCHEMA_VERSION


def test_tables_exist(db):
    # insert_job no debería fallar → la tabla existe
    db.insert_job("j1", "https://x/1", "best")
    assert db.get_job("j1") is not None


def test_init_is_idempotent(tmp_path):
    p = tmp_path / "o.db"
    Database(p).close()
    d2 = Database(p)  # reabrir no rompe ni re-migra
    assert d2.schema_version() == SCHEMA_VERSION
    d2.close()


# ------------------------------- jobs ------------------------------------ #
def test_insert_and_get_roundtrip(db):
    db.insert_job("j1", "https://x/1", "720p", created=1000.0)
    j = db.get_job("j1")
    assert j["url"] == "https://x/1"
    assert j["quality"] == "720p"
    assert j["status"] == "queued"
    assert j["created"] == 1000.0


def test_update_job_transition(db):
    db.insert_job("j1", "https://x/1", "best")
    db.update_job("j1", status="downloading", video_id="abc", extractor="youtube")
    j = db.get_job("j1")
    assert j["status"] == "downloading"
    assert j["video_id"] == "abc"
    assert j["extractor"] == "youtube"


def test_update_job_rejects_unknown_columns(db):
    db.insert_job("j1", "https://x/1", "best")
    with pytest.raises(ValueError):
        db.update_job("j1", percent=50.0)  # progreso live NO va a la DB (D2)


def test_get_active_jobs_filters(db):
    db.insert_job("a", "u", "best", status="downloading")
    db.insert_job("b", "u", "best", status="done")
    db.insert_job("c", "u", "best", status="queued")
    ids = {j["id"] for j in db.get_active_jobs()}
    assert ids == {"a", "c"}
    assert all(s in ACTIVE_STATUSES for s in ("downloading", "queued"))


def test_get_history_only_done_ordered(db):
    db.insert_job("a", "u", "best", status="done")
    db.update_job("a", completed=100)
    db.insert_job("b", "u", "best", status="done")
    db.update_job("b", completed=200)
    db.insert_job("c", "u", "best", status="error")
    hist = db.get_history()
    assert [j["id"] for j in hist] == ["b", "a"]  # más reciente primero
    assert "c" not in {j["id"] for j in hist}


def test_get_history_limit(db):
    for i in range(5):
        db.insert_job(f"j{i}", "u", "best", status="done")
        db.update_job(f"j{i}", completed=i)
    assert len(db.get_history(limit=2)) == 2
    assert len(db.get_history(limit=0)) == 5  # 0 = sin límite


# --------------------------- interrupted --------------------------------- #
def test_mark_interrupted_flips_active(db):
    db.insert_job("a", "u", "best", status="downloading")
    db.update_job("a", workdir="/tmp/wd_a")
    db.insert_job("b", "u", "best", status="done")
    affected = db.mark_interrupted()
    assert {x["id"] for x in affected} == {"a"}
    assert affected[0]["workdir"] == "/tmp/wd_a"  # para limpiar el tempdir (§5.1)
    assert db.get_job("a")["status"] == "interrupted"
    assert db.get_job("b")["status"] == "done"  # intacto


# ------------------------------ dedup ------------------------------------ #
def test_record_and_is_downloaded(db):
    assert db.is_downloaded("youtube", "vid1") is False
    db.insert_job("j1", "u", "best")
    db.record_download("youtube", "vid1", "j1")
    assert db.is_downloaded("youtube", "vid1") is True
    assert db.is_downloaded("youtube", "otro") is False


def test_record_download_idempotent(db):
    db.insert_job("j1", "u", "best")
    db.record_download("youtube", "vid1", "j1")
    db.record_download("youtube", "vid1", "j1")  # OR IGNORE, no rompe
    assert db.is_downloaded("youtube", "vid1") is True


def test_has_active_job_for_video(db):
    assert db.has_active_job_for_video("youtube", "vid1") is False

    db.insert_job("j1", "u", "best", status="downloading")
    db.update_job("j1", extractor="youtube", video_id="vid1")
    assert db.has_active_job_for_video("youtube", "vid1") is True

    db.insert_job("j2", "u", "best", status="done")
    db.update_job("j2", extractor="youtube", video_id="vid2")
    assert db.has_active_job_for_video("youtube", "vid2") is False

    assert db.has_active_job_for_video("vimeo", "vid1") is False


# ------------------------- migración json -------------------------------- #
def test_import_history_legacy_without_thumbnail(db):
    # entrada pre-v1.6.0: sin thumbnail
    entries = [
        {"url": "u1", "title": "Viejo", "quality": "best", "filename": "v.mp4",
         "size": 1234, "job_id": "old1", "completed": 1000},
    ]
    n = db.import_history_json(entries)
    assert n == 1
    j = db.get_job("old1")
    assert j["status"] == "done"
    assert j["thumbnail"] is None  # default None, no rompe (§5.2)
    assert j["title"] == "Viejo"


def test_import_history_idempotent_when_not_empty(db):
    db.insert_job("existing", "u", "best")
    n = db.import_history_json([{"url": "u", "job_id": "x", "completed": 1}])
    assert n == 0  # no importa si ya hay jobs


# ------------------------------ retención -------------------------------- #
def test_prune_history_keeps_recent(db):
    for i in range(5):
        db.insert_job(f"j{i}", "u", "best", status="done")
        db.update_job(f"j{i}", completed=i)
    deleted = db.prune_history(keep=2)
    assert deleted == 3
    remaining = {j["id"] for j in db.get_history(limit=0)}
    assert remaining == {"j3", "j4"}  # los 2 más recientes


def test_prune_history_noop_when_unlimited(db):
    db.insert_job("a", "u", "best", status="done")
    assert db.prune_history(keep=0) == 0  # 0 = sin límite, no borra


# ------------------------- concurrencia (smoke) -------------------------- #
def test_concurrent_inserts_serialized(tmp_path):
    import threading

    d = Database(tmp_path / "c.db")
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                d.insert_job(f"t{n}_{i}", "u", "best")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(d.get_active_jobs()) == 80  # 4 threads × 20, sin corrupción
    d.close()


# --------------------------- channels CRUD ------------------------------ #
def test_insert_and_get_channel_roundtrip(db):
    cid = db.insert_channel("https://youtube.com/@test", "720p", interval_minutes=120)
    assert cid > 0
    ch = db.get_channel(cid)
    assert ch["url"] == "https://youtube.com/@test"
    assert ch["quality"] == "720p"
    assert ch["interval_minutes"] == 120
    assert ch["enabled"] == 1


def test_list_channels_filters_enabled(db):
    c1 = db.insert_channel("https://a.com", "best")
    c2 = db.insert_channel("https://b.com", "best")
    db.update_channel(c2, enabled=0)
    all_ch = db.list_channels(enabled_only=False)
    assert len(all_ch) == 2
    enabled = db.list_channels(enabled_only=True)
    assert [c["id"] for c in enabled] == [c1]


def test_update_channel_partial(db):
    cid = db.insert_channel("https://x.com", "best")
    db.update_channel(cid, title="My Channel", quality="audio")
    ch = db.get_channel(cid)
    assert ch["title"] == "My Channel"
    assert ch["quality"] == "audio"
    assert ch["interval_minutes"] == 60  # default intacto


def test_update_channel_rejects_bad_column(db):
    cid = db.insert_channel("https://x.com", "best")
    with pytest.raises(ValueError):
        db.update_channel(cid, fake_col=1)


def test_delete_channel_removes_row(db):
    cid = db.insert_channel("https://x.com", "best")
    db.delete_channel(cid)
    assert db.get_channel(cid) is None


def test_touch_channel_updates_last_checked(db):
    cid = db.insert_channel("https://x.com", "best")
    before = db.get_channel(cid)
    assert before["last_checked"] is None
    db.touch_channel(cid)
    after = db.get_channel(cid)
    assert after["last_checked"] is not None
    assert after["last_checked"] > 0


def test_list_channels_ordered_by_created(db):
    db.insert_channel("https://a.com", "best", created=100)
    db.insert_channel("https://b.com", "best", created=200)
    channels = db.list_channels()
    assert [c["url"] for c in channels] == ["https://a.com", "https://b.com"]
