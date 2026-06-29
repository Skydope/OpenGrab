"""Tests de la capa de acceso SQLite (db.py). Corren en :memory: / temp, sin tocar nada real."""

from __future__ import annotations


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


# --------------------------- reconcile startup --------------------------- #
def test_reconcile_startup_splits_queued_and_orphans(db):
    db.insert_job("dl", "u", "best", status="downloading")
    db.update_job("dl", workdir="/tmp/wd_dl")
    db.insert_job("st", "u", "best", status="starting")
    db.insert_job("q", "u", "best", status="queued")
    db.insert_job("done", "u", "best", status="done")

    recon = db.reconcile_startup()

    # queued → requeued (se retoma, no se marca interrupted)
    assert recon["requeued"] == ["q"]
    assert db.get_job("q")["status"] == "queued"
    # starting/downloading → interrupted (con workdir para limpiar)
    assert {x["id"] for x in recon["interrupted"]} == {"dl", "st"}
    dl_row = next(x for x in recon["interrupted"] if x["id"] == "dl")
    assert dl_row["workdir"] == "/tmp/wd_dl"  # §5.1
    assert db.get_job("dl")["status"] == "interrupted"
    assert db.get_job("st")["status"] == "interrupted"
    # terminal intacto
    assert db.get_job("done")["status"] == "done"


def test_reconcile_startup_empty(db):
    assert db.reconcile_startup() == {"requeued": [], "interrupted": []}


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
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(d.get_active_jobs()) == 80  # 4 threads x 20, sin corrupcion
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


# ------------------------------- delete / clear -------------------------- #
def test_delete_job_removes_row(db):
    db.insert_job("j1", "u", "best")
    assert db.delete_job("j1") is True
    assert db.get_job("j1") is None


def test_delete_job_nonexistent(db):
    assert db.delete_job("phantom") is False


def test_clear_history_deletes_done_error_interrupted(db):
    db.insert_job("a", "u", "best", status="done")
    db.insert_job("b", "u", "best", status="error")
    db.insert_job("c", "u", "best", status="interrupted")
    db.insert_job("d", "u", "best", status="downloading")
    db.insert_job("e", "u", "best", status="queued")
    count = db.clear_history()
    assert count == 3
    assert db.get_job("a") is None
    assert db.get_job("b") is None
    assert db.get_job("c") is None
    assert db.get_job("d") is not None
    assert db.get_job("e") is not None


def test_get_deletable_jobs_returns_correct_set(db):
    db.insert_job("x", "u", "best", status="done")
    db.insert_job("y", "u", "best", status="error")
    db.insert_job("z", "u", "best", status="downloading")
    jobs = db.get_deletable_jobs()
    ids = {j["id"] for j in jobs}
    assert ids == {"x", "y"}


# ------------------------------ get_queued -------------------------------- #
def test_get_queued_returns_oldest_first(db):
    """get_queued returns the oldest queued jobs first, respecting limit."""
    db.insert_job("oldest", "u", "best", created=100.0)
    db.insert_job("middle", "u", "best", created=200.0)
    db.insert_job("newest", "u", "best", created=300.0)
    result = db.get_queued(2)
    assert len(result) == 2
    assert [r["id"] for r in result] == ["oldest", "middle"]


def test_get_queued_respects_limit(db):
    """get_queued returns exactly `limit` jobs when enough queued jobs exist."""
    for i in range(5):
        db.insert_job(f"job{i}", "u", "best", created=float(i * 100))
    result = db.get_queued(3)
    assert len(result) == 3


def test_get_queued_empty(db):
    """get_queued returns empty list when no queued jobs exist."""
    db.insert_job("done1", "u", "best", status="done")
    db.insert_job("done2", "u", "best", status="error")
    result = db.get_queued(10)
    assert result == []


def test_get_queued_only_returns_queued(db):
    """get_queued ignores jobs with non-queued statuses."""
    db.insert_job("queued1", "u", "best", status="queued", created=100.0)
    db.insert_job("done1", "u", "best", status="done", created=200.0)
    db.insert_job("downloading1", "u", "best", status="downloading", created=300.0)
    db.insert_job("queued2", "u", "best", status="queued", created=400.0)
    result = db.get_queued(10)
    ids = [r["id"] for r in result]
    assert "queued1" in ids
    assert "queued2" in ids
    assert "done1" not in ids
    assert "downloading1" not in ids


# ------------------------------ get_jobs (batch fetch) ------------------------------ #
def test_get_jobs_returns_multiple_by_id(db):
    """get_jobs fetches multiple jobs by ID in a single query."""
    db.insert_job("job1", "https://x.com/1", "720p")
    db.insert_job("job2", "https://x.com/2", "best")
    db.insert_job("job3", "https://x.com/3", "480p")
    db.update_job("job2", status="done", completed=999)

    result = db.get_jobs(["job1", "job2", "job3"])
    assert len(result) == 3
    ids = {r["id"] for r in result}
    assert ids == {"job1", "job2", "job3"}


def test_get_jobs_returns_empty_for_unknown_ids(db):
    """get_jobs returns empty list when no jobs match the IDs."""
    db.insert_job("existing", "https://x.com/1", "best")
    result = db.get_jobs(["nonexistent1", "nonexistent2"])
    assert result == []


def test_get_jobs_returns_partial_matches(db):
    """get_jobs returns only the jobs that exist among requested IDs."""
    db.insert_job("exists1", "https://x.com/1", "best")
    db.insert_job("exists2", "https://x.com/2", "best")
    result = db.get_jobs(["exists1", "missing", "exists2"])
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert ids == {"exists1", "exists2"}


def test_get_jobs_empty_input(db):
    """get_jobs returns empty list when given an empty list of IDs."""
    db.insert_job("job1", "https://x.com/1", "best")
    result = db.get_jobs([])
    assert result == []


# ------------------------------ BatchReq --------------------------------- #
def test_batch_req_accepts_urls_and_quality():
    """BatchReq model accepts urls list and quality string with default 'best'."""
    from models import BatchReq

    # Test with explicit quality
    req = BatchReq(urls=["https://youtu.be/abc", "https://youtu.be/xyz"], quality="720p")
    assert req.urls == ["https://youtu.be/abc", "https://youtu.be/xyz"]
    assert req.quality == "720p"

    # Test with default quality
    req_default = BatchReq(urls=["https://youtu.be/abc"])
    assert req_default.urls == ["https://youtu.be/abc"]
    assert req_default.quality == "best"

    # Test with empty urls list
    req_empty = BatchReq(urls=[])
    assert req_empty.urls == []
    assert req_empty.quality == "best"


# ------------------------------- settings --------------------------------- #
def test_get_setting_returns_none_when_not_exists(db):
    result = db.get_setting("max_jobs")
    assert result is None


def test_set_and_get_setting(db):
    db.set_setting("max_jobs", "4")
    result = db.get_setting("max_jobs")
    assert result == "4"


def test_set_setting_updates_existing(db):
    db.set_setting("quality_default", "best")
    db.set_setting("quality_default", "720p")
    result = db.get_setting("quality_default")
    assert result == "720p"


def test_get_all_settings_empty(db):
    result = db.get_all_settings()
    assert result == {}


def test_get_all_settings_returns_all(db):
    db.set_setting("max_jobs", "2")
    db.set_setting("quality_default", "best")
    db.set_setting("theme", "dark")
    result = db.get_all_settings()
    assert result == {"max_jobs": "2", "quality_default": "best", "theme": "dark"}


def test_set_setting_updates_timestamp(db):
    db.set_setting("max_jobs", "3")
    row = db.get_setting("max_jobs")
    assert row is not None
    # El valor debe ser string
    assert isinstance(row, str)
    assert row == "3"


# ------------------------------- resolve() -------------------------------- #
def test_resolve_default_when_no_source(monkeypatch, tmp_path):
    """Sin env, sin ini, sin tabla → devuelve default."""
    from state import AppState

    monkeypatch.delenv("OPENGRAB_MAX_JOBS", raising=False)
    db = Database(":memory:")
    state = AppState(db, tmp_path)
    val, origin = state.resolve("max_jobs", 2, int)
    assert val == 2
    assert origin == "default"


def test_resolve_table_over_default(monkeypatch, tmp_path):
    """Tabla tiene valor, sin env, sin ini → devuelve tabla."""
    from state import AppState

    monkeypatch.delenv("OPENGRAB_MAX_JOBS", raising=False)
    db = Database(":memory:")
    db.set_setting("max_jobs", "7")
    state = AppState(db, tmp_path)
    val, origin = state.resolve("max_jobs", 2, int)
    assert val == 7
    assert origin == "table"


def test_resolve_table_over_ini(monkeypatch, tmp_path):
    """La tabla (edición del usuario) gana sobre el ini (semilla del instalador).

    Garantiza el hot-reload: una vez que el usuario edita una setting, su valor
    en la tabla se aplica en vivo aunque el instalador haya sembrado el ini.
    """
    import config as _config
    from state import AppState

    monkeypatch.delenv("OPENGRAB_MAX_JOBS", raising=False)
    monkeypatch.setitem(_config._ini, "max_jobs", "3")
    db = Database(":memory:")
    db.set_setting("max_jobs", "9")
    state = AppState(db, tmp_path)
    val, origin = state.resolve("max_jobs", 2, int)
    assert val == 9
    assert origin == "table"


def test_resolve_ini_over_default(monkeypatch, tmp_path):
    """Sin env ni tabla, el ini actúa como semilla por encima del default."""
    import config as _config
    from state import AppState

    monkeypatch.delenv("OPENGRAB_MAX_JOBS", raising=False)
    monkeypatch.setitem(_config._ini, "max_jobs", "4")
    db = Database(":memory:")
    state = AppState(db, tmp_path)
    val, origin = state.resolve("max_jobs", 2, int)
    assert val == 4
    assert origin == "ini"


def test_resolve_casts_to_requested_type(monkeypatch, tmp_path):
    """El casteo funciona: string en tabla → int."""
    from state import AppState

    monkeypatch.delenv("OPENGRAB_MAX_JOBS", raising=False)
    db = Database(":memory:")
    db.set_setting("max_jobs", "7")
    state = AppState(db, tmp_path)
    val, origin = state.resolve("max_jobs", 2, int)
    assert val == 7
    assert origin == "table"
    assert isinstance(val, int)
