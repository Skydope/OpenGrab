import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_OPENGRAB_MODULES = ("app", "config", "models", "download", "routes")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Ensure clean environment for each test."""
    monkeypatch.setenv("OPENGRAB_HOST", "127.0.0.1")
    monkeypatch.setenv("OPENGRAB_PORT", "8880")
    monkeypatch.setenv(
        "OPENGRAB_DIR", os.path.join(os.path.dirname(__file__), "_test_downloads")
    )
    monkeypatch.setenv("OPENGRAB_TOKEN", "")
    monkeypatch.setenv("OPENGRAB_MAX_JOBS", "1")
    monkeypatch.setenv("OPENGRAB_AUTOUPDATE", "0")


def _make_client():
    """Create a fresh TestClient, clearing app module cache."""
    import importlib

    for mod in list(sys.modules):
        if mod in _OPENGRAB_MODULES or mod.startswith(
            ("app.", "config.", "models.", "download.", "routes.")
        ):
            del sys.modules[mod]
    from app import app
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def client():
    with _make_client() as c:
        yield c


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
