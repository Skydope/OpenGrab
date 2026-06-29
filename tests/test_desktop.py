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


# ----------------------- _native_webview_available ------------------------ #
def test_native_webview_unavailable_when_pywebview_missing(monkeypatch):
    import builtins

    monkeypatch.setattr(desktop.sys, "platform", "linux")
    _orig_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if name == "webview" or name.startswith("webview."):
            raise ImportError(f"No module named '{name}'")
        return _orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)
    assert desktop._native_webview_available() is False


def test_native_webview_available_windows_with_runtime(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop, "_webview2_runtime_installed", lambda: True)

    fake_webview = types.ModuleType("webview")
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    assert desktop._native_webview_available() is True


def test_native_webview_unavailable_windows_runtime_missing(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop, "_webview2_runtime_installed", lambda: False)

    fake_webview = types.ModuleType("webview")
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    assert desktop._native_webview_available() is False


def test_native_webview_available_linux_with_pywebview(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    fake_webview = types.ModuleType("webview")
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    assert desktop._native_webview_available() is True


# ------------------------------ _open_ui --------------------------------- #
def test_open_ui_falls_back_to_browser(monkeypatch):
    monkeypatch.setattr(desktop, "_native_webview_available", lambda: False)
    opened = {}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda u: opened.setdefault("url", u))
    desktop._open_ui_window(8800)
    assert "8800" in opened["url"]


def test_open_ui_uses_pywebview_when_available(monkeypatch):
    monkeypatch.setattr(desktop, "_native_webview_available", lambda: True)

    fake = types.ModuleType("webview")
    calls = {}
    fake.create_window = lambda *a, **k: calls.setdefault("window", True)  # type: ignore[attr-defined]
    fake.start = lambda: calls.setdefault("started", True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)

    desktop._open_ui_window(8800)
    assert calls.get("started") is True


def test_open_ui_no_msgbox_on_browser_fallback(monkeypatch):
    """En el nuevo diseño, el fallback a navegador es silencioso (el msgbox está en main)."""
    monkeypatch.setattr(desktop, "_native_webview_available", lambda: False)
    monkeypatch.setattr(desktop.webbrowser, "open", lambda u: None)

    msgbox_calls = []
    monkeypatch.setattr(desktop, "_msgbox", lambda *a, **k: msgbox_calls.append(a))

    desktop._open_ui_window(12345)
    assert len(msgbox_calls) == 0


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


# ------------------------ bootstrap (tasks 1.1-1.13) ----------------------- #
def test_bootstrap_without_ini_download_dir(monkeypatch, tmp_path):
    """desktop.py sin ini → fallback ~/Downloads/OpenGrab aunque env no esté."""
    # Sin OPENGRAB_DIR en env
    monkeypatch.delenv("OPENGRAB_DIR", raising=False)
    # Sin ini (configparser no encuentra archivo)
    monkeypatch.setenv("OPENGRAB_CONFIG", str(tmp_path / "no-existe.ini"))
    desktop._setup_env(12345)
    import os

    assert "Downloads" in os.environ["OPENGRAB_DIR"]
    assert os.environ["OPENGRAB_DIR"].endswith("OpenGrab")


def test_main_resolves_env_before_importing_config():
    """Regresión AppImage (Errno 30): ``_setup_env`` debe llamarse ANTES de
    ``from config import ...`` en ``main()``. config.OUT_DIR se congela en el
    import; si OPENGRAB_DIR no está seteada para entonces, cae a "./downloads"
    relativo al CWD — que en un AppImage es el squashfs de solo lectura.
    """
    import inspect

    src = inspect.getsource(desktop.main)
    setup_pos = src.find("_setup_env(")
    config_import_pos = src.find("from config import")

    assert setup_pos != -1, "main() debe llamar a _setup_env"
    assert config_import_pos != -1, "main() debe importar config localmente"
    assert setup_pos < config_import_pos, (
        "_setup_env() debe ejecutarse antes de importar config, o OUT_DIR "
        "se congela con el fallback relativo al CWD (read-only en AppImage)"
    )


def test_bootstrap_respects_env_override(monkeypatch, tmp_path):
    """OPENGRAB_DIR en env tiene prioridad sobre ini y default."""
    monkeypatch.setenv("OPENGRAB_DIR", "/my/custom/folder")
    # Un ini existe pero no debería importaren este caso
    ini_path = tmp_path / "config.ini"
    ini_path.write_text("[opengrab]\ndownload_dir = /ini/folder\n", encoding="utf-8")
    monkeypatch.setenv("OPENGRAB_CONFIG", str(ini_path))
    desktop._setup_env(12345)
    import os

    assert os.environ["OPENGRAB_DIR"] == "/my/custom/folder"


def test_bootstrap_resolves_ini_when_no_env(monkeypatch, tmp_path):
    """Sin env, usa download_dir del ini."""
    monkeypatch.delenv("OPENGRAB_DIR", raising=False)
    ini_path = tmp_path / "config.ini"
    ini_path.write_text("[opengrab]\ndownload_dir = /from/ini/folder\n", encoding="utf-8")
    monkeypatch.setenv("OPENGRAB_CONFIG", str(ini_path))
    desktop._setup_env(12345)
    import os

    assert os.environ["OPENGRAB_DIR"] == "/from/ini/folder"


# ------------------------ logging setup ----------------------------------- #
def test_setup_logging_adds_expected_handler(monkeypatch, tmp_path):
    """_setup_logging agrega RotatingFileHandler (y StreamHandler opcional)."""
    import logging

    monkeypatch.setenv("TEMP", str(tmp_path))
    root = logging.getLogger()
    root.handlers.clear()

    desktop._setup_logging()

    handlers = root.handlers
    assert len(handlers) >= 1
    from logging.handlers import RotatingFileHandler

    has_rotating = any(isinstance(h, RotatingFileHandler) for h in handlers)
    assert has_rotating, f"Expected RotatingFileHandler, got {[type(h).__name__ for h in handlers]}"


def test_setup_logging_replaces_existing_handlers(monkeypatch, tmp_path):
    """_setup_logging limpia handlers previos antes de agregar los propios."""
    import logging

    monkeypatch.setenv("TEMP", str(tmp_path))
    root = logging.getLogger()
    # Simular que hay un handler previo (p.ej. basicConfig en otro contexto)
    prev = logging.StreamHandler()
    root.addHandler(prev)

    desktop._setup_logging()

    assert prev not in root.handlers
    assert len(root.handlers) >= 1


def test_app_import_does_not_configure_root_handler(monkeypatch):
    """Importar app.py NO debe llamar basicConfig — el root logger queda limpio."""
    import logging

    root = logging.getLogger()
    root.handlers.clear()

    # Forzar fresh import de app
    for m in list(sys.modules):
        if m in ("app", "config", "db", "routes", "routers", "state", "download",
                 "engine_update", "models"):
            sys.modules.pop(m, None)

    import app  # noqa: F401 — testea side effect de import (no basicConfig)

    assert root.handlers == [], (
        f"app module-level code configured root handlers: {root.handlers}"
    )

    # Restaurar: el import de app recargó todos los submódulos


# ------------------------ tray reopen event ----------------------------- #
def test_tray_on_open_sets_reopen_event(monkeypatch):
    """Click izq ('Abrir OpenGrab', item default) setea _reopen_event;
    'Abrir en web' abre el navegador; la línea de estado refleja _tray_state."""
    import sys
    import threading
    import types

    reopen_event = threading.Event()
    monkeypatch.setattr(desktop, "_reopen_event", reopen_event)

    opened: dict[str, str] = {}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda u: opened.setdefault("url", u))

    # Captura (text, action). text puede ser str o callable (línea de estado).
    items: list[tuple[object, object]] = []

    class _FakeMenuItem:
        def __init__(self, text: object, action: object,
                     default: bool = False, enabled: bool = True) -> None:
            items.append((text, action))

    fake_pystray = types.ModuleType("pystray")
    fake_pystray.Menu = lambda *items: None  # type: ignore[attr-defined]
    fake_pystray.MenuItem = _FakeMenuItem  # type: ignore[attr-defined]
    fake_pystray.Menu.SEPARATOR = object()  # type: ignore[attr-defined]

    class _FakeIcon:
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        def run(self, setup: object = None) -> None:
            # No invocamos setup: evita arrancar el poller (red) en el test.
            pass

    fake_pystray.Icon = _FakeIcon  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(desktop, "_get_tray_image", lambda active=None: None)

    desktop._system_tray(12345)

    # Click izquierdo (default):
    on_open = next((a for t, a in items if t == "Abrir OpenGrab"), None)
    assert on_open is not None, "No se capturó el callback de 'Abrir OpenGrab'"

    # En Windows → señaliza reopen (WebView2 vía main thread), sin abrir browser.
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop, "_native_webview_available", lambda: True)
    on_open(None, None)
    assert reopen_event.is_set(), "_reopen_event debería estar seteado en Windows"
    assert "url" not in opened, "En Windows no debe abrir el navegador"

    # En Linux con pywebview → también señaliza reopen (GTK requiere main thread).
    reopen_event.clear()
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop, "_native_webview_available", lambda: True)
    on_open(None, None)
    assert reopen_event.is_set(), "_reopen_event debería estar seteado con webview nativo"
    assert "url" not in opened, "Con webview nativo no debe abrir el navegador"

    # En Linux sin pywebview → abre el navegador directo (fallback).
    reopen_event.clear()
    opened.clear()
    monkeypatch.setattr(desktop, "_native_webview_available", lambda: False)
    on_open(None, None)
    assert not reopen_event.is_set(), "Sin webview nativo no debe usar _reopen_event"
    assert opened.get("url") == "http://127.0.0.1:12345"

    # 'Abrir en web' → navegador en cualquier plataforma.
    opened.clear()
    on_web = next((a for t, a in items if t == "Abrir en web"), None)
    assert on_web is not None, "No se capturó el callback de 'Abrir en web'"
    on_web(None, None)
    assert opened.get("url") == "http://127.0.0.1:12345"

    # Línea de estado: el text es un callable que lee _tray_state.
    estado_text = next((t for t, _ in items if callable(t)), None)
    assert estado_text is not None, "No se encontró la línea de estado (callable)"
    monkeypatch.setitem(desktop._tray_state, "active", True)
    monkeypatch.setitem(desktop._tray_state, "estado", "Descargando 42%")
    rendered = estado_text(None)
    assert "Estado:" in rendered and "Descargando 42%" in rendered
