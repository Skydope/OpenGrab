"""Entrypoint de escritorio de OpenGrab (modo binario .exe).

Levanta el server FastAPI en loopback con un puerto efímero, sin auth (single-user),
guarda en la carpeta de Descargas del usuario, hace hot-swap de yt-dlp y abre la UI
(ventana nativa vía WebView2 + pywebview, con fallback al navegador). Single-instance
crash-safe: named mutex en Windows, flock en el resto.

Las funciones ``_free_port``, ``_setup_env``, ``_wait_healthy`` y ``acquire_single_instance``
están factorizadas para testearse sin levantar uvicorn ni abrir navegadores reales.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

_HEALTH_TIMEOUT = 10.0
_lock_handle: object = None
_server_error: Exception | None = None


def _msgbox(text: str, title: str = "OpenGrab", icon: str = "error") -> None:
    """Muestra un MessageBox. En modo ``--windowed``, ``print()`` no se ve."""
    if sys.platform != "win32":
        print(f"[opengrab] {title}: {text}")
        return
    import ctypes

    icons = {"error": 0x10, "warn": 0x30, "info": 0x40}
    ctypes.windll.user32.MessageBoxW(0, text, title, icons.get(icon, 0x10))


def _free_port() -> int:
    """Puerto efímero asignado por el SO (evita el clásico '8800 ocupado')."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _setup_env(port: int) -> None:
    """Defaults de escritorio. ``setdefault`` para respetar overrides del usuario."""
    os.environ.setdefault("OPENGRAB_HOST", "127.0.0.1")
    os.environ.setdefault("OPENGRAB_PORT", str(port))
    os.environ.setdefault("OPENGRAB_NO_AUTH", "1")
    os.environ.setdefault(
        "OPENGRAB_DIR", str(Path.home() / "Downloads" / "OpenGrab")
    )


def acquire_single_instance(name: str = "OpenGrab_SingleInstance") -> bool:
    """True si esta es la única instancia; False si ya hay otra corriendo.

    Windows: named mutex (el SO lo libera al morir el proceso → crash-safe).
    Resto: flock sobre un lockfile en el tmpdir (también liberado por el SO).
    """
    global _lock_handle
    if sys.platform == "win32":
        import ctypes

        ERROR_ALREADY_EXISTS = 183
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        _lock_handle = handle
        return True

    import tempfile

    import fcntl

    lock_path = Path(tempfile.gettempdir()) / f"{name}.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _lock_handle = fh
    return True


def _wait_healthy(port: int, timeout: float = _HEALTH_TIMEOUT) -> bool:
    """Reintenta GET /health hasta 200 o timeout. True si el server quedó vivo."""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 — el server todavía no levantó
            time.sleep(0.2)
    return False


def _serve(port: int) -> None:
    global _server_error
    try:
        import uvicorn

        from app import app

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            log_config={"version": 1, "disable_existing_loggers": True},
        )
    except Exception as exc:
        _server_error = exc


def _webview2_available() -> bool:
    """True si el runtime de WebView2 está disponible para pywebview."""
    if sys.platform != "win32":
        return False
    try:
        import webview  # type: ignore[import-not-found,unused-ignore]
        from webview.platforms.edgechromium import EdgeChrome  # noqa: F401  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return False
    return True


def _open_ui(port: int) -> bool:
    """Abre la UI. True si ventana nativa (bloqueante), False si navegador.

    Prefiere pywebview + WebView2 (ventana nativa). Si WebView2 no está instalado,
    muestra un aviso y cae al navegador del sistema.
    """
    url = f"http://127.0.0.1:{port}"
    if _webview2_available():
        import webview

        webview.create_window("OpenGrab", url, width=980, height=720)
        webview.start()
        return True

    _msgbox(
        "WebView2 Runtime no está instalado.\n\n"
        "OpenGrab se abrirá en tu navegador.\n"
        "Para usar la ventana nativa, reinstalá OpenGrab y\n"
        "asegurate de marcar 'WebView2 Runtime'.",
        "OpenGrab", "info",
    )
    webbrowser.open(url)
    return False


def main() -> int:
    if not acquire_single_instance():
        _msgbox(
            "OpenGrab ya está corriendo.\n\n"
            "Revisá la barra de tareas o la bandeja del sistema.",
            "OpenGrab", "info",
        )
        return 0

    # Hot-swap de yt-dlp ANTES de importar app (que importa download → yt_dlp).
    try:
        import engine_update

        engine_update.check_and_update()
    except Exception as exc:  # noqa: BLE001 — degradar al yt-dlp bundleado
        _msgbox(f"No se pudo actualizar yt-dlp:\n{exc}", "OpenGrab", "warn")

    port = _free_port()
    _setup_env(port)

    threading.Thread(target=_serve, args=(port,), daemon=True).start()

    if not _wait_healthy(port):
        if _server_error is not None:
            _msgbox(
                f"Error al iniciar el servidor:\n{_server_error}",
                "OpenGrab", "error",
            )
        else:
            _msgbox(
                "El servidor no pudo iniciarse.\n\n"
                "Revisá que el puerto no esté bloqueado por un firewall.",
                "OpenGrab", "error",
            )
        return 1

    # pywebview bloquea hasta cerrar la ventana; el navegador retorna al toque
    # y entonces hay que mantener vivo el proceso (server en thread daemon).
    if not _open_ui(port):
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
