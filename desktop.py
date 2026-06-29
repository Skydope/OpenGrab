"""Entrypoint de escritorio de OpenGrab (modo binario .exe).

Levanta el server FastAPI en loopback con un puerto efímero, sin auth (single-user),
guarda en la carpeta de Descargas del usuario, hace hot-swap de yt-dlp y abre la UI
(ventana nativa vía WebView2 + pywebview, con fallback al navegador). Single-instance
crash-safe: named mutex en Windows, flock en el resto.

La ventana se abre al iniciar. Al cerrarla, la app sigue viva en la bandeja del sistema
con un menú "Abrir OpenGrab" (reabre ventana WebView2) y "Salir" (termina el proceso).

Las funciones ``_free_port``, ``_setup_env``, ``_wait_healthy`` y ``acquire_single_instance``
están factorizadas para testearse sin levantar uvicorn ni abrir navegadores reales.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pystray

# Flag de modo desktop — debe setearse antes de cualquier import de config.
os.environ.setdefault("OPENGRAB_DESKTOP", "1")

_HEALTH_TIMEOUT = 10.0
_lock_handle: object = None
_server_error: Exception | None = None
_log = logging.getLogger("opengrab.desktop")
_tray_icon: pystray.Icon | None = None  # seteado por _system_tray()
_reopen_event = threading.Event()      # señal tray → main para reopen WebView2
_tray_stop = threading.Event()         # corta el poller del tray al salir
_tray_state: dict[str, object] = {     # estado vivo mostrado en el tray
    "active": False,
    "tooltip": "OpenGrab",
    "estado": "Inactivo",
}


def _setup_logging() -> None:
    """Configura logging a archivo para modo desktop (console=False).

    Escribe a ``%TEMP%\\opengrab.log`` con rotación (5 MB, 3 backups).
    Si hay consola visible, también emite a stderr.
    Debe llamarse **antes** de ``_serve()`` para que uvicorn herede la config.
    """
    import logging.handlers

    log_dir = Path(os.environ.get("TEMP", str(Path.home())))
    log_path = log_dir / "opengrab.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    fh = logging.handlers.RotatingFileHandler(
        str(log_path),
        encoding="utf-8",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(fh)

    # StreamHandler solo si hay TTY (console=True en PyInstaller)
    try:
        if sys.stdout and hasattr(sys.stdout, "fileno") and os.isatty(sys.stdout.fileno()):
            sh = logging.StreamHandler()
            sh.setLevel(logging.INFO)
            sh.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
            root.addHandler(sh)
    except (OSError, AttributeError):
        pass

    # Silenciar access log de uvicorn (ruido)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # Silenciar imports de PIL (ruido al cargar _get_tray_image)
    logging.getLogger("PIL").setLevel(logging.WARNING)


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
    # Resolver OPENGRAB_DIR ANTES de importar config. OUT_DIR se computa
    # a nivel de módulo — si no seteamos la variable a tiempo, usa el
    # fallback "./downloads" (relativo al CWD) en vez del directorio desktop.
    if "OPENGRAB_DIR" not in os.environ:
        import configparser

        # Misma ruta de INI que config._load_ini().
        if sys.platform == "win32":
            base = Path(os.environ.get(
                "APPDATA", str(Path.home() / "AppData" / "Roaming")
            ))
        else:
            base = Path(os.environ.get(
                "XDG_CONFIG_HOME", str(Path.home() / ".config")
            ))
        ini_path = os.environ.get(
            "OPENGRAB_CONFIG", str(base / "OpenGrab" / "config.ini")
        )

        default_dir = ""
        try:
            cp = configparser.ConfigParser()
            cp.read(ini_path, encoding="utf-8")
            if "opengrab" in cp:
                default_dir = cp["opengrab"].get("download_dir", "")
        except Exception:
            pass

        if not default_dir:
            default_dir = str(Path.home() / "Downloads" / "OpenGrab")

        os.environ["OPENGRAB_DIR"] = default_dir
        # Nota: NO importamos config acá — lo hará _serve() cuando arranque la
        # app. Para ese momento OPENGRAB_DIR ya está en el entorno y OUT_DIR
        # tomará el valor correcto.


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
        except Exception:
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
            log_config=None,
        )
    except Exception as exc:
        _server_error = exc


def _webview2_runtime_installed() -> bool:
    """True si el runtime de WebView2 (Evergreen) está instalado en el sistema.

    Consulta el registro de Windows buscando la key de EdgeUpdate para el runtime
    o los canales de Edge (Beta/Dev/Canary). Para HKLM prueba tanto el path nativo
    como WOW6432Node: el bootstrapper oficial de 32-bit escribe en WOW6432Node, y
    Python 64-bit no redirige automáticamente.
    """
    if sys.platform != "win32":
        return False
    import winreg

    _hklm_roots: tuple[str, ...] = (
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
        r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
    )

    builds: list[tuple[str, str]] = [
        ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "WebView2 Runtime"),
        ("{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}", "Edge Beta"),
        ("{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "Edge Dev"),
        ("{65C35B14-6C1D-4122-AC46-7148CC9D6497}", "Edge Canary"),
    ]

    for guid, _desc in builds:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            if hive == winreg.HKEY_LOCAL_MACHINE:
                roots = _hklm_roots
            else:
                roots = (r"Software\Microsoft\EdgeUpdate\Clients",)
            for root in roots:
                try:
                    with winreg.OpenKey(hive, rf"{root}\{guid}") as key:
                        pv, _ = winreg.QueryValueEx(key, "pv")
                        major = int(str(pv).split(".")[0])
                        if major >= 86:  # mínimo para WebView2
                            _log.debug("WebView2 encontrado: %s v%s", _desc, pv)
                            return True
                except (OSError, ValueError, IndexError):
                    continue

    return False


def _webview2_available() -> bool:
    """True si podemos abrir una ventana nativa con WebView2 + pywebview.

    Verifica tres condiciones:
    1. Estamos en Windows (única plataforma con WebView2).
    2. pythonnet + webview son importables (los DLLs nativos están disponibles).
    3. El runtime de WebView2 está instalado en el sistema (registro).
    """
    if sys.platform != "win32":
        return False

    try:
        import webview  # noqa: F401
        from webview.platforms.edgechromium import EdgeChrome  # noqa: F401
    except ImportError as exc:
        _log.warning("webview2_available: falló import de pywebview: %s", exc)
        return False

    if not _webview2_runtime_installed():
        _log.warning("webview2_available: runtime de WebView2 no encontrado")
        return False

    return True


class _JsApi:
    """Puente JS↔Python expuesto en la ventana WebView2 como ``window.pywebview.api``.

    El front lo usa para abrir un diálogo nativo de selección de carpeta (botón
    "Guardar en…"). Solo existe cuando la UI corre dentro de WebView2; en el
    fallback al navegador ``window.pywebview`` no está definido y el front cae a
    pedir la ruta por otro medio.
    """

    def pick_folder(self) -> str | None:
        """Abre un diálogo nativo de carpeta. Devuelve la ruta o ``None`` si se canceló."""
        try:
            import webview

            windows = webview.windows
            if not windows:
                return None
            result = windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:
            _log.exception("pick_folder: falló el diálogo nativo")
            return None
        if not result:
            return None
        # create_file_dialog devuelve una tupla/lista de rutas (o str en algunas
        # plataformas). Tomamos la primera.
        if isinstance(result, (list, tuple)):
            return str(result[0]) if result else None
        return str(result)


def _open_ui_window(port: int) -> None:
    """Abre la UI con WebView2 (bloquea el thread caller hasta que se cierra).

    Si WebView2 no está disponible o falla, abre en el navegador."""
    url = f"http://127.0.0.1:{port}"
    if _webview2_available():
        try:
            import webview

            webview.create_window(
                "OpenGrab", url, width=980, height=720, js_api=_JsApi(),
            )
            webview.start()
        except Exception:
            _log.exception("webview falló, abriendo en navegador")
            webbrowser.open(url)
    else:
        webbrowser.open(url)


def _get_tray_image(active: bool | None = None) -> object:
    """Genera el icono de la bandeja (sin depender de archivos externos).

    Si ``active`` no es ``None``, dibuja un punto de estado abajo-derecha:
    verde si está descargando, rojo si está inactivo.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Fondo redondeado oscuro
    draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(28, 33, 43, 255))
    # Play button simplificado (triángulo ámbar)
    draw.polygon([(24, 18), (24, 46), (44, 32)], fill=(232, 160, 44, 255))
    if active is not None:
        color = (46, 204, 113, 255) if active else (231, 76, 60, 255)
        draw.ellipse([42, 42, 60, 60], fill=color, outline=(28, 33, 43, 255), width=2)
    return img


def _format_tray_status(jobs: list[dict[str, Any]]) -> tuple[bool, str, str]:
    """Resume el estado de descargas para la bandeja, a partir de ``/api/jobs``.

    Devuelve ``(active, tooltip, estado)``:
      - ``active``: hay al menos un job descargando o procesando.
      - ``tooltip``: texto de hover del icono (truncado a ~120 chars).
      - ``estado``: etiqueta para la línea 'Estado:' del menú.

    Función pura (sin I/O) para poder testearla sin GUI ni red.
    """
    downloading = [j for j in jobs if j.get("status") in ("downloading", "processing")]
    if downloading:
        j = max(downloading, key=lambda x: x.get("percent") or 0.0)
        pct = int(j.get("percent") or 0)
        title = str(j.get("title") or j.get("filename") or "descarga").strip()
        short = title if len(title) <= 60 else title[:59] + "…"
        n = len(downloading)
        suffix = f" (+{n - 1})" if n > 1 else ""
        tooltip = f"OpenGrab — ↓ {pct}% · {short}{suffix}"
        estado = f"Descargando {pct}% · {short}{suffix}"
        return True, tooltip[:120], estado
    queued = [j for j in jobs if j.get("status") in ("queued", "starting")]
    if queued:
        return False, "OpenGrab — en cola", f"En cola ({len(queued)})"
    return False, "OpenGrab — inactivo", "Inactivo"


def _poll_tray_status(port: int) -> None:
    """Pollea ``/api/jobs`` y refresca tooltip, icono (punto verde/rojo) y menú.

    Corre en thread daemon hasta que ``_tray_stop`` se setea (al salir). En
    modo desktop ``OPENGRAB_NO_AUTH=1``, así que no necesita token.
    """
    import json

    url = f"http://127.0.0.1:{port}/api/jobs?recent=0"
    last_active: bool | None = None
    while not _tray_stop.wait(1.5):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                payload = json.loads(r.read().decode("utf-8"))
            jobs = payload if isinstance(payload, list) else []
        except Exception:
            jobs = []
        active, tooltip, estado = _format_tray_status(jobs)
        _tray_state.update(active=active, tooltip=tooltip, estado=estado)
        icon = _tray_icon
        if icon is None:
            continue
        try:
            icon.title = tooltip
            if active != last_active:
                icon.icon = _get_tray_image(active)
                last_active = active
            icon.update_menu()
        except Exception:
            _log.debug("poll_tray_status: no se pudo actualizar el icono", exc_info=True)


def _system_tray(port: int) -> None:
    """Bandeja del sistema. Corre en thread secundario (no-daemon).

    Click izquierdo (item ``default``) → en Windows señaliza al main thread vía
    ``_reopen_event`` para abrir WebView2; en Linux/macOS abre el navegador
    directo. Click derecho → menú con el estado vivo de la descarga
    ("Estado: 🟢/🔴 …"), "Abrir en web" (navegador) y "Salir". Un poller daemon
    refresca tooltip, icono y menú cada ~1.5s.
    """
    global _tray_icon

    try:
        import pystray

        def _estado_text(item: object) -> str:
            dot = "🟢" if _tray_state.get("active") else "🔴"
            return f"Estado: {dot} {_tray_state.get('estado', 'Inactivo')}"

        def _on_open(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            # Windows: WebView2 requiere main thread → señalizamos vía evento.
            # Linux/macOS: no hay WebView2, abrimos el navegador directo (no
            # necesita main thread) en vez de depender del fallback del loop.
            if sys.platform == "win32":
                _reopen_event.set()
            else:
                webbrowser.open(f"http://127.0.0.1:{port}")

        def _on_open_web(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            webbrowser.open(f"http://127.0.0.1:{port}")

        def _on_exit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            _log.info("tray: salir solicitado por usuario")
            _tray_stop.set()
            icon.stop()

        def _setup(icon: pystray.Icon) -> None:
            icon.visible = True
            threading.Thread(
                target=_poll_tray_status, args=(port,),
                daemon=True, name="og-tray-poll",
            ).start()

        _tray_icon = pystray.Icon(
            "OpenGrab",
            _get_tray_image(False),
            "OpenGrab",
            menu=pystray.Menu(
                pystray.MenuItem(_estado_text, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Abrir OpenGrab", _on_open, default=True),
                pystray.MenuItem("Abrir en web", _on_open_web),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", _on_exit),
            ),
        )
        _tray_icon.run(setup=_setup)
        _log.info("tray: icon.run() retornó")
    except Exception:
        _log.exception("tray: error inesperado")


def main() -> int:
    if not acquire_single_instance():
        _msgbox(
            "OpenGrab ya está corriendo.\n\n"
            "Revisá la barra de tareas o la bandeja del sistema.",
            "OpenGrab", "info",
        )
        return 0

    _setup_logging()

    # OPENGRAB_DIR debe resolverse ANTES de importar config: config.OUT_DIR se
    # computa a nivel de módulo durante el import y, sin la variable seteada,
    # cae al fallback "./downloads" relativo al CWD. En un AppImage el CWD es el
    # montaje squashfs de solo lectura → OSError [Errno 30] al hacer mkdir.
    port = _free_port()
    _setup_env(port)

    from config import VERSION

    _log.info("OpenGrab desktop v%s iniciado — %s", VERSION, sys.platform)

    # Hot-swap de yt-dlp ANTES de importar app (que importa download → yt_dlp).
    try:
        import engine_update

        engine_update.check_and_update()
    except Exception as exc:
        _msgbox(f"No se pudo actualizar yt-dlp:\n{exc}", "OpenGrab", "warn")

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

    # Tray en thread no-daemon → vive independiente del main thread.
    # pywebview 6.x requiere webview.start() en thread principal.
    # El tray señaliza al main thread vía _reopen_event para reabrir ventanas.
    tray_thread = threading.Thread(
        target=_system_tray, args=(port,), daemon=False, name="og-tray",
    )
    tray_thread.start()

    try:
        has_webview = _webview2_available()
        if has_webview:
            _open_ui_window(port)
            _log.info("ventana cerrada, app sigue en tray")
        else:
            webbrowser.open(f"http://127.0.0.1:{port}")

        while tray_thread.is_alive():
            if _reopen_event.wait(timeout=0.5):
                _reopen_event.clear()
                if has_webview:
                    _log.info("reabriendo ventana WebView2 desde tray")
                    _open_ui_window(port)
                else:
                    webbrowser.open(f"http://127.0.0.1:{port}")
    finally:
        tray_thread.join(timeout=8)
        if tray_thread.is_alive():
            _log.warning("tray thread no terminó en 8s")

    _log.info("OpenGrab finalizado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
