"""Hot-swap de yt-dlp para el binario de escritorio.

El binario bundlea un yt-dlp como piso (vía ``collect_all`` en el .spec, que lo deja
suelto y resoluble por el PathFinder normal — ver el spike en binary-plan.md §2.3).
Este módulo descarga el wheel más nuevo de yt-dlp a un directorio escribible del usuario
y lo antepone a ``sys.path``, de modo que ``import yt_dlp`` levante la versión nueva sin
recompilar el .exe ni necesitar pip.

Las funciones puras (``_engine_dir``, ``should_check``, ``prepend_to_path``) no tocan red
y son las que cubren los tests. ``check_and_update`` es el punto de entrada con red.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

_THROTTLE_SECONDS = 24 * 60 * 60  # no chequear más de 1 vez por día
_STAMP_NAME = ".last_update"
_PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"


def _engine_dir() -> Path:
    """Directorio escribible donde vive el yt-dlp hot-swappeado.

    Windows: ``%LOCALAPPDATA%\\OpenGrab\\engine``. Otros (dev/tests): ``~/.local/share``.
    Se puede forzar con ``OPENGRAB_ENGINE_DIR`` (lo usan los tests).
    """
    override = os.environ.get("OPENGRAB_ENGINE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "OpenGrab" / "engine"


def should_check(engine_dir: Path, now: float | None = None) -> bool:
    """True si pasó el throttle desde la última verificación (función pura)."""
    now = time.time() if now is None else now
    stamp = engine_dir / _STAMP_NAME
    try:
        last = float(stamp.read_text().strip())
    except (OSError, ValueError):
        return True  # nunca se chequeó
    return (now - last) >= _THROTTLE_SECONDS


def _write_stamp(engine_dir: Path, now: float | None = None) -> None:
    now = time.time() if now is None else now
    engine_dir.mkdir(parents=True, exist_ok=True)
    (engine_dir / _STAMP_NAME).write_text(str(now))


def prepend_to_path(engine_dir: Path) -> bool:
    """Antepone engine_dir a sys.path si contiene un yt_dlp. Devuelve si lo hizo.

    Idempotente. Debe llamarse ANTES del primer ``import yt_dlp`` (función pura)."""
    if (engine_dir / "yt_dlp").is_dir():
        p = str(engine_dir)
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
        return True
    return False


def _latest_wheel_url() -> tuple[str, str]:
    """(version, url) del wheel más nuevo de yt-dlp en PyPI. Toca red."""
    # URL constante https a PyPI
    with urllib.request.urlopen(_PYPI_JSON, timeout=15) as r:  # nosec B310
        data = json.load(r)
    version = data["info"]["version"]
    for f in data["releases"].get(version, []):
        if f["filename"].endswith(".whl"):
            return version, f["url"]
    raise RuntimeError("no se encontró wheel de yt-dlp en PyPI")


def _install_wheel(url: str, engine_dir: Path) -> None:
    """Descarga el wheel (un zip) y extrae yt_dlp/ a engine_dir. Toca red."""
    engine_dir.mkdir(parents=True, exist_ok=True)
    tmp = engine_dir / "_yt_dlp.whl"
    # URL de release de PyPI (https, derivada del JSON oficial)
    with urllib.request.urlopen(url, timeout=60) as r:  # nosec B310
        tmp.write_bytes(r.read())
    with zipfile.ZipFile(tmp) as z:
        for name in z.namelist():
            if name.startswith("yt_dlp/"):
                z.extract(name, engine_dir)
    tmp.unlink(missing_ok=True)


def check_and_update(force: bool = False) -> dict[str, object]:
    """Punto de entrada. Antepone el engine existente y, si corresponde, actualiza.

    Devuelve un dict de estado (sirve para el endpoint /api/engine/update). Nunca
    levanta hacia afuera: ante cualquier fallo, cae al yt-dlp bundleado.
    """
    engine_dir = _engine_dir()
    result: dict[str, object] = {"updated": False, "version": None, "used_bundled": True}

    if force or should_check(engine_dir):
        try:
            version, url = _latest_wheel_url()
            _install_wheel(url, engine_dir)
            _write_stamp(engine_dir)
            result.update(updated=True, version=version)
            try:
                from metrics import ytdlp_version
                ytdlp_version.info({"version": version})
            except Exception:
                pass  # metrics module may not be loaded yet (pre-lifespan)
        except Exception as exc:
            result["error"] = str(exc)

    if prepend_to_path(engine_dir):
        result["used_bundled"] = False
    return result
