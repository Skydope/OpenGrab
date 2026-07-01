from __future__ import annotations

import configparser
import os
import secrets
import sys
from pathlib import Path


def _int_env(key: str, default: int, min_val: int | None = None) -> int:
    raw = os.environ.get(key, str(default))
    try:
        val = int(raw)
    except ValueError:
        print(
            f"[opengrab] WARNING: {key}={raw!r} invalido, usando {default}",
            file=sys.stderr,
        )
        return default
    if min_val is not None and val < min_val:
        print(
            f"[opengrab] WARNING: {key}={val} menor que minimo {min_val}, usando {min_val}",
            file=sys.stderr,
        )
        return min_val
    return val


def _load_ini() -> dict[str, str]:
    """Lee ``config.ini`` como fuente de defaults para desktop.

    El instalador Inno Setup escribe ``%APPDATA%\\OpenGrab\\config.ini``.
    En Docker no existe → dict vacío → comportamiento sin cambios.
    Las variables de entorno siempre tienen precedencia sobre el INI.
    """
    if sys.platform == "win32":
        base = Path(
            os.environ.get(
                "APPDATA", str(Path.home() / "AppData" / "Roaming")
            )
        )
    else:
        base = Path(
            os.environ.get(
                "XDG_CONFIG_HOME", str(Path.home() / ".config")
            )
        )
    ini_path = os.environ.get(
        "OPENGRAB_CONFIG", str(base / "OpenGrab" / "config.ini")
    )
    try:
        cp = configparser.ConfigParser()
        cp.read(ini_path, encoding="utf-8")
        return dict(cp["opengrab"]) if "opengrab" in cp else {}
    except Exception:  # INI faltante o corrupto es normal (primer arranque, FS roto); fallback silencioso
        return {}


_ini = _load_ini()

# Mapeo de keys de settings a variables de entorno (para el resolver).
_SETTING_ENV: dict[str, str] = {
    "max_jobs": "OPENGRAB_MAX_JOBS",
    "max_total_mb": "OPENGRAB_MAX_TOTAL_MB",
    "max_size_mb": "OPENGRAB_MAX_SIZE_MB",
    "history_max": "OPENGRAB_HISTORY_MAX",
}


def _ini_int(key: str, default: int) -> int:
    raw = _ini.get(key, "")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# Constants (env vars override INI values)
# --------------------------------------------------------------------------- #
HOST = os.environ.get("OPENGRAB_HOST", "127.0.0.1")
PORT = _int_env("OPENGRAB_PORT", _ini_int("port", 8800), min_val=1)

IS_DESKTOP = os.environ.get("OPENGRAB_DESKTOP", "").strip() == "1"


def _default_download_dir() -> str:
    """Fallback de OUT_DIR cuando ni OPENGRAB_DIR ni el INI lo definen.

    En modo desktop el CWD puede ser de solo lectura (un AppImage corre desde
    un montaje squashfs), por lo que el fallback es un path absoluto en el home
    en vez de "./downloads" relativo al CWD. Esto hace a OUT_DIR robusto al
    orden de imports: aunque config se importe antes de que el entrypoint
    resuelva OPENGRAB_DIR, el fallback nunca resuelve contra el CWD read-only.
    En Docker/dev (sin OPENGRAB_DESKTOP) se mantiene "./downloads", relativo al
    repo, que es el contrato existente.
    """
    if IS_DESKTOP:
        return str(Path.home() / "Downloads" / "OpenGrab")
    return "./downloads"


OUT_DIR = Path(
    os.environ.get(
        "OPENGRAB_DIR",
        _ini.get("download_dir", _default_download_dir()),
    )
).resolve()

# --- Auth ---
_raw_token = os.environ.get("OPENGRAB_TOKEN", "").strip()
if not _raw_token:
    _raw_token = _ini.get("token", "").strip()

_no_auth_env = os.environ.get("OPENGRAB_NO_AUTH", "").strip()
_ini_no_auth = _ini.get("no_auth", "").strip().lower()

if _no_auth_env == "1":
    TOKEN = ""
    TOKEN_WAS_GENERATED = False
elif _ini_no_auth in ("true", "1", "yes"):
    TOKEN = ""
    TOKEN_WAS_GENERATED = False
elif not _raw_token:
    TOKEN = secrets.token_urlsafe(16)
    TOKEN_WAS_GENERATED = True
else:
    TOKEN = _raw_token
    TOKEN_WAS_GENERATED = False

# Nota: max_jobs / max_size_mb / max_total_mb NO viven como constantes acá.
# Son settings con respaldo en la tabla y se consumen en vivo via
# state.resolve(...) (dispatcher, download, limites), lo que permite editarlas
# desde la UI sin reiniciar. Mantener constantes import-time aca duplicaria la
# fuente de verdad y confundiria (editarlas no tendria efecto).

LOG_FORMAT = os.environ.get(
    "OPENGRAB_LOG_FORMAT", _ini.get("log_format", "text")
).strip().lower()
if LOG_FORMAT not in ("text", "json"):
    LOG_FORMAT = "text"
LOG_LEVEL = os.environ.get(
    "OPENGRAB_LOG_LEVEL", _ini.get("log_level", "INFO")
).strip().upper()

TRUST_XFF = os.environ.get("OPENGRAB_TRUST_XFF", "").strip() == "1"
SECURE_DELETE = os.environ.get("OPENGRAB_SECURE_DELETE", "0").strip() == "1"
DB_PATH = OUT_DIR / "opengrab.db"


def resource_path(rel: str) -> Path:
    """Resuelve recursos bundleados tanto en dev como bajo PyInstaller.

    En un binario congelado, los recursos se extraen a ``sys._MEIPASS`` (onefile)
    o viven junto al ejecutable (onedir); fuera del binario, son relativos a este
    archivo. No rompe el modo Docker/dev: si ``_MEIPASS`` no existe, cae al path normal.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / rel


def _get_version() -> str:
    import tomllib

    pyproject = resource_path("pyproject.toml")
    if pyproject.exists():
        with open(pyproject, "rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    return "0.0.0"


VERSION = _get_version()

FORMATS = {
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bv*+ba/b",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]",
    "480p":  "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/"
             "best[height<=480][ext=mp4]/bv*[height<=480]+ba/b[height<=480]",
    "audio": "bestaudio/best",
    "worst": "worst[ext=mp4]/worst",
}


_STATIC_DIR = resource_path("static")
