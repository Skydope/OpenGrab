import asyncio
from unittest.mock import patch
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


def _make_husk(out_dir, name, with_residue):
    """Crea un workdir opengrab_* simulando estado post-finalize:
    el keeper ya se movió afuera; queda (o no) residuo adentro."""
    wd = out_dir / name
    wd.mkdir()
    if with_residue:
        (wd / "video.f137.mp4.part").write_bytes(b"x" * 1024)   # fragmento HLS
        (wd / "audio.f140.m4a").write_bytes(b"y" * 512)         # stream sin mergear
    return wd


def test_flush_borra_husk_con_residuo(st):
    """El bug: os.rmdir dejaba leakear husks no vacíos. flush_pending_cleanups
    los borra."""
    wd = _make_husk(st.out_dir, "opengrab_abc", with_residue=True)
    st._schedule_tempdir_cleanup(str(wd))

    removed = st.flush_pending_cleanups()

    assert removed == 1
    assert not wd.exists()
    assert str(wd) not in st._pending_cleanups


def test_flush_borra_husk_vacio(st):
    """Caso feliz (merge limpio): workdir vacío también se va."""
    wd = _make_husk(st.out_dir, "opengrab_empty", with_residue=False)
    st._schedule_tempdir_cleanup(str(wd))
    assert st.flush_pending_cleanups() == 1
    assert not wd.exists()


def test_flush_self_heal_entrada_stale(st):
    """Si el dir ya no existe (borrado externo), la entrada se descarta igual."""
    st._schedule_tempdir_cleanup(str(st.out_dir / "opengrab_ghost"))
    st.flush_pending_cleanups()
    assert st._pending_cleanups == set()


def test_flush_invalida_cache_usage(st):
    wd = _make_husk(st.out_dir, "opengrab_cache", with_residue=True)
    st._schedule_tempdir_cleanup(str(wd))
    with st._usage_lock:
        st._usage_cache = 999
        st._usage_cache_ts = 1e18  # cache "fresca"
    st.flush_pending_cleanups()
    assert st._usage_cache_ts == 0.0  # invalidada
