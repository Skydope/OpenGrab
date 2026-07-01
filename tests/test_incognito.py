"""Tests del modo incógnito (descarga sin historial, entrega a carpeta elegida).

Cubre las fricciones identificadas en el diseño:
- Migración v2→v3 idempotente (columna ``incognito``) sobre DBs preexistentes.
- ``get_queued`` excluye incógnito (dispatch_loop nunca lo auto-reanuda).
- ``reconcile_startup`` borra filas incógnito y devuelve ``incognito_dropped``.
- ``_secure_delete_*`` con ``force=True`` sobrescribe aunque el flag global esté off.
- ``_run_download`` incógnito: mueve a ``incognito_dir``, borra la fila de DB,
  no registra en ``downloaded_urls`` (dedup) y wipea el workdir.
- La API exige ``incognito_dir`` cuando ``incognito=True``.
"""

import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db import Database
from models import Job
from state import AppState


# --------------------------------------------------------------------- #
# Migración v2 -> v3
# --------------------------------------------------------------------- #
def _build_v2_db(path: Path) -> None:
    """Crea una DB con el esquema v2 (sin columna ``incognito``)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, quality TEXT NOT NULL,
            status TEXT NOT NULL, title TEXT, filename TEXT, filepath TEXT,
            mime TEXT, size INTEGER, thumbnail TEXT, error TEXT,
            video_id TEXT, extractor TEXT, workdir TEXT,
            created REAL NOT NULL, completed INTEGER
        );
        CREATE TABLE downloaded_urls (
            extractor TEXT NOT NULL, video_id TEXT NOT NULL,
            channel_id INTEGER, job_id TEXT, downloaded_at INTEGER NOT NULL,
            PRIMARY KEY (extractor, video_id)
        );
        CREATE TABLE settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated INTEGER NOT NULL
        );
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE,
            title TEXT, quality TEXT NOT NULL DEFAULT 'best',
            interval_minutes INTEGER NOT NULL DEFAULT 60,
            enabled INTEGER NOT NULL DEFAULT 1, last_checked INTEGER,
            created INTEGER NOT NULL
        );
        """
    )
    conn.execute("PRAGMA user_version=2")
    conn.execute(
        "INSERT INTO jobs (id, url, quality, status, created) "
        "VALUES ('old', 'u', 'best', 'done', 1.0)"
    )
    conn.commit()
    conn.close()


def test_migration_v2_to_v3_adds_incognito_column(tmp_path):
    db_path = tmp_path / "v2.db"
    _build_v2_db(db_path)

    db = Database(db_path)
    try:
        assert db.schema_version() == 3
        cols = {
            r["name"]
            for r in db._conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        assert "incognito" in cols
        # Fila preexistente intacta y con default 0.
        old = db.get_job("old")
        assert old is not None
        assert old["incognito"] == 0
    finally:
        db.close()


def test_migration_is_idempotent_on_fresh_db(tmp_path):
    """Una DB nueva (creada por el DDL v3) no debe fallar al correr _migrate."""
    db = Database(tmp_path / "fresh.db")
    try:
        assert db.schema_version() == 3
        db.insert_job("j", "u", "best", incognito=True)
        assert db.get_job("j")["incognito"] == 1
    finally:
        db.close()


# --------------------------------------------------------------------- #
# get_queued / reconcile excluyen y descartan incógnito
# --------------------------------------------------------------------- #
def test_get_queued_excludes_incognito(tmp_path):
    db = Database(tmp_path / "q.db")
    try:
        db.insert_job("normal", "u", "best", status="queued")
        db.insert_job("incog", "u", "best", status="queued", incognito=True)
        ids = {r["id"] for r in db.get_queued(limit=10)}
        assert ids == {"normal"}
    finally:
        db.close()


def test_reconcile_drops_incognito_rows(tmp_path):
    db = Database(tmp_path / "r.db")
    try:
        db.insert_job("incog", "u", "best", status="downloading", incognito=True)
        db.update_job("incog", workdir="/tmp/wd_incog")
        db.insert_job("normal", "u", "best", status="queued")

        recon = db.reconcile_startup()

        dropped_ids = {d["id"] for d in recon["incognito_dropped"]}
        assert dropped_ids == {"incog"}
        # La fila incógnito ya no existe en la DB.
        assert db.get_job("incog") is None
        # El workdir se devuelve para que el caller lo wipee.
        wd = next(d["workdir"] for d in recon["incognito_dropped"] if d["id"] == "incog")
        assert wd == "/tmp/wd_incog"
        # El job normal sigue su curso (requeued).
        assert recon["requeued"] == ["normal"]
    finally:
        db.close()


# --------------------------------------------------------------------- #
# secure delete forzado
# --------------------------------------------------------------------- #
def test_secure_delete_force_overwrites_when_global_flag_off(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SECURE_DELETE", False)
    f = tmp_path / "secret.bin"
    f.write_bytes(b"datos sensibles" * 100)

    # force=True debe borrar incluso con el flag global apagado.
    AppState._secure_delete_file(str(f), force=True)
    assert not f.exists()


def test_secure_delete_workdir_force_removes_tree(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SECURE_DELETE", False)
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "a.part").write_bytes(b"x" * 50)
    (wd / "b.mp4").write_bytes(b"y" * 50)

    AppState._secure_delete_workdir(str(wd), force=True)
    assert not wd.exists()


# --------------------------------------------------------------------- #
# _run_download en modo incógnito
# --------------------------------------------------------------------- #
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


def _mock_ydl(info):
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.extract_info.return_value = info
    return ydl


def test_run_download_incognito_delivers_and_leaves_no_trace(dl_state, tmp_path):
    from download import _run_download

    loop = asyncio.new_event_loop()
    jid = "ig1"
    dl_state.jobs[jid] = Job(id=jid, created=time.time(), incognito=True)
    dl_state.job_events[jid] = asyncio.Event()
    dl_state.db.insert_job(jid, "https://x/1", "best", incognito=True)

    # Archivo "descargado" por yt-dlp (simulado). Lo ponemos FUERA de out_dir
    # para no colisionar con el workdir interno que crea _run_download (mkdtemp
    # dentro de out_dir), cuyo nombre es impredecible.
    src_dir = tmp_path / "scratch"
    src_dir.mkdir()
    video = src_dir / "Clip.mp4"
    video.write_bytes(b"contenido")
    info = {
        "title": "Clip",
        "requested_downloads": [{"filepath": str(video)}],
        "extractor_key": "Youtube",
        "id": "vid123",
    }

    incognito_dir = tmp_path / "destino_privado"

    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)):
        _run_download(
            dl_state, jid, "https://youtu.be/abc", "best", loop,
            incognito=True, incognito_dir=str(incognito_dir),
        )
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "done"
    # El archivo se entregó a la carpeta elegida.
    delivered = incognito_dir / "Clip.mp4"
    assert delivered.exists()
    assert job.filepath == str(delivered)
    # El original ya no está y el workdir interno fue wipeado (no quedan
    # carpetas opengrab_* en out_dir) y job.workdir quedó vacío.
    assert not video.exists()
    assert list(dl_state.out_dir.glob("opengrab_*")) == []
    assert job.workdir == ""
    # Sin rastro en la DB: ni la fila de jobs ni el dedup.
    assert dl_state.db.get_job(jid) is None
    assert dl_state.db.is_downloaded("Youtube", "vid123") is False


def test_run_download_incognito_move_failure_preserves_file(dl_state, tmp_path):
    """Si _move_incognito falla tras completar la descarga, NO se pierde el
    archivo: el workdir no se wipea, el estado queda 'error' con la ruta, y la
    fila se borra igual (para que reconcile no wipee el residuo)."""
    from download import _run_download

    loop = asyncio.new_event_loop()
    jid = "ig3"
    dl_state.jobs[jid] = Job(id=jid, created=time.time(), incognito=True)
    dl_state.job_events[jid] = asyncio.Event()
    dl_state.db.insert_job(jid, "https://x/3", "best", incognito=True)

    src_dir = tmp_path / "scratch"
    src_dir.mkdir()
    video = src_dir / "Clip.mp4"
    video.write_bytes(b"contenido")
    info = {"title": "Clip", "requested_downloads": [{"filepath": str(video)}]}

    incognito_dir = tmp_path / "destino"

    # _move_incognito falla con OSError (simula disco lleno / permisos).
    with patch("download.yt_dlp.YoutubeDL", return_value=_mock_ydl(info)), \
         patch.object(type(dl_state), "_move_incognito",
                      side_effect=OSError("no space left")):
        _run_download(
            dl_state, jid, "https://youtu.be/abc", "best", loop,
            incognito=True, incognito_dir=str(incognito_dir),
        )
    loop.close()

    job = dl_state.jobs[jid]
    assert job.status == "error"
    # El archivo NO se perdió: sigue en su lugar.
    assert video.exists()
    assert job.filepath == str(video)
    # El error expone la ruta para recuperación manual.
    assert str(video) in job.error
    # La fila se borró (reconcile no debe wipear el residuo al reiniciar).
    assert dl_state.db.get_job(jid) is None


def test_incognito_and_savefile_share_move_core(dl_state):
    """_move_incognito y move_job_file delegan en el mismo core _move_file_locked."""
    import inspect

    src = inspect.getsource(dl_state._move_incognito.__func__)
    assert "_move_file_locked" in src
    src2 = inspect.getsource(dl_state.move_job_file.__func__)
    assert "_move_file_locked" in src2


def test_run_download_incognito_forces_off_sidecars(dl_state, tmp_path):
    """subs/thumb/infojson se ignoran en incógnito (un único archivo limpio)."""
    from download import _run_download

    loop = asyncio.new_event_loop()
    jid = "ig2"
    dl_state.jobs[jid] = Job(id=jid, created=time.time(), incognito=True)
    dl_state.job_events[jid] = asyncio.Event()
    dl_state.db.insert_job(jid, "https://x/2", "best", incognito=True)

    wd = Path(tempfile.mkdtemp(prefix="opengrab_", dir=dl_state.out_dir))
    video = wd / "V.mp4"
    video.write_bytes(b"c")
    info = {"title": "V", "requested_downloads": [{"filepath": str(video)}]}

    captured = {}

    def _capture(opts):
        captured.update(opts)
        return _mock_ydl(info)

    incognito_dir = tmp_path / "out"
    with patch("download.yt_dlp.YoutubeDL", side_effect=_capture):
        _run_download(
            dl_state, jid, "https://youtu.be/abc", "best", loop,
            subs=True, thumb=True, infojson=True,
            incognito=True, incognito_dir=str(incognito_dir),
        )
    loop.close()

    # Pese a pedir subs/thumb/infojson, en incógnito NO se setean.
    assert "writesubtitles" not in captured
    assert "writethumbnail" not in captured
    assert "writeinfojson" not in captured
    # Hardening presente.
    assert captured.get("cachedir") is False
    assert "User-Agent" in captured.get("http_headers", {})


# --------------------------------------------------------------------- #
# API: incognito_dir obligatorio
# --------------------------------------------------------------------- #
def test_api_incognito_requires_dir(client):
    r = client.post(
        "/api/jobs",
        json={"url": "https://youtu.be/abc", "quality": "best", "incognito": True},
    )
    assert r.status_code == 400


def test_api_incognito_blank_dir_rejected(client):
    r = client.post(
        "/api/jobs",
        json={
            "url": "https://youtu.be/abc", "quality": "best",
            "incognito": True, "incognito_dir": "   ",
        },
    )
    assert r.status_code == 400
