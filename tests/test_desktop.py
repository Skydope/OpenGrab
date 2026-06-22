"""Tests de la lógica de escritorio (desktop.py) y del hot-swap (engine_update.py).

No levantan uvicorn ni abren navegadores: cubren las funciones puras y las costuras.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

import desktop
import engine_update


# ------------------------------ _free_port ------------------------------- #
def test_free_port_returns_usable_port():
    p = desktop._free_port()
    assert isinstance(p, int)
    assert p > 1024


def test_free_port_varies():
    ports = {desktop._free_port() for _ in range(5)}
    # No garantizamos unicidad estricta, pero 5 binds efímeros no deberían colapsar a 1.
    assert len(ports) > 1


# ------------------------------ _setup_env ------------------------------- #
def test_setup_env_sets_desktop_defaults(monkeypatch):
    for k in ("OPENGRAB_HOST", "OPENGRAB_PORT", "OPENGRAB_NO_AUTH", "OPENGRAB_DIR"):
        monkeypatch.delenv(k, raising=False)
    desktop._setup_env(12345)
    import os

    assert os.environ["OPENGRAB_NO_AUTH"] == "1"
    assert os.environ["OPENGRAB_HOST"] == "127.0.0.1"
    assert os.environ["OPENGRAB_PORT"] == "12345"
    assert "Downloads" in os.environ["OPENGRAB_DIR"]


def test_setup_env_respects_overrides(monkeypatch):
    monkeypatch.setenv("OPENGRAB_DIR", "/custom/path")
    desktop._setup_env(9999)
    import os

    assert os.environ["OPENGRAB_DIR"] == "/custom/path"  # setdefault no pisa


# -------------------------- single instance ------------------------------ #
def test_single_instance_second_acquire_fails():
    if sys.platform == "win32":
        pytest.skip("flock path es no-Windows; el mutex se testea en Windows")
    import fcntl
    import tempfile

    name = "OpenGrabTest_" + str(time.time()).replace(".", "")
    # Primera instancia adquiere y retiene el lock (vía desktop._lock_handle).
    assert desktop.acquire_single_instance(name) is True
    # Un segundo fd sobre el MISMO lockfile no puede tomar el lock → OSError.
    lock_path = Path(tempfile.gettempdir()) / f"{name}.lock"
    fh2 = open(lock_path, "w")
    with pytest.raises(OSError):
        fcntl.flock(fh2, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fh2.close()


# ------------------------------ health gate ------------------------------ #
def test_wait_healthy_times_out_fast(monkeypatch):
    # Puerto cerrado → siempre falla → debe respetar el timeout y devolver False.
    start = time.time()
    ok = desktop._wait_healthy(_free := desktop._free_port(), timeout=0.6)
    assert ok is False
    assert time.time() - start < 3  # no se cuelga


def test_wait_healthy_succeeds_when_server_ok(monkeypatch):
    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert desktop._wait_healthy(12345, timeout=1.0) is True


# ----------------------- engine_update (hot-swap) ------------------------ #
def test_should_check_true_when_never_checked(tmp_path):
    assert engine_update.should_check(tmp_path) is True


def test_should_check_throttle(tmp_path):
    engine_update._write_stamp(tmp_path, now=1000.0)
    assert engine_update.should_check(tmp_path, now=1000.0 + 60) is False  # <24h
    assert engine_update.should_check(tmp_path, now=1000.0 + 25 * 3600) is True  # >24h


def test_prepend_to_path_only_if_yt_dlp_present(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_update.sys, "path", list(sys.path))
    # Sin yt_dlp → no toca el path.
    assert engine_update.prepend_to_path(tmp_path) is False
    # Con yt_dlp → lo antepone.
    (tmp_path / "yt_dlp").mkdir()
    assert engine_update.prepend_to_path(tmp_path) is True
    assert engine_update.sys.path[0] == str(tmp_path)


def test_prepend_to_path_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_update.sys, "path", list(sys.path))
    (tmp_path / "yt_dlp").mkdir()
    engine_update.prepend_to_path(tmp_path)
    engine_update.prepend_to_path(tmp_path)
    assert engine_update.sys.path.count(str(tmp_path)) == 1  # no duplica


def test_engine_dir_honors_override(monkeypatch):
    monkeypatch.setenv("OPENGRAB_ENGINE_DIR", "/tmp/some/engine")
    assert engine_update._engine_dir() == Path("/tmp/some/engine")
