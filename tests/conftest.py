import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_OPENGRAB_MODULES = ("app", "config", "models", "download", "routes", "state")


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


def _make_client(**extra_env):
    for mod in list(sys.modules):
        if mod in _OPENGRAB_MODULES or mod.startswith(
            ("app.", "config.", "models.", "download.", "routes.", "state.")
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
