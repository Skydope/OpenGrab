"""Tests de la lógica de escritorio (desktop.py) y del hot-swap (engine_update.py).

No levantan uvicorn ni abren navegadores: cubren las funciones puras y las costuras.
"""

from __future__ import annotations

import sys
import time
import types
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

    assert os.environ["OPENGRAB_DIR"] == "/custom/path"


# -------------------------- single instance ------------------------------ #
def test_single_instance_second_acquire_fails():
    if sys.platform == "win32":
        pytest.skip("flock path es no-Windows; el mutex se testea en Windows")
    import fcntl
    import tempfile

    name = "OpenGrabTest_" + str(time.time()).replace(".", "")
    assert desktop.acquire_single_instance(name) is True
    lock_path = Path(tempfile.gettempdir()) / f"{name}.lock"
    fh2 = open(lock_path, "w")
    with pytest.raises(OSError):
        fcntl.flock(fh2, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fh2.close()


# ------------------------------ health gate ------------------------------ #
def test_wait_healthy_times_out_fast(monkeypatch):
    start = time.time()
    ok = desktop._wait_healthy(_free := desktop._free_port(), timeout=0.6)
    assert ok is False
    assert time.time() - start < 3


def test_wait_healthy_succeeds_when_server_ok(monkeypatch):
    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(desktop.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert desktop._wait_healthy(12345, timeout=1.0) is True


# ------------------------------ _msgbox ---------------------------------- #
def test_msgbox_non_windows(monkeypatch, capsys):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    desktop._msgbox("mensaje de prueba", "Titulo", "warn")
    captured = capsys.readouterr()
    assert "Titulo: mensaje de prueba" in captured.out


def test_msgbox_win_calls_messagebox(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    calls = {}

    class _FakeWindll:
        class user32:
            @staticmethod
            def MessageBoxW(hwnd, text, title, flags):
                calls["text"] = text
                calls["title"] = title
                calls["flags"] = flags
                return 1

    fake_ctypes = types.ModuleType("ctypes")
    fake_ctypes.windll = _FakeWindll()
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    desktop._msgbox("error fatal", "OpenGrab", "error")
    assert calls["text"] == "error fatal"
    assert calls["flags"] == 0x10


# ----------------------- _webview2_available ----------------------------- #
def test_webview2_unavailable_non_windows(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop._webview2_available() is False


def test_webview2_available_when_edgechromium_importable(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")

    fake_webview = types.ModuleType("webview")
    fake_edge = types.ModuleType("webview.platforms.edgechromium")

    class _FakeEdgeChrome:
        pass

    fake_edge.EdgeChrome = _FakeEdgeChrome
    fake_webview.platforms = types.ModuleType("webview.platforms")
    fake_webview.platforms.edgechromium = fake_edge

    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setitem(sys.modules, "webview.platforms", fake_webview.platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", fake_edge)

    assert desktop._webview2_available() is True


def test_webview2_unavailable_when_edgechromium_missing(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")

    fake_webview = types.ModuleType("webview")
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    assert desktop._webview2_available() is False


# ------------------------------ _open_ui --------------------------------- #
def test_open_ui_falls_back_to_browser(monkeypatch):
    monkeypatch.setattr(desktop, "_webview2_available", lambda: False)

    msgbox_calls = []
    monkeypatch.setattr(desktop, "_msgbox", lambda *a, **k: msgbox_calls.append(a))

    opened = {}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda u: opened.setdefault("url", u))

    assert desktop._open_ui(8800) is False
    assert "8800" in opened["url"]
    assert len(msgbox_calls) == 1
    assert "WebView2" in msgbox_calls[0][0]


def test_open_ui_uses_pywebview_when_available(monkeypatch):
    monkeypatch.setattr(desktop, "_webview2_available", lambda: True)

    fake = types.ModuleType("webview")
    calls = {}
    fake.create_window = lambda *a, **k: calls.setdefault("window", True)  # type: ignore[attr-defined]
    fake.start = lambda: calls.setdefault("started", True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)

    assert desktop._open_ui(8800) is True
    assert calls.get("started") is True


def test_open_ui_msgbox_received_on_fallback(monkeypatch):
    """Verifica que el MessageBox de fallback recibe los parámetros correctos."""
    monkeypatch.setattr(desktop, "_webview2_available", lambda: False)
    monkeypatch.setattr(desktop.webbrowser, "open", lambda u: None)

    msgbox_calls = []
    monkeypatch.setattr(desktop, "_msgbox", lambda text, title="", icon="": msgbox_calls.append(
        {"text": text, "title": title, "icon": icon}
    ))

    desktop._open_ui(12345)
    assert msgbox_calls[0]["icon"] == "info"
    assert "reinstalá" in msgbox_calls[0]["text"]


# ----------------------- engine_update (hot-swap) ------------------------ #
def test_should_check_true_when_never_checked(tmp_path):
    assert engine_update.should_check(tmp_path) is True


def test_should_check_throttle(tmp_path):
    engine_update._write_stamp(tmp_path, now=1000.0)
    assert engine_update.should_check(tmp_path, now=1000.0 + 60) is False  # <24h
    assert engine_update.should_check(tmp_path, now=1000.0 + 25 * 3600) is True  # >24h


def test_prepend_to_path_only_if_yt_dlp_present(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_update.sys, "path", list(sys.path))
    assert engine_update.prepend_to_path(tmp_path) is False
    (tmp_path / "yt_dlp").mkdir()
    assert engine_update.prepend_to_path(tmp_path) is True
    assert engine_update.sys.path[0] == str(tmp_path)


def test_prepend_to_path_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_update.sys, "path", list(sys.path))
    (tmp_path / "yt_dlp").mkdir()
    engine_update.prepend_to_path(tmp_path)
    engine_update.prepend_to_path(tmp_path)
    assert engine_update.sys.path.count(str(tmp_path)) == 1


def test_engine_dir_honors_override(monkeypatch):
    monkeypatch.setenv("OPENGRAB_ENGINE_DIR", "/tmp/some/engine")
    assert engine_update._engine_dir() == Path("/tmp/some/engine")
