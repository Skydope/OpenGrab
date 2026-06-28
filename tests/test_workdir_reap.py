"""Tests para schedule_workdir_if_external: unifica el cleanup del workdir
husk en modo desktop, con guard ante finalize fallido (el keeper sigue
dentro del workdir -> no se borra)."""

from pathlib import Path

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


def test_keeper_externo_programa_y_limpia_workdir(st, tmp_path):
    """Desktop finalize OK: el final está en library_dir, fuera del workdir."""
    wd = tmp_path / "opengrab_x"
    wd.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    keeper = library / "video.mp4"
    keeper.write_bytes(b"final")

    job = Job(id="j1", created=0.0)
    job.workdir = str(wd)
    job.filepath = str(keeper)

    assert st.schedule_workdir_if_external(job) is True
    assert job.workdir == ""
    assert str(wd) in st._pending_cleanups


def test_keeper_dentro_del_workdir_es_noop(st, tmp_path):
    """Finalize FALLIDO: el final sigue dentro del workdir y se sirve desde
    ahí -> NO se programa para borrado (evitamos pérdida de datos)."""
    wd = tmp_path / "opengrab_y"
    wd.mkdir()
    keeper = wd / "video.mp4"
    keeper.write_bytes(b"final")

    job = Job(id="j2", created=0.0)
    job.workdir = str(wd)
    job.filepath = str(keeper)

    assert st.schedule_workdir_if_external(job) is False
    assert job.workdir == str(wd)            # preservado
    assert st._pending_cleanups == set()


def test_sin_workdir_es_noop(st, tmp_path):
    """Server mode: el workdir ya se limpió arriba (job.workdir == '')."""
    job = Job(id="j3", created=0.0)
    job.workdir = ""
    job.filepath = str(tmp_path / "out" / "video.mp4")
    assert st.schedule_workdir_if_external(job) is False
    assert st._pending_cleanups == set()


def test_sin_filepath_es_noop(st, tmp_path):
    """Sin filepath no podemos decidir dónde quedó el keeper -> no tocar."""
    wd = tmp_path / "opengrab_z"
    wd.mkdir()
    job = Job(id="j4", created=0.0)
    job.workdir = str(wd)
    job.filepath = ""
    assert st.schedule_workdir_if_external(job) is False
    assert job.workdir == str(wd)
    assert st._pending_cleanups == set()


def test_keeper_en_subcarpeta_del_workdir_es_noop(st, tmp_path):
    """Defensa: filepath anidado dentro del workdir también cuenta como dentro."""
    wd = tmp_path / "opengrab_nested"
    (wd / "sub").mkdir(parents=True)
    keeper = wd / "sub" / "video.mp4"
    keeper.write_bytes(b"final")
    job = Job(id="j5", created=0.0)
    job.workdir = str(wd)
    job.filepath = str(keeper)
    assert st.schedule_workdir_if_external(job) is False
    assert job.workdir == str(wd)
