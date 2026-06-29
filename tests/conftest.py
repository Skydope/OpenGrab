import asyncio
import inspect
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Fix slowapi DeprecationWarning en Python 3.14+:
# asyncio.iscoroutinefunction esta deprecado; el reemplazo es inspect.iscoroutinefunction.
# Este parche a nivel modulo asegura que cualquier import de routes.py
# (directo o via app.py) tenga el monkeypatch aplicado antes de que
# los decoradores @limiter.limit llamen a asyncio.iscoroutinefunction().
asyncio.iscoroutinefunction = inspect.iscoroutinefunction  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).parent.parent))

_OPENGRAB_MODULES = ("app", "config", "models", "download", "routes", "state", "db")


@pytest.fixture(autouse=True)
def _reset_settings_table():
    """Limpia la tabla ``settings`` antes de cada test.

    La DB de tests vive en disco (``_test_downloads/opengrab.db``) y se
    comparte entre tests de la sesión. Sin esto, un ``set_setting()`` en un
    test se filtra a los siguientes (p.ej. ``library_dir`` filtrado al
    ``_finalize_desktop`` del test siguiente, que resolvía a un tempdir ya
    borrado en vez de caer al fallback).

    Setup-clearing (antes del yield): protege tanto leaks cross-test como
    residuo de una corrida anterior que crasheó a mitad (el cleanup de
    sesión solo borra _test_downloads en salida limpia).
    """
    import sqlite3

    db_path = Path(__file__).parent / "_test_downloads" / "opengrab.db"
    if db_path.exists():
        try:
            con = sqlite3.connect(str(db_path), timeout=5)
            con.execute("DELETE FROM settings")
            con.commit()
            con.close()
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e):
                raise
    yield


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setenv("OPENGRAB_HOST", "127.0.0.1")
    monkeypatch.setenv("OPENGRAB_PORT", "8880")
    monkeypatch.setenv(
        "OPENGRAB_DIR", os.path.join(os.path.dirname(__file__), "_test_downloads")
    )
    monkeypatch.setenv("OPENGRAB_TOKEN", "test-token")
    monkeypatch.setenv("OPENGRAB_MAX_JOBS", "1")
    monkeypatch.setenv("OPENGRAB_AUTOUPDATE", "0")
    monkeypatch.setenv(
        "OPENGRAB_CONFIG",
        str(Path(tempfile.gettempdir()) / "opengrab_test_nonexistent.ini"),
    )


@pytest.fixture(autouse=True)
def _neutralize_dns(monkeypatch):
    """Solo intercepta la resolucion del gate SSRF, no el networking general.

    El gate llama ``getaddrinfo(host, None, family=AF_UNSPEC)`` (port=None).
    Fakeamos *solo* esa firma a una IP publica fija para que las rutas que
    postean URLs reales pasen sin lookups de red; cualquier otra llamada
    (con puerto, del propio test infra) delega al resolver real para no
    colgar el teardown intentando conectar a la IP fake. Los tests que
    prueban el bloqueo por DNS sobreescriben este atributo con su propio fake.
    """
    import socket

    real_getaddrinfo = socket.getaddrinfo

    def _fake(host, port, *args, **kwargs):
        if port is None:  # firma del gate SSRF
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    yield


def _make_client(**extra_env):
    for mod in list(sys.modules):
        if mod in _OPENGRAB_MODULES or mod.startswith(
            ("app.", "config.", "models.", "download.", "routes.", "state.", "db.")
        ):
            del sys.modules[mod]
    for key, val in extra_env.items():
        os.environ[key] = val
    from app import app
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def client():
    with _make_client() as c:
        c.cookies.set("opengrab_token", "test-token")
        yield c


@pytest.fixture
def client_no_auth(monkeypatch):
    monkeypatch.delenv("OPENGRAB_TOKEN", raising=False)
    monkeypatch.setenv("OPENGRAB_NO_AUTH", "1")
    with _make_client() as c:
        yield c


@pytest.fixture
def app_state(client):
    return client.app.state.opengrab


@pytest.fixture
def client_with_token(monkeypatch):
    monkeypatch.setenv("OPENGRAB_TOKEN", "test-token")
    with _make_client() as c:
        yield c


@pytest.fixture
def authed_client(monkeypatch):
    monkeypatch.setenv("OPENGRAB_TOKEN", "test-token")
    with _make_client() as c:
        c.cookies.set("opengrab_token", "test-token")
        yield c


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_downloads():
    yield
    import shutil

    test_dir = Path(__file__).parent / "_test_downloads"
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)
